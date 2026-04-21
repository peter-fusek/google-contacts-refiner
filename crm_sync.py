"""
CRM Sync — sync CRM notes and tags from dashboard to Google Contacts.

Reads crm_state.json from GCS/local data dir and:
  1. Writes CRM notes to Google Contacts biographies (marker block pattern)
  2. Syncs CRM tags to Google Contacts groups (additive only, never removes)
  3. (Opt-in via ENABLE_OMNICHANNEL_WRITEBACK) writes a metadata-only
     Omnichannel block summarizing multi-channel contact activity into
     the same biography. Zero message content per
     docs/schemas/interaction.md §Privacy defaults.

Uses the same marker block pattern as interaction_scanner.py and linkedin_scanner.py.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    DATA_DIR,
    FOLLOWUP_BEEPER_KPI_FILE,
    FOLLOWUP_OWN_COMPANY_DOMAINS,
    FOLLOWUP_OWN_COMPANY_ORG_KEYWORDS,
)
from harvester.crm_omnichannel import (
    backup_biographies,
    build_block,
    merge_into_biography,
    should_update,
)
from harvester.scoring_signals import load_kpis_from_json

logger = logging.getLogger("crm_sync")

CRM_NOTE_MARKER = "── CRM Notes"
CRM_TAG_PREFIX = "CRM:"

# Google People API write rate cap is 60 QPM (documented on the quotas page).
# We keep a local floor to avoid bursts that can trigger backoff — matches the
# rollback scripts' convention for Sprint 3.33 S3.
_PEOPLE_API_MIN_INTERVAL_SECONDS = 1.1


def load_crm_state() -> dict:
    """Load crm_state.json from local data dir."""
    path = DATA_DIR / "crm_state.json"
    if not path.exists():
        logger.warning("crm_state.json not found at %s", path)
        return {"version": 1, "contacts": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_crm_state(state: dict) -> None:
    """Save crm_state.json back to local data dir."""
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path = DATA_DIR / "crm_state.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _strip_crm_block(note: str) -> str:
    """Remove the CRM Notes marker block from a biography note."""
    if CRM_NOTE_MARKER not in note:
        return note

    lines = note.split("\n")
    result = []
    in_block = False
    for line in lines:
        if CRM_NOTE_MARKER in line:
            in_block = True
            continue
        if in_block:
            if not line.strip():
                in_block = False
                continue
            if line.startswith("──"):
                # Next marker block — stop skipping
                in_block = False
                result.append(line)
                continue
            continue  # Skip content lines within block
        result.append(line)

    return "\n".join(result).strip()


def _build_crm_block(notes: str) -> str:
    """Build a CRM Notes marker block."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = f"{CRM_NOTE_MARKER} (updated {date_str}) ──"
    return f"{header}\n{notes.strip()}"


def _insert_crm_block(existing_note: str, crm_block: str) -> str:
    """Insert CRM Notes block into biography, after other marker blocks if present."""
    if not existing_note.strip():
        return crm_block

    # Insert at end of marker blocks, before user's free text
    # Strategy: find the last marker block end, insert after it
    lines = existing_note.split("\n")
    last_marker_end = -1
    in_block = False

    for i, line in enumerate(lines):
        if "──" in line and line.strip().startswith("──"):
            in_block = True
            last_marker_end = i
            continue
        if in_block:
            if not line.strip():
                last_marker_end = i
                in_block = False
            else:
                last_marker_end = i

    if last_marker_end >= 0:
        # Insert after the last marker block
        before = lines[:last_marker_end + 1]
        after = lines[last_marker_end + 1:]
        return "\n".join(before) + "\n\n" + crm_block + ("\n\n" + "\n".join(after).strip() if "\n".join(after).strip() else "")

    # No marker blocks found — prepend
    return f"{crm_block}\n\n{existing_note.strip()}"


def sync_notes(client, crm_state: dict, dry_run: bool = False) -> dict:
    """
    Sync CRM notes to Google Contacts biographies.

    Only syncs contacts where notes are non-empty and have changed since last sync.
    Uses marker block pattern to isolate CRM content from other note blocks.

    Returns dict with counts: synced, skipped, errors.
    """
    contacts = crm_state.get("contacts", {})
    synced = 0
    skipped = 0
    errors = 0

    # Filter contacts with non-empty notes
    to_sync = []
    for rn, state in contacts.items():
        notes = state.get("notes", "").strip()
        if not notes:
            continue
        synced_at = state.get("notesSyncedAt", "")
        changed_at = state.get("stageChangedAt", "")
        # Sync if never synced or if state was updated after last sync
        # Simple heuristic: always sync if notes exist (cheap to check in update)
        to_sync.append((rn, state))

    if not to_sync:
        logger.info("No CRM notes to sync")
        return {"synced": 0, "skipped": 0, "errors": 0}

    logger.info("Syncing CRM notes for %d contacts", len(to_sync))

    for rn, state in to_sync:
        notes = state["notes"].strip()
        try:
            # Fetch current biography
            person = client.get_contact(rn, person_fields="biographies,metadata")
            etag = person.get("etag", "")
            existing_note = ""
            for bio in person.get("biographies", []):
                if bio.get("contentType") == "TEXT_PLAIN":
                    existing_note = bio.get("value", "")
                    break

            # Strip old CRM block, build new one, insert
            clean = _strip_crm_block(existing_note)
            crm_block = _build_crm_block(notes)
            new_note = _insert_crm_block(clean, crm_block)

            if new_note == existing_note:
                skipped += 1
                continue

            if dry_run:
                logger.info("  [DRY RUN] Would update %s", rn)
                synced += 1
                continue

            body = {"biographies": [{"value": new_note, "contentType": "TEXT_PLAIN"}]}
            client.update_contact(rn, etag, body, update_fields="biographies")

            # Mark as synced
            state["notesSyncedAt"] = datetime.now(timezone.utc).isoformat()
            synced += 1
            logger.info("  Synced notes for %s", rn)

        except Exception as e:
            logger.warning("  Failed to sync notes for %s: %s", rn, e)
            errors += 1

    return {"synced": synced, "skipped": skipped, "errors": errors}


def sync_tags(client, crm_state: dict, dry_run: bool = False) -> dict:
    """
    Sync CRM tags to Google Contacts groups (additive only, NEVER removes).

    Tags are prefixed with CRM_TAG_PREFIX to distinguish from manual groups.
    Creates groups as needed, adds contacts to groups.

    Returns dict with counts: groups_created, memberships_added, errors.
    """
    contacts = crm_state.get("contacts", {})
    groups_created = 0
    memberships_added = 0
    errors = 0

    # Collect all unique tags across contacts
    tag_contacts: dict[str, list[str]] = {}  # tag -> [resourceName, ...]
    for rn, state in contacts.items():
        for tag in state.get("tags", []):
            if not tag.strip():
                continue
            group_name = f"{CRM_TAG_PREFIX}{tag.strip()}"
            tag_contacts.setdefault(group_name, []).append(rn)

    if not tag_contacts:
        logger.info("No CRM tags to sync")
        return {"groups_created": 0, "memberships_added": 0, "errors": 0}

    logger.info("Syncing %d CRM tag groups", len(tag_contacts))

    # Fetch existing groups
    existing_groups = client.get_all_contact_groups()
    group_map = {g["name"]: g["resourceName"] for g in existing_groups}

    for group_name, resource_names in tag_contacts.items():
        try:
            # Ensure group exists
            if group_name not in group_map:
                if dry_run:
                    logger.info("  [DRY RUN] Would create group: %s", group_name)
                    groups_created += 1
                    continue
                result = client.create_contact_group(group_name)
                group_rn = result["resourceName"]
                group_map[group_name] = group_rn
                groups_created += 1
                logger.info("  Created group: %s", group_name)
            else:
                group_rn = group_map[group_name]

            if dry_run:
                logger.info("  [DRY RUN] Would add %d contacts to %s", len(resource_names), group_name)
                memberships_added += len(resource_names)
                continue

            # Add contacts to group (additive — People API ignores already-members)
            client.add_contact_to_group(group_rn, resource_names)
            memberships_added += len(resource_names)
            logger.info("  Added %d contacts to %s", len(resource_names), group_name)

        except Exception as e:
            logger.warning("  Failed to sync tag group %s: %s", group_name, e)
            errors += 1

    return {"groups_created": groups_created, "memberships_added": memberships_added, "errors": errors}


def _is_own_company_contact(contact: dict) -> bool:
    """Skip own-company (Instarea) teammates' cards from omnichannel writeback.

    Mirrors followup_scorer._is_own_company so the same set of contacts
    that are excluded from lead scoring are also excluded from biography
    writebacks. Peter's colleagues don't need Beeper rollups on their
    Google Contacts cards.
    """
    orgs = contact.get("organizations", []) or []
    org_name = (orgs[0].get("name", "") if orgs else "").lower()
    if any(kw in org_name for kw in FOLLOWUP_OWN_COMPANY_ORG_KEYWORDS):
        return True
    emails = {
        (e.get("value") or "").lower()
        for e in (contact.get("emailAddresses") or [])
    }
    for email in emails:
        if "@" in email:
            domain = email.split("@")[-1]
            if domain in FOLLOWUP_OWN_COMPANY_DOMAINS:
                return True
    return False


def sync_omnichannel(
    client,
    *,
    dry_run: bool = False,
    backup_dir: Optional[Path] = None,
    kpi_path: Optional[Path] = None,
) -> dict:
    """Sync ContactKPI rollups into Google Contacts biographies.

    Reads data/interactions/contact_kpis.json (written by
    `python main.py score-interactions`), renders a fenced Omnichannel
    block per contact via `harvester.crm_omnichannel.build_block`,
    merges into the existing biography, writes back via People API
    with diff-based skip.

    Gates:
      - `ENABLE_OMNICHANNEL_WRITEBACK` env var must be truthy — default off
        during Sprint 3.33 S3 dry-run phase, flipped after first clean run.
      - Skips own-company contacts (matches followup_scorer convention).
      - Writes a backup of every touched biography to `backup_dir` BEFORE
        any API call — the global deletion policy requires reversibility.
      - Rate-limited to Google People API's 60 QPM ceiling.
      - Privacy red line: `build_block` is metadata-only; no message text
        can reach the biography even if the KPI file is poisoned.

    Returns a result dict with per-status counts and the backup path.
    """
    if not os.getenv("ENABLE_OMNICHANNEL_WRITEBACK", "").lower() in ("1", "true", "yes"):
        logger.info("sync_omnichannel: disabled (ENABLE_OMNICHANNEL_WRITEBACK not truthy)")
        return {"status": "disabled", "synced": 0, "skipped_no_kpi": 0,
                "skipped_own_co": 0, "skipped_no_change": 0, "errors": 0}

    kpi_path = kpi_path or FOLLOWUP_BEEPER_KPI_FILE
    kpis = load_kpis_from_json(kpi_path)
    if not kpis:
        logger.info(
            "sync_omnichannel: no ContactKPI data at %s — nothing to write",
            kpi_path,
        )
        return {"status": "empty", "synced": 0, "skipped_no_kpi": 0,
                "skipped_own_co": 0, "skipped_no_change": 0, "errors": 0}

    if backup_dir is None:
        backup_dir = DATA_DIR / "biography_backups"
    date_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    time_stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    backup_path = backup_dir / f"biographies_{date_stamp}_{time_stamp}.json"

    stats = {
        "status": "ok",
        "synced": 0,
        "skipped_no_kpi": 0,
        "skipped_own_co": 0,
        "skipped_no_change": 0,
        "errors": 0,
    }

    # Fetch + backup biographies BEFORE any write. We pull contacts in a
    # single pass (each get_contact is ~150ms + rate limit) so the backup
    # file reflects the exact state we're about to mutate — no TOCTOU window.
    logger.info(
        "sync_omnichannel: %d contacts have KPI data; fetching biographies "
        "(rate-limited to %.1fs between calls)…",
        len(kpis), _PEOPLE_API_MIN_INTERVAL_SECONDS,
    )
    contacts_by_rn: dict[str, dict] = {}
    last_call = 0.0
    for rn in list(kpis.keys()):
        elapsed = time.monotonic() - last_call
        if elapsed < _PEOPLE_API_MIN_INTERVAL_SECONDS:
            time.sleep(_PEOPLE_API_MIN_INTERVAL_SECONDS - elapsed)
        last_call = time.monotonic()
        try:
            person = client.get_contact(
                rn,
                person_fields="biographies,metadata,names,emailAddresses,organizations",
            )
            contacts_by_rn[rn] = person
        except Exception as e:
            logger.warning("sync_omnichannel: fetch failed for %s: %s", rn, e)
            stats["errors"] += 1

    backup_biographies(
        list(contacts_by_rn.values()),
        backup_path,
        note="pre sync_omnichannel",
    )
    logger.info("sync_omnichannel: backup written → %s", backup_path)

    # Second pass: per-contact diff + write.
    last_write = 0.0
    for rn, kpi in kpis.items():
        contact = contacts_by_rn.get(rn)
        if not contact:
            stats["skipped_no_kpi"] += 1
            continue

        if _is_own_company_contact(contact):
            stats["skipped_own_co"] += 1
            continue

        try:
            bios = contact.get("biographies", []) or []
            existing_bio = bios[0].get("value", "") if bios else ""
            etag = contact.get("etag", "")

            new_block = build_block(kpi)
            if not should_update(existing_bio, new_block):
                stats["skipped_no_change"] += 1
                continue

            new_bio = merge_into_biography(existing_bio, new_block)

            if dry_run:
                stats["synced"] += 1  # "would sync"
                logger.info("  [DRY RUN] sync_omnichannel → %s", rn)
                continue

            # Rate-limit write calls separately from fetch — both paths hit
            # the same 60 QPM bucket.
            elapsed = time.monotonic() - last_write
            if elapsed < _PEOPLE_API_MIN_INTERVAL_SECONDS:
                time.sleep(_PEOPLE_API_MIN_INTERVAL_SECONDS - elapsed)
            last_write = time.monotonic()

            body = {"biographies": [{"value": new_bio, "contentType": "TEXT_PLAIN"}]}
            client.update_contact(rn, etag, body, update_fields="biographies")
            stats["synced"] += 1
            logger.info("  sync_omnichannel: wrote %s", rn)
        except Exception as e:
            logger.warning("sync_omnichannel failed for %s: %s", rn, e)
            stats["errors"] += 1

    stats["backup"] = str(backup_path)
    logger.info(f"sync_omnichannel stats: {stats}")
    return stats


def run_crm_sync(client=None, dry_run: bool = False) -> dict:
    """
    Main entry point for CRM sync.

    Loads crm_state.json, syncs notes to biographies and tags to groups.
    Saves updated crm_state.json with notesSyncedAt timestamps.

    Returns combined result dict.
    """
    from auth import authenticate
    from api_client import PeopleAPIClient

    if client is None:
        creds = authenticate()
        client = PeopleAPIClient(creds)

    crm_state = load_crm_state()

    notes_result = sync_notes(client, crm_state, dry_run=dry_run)
    omni_result = sync_omnichannel(client, dry_run=dry_run)
    tags_result = sync_tags(client, crm_state, dry_run=dry_run)

    # Save updated state with notesSyncedAt timestamps
    if not dry_run and notes_result["synced"] > 0:
        save_crm_state(crm_state)
        from utils import upload_file_to_gcs
        upload_file_to_gcs(
            DATA_DIR / "crm_state.json",
            "data/crm_state.json",
            "CRM Sync",
        )

    return {
        "notes": notes_result,
        "omnichannel": omni_result,
        "tags": tags_result,
    }

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
    CRM_TAG_ALIASES,
    CRM_TAG_FUZZY_THRESHOLD,
    CRM_TAG_PREFIX_STRING,
    CRM_TAG_USE_PREFIX,
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
from unidecode import unidecode

logger = logging.getLogger("crm_sync")

CRM_NOTE_MARKER = "── CRM Notes"
CRM_STAGE_MARKER = "── CRM Stage:"  # single-line marker; see _build_stage_line/_strip_stage_line
CRM_TAG_PREFIX = CRM_TAG_PREFIX_STRING  # back-compat alias for callers that imported the old name


def _fold(name: str) -> str:
    """ASCII-fold + lowercase a label name for diacritic-insensitive matching."""
    return unidecode(name).strip().lower()


def _resolve_tag_to_group_name(
    raw_tag: str,
    existing_names: list[str],
) -> tuple[str, str]:
    """Map a raw CRM tag to the Google contact group name to use.

    Resolution order (first match wins):
      1. Explicit alias (`CRM_TAG_ALIASES[fold(raw)]`) — honours Peter's shorthand.
      2. Exact match on existing group name — case/diacritic-insensitive.
      3. Fuzzy match against existing group names (rapidfuzz token_sort_ratio).
      4. Fallback: create a new group (prefixed if `CRM_TAG_USE_PREFIX`, else bare).

    Returns (group_name, route) where `route` names which rule fired
    (for logging).
    """
    raw = raw_tag.strip()
    folded = _fold(raw)

    # 1. Alias
    if folded in CRM_TAG_ALIASES:
        return CRM_TAG_ALIASES[folded], "alias"

    # 2. Exact (case/diacritic-insensitive) match against Peter's existing labels
    for name in existing_names:
        if _fold(name) == folded:
            return name, "fold-exact"

    # 3. Fuzzy — rapidfuzz is already a project dependency (followup scorer).
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        fuzz = process = None  # type: ignore[assignment]
    if process is not None and existing_names:
        match = process.extractOne(
            folded,
            [_fold(n) for n in existing_names],
            scorer=fuzz.token_sort_ratio,
        )
        if match and match[1] >= CRM_TAG_FUZZY_THRESHOLD:
            return existing_names[match[2]], f"fuzzy@{int(match[1])}"

    # 4. Create fresh — bare by default (#172), prefixed only if env flag set
    if CRM_TAG_USE_PREFIX:
        return f"{CRM_TAG_PREFIX_STRING}{raw}", "create-prefixed"
    return raw, "create-bare"

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


def _build_stage_line(stage: str) -> str:
    """Build the single-line CRM Stage marker (Option D for #148).

    Pipeline stage is a workflow attribute, not a stable taxonomy label — so
    it lives as one searchable line inside the biography rather than as a
    Google contact group. Greppable on mobile Google Contacts via "CRM
    Stage:" substring. Rewritten on each change with no group-removal churn.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{CRM_STAGE_MARKER} {stage} (updated {date_str}) ──"


def _strip_stage_line(note: str) -> str:
    """Remove any prior CRM Stage marker line from a biography note."""
    if CRM_STAGE_MARKER not in note:
        return note
    kept = [ln for ln in note.split("\n") if CRM_STAGE_MARKER not in ln]
    # Collapse any double-blank introduced by removal.
    out: list[str] = []
    blank = False
    for ln in kept:
        if ln.strip() == "":
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(ln)
    return "\n".join(out).strip()


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

    # Filter out "profile-only" (bare-digit) resourceNames up front — Google
    # People API rejects them from contactGroups.members.modify (see #171).
    filtered: list[tuple[str, list[str]]] = []
    skipped_legacy = 0
    raw_by_contact: dict[str, list[str]] = {}
    for rn, state in contacts.items():
        if not rn.startswith("people/c"):
            skipped_legacy += 1
            continue
        tags = [t.strip() for t in state.get("tags", []) if t.strip()]
        if tags:
            raw_by_contact[rn] = tags
    if skipped_legacy:
        logger.info(
            "Skipped %d contacts with legacy (non-c) resourceName from tag sync",
            skipped_legacy,
        )
    if not raw_by_contact:
        logger.info("No CRM tags to sync")
        return {"groups_created": 0, "memberships_added": 0, "errors": 0}

    # Fetch existing groups once so alias/fold/fuzzy resolution has a universe
    # to match against (user-created groups only — system groups like
    # "myContacts" aren't candidates).
    existing_groups = client.get_all_contact_groups()
    user_groups = [
        g for g in existing_groups
        if g.get("groupType") in (None, "USER_CONTACT_GROUP")
    ]
    existing_names = [g["name"] for g in user_groups]
    group_map = {g["name"]: g["resourceName"] for g in existing_groups}

    # Resolve raw tag → canonical group name (reuse existing labels where
    # possible, per #172). Cache resolution to keep the log concise.
    resolve_cache: dict[str, tuple[str, str]] = {}
    tag_contacts: dict[str, list[str]] = {}
    for rn, tags in raw_by_contact.items():
        for raw in tags:
            if raw not in resolve_cache:
                resolve_cache[raw] = _resolve_tag_to_group_name(raw, existing_names)
            group_name, route = resolve_cache[raw]
            tag_contacts.setdefault(group_name, []).append(rn)
    for raw, (group_name, route) in sorted(resolve_cache.items()):
        if raw != group_name:
            logger.info("  tag %r → %r (%s)", raw, group_name, route)

    logger.info("Syncing %d CRM tag groups", len(tag_contacts))

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


def sync_stages(client, crm_state: dict, dry_run: bool = False) -> dict:
    """Sync CRM kanban stage to a single-line marker in each contact's biography.

    Implements #148 Option D: rather than mirroring stage as `CRM:stage-*`
    Google groups (which would require a removal-on-move exception to the
    global "never remove from groups" policy), stage lives as a searchable
    line inside the biography:

        ── CRM Stage: opportunity (updated 2026-04-21) ──

    Greppable on mobile Google Contacts; replaced cleanly on each run; no
    group sidebar clutter; no policy conflict.

    Skips `inbox` (default stage, low-signal) unless env overrides it.
    """
    contacts = crm_state.get("contacts", {})
    synced = 0
    skipped = 0
    errors = 0
    skip_inbox = os.getenv("CRM_STAGE_SYNC_SKIP_INBOX", "true").lower() in ("1", "true", "yes")

    to_sync: list[tuple[str, str]] = []
    for rn, state in contacts.items():
        if not rn.startswith("people/c"):
            continue
        stage = (state.get("stage") or "").strip()
        if not stage:
            continue
        if skip_inbox and stage == "inbox":
            continue
        to_sync.append((rn, stage))

    if not to_sync:
        logger.info("No CRM stages to sync (or all are inbox + skip enabled)")
        return {"synced": 0, "skipped": 0, "errors": 0}

    logger.info("Syncing CRM stages for %d contacts (as biography marker line)", len(to_sync))

    for rn, stage in to_sync:
        try:
            person = client.get_contact(rn, person_fields="biographies,metadata")
            etag = person.get("etag", "")
            existing_note = ""
            for bio in person.get("biographies", []):
                if bio.get("contentType") == "TEXT_PLAIN":
                    existing_note = bio.get("value", "")
                    break

            # Strip any prior stage line, re-insert a fresh one. The stage
            # line sits after existing marker blocks for consistency with
            # the Omnichannel/Notes convention — biographies keep a stable
            # top-to-bottom reading order.
            stripped = _strip_stage_line(existing_note)
            stage_line = _build_stage_line(stage)
            new_note = _insert_crm_block(stripped, stage_line)

            if new_note == existing_note:
                skipped += 1
                continue

            if dry_run:
                logger.info("  [DRY RUN] Would set stage line on %s → %s", rn, stage)
                synced += 1
                continue

            body = {"biographies": [{"value": new_note, "contentType": "TEXT_PLAIN"}]}
            client.update_contact(rn, etag, body, update_fields="biographies")
            synced += 1
            logger.info("  sync_stages: wrote %s → %s", rn, stage)
        except Exception as e:
            logger.warning("  Failed to sync stage for %s: %s", rn, e)
            errors += 1

    return {"synced": synced, "skipped": skipped, "errors": errors}


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
    stages_result = sync_stages(client, crm_state, dry_run=dry_run)

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
        "stages": stages_result,
    }

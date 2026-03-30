"""
CRM Sync — sync CRM notes and tags from dashboard to Google Contacts.

Reads crm_state.json from GCS/local data dir and:
  1. Writes CRM notes to Google Contacts biographies (marker block pattern)
  2. Syncs CRM tags to Google Contacts groups (additive only, never removes)

Uses the same marker block pattern as interaction_scanner.py and linkedin_scanner.py.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger("crm_sync")

CRM_NOTE_MARKER = "── CRM Notes"
CRM_TAG_PREFIX = "CRM:"


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
        "tags": tags_result,
    }

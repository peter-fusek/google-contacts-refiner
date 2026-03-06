"""
Batch processing with interactive user approval.
Displays diffs, handles approval/rejection, executes changes via API.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from config import DATA_DIR, BATCH_SIZE, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM
from api_client import PeopleAPIClient
from changelog import ChangeLog
from recovery import RecoveryManager
from analyzer import confidence_emoji, format_contact_changes
from utils import get_resource_name, get_etag


REJECTED_FILE = DATA_DIR / "rejected_batches.json"


def build_update_body(person: dict, changes: list[dict]) -> dict:
    """
    Build a People API update body from a list of changes.

    Handles:
    - Simple field updates (e.g., phoneNumbers[0].value)
    - New field additions (e.g., phoneNumbers[+])
    - Complex nested updates

    Args:
        person: Original person resource.
        changes: List of change dicts.

    Returns:
        Dict suitable for People API updateContact body.
    """
    body = {}
    update_fields = set()

    # Normalize AI-generated field names → People API field names
    VALID_FIELDS = {
        "names", "phoneNumbers", "emailAddresses", "addresses",
        "organizations", "urls", "birthdays", "events", "userDefined",
        "biographies", "nicknames",
    }
    field_aliases = {
        "name": "names",
        "phone": "phoneNumbers",
        "phones": "phoneNumbers",
        "email": "emailAddresses",
        "emails": "emailAddresses",
        "address": "addresses",
        "organization": "organizations",
        "url": "urls",
        "birthday": "birthdays",
        "event": "events",
    }

    # Group changes by top-level field
    field_changes: dict[str, list[dict]] = {}
    for change in changes:
        top_field = change["field"].split("[")[0].split(".")[0]
        top_field = field_aliases.get(top_field, top_field)
        if top_field not in VALID_FIELDS:
            continue
        field_changes.setdefault(top_field, []).append(change)

    for top_field, changes_group in field_changes.items():
        update_fields.add(top_field)

        # Get current data for this field
        current_data = list(person.get(top_field, []))

        for change in changes_group:
            field_path = change["field"]
            new_value = change["new"]
            # AI may return dicts/lists instead of strings — coerce to string
            if isinstance(new_value, (dict, list)):
                new_value = str(new_value)

            # Parse field path: fieldName[index].subField
            match = re.match(r'(\w+)\[(\+|\d+)\](?:\.(\w+))?', field_path)
            if not match:
                continue

            array_field = field_aliases.get(match.group(1), match.group(1))
            index_str = match.group(2)
            sub_field = match.group(3)

            if index_str == "+":
                # Add new entry
                extra = change.get("extra", {})
                if array_field == "phoneNumbers":
                    new_entry = {"value": new_value, "type": extra.get("type", "other")}
                    current_data.append(new_entry)
                elif array_field == "emailAddresses":
                    new_entry = {"value": new_value, "type": extra.get("type", "other")}
                    current_data.append(new_entry)
                elif array_field == "urls":
                    new_entry = {"value": new_value, "type": extra.get("type", "other")}
                    current_data.append(new_entry)
                elif array_field == "birthdays":
                    date_data = extra.get("date", {})
                    new_entry = {"date": date_data}
                    current_data.append(new_entry)
                elif array_field == "events":
                    date_parts = new_value.split("-")
                    new_entry = {
                        "date": {
                            "year": int(date_parts[0]),
                            "month": int(date_parts[1]),
                            "day": int(date_parts[2]),
                        },
                        "type": extra.get("type", "other"),
                    }
                    current_data.append(new_entry)
                elif array_field == "userDefined":
                    new_entry = {
                        "key": extra.get("key", ""),
                        "value": extra.get("value", new_value),
                    }
                    current_data.append(new_entry)
                elif array_field == "organizations":
                    new_entry = {"name": new_value}
                    current_data.append(new_entry)
                elif array_field == "names":
                    # Adding name fields to a new name entry
                    if current_data:
                        # Update existing first entry
                        if sub_field:
                            current_data[0][sub_field] = new_value
                    else:
                        new_entry = {}
                        if sub_field:
                            new_entry[sub_field] = new_value
                        current_data.append(new_entry)
                else:
                    # Generic append
                    current_data.append({"value": new_value})

            else:
                # Update existing entry
                idx = int(index_str)
                if idx < len(current_data):
                    if sub_field:
                        current_data[idx][sub_field] = new_value
                    elif array_field == "organizations":
                        current_data[idx]["name"] = new_value
                    elif array_field == "addresses":
                        current_data[idx]["formattedValue"] = new_value
                    else:
                        current_data[idx]["value"] = new_value

        body[top_field] = current_data

    return body, ",".join(update_fields)


def format_batch_header(batch_num: int, total_batches: int, start_idx: int, end_idx: int) -> str:
    """Format batch header."""
    return (
        f"\n{'═' * 50}\n"
        f" BATCH {batch_num}/{total_batches} (kontakty {start_idx}-{end_idx})\n"
        f"{'═' * 50}\n"
    )


def format_batch_footer(stats: dict) -> str:
    """Format batch footer with stats."""
    return (
        f"\n{'═' * 50}\n"
        f"Zmeny: 🟢 {stats.get('high', 0)} high | "
        f"🟡 {stats.get('medium', 0)} medium | "
        f"🔴 {stats.get('low', 0)} low\n"
    )


def prompt_user_approval(batch_num: int) -> tuple[str, list[int]]:
    """
    Prompt user for batch approval.

    Returns:
        (action, skip_indices)
        action: 'approve', 'reject', 'edit', 'quit'
        skip_indices: List of contact indices to skip (only if action == 'approve')
    """
    print("Schváliť? [y/n/čísla na preskočenie/e pre edit/q pre ukončenie]:", end=" ")
    try:
        response = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "quit", []

    if response in ("y", "yes", "a", "ano"):
        return "approve", []
    elif response in ("n", "no", "nie"):
        return "reject", []
    elif response in ("e", "edit"):
        return "edit", []
    elif response in ("q", "quit", "koniec"):
        return "quit", []
    else:
        # Try to parse skip indices
        try:
            skip = [int(x.strip()) for x in response.split(",") if x.strip().isdigit()]
            if skip:
                return "approve", skip
        except ValueError:
            pass
        return "reject", []


def process_batches(
    workplan: dict,
    contacts_lookup: dict[str, dict],
    client: PeopleAPIClient,
    changelog: ChangeLog,
    recovery: RecoveryManager,
    start_from_batch: int = 1,
    memory=None,
    auto_mode: bool = False,
    auto_confidence_threshold: float = 0.90,
):
    """
    Process all batches interactively.

    Args:
        workplan: Loaded workplan dict.
        contacts_lookup: {resourceName: person} for all contacts.
        client: API client.
        changelog: ChangeLog instance.
        recovery: RecoveryManager instance.
        start_from_batch: Resume from this batch number.
    """
    batches = workplan["batches"]
    total_batches = len(batches)
    total_processed = 0
    total_success = 0
    total_failed = 0
    total_skipped = 0

    rejected = _load_rejected()
    skipped_for_review = []  # For auto-mode: changes below threshold

    for batch in batches:
        batch_num = batch["batch_num"]
        if batch_num < start_from_batch:
            total_processed += batch["stats"]["contacts"]
            continue

        contacts_in_batch = batch["contacts"]
        start_idx = total_processed + 1
        end_idx = total_processed + len(contacts_in_batch)

        if auto_mode:
            # Auto-mode: split changes by confidence threshold
            action = "approve"
            skip_indices = []

            for i, result in enumerate(contacts_in_batch):
                low_conf_changes = [
                    c for c in result.get("changes", [])
                    if c.get("confidence", 0) < auto_confidence_threshold
                ]
                if low_conf_changes:
                    # Log low-confidence changes for review
                    skipped_for_review.append({
                        "resourceName": result["resourceName"],
                        "displayName": result["displayName"],
                        "skipped_changes": low_conf_changes,
                    })
                    # Keep only high-confidence changes
                    result["changes"] = [
                        c for c in result.get("changes", [])
                        if c.get("confidence", 0) >= auto_confidence_threshold
                    ]

            print(f"   🤖 Batch {batch_num}/{total_batches} (auto-mode)")
        else:
            # Interactive mode
            # Display batch
            print(format_batch_header(batch_num, total_batches, start_idx, end_idx))

            for i, result in enumerate(contacts_in_batch):
                if result["changes"]:
                    print(format_contact_changes(result, start_idx + i))
                    print()

            print(format_batch_footer(batch["stats"]))

            # Get user approval
            action, skip_indices = prompt_user_approval(batch_num)

        if action == "quit":
            print("\n⏸  Ukončujem. Môžeš pokračovať cez 'python main.py resume'.")
            recovery.save_checkpoint(batch_num - 1, total_processed)
            return

        if action == "reject":
            print(f"   ❌ Batch {batch_num} odmietnutý.")
            rejected.append({
                "batch_num": batch_num,
                "rejected_at": datetime.now().isoformat(),
                "contacts": [r["resourceName"] for r in contacts_in_batch],
            })
            _save_rejected(rejected)

            # Record rejections in memory
            if memory:
                for result in contacts_in_batch:
                    for change in result.get("changes", []):
                        memory.record_rejection(change)

            total_skipped += len(contacts_in_batch)
            total_processed += len(contacts_in_batch)
            recovery.save_checkpoint(batch_num, total_processed)
            continue

        if action == "edit":
            print("   ✏️  Edit mód — v tejto verzii nie je podporovaný. Preskakujem batch.")
            total_skipped += len(contacts_in_batch)
            total_processed += len(contacts_in_batch)
            recovery.save_checkpoint(batch_num, total_processed)
            continue

        # action == "approve"
        skip_global = {start_idx + s for s in skip_indices}

        # Execute changes
        changelog.log_batch_start(batch_num, len(contacts_in_batch))
        batch_success = 0
        batch_failed = 0

        for i, result in enumerate(contacts_in_batch):
            contact_idx = start_idx + i
            if contact_idx in skip_global:
                print(f"   ⏭  [{contact_idx}] preskočený")
                total_skipped += 1
                continue

            if not result["changes"]:
                continue

            resource_name = result["resourceName"]
            person = contacts_lookup.get(resource_name)
            if not person:
                print(f"   ⚠️  [{contact_idx}] kontakt nenájdený: {resource_name}")
                batch_failed += 1
                continue

            try:
                # Build update body
                body, update_fields = build_update_body(person, result["changes"])

                if not update_fields:
                    continue

                # Log changes BEFORE applying
                for change in result["changes"]:
                    changelog.log_change(
                        resource_name=resource_name,
                        field=change["field"],
                        old_value=str(change.get("old", "")),
                        new_value=str(change["new"]),
                        reason=change["reason"],
                        confidence=change["confidence"],
                        batch=batch_num,
                    )

                # Execute update
                etag = result.get("etag") or get_etag(person)
                client.update_contact(
                    resource_name=resource_name,
                    etag=etag,
                    person_body=body,
                    update_fields=update_fields,
                )

                batch_success += 1
                print(f"   ✅ [{contact_idx}] {result['displayName']}")

                # Record approvals in memory
                if memory:
                    for change in result["changes"]:
                        memory.record_approval(change)

            except Exception as e:
                batch_failed += 1
                print(f"   ❌ [{contact_idx}] {result['displayName']}: {e}")

        changelog.log_batch_end(batch_num, batch_success, batch_failed)
        total_success += batch_success
        total_failed += batch_failed
        total_processed += len(contacts_in_batch)

        print(f"\n   Batch {batch_num}: ✅ {batch_success} | ❌ {batch_failed} | ⏭ {len(skip_global)}")

        recovery.save_checkpoint(batch_num, total_processed)

    # All batches done
    recovery.mark_completed()

    # Save memory with session summary
    if memory:
        memory.record_session(total_processed, total_success)
        memory.save()

    print()
    print("═══════════════════════════════════════════")
    print("          DOKONČENÉ")
    print("═══════════════════════════════════════════")
    print(f"  Úspešné:    {total_success}")
    print(f"  Zlyhané:    {total_failed}")
    print(f"  Preskočené: {total_skipped}")
    if skipped_for_review:
        print(f"  Na review:  {len(skipped_for_review)}")
    print("═══════════════════════════════════════════")

    return {
        "success": total_success,
        "failed": total_failed,
        "skipped": total_skipped,
        "skipped_for_review": skipped_for_review,
    }


def _load_rejected() -> list:
    """Load rejected batches from file."""
    if REJECTED_FILE.exists():
        with open(REJECTED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_rejected(rejected: list):
    """Save rejected batches to file."""
    with open(REJECTED_FILE, "w", encoding="utf-8") as f:
        json.dump(rejected, f, ensure_ascii=False, indent=2)

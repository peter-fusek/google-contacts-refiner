"""
Backup and restore functionality for Google Contacts.
Creates timestamped full JSON backups and verifies integrity.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from config import DATA_DIR, PERSON_FIELDS
from api_client import PeopleAPIClient
from utils import get_display_name


def create_backup(client: PeopleAPIClient) -> Path:
    """
    Create a complete backup of all contacts and contact groups.

    Returns:
        Path to the backup file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"backup_{timestamp}.json"

    print("📦 Zálohujem všetky kontakty...")
    print()

    # ── Fetch all contacts ─────────────────────────────────────────
    def progress(fetched, total):
        print(f"\r   Stiahnuté: {fetched} / ~{total}  ", end="", flush=True)

    contacts = client.get_all_contacts(
        person_fields=PERSON_FIELDS,
        progress_callback=progress,
    )
    print()
    print(f"   ✅ Stiahnutých kontaktov: {len(contacts)}")

    # ── Fetch all contact groups ──────────────────────────────────
    print("   Sťahujem contact groups/labels...")
    groups = client.get_all_contact_groups()
    print(f"   ✅ Contact groups: {len(groups)}")

    # ── Fetch group memberships ───────────────────────────────────
    print("   Sťahujem členov skupín...")
    group_members = {}
    for g in groups:
        rn = g.get("resourceName", "")
        if rn and g.get("groupType") == "USER_CONTACT_GROUP":
            try:
                members = client.get_contact_group_members(rn)
                group_members[rn] = members
            except Exception as e:
                print(f"   ⚠️  Nepodarilo sa načítať členov {g.get('name', rn)}: {e}")
                group_members[rn] = []

    # ── Build backup structure ────────────────────────────────────
    backup_data = {
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "total_contacts": len(contacts),
            "total_groups": len(groups),
            "person_fields": PERSON_FIELDS,
            "version": "1.0",
        },
        "contacts": contacts,
        "contact_groups": groups,
        "group_members": group_members,
    }

    # ── Write backup ──────────────────────────────────────────────
    print(f"   Zapisujem zálohu do {backup_path}...")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)

    file_size = backup_path.stat().st_size
    print(f"   ✅ Záloha uložená: {file_size / 1024 / 1024:.1f} MB")

    # ── Verify backup ─────────────────────────────────────────────
    print("   Overujem integritu zálohy...")
    if verify_backup(backup_path, len(contacts)):
        print("   ✅ Integrita OK")
    else:
        print("   ❌ CHYBA integrity! Záloha môže byť poškodená!")
        sys.exit(1)

    print()
    print(f"✅ Záloha hotová: {backup_path}")
    return backup_path


def verify_backup(backup_path: Path, expected_count: int) -> bool:
    """
    Verify backup file integrity.

    Checks:
    - File is valid JSON
    - Contains expected number of contacts
    - Each contact has resourceName
    """
    try:
        with open(backup_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        contacts = data.get("contacts", [])

        # Check count
        if len(contacts) != expected_count:
            print(f"   ⚠️  Počet kontaktov v zálohe ({len(contacts)}) != očakávaný ({expected_count})")
            return False

        # Check each contact has resourceName
        missing_rn = [i for i, c in enumerate(contacts) if not c.get("resourceName")]
        if missing_rn:
            print(f"   ⚠️  {len(missing_rn)} kontaktov bez resourceName")
            return False

        # Check metadata
        meta = data.get("metadata", {})
        if meta.get("total_contacts") != expected_count:
            print(f"   ⚠️  Metadata total_contacts ({meta.get('total_contacts')}) != skutočnosť ({expected_count})")
            return False

        return True

    except json.JSONDecodeError as e:
        print(f"   ❌ Nevalidný JSON: {e}")
        return False
    except Exception as e:
        print(f"   ❌ Chyba pri overovaní: {e}")
        return False


def load_backup(backup_path: Path) -> dict:
    """Load a backup file and return its contents."""
    with open(backup_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_latest_backup() -> Path | None:
    """Find the most recent backup file."""
    backups = sorted(DATA_DIR.glob("backup_*.json"), reverse=True)
    return backups[0] if backups else None


def list_backups() -> list[Path]:
    """List all backup files, newest first."""
    return sorted(DATA_DIR.glob("backup_*.json"), reverse=True)


def restore_contact_from_backup(client: PeopleAPIClient, backup_data: dict, resource_name: str) -> bool:
    """
    Restore a single contact from backup data.

    This is a simplified restore — it replaces the contact's fields
    with the backed-up version.
    """
    contacts = backup_data.get("contacts", [])
    backup_contact = None
    for c in contacts:
        if c.get("resourceName") == resource_name:
            backup_contact = c
            break

    if not backup_contact:
        print(f"   ❌ Kontakt {resource_name} nie je v zálohe")
        return False

    try:
        # Get current etag (required for update)
        current = client.get_contact(resource_name)
        current_etag = current.get("etag", "")

        # Build update body from backup
        body = {}
        for field in ["names", "emailAddresses", "phoneNumbers", "addresses",
                       "organizations", "biographies", "birthdays", "events",
                       "externalIds", "nicknames", "occupations", "relations",
                       "urls", "userDefined"]:
            if field in backup_contact:
                body[field] = backup_contact[field]

        result = client.update_contact(
            resource_name=resource_name,
            etag=current_etag,
            person_body=body,
        )
        return True

    except Exception as e:
        print(f"   ❌ Chyba pri obnove {resource_name}: {e}")
        return False

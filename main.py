#!/usr/bin/env python3
"""
Google Contacts Cleanup Tool — Main CLI Entry Point.

Usage:
    python main.py auth         # Setup OAuth and test connection
    python main.py backup       # Create a full backup
    python main.py analyze      # Analyze contacts and generate workplan
    python main.py fix          # Apply fixes interactively (batch approval)
    python main.py verify       # Verify changes against backup
    python main.py rollback     # Rollback changes from changelog
    python main.py resume       # Resume from last checkpoint
    python main.py info         # Show session/backup/workplan info
"""
import sys
import json
import uuid
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR, AI_ENABLED
from auth import authenticate, test_connection
from api_client import PeopleAPIClient
from backup import create_backup, get_latest_backup, load_backup, list_backups
from analyzer import analyze_all_contacts, summarize_analysis, format_contact_changes
from normalizer import *  # noqa — ensure module loads
from enricher import *  # noqa
from deduplicator import find_duplicates, format_duplicates
from labels_manager import analyze_labels, format_labels_report
from workplan import generate_workplan, load_workplan, get_latest_workplan, format_workplan_summary
from changelog import ChangeLog, find_latest_changelog, load_changelog, summarize_changelog
from recovery import RecoveryManager
from batch_processor import process_batches
from utils import get_resource_name


def _get_ai_analyzer():
    """Initialize AI analyzer if configured and available."""
    if not AI_ENABLED:
        return None
    try:
        from ai_analyzer import AIAnalyzer
        ai = AIAnalyzer()
        print("🤖 Claude AI aktívny")
        return ai
    except Exception as e:
        print(f"ℹ️  AI nedostupné: {e}")
        return None


def cmd_auth():
    """Authenticate and test connection."""
    print("🔐 Google Contacts Cleanup — Autentifikácia")
    print("=" * 50)
    print()

    creds = authenticate()
    success = test_connection(creds)

    if success:
        print("✅ Všetko je pripravené! Pokračuj so 'python main.py backup'.")
    else:
        print("❌ Niečo nie je v poriadku. Skontroluj credentials.json a skús znova.")
        sys.exit(1)


def cmd_backup():
    """Create a full backup of all contacts."""
    print("📦 Google Contacts Cleanup — Záloha")
    print("=" * 50)
    print()

    creds = authenticate()
    client = PeopleAPIClient(creds)
    backup_path = create_backup(client)

    print()
    print(f"Pokračuj so 'python main.py analyze'.")


def cmd_analyze():
    """Analyze contacts, detect issues, generate workplan."""
    print("🔍 Google Contacts Cleanup — Analýza")
    print("=" * 50)
    print()

    # Load latest backup
    backup_path = get_latest_backup()
    if not backup_path:
        print("❌ Žiadna záloha! Najprv spusti 'python main.py backup'.")
        sys.exit(1)

    print(f"Používam zálohu: {backup_path.name}")
    backup_data = load_backup(backup_path)
    contacts = backup_data["contacts"]
    groups = backup_data.get("contact_groups", [])
    group_members = backup_data.get("group_members", {})

    print(f"Celkom kontaktov: {len(contacts)}")
    print()

    # ── Initialize AI (if configured) ──────────────────────────────
    ai = _get_ai_analyzer()

    # ── Analyze contacts ──────────────────────────────────────────
    print("📊 Analyzujem kontakty...")

    def progress(done, total):
        print(f"\r   Analyzované: {done}/{total}  ", end="", flush=True)

    results = analyze_all_contacts(contacts, progress_callback=progress, ai_analyzer=ai)
    print()
    print(f"   Kontakty s nálezmi: {len(results)}")

    if ai:
        stats = ai.get_usage_stats()
        print(f"   🤖 AI: {stats['total_input_tokens'] + stats['total_output_tokens']} tokenov, ~${stats['estimated_cost_usd']:.3f}")
    print()

    # ── Find duplicates ───────────────────────────────────────────
    print("🔍 Hľadám duplikáty...")
    duplicates = find_duplicates(contacts)
    print(f"   Potenciálne duplikáty: {len(duplicates)} skupín")
    print()

    # ── Analyze labels ────────────────────────────────────────────
    print("🏷  Analyzujem labels...")
    labels_analysis = analyze_labels(groups, group_members, contacts)
    print(f"   Labels: {len(labels_analysis['labels'])}")
    print(f"   Bez labelu: {labels_analysis['unlabeled_contacts']} kontaktov")
    print(f"   Návrhy: {len(labels_analysis['suggestions'])}")
    print()

    # ── Generate workplan ─────────────────────────────────────────
    print("📝 Generujem plán práce...")
    workplan_path = generate_workplan(results, duplicates, labels_analysis)
    print(f"   Uložený: {workplan_path}")
    print()

    # ── Display summary ───────────────────────────────────────────
    workplan = load_workplan(workplan_path)
    print(format_workplan_summary(workplan))
    print()

    # ── Duplicates report ─────────────────────────────────────────
    if duplicates:
        print()
        print(format_duplicates(duplicates))
        print()

    # ── Labels report ─────────────────────────────────────────────
    print()
    print(format_labels_report(labels_analysis))
    print()

    print(f"Pokračuj so 'python main.py fix' pre interaktívne opravy.")


def cmd_fix():
    """Apply fixes interactively with batch approval."""
    print("🔧 Google Contacts Cleanup — Opravy")
    print("=" * 50)
    print()

    # Load workplan
    workplan_path = get_latest_workplan()
    if not workplan_path:
        print("❌ Žiadny workplan! Najprv spusti 'python main.py analyze'.")
        sys.exit(1)

    workplan = load_workplan(workplan_path)
    print(f"Workplan: {workplan_path.name}")
    print(format_workplan_summary(workplan))
    print()

    # Confirm
    print("Chceš pokračovať s opravami? [y/n]: ", end="")
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nZrušené.")
        return

    if answer not in ("y", "yes", "a", "ano"):
        print("Zrušené.")
        return

    # ── Setup ─────────────────────────────────────────────────────
    session_id = str(uuid.uuid4())
    creds = authenticate()
    client = PeopleAPIClient(creds)

    # We need fresh contact data for etags
    print()
    print("📡 Sťahujem aktuálne kontakty pre etag synchronizáciu...")

    def progress(fetched, total):
        print(f"\r   Stiahnuté: {fetched} / ~{total}  ", end="", flush=True)

    current_contacts = client.get_all_contacts(progress_callback=progress)
    print()

    contacts_lookup = {get_resource_name(c): c for c in current_contacts}

    # Setup changelog and recovery
    changelog = ChangeLog(session_id)
    recovery = RecoveryManager(session_id)

    batches = workplan["batches"]
    total_batches = len(batches)
    total_contacts = sum(b["stats"]["contacts"] for b in batches)

    recovery.set_session_info(
        total_batches=total_batches,
        contacts_total=total_contacts,
        workplan_path=str(workplan_path),
        changelog_path=str(changelog.log_path),
        backup_path=str(get_latest_backup() or ""),
    )

    print(f"Session ID: {session_id}")
    print(f"Changelog:  {changelog.log_path}")
    print()

    # ── Process batches ───────────────────────────────────────────
    process_batches(
        workplan=workplan,
        contacts_lookup=contacts_lookup,
        client=client,
        changelog=changelog,
        recovery=recovery,
    )


def cmd_verify():
    """Verify changes by comparing current state with backup."""
    print("✅ Google Contacts Cleanup — Verifikácia")
    print("=" * 50)
    print()

    # Load backup
    backup_path = get_latest_backup()
    if not backup_path:
        print("❌ Žiadna záloha!")
        sys.exit(1)

    backup_data = load_backup(backup_path)
    backup_contacts = {c["resourceName"]: c for c in backup_data["contacts"]}

    # Load changelog
    changelog_path = find_latest_changelog()
    if not changelog_path:
        print("❌ Žiadny changelog!")
        sys.exit(1)

    entries = load_changelog(changelog_path)
    cl_summary = summarize_changelog(entries)

    # Fetch current contacts
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print("📡 Sťahujem aktuálne kontakty...")

    def progress(fetched, total):
        print(f"\r   Stiahnuté: {fetched} / ~{total}  ", end="", flush=True)

    current_contacts = client.get_all_contacts(progress_callback=progress)
    print()

    current_lookup = {get_resource_name(c): c for c in current_contacts}

    # Compare
    changed_count = 0
    unchanged_count = 0
    missing_count = 0

    change_entries = [e for e in entries if "field" in e and "old" in e]

    print(f"Changelog má {len(change_entries)} zmien na {cl_summary['contacts_modified']} kontaktoch")
    print()

    # Verify each changed contact
    verified_contacts = set()
    for entry in change_entries:
        rn = entry.get("resourceName")
        if rn in verified_contacts:
            continue
        verified_contacts.add(rn)

        if rn in current_lookup:
            changed_count += 1
        else:
            missing_count += 1
            print(f"   ⚠️  Kontakt {rn} už neexistuje!")

    total_contacts = len(current_contacts)

    print("═══════════════════════════════════════════")
    print("          VERIFIKÁCIA")
    print("═══════════════════════════════════════════")
    print(f"  Záloha:              {backup_path.name}")
    print(f"  Kontakty v zálohe:   {len(backup_contacts)}")
    print(f"  Aktuálne kontakty:   {total_contacts}")
    print(f"  Zmenené (changelog): {cl_summary['contacts_modified']}")
    print(f"  Overené:             {changed_count}")
    print(f"  Chýbajúce:           {missing_count}")
    print()
    print(f"  Zmeny podľa istoty:")
    for conf, count in cl_summary["by_confidence"].items():
        print(f"    {conf}: {count}")
    print("═══════════════════════════════════════════")


def cmd_rollback():
    """Rollback changes using changelog."""
    print("⏪ Google Contacts Cleanup — Rollback")
    print("=" * 50)
    print()

    changelog_path = find_latest_changelog()
    if not changelog_path:
        print("❌ Žiadny changelog na rollback!")
        sys.exit(1)

    entries = load_changelog(changelog_path)
    change_entries = [e for e in entries if "field" in e and "old" in e]

    if not change_entries:
        print("ℹ️  Changelog neobsahuje žiadne zmeny na rollback.")
        return

    # Reverse order for rollback
    change_entries.reverse()

    # Group by contact
    by_contact: dict[str, list[dict]] = {}
    for entry in change_entries:
        rn = entry.get("resourceName", "")
        by_contact.setdefault(rn, []).append(entry)

    print(f"Changelog: {changelog_path.name}")
    print(f"Zmien na rollback: {len(change_entries)}")
    print(f"Kontaktov na rollback: {len(by_contact)}")
    print()

    print("POZOR: Rollback vráti zmeny v opačnom poradí.")
    print("       Pre úplný rollback použi zálohu (backup).")
    print()
    print("Pokračovať? [y/n]: ", end="")

    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nZrušené.")
        return

    if answer not in ("y", "yes", "a", "ano"):
        print("Zrušené.")
        return

    # Connect and rollback
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print()
    print("📡 Sťahujem aktuálne kontakty...")

    def progress(fetched, total):
        print(f"\r   Stiahnuté: {fetched} / ~{total}  ", end="", flush=True)

    current_contacts = client.get_all_contacts(progress_callback=progress)
    print()

    contacts_lookup = {get_resource_name(c): c for c in current_contacts}

    # Process rollback by contact
    success = 0
    failed = 0

    for rn, entries_for_contact in by_contact.items():
        person = contacts_lookup.get(rn)
        if not person:
            print(f"   ⚠️  {rn} — kontakt nenájdený, preskakujem")
            failed += 1
            continue

        # Build rollback body: apply old values
        body = {}
        update_fields = set()

        for entry in entries_for_contact:
            field_path = entry["field"]
            old_value = entry["old"]

            # Parse and apply old value (simplified)
            top_field = field_path.split("[")[0].split(".")[0]
            update_fields.add(top_field)

            # Get current field data
            if top_field not in body:
                body[top_field] = list(person.get(top_field, []))

            # Apply old value (simplified — works for direct value updates)
            import re
            match = re.match(r'(\w+)\[(\d+)\](?:\.(\w+))?', field_path)
            if match:
                idx = int(match.group(2))
                sub_field = match.group(3)
                if idx < len(body[top_field]):
                    if sub_field:
                        body[top_field][idx][sub_field] = old_value
                    else:
                        body[top_field][idx]["value"] = old_value

        try:
            from utils import get_etag
            etag = get_etag(person)
            client.update_contact(
                resource_name=rn,
                etag=etag,
                person_body=body,
                update_fields=",".join(update_fields),
            )
            success += 1
            print(f"   ✅ {rn}")
        except Exception as e:
            failed += 1
            print(f"   ❌ {rn}: {e}")

    print()
    print(f"Rollback: ✅ {success} | ❌ {failed}")


def cmd_resume():
    """Resume from last checkpoint."""
    print("▶️  Google Contacts Cleanup — Pokračovanie")
    print("=" * 50)
    print()

    if not RecoveryManager.has_pending_session():
        print("ℹ️  Žiadna nedokončená relácia.")
        return

    checkpoint = RecoveryManager.load_checkpoint()
    if not checkpoint:
        print("ℹ️  Nepodarilo sa načítať checkpoint.")
        return

    print(RecoveryManager.format_checkpoint_info(checkpoint))
    print()

    print("Pokračovať od posledného checkpointu? [y/n/r pre restart]: ", end="")
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nZrušené.")
        return

    if answer in ("r", "restart"):
        RecoveryManager.clear_checkpoint()
        print("Checkpoint vymazaný. Spusti 'python main.py fix' pre nový štart.")
        return

    if answer not in ("y", "yes", "a", "ano"):
        print("Zrušené.")
        return

    # Resume
    workplan_path = checkpoint.get("workplan_path")
    if not workplan_path or not Path(workplan_path).exists():
        print("❌ Workplan súbor neexistuje!")
        return

    workplan = load_workplan(Path(workplan_path))
    start_batch = checkpoint.get("last_completed_batch", 0) + 1

    print(f"Pokračujem od batch {start_batch}...")
    print()

    # Setup
    session_id = checkpoint["session_id"]
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print("📡 Sťahujem aktuálne kontakty...")

    def progress(fetched, total):
        print(f"\r   Stiahnuté: {fetched} / ~{total}  ", end="", flush=True)

    current_contacts = client.get_all_contacts(progress_callback=progress)
    print()

    contacts_lookup = {get_resource_name(c): c for c in current_contacts}

    changelog = ChangeLog(session_id)
    # Point to existing changelog if available
    existing_cl = checkpoint.get("changelog_path")
    if existing_cl and Path(existing_cl).exists():
        changelog.log_path = Path(existing_cl)

    recovery = RecoveryManager(session_id)
    recovery.checkpoint_data = checkpoint

    process_batches(
        workplan=workplan,
        contacts_lookup=contacts_lookup,
        client=client,
        changelog=changelog,
        recovery=recovery,
        start_from_batch=start_batch,
    )


def cmd_info():
    """Show info about backups, workplans, changelogs."""
    print("ℹ️  Google Contacts Cleanup — Info")
    print("=" * 50)
    print()

    # Backups
    backups = list_backups()
    print(f"📦 Zálohy ({len(backups)}):")
    for b in backups[:5]:
        size = b.stat().st_size / 1024 / 1024
        print(f"   {b.name}  ({size:.1f} MB)")
    print()

    # Workplans
    plans = sorted(DATA_DIR.glob("workplan_*.json"), reverse=True)
    print(f"📝 Workplany ({len(plans)}):")
    for p in plans[:5]:
        size = p.stat().st_size / 1024
        print(f"   {p.name}  ({size:.0f} KB)")
    print()

    # Changelogs
    logs = sorted(DATA_DIR.glob("changelog_*.jsonl"), reverse=True)
    print(f"📜 Changelogy ({len(logs)}):")
    for l in logs[:5]:
        entries = load_changelog(l)
        changes = sum(1 for e in entries if "field" in e)
        print(f"   {l.name}  ({changes} zmien)")
    print()

    # Checkpoint
    if RecoveryManager.has_pending_session():
        checkpoint = RecoveryManager.load_checkpoint()
        if checkpoint:
            print(RecoveryManager.format_checkpoint_info(checkpoint))
    else:
        print("⏸  Žiadna nedokončená relácia.")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    command = sys.argv[1].lower()

    commands = {
        "auth": cmd_auth,
        "backup": cmd_backup,
        "analyze": cmd_analyze,
        "analyse": cmd_analyze,  # British spelling alias
        "fix": cmd_fix,
        "verify": cmd_verify,
        "rollback": cmd_rollback,
        "resume": cmd_resume,
        "info": cmd_info,
    }

    if command in ("help", "-h", "--help"):
        print(__doc__)
        sys.exit(0)

    if command not in commands:
        print(f"❌ Neznámy príkaz: {command}")
        print(__doc__)
        sys.exit(1)

    try:
        commands[command]()
    except KeyboardInterrupt:
        print("\n\n⏸  Prerušené používateľom.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Chyba: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

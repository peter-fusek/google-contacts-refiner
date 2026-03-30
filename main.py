#!/usr/bin/env python3
"""
Google Contacts Cleanup Tool — Main CLI Entry Point.

Usage:
    python main.py auth           # Setup OAuth and test connection
    python main.py backup         # Create a full backup
    python main.py analyze        # Analyze contacts and generate workplan
    python main.py fix            # Apply fixes interactively (batch approval)
    python main.py verify         # Verify changes against backup
    python main.py rollback       # Rollback changes from changelog
    python main.py resume         # Resume from last checkpoint
    python main.py info           # Show session/backup/workplan info
    python main.py auth-activity  # Authenticate for Gmail+Calendar scanning
    python main.py tag-activity   # Scan interactions and assign year labels
    python main.py ltns           # Identify LTNS contacts and generate reconnect prompts
    python main.py linkedin-scan  # Scan LinkedIn profiles for social signals
    python main.py followup       # Score FollowUp candidates (LinkedIn + interaction signals)
"""
import os
import sys
import json
import uuid
import hashlib
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR, AI_REVIEW_CHECKPOINT, AI_REVIEW_HISTORY, AI_MAX_CONTACTS_PER_BATCH
from auth import authenticate, test_connection
from memory import MemoryManager
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
    # Check at runtime (env var may change between phases)
    if os.getenv("AI_ENABLED", "true").lower() != "true":
        return None
    try:
        from ai_analyzer import AIAnalyzer
        ai = AIAnalyzer()
        print("🤖 Claude AI active")
        return ai
    except Exception as e:
        print(f"ℹ️  AI not available: {e}")
        return None


def cmd_auth():
    """Authenticate and test connection."""
    print("🔐 Google Contacts Cleanup — Authentication")
    print("=" * 50)
    print()

    creds = authenticate()
    success = test_connection(creds)

    if success:
        print("✅ All set! Continue with 'python main.py backup'.")
    else:
        print("❌ Something went wrong. Check credentials.json and try again.")
        sys.exit(1)


def cmd_backup():
    """Create a full backup of all contacts."""
    print("📦 Google Contacts Cleanup — Backup")
    print("=" * 50)
    print()

    creds = authenticate()
    client = PeopleAPIClient(creds)
    backup_path = create_backup(client)

    print()
    print(f"Continue with 'python main.py analyze'.")


def cmd_analyze():
    """Analyze contacts, detect issues, generate workplan."""
    print("🔍 Google Contacts Cleanup — Analysis")
    print("=" * 50)
    print()

    # Load latest backup
    backup_path = get_latest_backup()
    if not backup_path:
        print("❌ No backup found! Run 'python main.py backup' first.")
        sys.exit(1)

    print(f"Using backup: {backup_path.name}")
    backup_data = load_backup(backup_path)
    contacts = backup_data["contacts"]
    groups = backup_data.get("contact_groups", [])
    group_members = backup_data.get("group_members", {})

    print(f"Total contacts: {len(contacts)}")
    print()

    # ── Initialize AI (if configured) ──────────────────────────────
    ai = _get_ai_analyzer()

    # ── Analyze contacts ──────────────────────────────────────────
    print("📊 Analyzing contacts...")

    def progress(done, total):
        print(f"\r   Analyzed: {done}/{total}  ", end="", flush=True)

    results = analyze_all_contacts(contacts, progress_callback=progress, ai_analyzer=ai)
    print()
    print(f"   Contacts with findings: {len(results)}")

    if ai:
        stats = ai.get_usage_stats()
        print(f"   🤖 AI: {stats['total_input_tokens'] + stats['total_output_tokens']} tokens, ~${stats['estimated_cost_usd']:.3f}")
    print()

    # ── Find duplicates ───────────────────────────────────────────
    print("🔍 Finding duplicates...")
    duplicates = find_duplicates(contacts)
    print(f"   Potential duplicates: {len(duplicates)} groups")
    print()

    # ── Analyze labels ────────────────────────────────────────────
    print("🏷  Analyzing labels...")
    labels_analysis = analyze_labels(groups, group_members, contacts)
    print(f"   Labels: {len(labels_analysis['labels'])}")
    print(f"   Unlabeled: {labels_analysis['unlabeled_contacts']} contacts")
    print(f"   Suggestions: {len(labels_analysis['suggestions'])}")
    print()

    # ── Generate workplan ─────────────────────────────────────────
    print("📝 Generating workplan...")
    workplan_path = generate_workplan(results, duplicates, labels_analysis)
    print(f"   Saved: {workplan_path}")
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

    print(f"Continue with 'python main.py fix' for interactive fixes.")


def cmd_fix(auto_mode=False, confidence_threshold=0.90, dry_run=False):
    """Apply fixes interactively (or automatically in auto-mode)."""
    if auto_mode:
        print("🤖 Google Contacts Cleanup — Automatic Fixes")
    else:
        print("🔧 Google Contacts Cleanup — Fixes")
    print("=" * 50)
    print()

    # Load workplan
    workplan_path = get_latest_workplan()
    if not workplan_path:
        print("❌ No workplan found! Run 'python main.py analyze' first.")
        sys.exit(1)

    workplan = load_workplan(workplan_path)
    print(f"Workplan: {workplan_path.name}")
    print(format_workplan_summary(workplan))
    print()

    if dry_run:
        print("ℹ️  DRY RUN — no changes will be applied.")
        return

    # Confirm (skip in auto mode)
    if not auto_mode:
        print("Continue with fixes? [y/n]: ", end="")
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return

        if answer not in ("y", "yes", "a", "ano"):
            print("Cancelled.")
            return

    # ── Setup ─────────────────────────────────────────────────────
    session_id = str(uuid.uuid4())
    creds = authenticate()
    client = PeopleAPIClient(creds)

    # We need fresh contact data for etags
    print()
    print("📡 Fetching current contacts for etag sync...")

    def progress(fetched, total):
        print(f"\r   Fetched: {fetched} / ~{total}  ", end="", flush=True)

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
    if auto_mode:
        print(f"Mode:       automatic (confidence >= {confidence_threshold})")
    print()

    # ── Process batches ───────────────────────────────────────────
    mem = MemoryManager()
    result = process_batches(
        workplan=workplan,
        contacts_lookup=contacts_lookup,
        client=client,
        changelog=changelog,
        recovery=recovery,
        memory=mem,
        auto_mode=auto_mode,
        auto_confidence_threshold=confidence_threshold,
    )

    # In auto-mode, handle review file and notifications
    if auto_mode and result:
        from notifier import (
            send_macos_notification,
            generate_run_summary,
            write_review_file,
        )

        skipped = result.get("skipped_for_review", [])
        review_path = write_review_file(skipped)

        summary = generate_run_summary(
            changes_applied=result.get("success", 0),
            changes_failed=result.get("failed", 0),
            changes_skipped=result.get("skipped", 0),
            skipped_for_review=skipped,
        )
        print()
        print(summary)

        if review_path:
            print(f"\n📋 Review file: {review_path}")

        # Send macOS notification
        msg = f"✅ {result.get('success', 0)} changes"
        if skipped:
            msg += f", 📋 {len(skipped)} for review"
        send_macos_notification("Contacts Refiner", msg)

    if result:
        result["session_id"] = session_id
    return result


def cmd_ai_review(resume=False) -> int:
    """AI review of MEDIUM confidence changes — checkpointed, resumable.
    Returns the number of promoted (MEDIUM->HIGH) changes."""
    print("🤖 Google Contacts Cleanup — AI Review")
    print("=" * 50)
    print()

    # Load checkpoint or start fresh
    checkpoint = {}
    if resume and AI_REVIEW_CHECKPOINT.exists():
        checkpoint = json.loads(AI_REVIEW_CHECKPOINT.read_text(encoding="utf-8"))
        print(f"Resuming from position {checkpoint.get('last_reviewed', 0)}")

    # Load workplan
    workplan_path = checkpoint.get("workplan_path")
    if workplan_path:
        workplan_path = Path(workplan_path)
    else:
        workplan_path = get_latest_workplan()

    if not workplan_path or not workplan_path.exists():
        print("❌ No workplan found!")
        return

    workplan = load_workplan(workplan_path)
    print(f"Workplan: {workplan_path.name}")

    # Load backup for full contact data (AI needs context)
    backup_path = get_latest_backup()
    if not backup_path:
        print("❌ No backup found!")
        return

    backup_data = load_backup(backup_path)
    contacts_by_rn = {
        c.get("resourceName", ""): c
        for c in backup_data["contacts"]
    }

    # Load AI review history (skip contacts already reviewed with same changes)
    ai_history = {}
    if AI_REVIEW_HISTORY.exists():
        try:
            ai_history = json.loads(AI_REVIEW_HISTORY.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            ai_history = {}

    def _changes_hash(changes: list[dict]) -> str:
        """Stable hash of changes for dedup across runs."""
        key = json.dumps(
            sorted([{k: c[k] for k in ("field", "old", "new") if k in c} for c in changes],
                   key=lambda x: x.get("field", "")),
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    # Collect contacts with MEDIUM confidence changes
    all_medium_items = []  # (batch_idx, contact_idx, resourceName, changes)
    for bi, batch in enumerate(workplan["batches"]):
        for ci, contact in enumerate(batch["contacts"]):
            medium_changes = [
                ch for ch in contact.get("changes", [])
                if 0.60 <= ch.get("confidence", 0) < 0.90
            ]
            if medium_changes:
                all_medium_items.append((bi, ci, contact["resourceName"], medium_changes))

    # Filter out already-reviewed contacts (same changes hash)
    medium_items = []
    skipped_from_history = 0
    for item in all_medium_items:
        bi, ci, rn, changes = item
        h = _changes_hash(changes)
        if ai_history.get(rn) == h:
            skipped_from_history += 1
        else:
            medium_items.append(item)

    print(f"Contacts with MEDIUM changes: {len(all_medium_items)}")
    if skipped_from_history:
        print(f"   Skipped (already reviewed): {skipped_from_history}")
    print(f"   For review: {len(medium_items)}")

    if not medium_items:
        print("ℹ️  No MEDIUM changes for AI review.")
        _cleanup_ai_checkpoint()
        return 0

    # Initialize AI
    ai = _get_ai_analyzer()
    if not ai:
        print("❌ AI not available!")
        return 0

    # Process in batches of AI_MAX_CONTACTS_PER_BATCH
    start_from = checkpoint.get("last_reviewed", 0)
    total = len(medium_items)
    promoted = 0
    demoted = 0

    for i in range(start_from, total, AI_MAX_CONTACTS_PER_BATCH):
        batch_items = medium_items[i:i + AI_MAX_CONTACTS_PER_BATCH]

        # Prepare (contact, changes) tuples
        contacts_with_changes = []
        for bi, ci, rn, changes in batch_items:
            person = contacts_by_rn.get(rn, {})
            contacts_with_changes.append((person, changes))

        # Call AI
        ai_batch_failed = False
        try:
            enhanced_list = ai.enhance_batch(contacts_with_changes)
        except Exception as e:
            print(f"   ⚠️  AI batch error: {e}")
            enhanced_list = [ch for _, ch in contacts_with_changes]
            ai_batch_failed = True

        # Update workplan with AI results
        for j, (bi, ci, rn, orig_changes) in enumerate(batch_items):
            if j < len(enhanced_list):
                new_changes = enhanced_list[j]
                # Replace MEDIUM changes in workplan contact
                contact = workplan["batches"][bi]["contacts"][ci]
                # Keep HIGH changes unchanged, replace MEDIUM with AI result
                high_changes = [
                    ch for ch in contact["changes"]
                    if ch.get("confidence", 0) >= 0.90
                ]
                low_changes = [
                    ch for ch in contact["changes"]
                    if ch.get("confidence", 0) < 0.60
                ]
                contact["changes"] = high_changes + new_changes + low_changes

                # Count promotions/demotions
                for ch in new_changes:
                    if ch.get("confidence", 0) >= 0.90:
                        promoted += 1
                    elif ch.get("confidence", 0) < 0.60:
                        demoted += 1

                # Recompute stats
                all_ch = contact["changes"]
                contact["stats"] = {
                    "high": sum(1 for c in all_ch if c.get("confidence", 0) >= 0.90),
                    "medium": sum(1 for c in all_ch if 0.60 <= c.get("confidence", 0) < 0.90),
                    "low": sum(1 for c in all_ch if c.get("confidence", 0) < 0.60),
                    "total": len(all_ch),
                }

                # Record in history so next run skips this contact (skip if AI batch failed)
                if not ai_batch_failed:
                    ai_history[rn] = _changes_hash(orig_changes)

        end_idx = min(i + AI_MAX_CONTACTS_PER_BATCH, total)
        print(f"   AI reviewed: {end_idx}/{total}")

        # Save workplan incrementally (so timeout doesn't lose results)
        with open(workplan_path, "w", encoding="utf-8") as f:
            json.dump(workplan, f, ensure_ascii=False, indent=2)

        # Save checkpoint and history incrementally
        AI_REVIEW_CHECKPOINT.write_text(json.dumps({
            "status": "in_progress",
            "workplan_path": str(workplan_path),
            "last_reviewed": end_idx,
            "total": total,
            "promoted": promoted,
            "demoted": demoted,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        AI_REVIEW_HISTORY.write_text(
            json.dumps(ai_history, ensure_ascii=False), encoding="utf-8"
        )

    # Print AI stats
    stats = ai.get_usage_stats()
    print()
    print(f"🤖 AI review complete:")
    print(f"   Promoted (MEDIUM→HIGH): {promoted}")
    print(f"   Demoted (MEDIUM→LOW):   {demoted}")
    print(f"   Tokens: {stats['total_input_tokens'] + stats['total_output_tokens']}")
    print(f"   Cost:   ~${stats['estimated_cost_usd']:.3f}")

    # Cleanup checkpoint
    _cleanup_ai_checkpoint()

    # Log AI learnings count
    learnings = ai.get_new_learnings()
    if learnings:
        print(f"   Learned patterns: {len(learnings)}")

    return promoted


def _cleanup_ai_checkpoint():
    """Remove AI review checkpoint file."""
    if AI_REVIEW_CHECKPOINT.exists():
        AI_REVIEW_CHECKPOINT.unlink()


def cmd_verify():
    """Verify changes by comparing current state with backup."""
    print("✅ Google Contacts Cleanup — Verification")
    print("=" * 50)
    print()

    # Load backup
    backup_path = get_latest_backup()
    if not backup_path:
        print("❌ No backup found!")
        sys.exit(1)

    backup_data = load_backup(backup_path)
    backup_contacts = {c["resourceName"]: c for c in backup_data["contacts"]}

    # Load changelog
    changelog_path = find_latest_changelog()
    if not changelog_path:
        print("❌ No changelog found!")
        sys.exit(1)

    entries = load_changelog(changelog_path)
    cl_summary = summarize_changelog(entries)

    # Fetch current contacts
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print("📡 Fetching current contacts...")

    def progress(fetched, total):
        print(f"\r   Fetched: {fetched} / ~{total}  ", end="", flush=True)

    current_contacts = client.get_all_contacts(progress_callback=progress)
    print()

    current_lookup = {get_resource_name(c): c for c in current_contacts}

    # Compare
    changed_count = 0
    unchanged_count = 0
    missing_count = 0

    change_entries = [e for e in entries if "field" in e and "old" in e]

    print(f"Changelog has {len(change_entries)} changes on {cl_summary['contacts_modified']} contacts")
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
            print(f"   ⚠️  Contact {rn} no longer exists!")

    total_contacts = len(current_contacts)

    print("═══════════════════════════════════════════")
    print("          VERIFICATION")
    print("═══════════════════════════════════════════")
    print(f"  Backup:              {backup_path.name}")
    print(f"  Contacts in backup:  {len(backup_contacts)}")
    print(f"  Current contacts:    {total_contacts}")
    print(f"  Changed (changelog): {cl_summary['contacts_modified']}")
    print(f"  Verified:            {changed_count}")
    print(f"  Missing:             {missing_count}")
    print()
    print(f"  Changes by confidence:")
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
        print("❌ No changelog for rollback!")
        sys.exit(1)

    entries = load_changelog(changelog_path)
    change_entries = [e for e in entries if "field" in e and "old" in e]

    if not change_entries:
        print("ℹ️  Changelog contains no changes to rollback.")
        return

    # Reverse order for rollback
    change_entries.reverse()

    # Group by contact
    by_contact: dict[str, list[dict]] = {}
    for entry in change_entries:
        rn = entry.get("resourceName", "")
        by_contact.setdefault(rn, []).append(entry)

    print(f"Changelog: {changelog_path.name}")
    print(f"Changes to rollback: {len(change_entries)}")
    print(f"Contacts to rollback: {len(by_contact)}")
    print()

    print("WARNING: Rollback reverts changes in reverse order.")
    print("         For a full rollback, use a backup instead.")
    print()
    print("Continue? [y/n]: ", end="")

    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return

    if answer not in ("y", "yes", "a", "ano"):
        print("Cancelled.")
        return

    # Connect and rollback
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print()
    print("📡 Fetching current contacts...")

    def progress(fetched, total):
        print(f"\r   Fetched: {fetched} / ~{total}  ", end="", flush=True)

    current_contacts = client.get_all_contacts(progress_callback=progress)
    print()

    contacts_lookup = {get_resource_name(c): c for c in current_contacts}

    # Process rollback by contact
    success = 0
    failed = 0

    for rn, entries_for_contact in by_contact.items():
        person = contacts_lookup.get(rn)
        if not person:
            print(f"   ⚠️  {rn} — contact not found, skipping")
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
    print("▶️  Google Contacts Cleanup — Resume")
    print("=" * 50)
    print()

    if not RecoveryManager.has_pending_session():
        print("ℹ️  No pending session.")
        return

    checkpoint = RecoveryManager.load_checkpoint()
    if not checkpoint:
        print("ℹ️  Failed to load checkpoint.")
        return

    print(RecoveryManager.format_checkpoint_info(checkpoint))
    print()

    # In cloud environment, auto-approve resume
    from config import ENVIRONMENT
    if ENVIRONMENT != "cloud":
        print("Resume from last checkpoint? [y/n/r to restart]: ", end="")
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return

        if answer in ("r", "restart"):
            RecoveryManager.clear_checkpoint()
            print("Checkpoint cleared. Run 'python main.py fix' for a fresh start.")
            return

        if answer not in ("y", "yes", "a", "ano"):
            print("Cancelled.")
            return

    # Resume
    workplan_path = checkpoint.get("workplan_path")
    if not workplan_path or not Path(workplan_path).exists():
        print("❌ Workplan file does not exist!")
        return

    workplan = load_workplan(Path(workplan_path))
    start_batch = checkpoint.get("last_completed_batch", 0) + 1

    print(f"Resuming from batch {start_batch}...")
    print()

    # Setup
    session_id = checkpoint["session_id"]
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print("📡 Fetching current contacts...")

    def progress(fetched, total):
        print(f"\r   Fetched: {fetched} / ~{total}  ", end="", flush=True)

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

    mem = MemoryManager()
    process_batches(
        workplan=workplan,
        contacts_lookup=contacts_lookup,
        client=client,
        changelog=changelog,
        recovery=recovery,
        start_from_batch=start_batch,
        memory=mem,
        auto_mode=ENVIRONMENT == "cloud",
        auto_confidence_threshold=0.90,
    )


def cmd_info():
    """Show info about backups, workplans, changelogs."""
    print("ℹ️  Google Contacts Cleanup — Info")
    print("=" * 50)
    print()

    # Backups
    backups = list_backups()
    print(f"📦 Backups ({len(backups)}):")
    for b in backups[:5]:
        size = b.stat().st_size / 1024 / 1024
        print(f"   {b.name}  ({size:.1f} MB)")
    print()

    # Workplans
    plans = sorted(DATA_DIR.glob("workplan_*.json"), reverse=True)
    print(f"📝 Workplans ({len(plans)}):")
    for p in plans[:5]:
        size = p.stat().st_size / 1024
        print(f"   {p.name}  ({size:.0f} KB)")
    print()

    # Changelogs
    logs = sorted(DATA_DIR.glob("changelog_*.jsonl"), reverse=True)
    print(f"📜 Changelogs ({len(logs)}):")
    for l in logs[:5]:
        entries = load_changelog(l)
        changes = sum(1 for e in entries if "field" in e)
        print(f"   {l.name}  ({changes} changes)")
    print()

    # Checkpoint
    if RecoveryManager.has_pending_session():
        checkpoint = RecoveryManager.load_checkpoint()
        if checkpoint:
            print(RecoveryManager.format_checkpoint_info(checkpoint))
    else:
        print("⏸  No pending session.")


def cmd_auth_activity():
    """Authenticate both accounts for Gmail + Calendar access."""
    from config import ACTIVITY_ACCOUNTS
    from auth import authenticate_for_activity

    print("🔐 Activity Tagging — Authentication")
    print("=" * 50)
    print()

    for account in ACTIVITY_ACCOUNTS:
        email = account["email"]
        print(f"── {email} ──")
        try:
            creds = authenticate_for_activity(email)
            # Quick test: list 1 Gmail message
            from googleapiclient.discovery import build
            gmail = build("gmail", "v1", credentials=creds)
            result = gmail.users().messages().list(userId="me", maxResults=1).execute()
            count = result.get("resultSizeEstimate", 0)
            print(f"✅ Gmail OK (messages: ~{count})")

            cal = build("calendar", "v3", credentials=creds)
            cal_list = cal.calendarList().list(maxResults=1).execute()
            print(f"✅ Calendar OK")
        except Exception as e:
            print(f"❌ Error: {e}")
        print()

    print("✅ Done! Now you can run 'python main.py tag-activity'")


def cmd_tag_activity(skip_scan=False, dry_run=False):
    """Scan Gmail + Calendar and assign year-based labels to contacts."""
    from config import ACTIVITY_ACCOUNTS
    from auth import authenticate, authenticate_for_activity
    from interaction_scanner import InteractionScanner

    print("🏷  Activity Tagging — Scanning and labeling contacts")
    print("=" * 50)
    print()

    if dry_run:
        print("ℹ️  DRY RUN — no labels will be assigned")
        print()

    # Step 1: Get contacts
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print("📡 Fetching contacts...")

    def progress(fetched, total):
        print(f"\r   Fetched: {fetched} / ~{total}  ", end="", flush=True)

    contacts = client.get_all_contacts(progress_callback=progress)
    print()
    print(f"   Total contacts: {len(contacts)}")
    print()

    # Step 2: Authenticate activity accounts
    account_credentials = []
    if not skip_scan:
        for account in ACTIVITY_ACCOUNTS:
            email = account["email"]
            print(f"🔐 Authenticating {email}...")
            try:
                acreds = authenticate_for_activity(email)
                account_credentials.append((email, acreds))
                print(f"   ✅ OK")
            except Exception as e:
                print(f"   ⚠️  Skipping {email}: {e}")
        print()

    # Step 3: Scan and assign
    scanner = InteractionScanner(contacts)
    stats = scanner.run_full_scan(
        account_credentials=account_credentials,
        client=client,
        skip_scan=skip_scan,
        dry_run=dry_run,
    )

    # Print results
    print()
    print("═══════════════════════════════════════════")
    print("       ACTIVITY TAGGING RESULTS")
    print("═══════════════════════════════════════════")
    for label in sorted(stats.keys()):
        count = stats[label]
        print(f"  {label}: {count} contacts {'(would assign)' if dry_run else 'assigned'}")
    total = sum(stats.values())
    print(f"  ────────────────────")
    print(f"  Total: {total}")
    print("═══════════════════════════════════════════")


def cmd_linkedin_match(csv_path: str, dry_run: bool = False):
    """Match LinkedIn connections to Google Contacts and enrich."""
    from linkedin_matcher import (
        parse_linkedin_csv, match_connections,
        generate_enrichment_changes, format_match_report,
    )

    print("🔗 LinkedIn Connection Matching")
    print("=" * 50)

    # Parse LinkedIn export
    print(f"📄 Parsing LinkedIn CSV: {csv_path}")
    connections = parse_linkedin_csv(csv_path)
    print(f"   Connections found: {len(connections)}")

    if not connections:
        print("❌ No connections found in CSV. Check the file format.")
        return

    # Load latest backup for contacts
    backup_path = get_latest_backup()
    if not backup_path:
        print("❌ No backup found! Run 'python main.py backup' first.")
        sys.exit(1)

    backup_data = load_backup(backup_path)
    contacts = backup_data["contacts"]
    print(f"   Google Contacts: {len(contacts)}")
    print()

    # Match
    print("🔍 Matching connections to contacts...")
    matches = match_connections(connections, contacts)
    print(f"   Matches: {len(matches)}")
    print()

    # Generate enrichment changes
    results = generate_enrichment_changes(matches)

    # Display report
    print(format_match_report(matches, results))

    if not results:
        print("\n   No enrichment changes to apply.")
        return

    if dry_run:
        print(f"\n   🔍 Dry run — {len(results)} contacts would be enriched.")
        return

    # Save as workplan for review/apply via normal fix pipeline
    from workplan import generate_workplan_from_results, format_workplan_summary, load_workplan
    workplan_path = generate_workplan_from_results(results, source="linkedin")
    print(f"\n   📝 Workplan saved: {workplan_path}")
    print("   Run 'python main.py fix' to review and apply changes.")


def cmd_ltns(skip_scan=False, dry_run=False, no_prompts=False):
    """Identify LTNS (Long Time No See) contacts and generate reconnect prompts."""
    from config import ACTIVITY_ACCOUNTS, LTNS_TOP_N
    from auth import authenticate, authenticate_for_activity
    from interaction_scanner import InteractionScanner

    print("🔄 LTNS — Long Time No See Reconnect")
    print("=" * 50)
    print()

    if dry_run:
        print("ℹ️  DRY RUN — no groups or notes will be updated")
        print()

    # Step 1: Get contacts
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print("📡 Fetching contacts...")

    def progress(fetched, total):
        print(f"\r   Fetched: {fetched} / ~{total}  ", end="", flush=True)

    contacts = client.get_all_contacts(progress_callback=progress)
    print()
    print(f"   Total contacts: {len(contacts)}")
    print()

    # Step 2: Authenticate activity accounts
    account_credentials = []
    if not skip_scan:
        for account in ACTIVITY_ACCOUNTS:
            email = account["email"]
            print(f"🔐 Authenticating {email}...")
            try:
                acreds = authenticate_for_activity(email)
                account_credentials.append((email, acreds))
                print(f"   ✅ OK")
            except Exception as e:
                print(f"   ⚠️  Skipping {email}: {e}")
        print()

    # Step 3: Scan interactions
    scanner = InteractionScanner(contacts)
    if not skip_scan:
        for account_email, acreds in account_credentials:
            print(f"📧 Scanning {account_email}...")
            scanner.scan_gmail(acreds, account_email)
            scanner.scan_calendar(acreds, account_email)
        print()

    # Step 4: Identify LTNS candidates
    print("🔍 Identifying LTNS candidates...")
    ltns_list = scanner.identify_ltns(client, top_n=LTNS_TOP_N, dry_run=dry_run)

    if not ltns_list:
        print("ℹ️  No LTNS candidates found.")
        return

    # Step 5: Display results
    print()
    print("═══════════════════════════════════════════")
    print(f"       LTNS TOP {len(ltns_list)} RECONNECT LIST")
    print("═══════════════════════════════════════════")
    for i, c in enumerate(ltns_list, 1):
        social = ""
        for u in c.get("urls", []):
            if u["type"] in ("linkedin", "facebook"):
                social += f" [{u['type'][0].upper()}]"
        org_info = f" @ {c['org']}" if c.get("org") else ""
        title_info = f" ({c['title']})" if c.get("title") else ""
        print(
            f"  {i:3d}. {c['name']:<30s}{org_info}{title_info}"
            f"  last: {c['last_date']}  gap: {c['months_gap']:>5.1f}m"
            f"  score: {c['score']:>7.1f}{social}"
        )
    print("═══════════════════════════════════════════")

    # Count social signals
    with_linkedin = sum(1 for c in ltns_list if any(u["type"] == "linkedin" for u in c.get("urls", [])))
    with_facebook = sum(1 for c in ltns_list if any(u["type"] == "facebook" for u in c.get("urls", [])))
    print(f"  LinkedIn profiles: {with_linkedin}")
    print(f"  Facebook profiles: {with_facebook}")
    print()

    # Step 6: Generate reconnect prompts
    if not no_prompts:
        print("🤖 Generating reconnect prompts...")
        updated = scanner.generate_reconnect_prompts(client, ltns_list, dry_run=dry_run)
        print(f"   Prompts {'generated' if dry_run else 'written'}: {updated}")

    # Save the list to data dir for reference
    import json
    from config import DATA_DIR
    ltns_path = DATA_DIR / "ltns_list.json"
    ltns_data = {
        "generated": datetime.now().isoformat(),
        "count": len(ltns_list),
        "candidates": ltns_list,
    }
    with open(ltns_path, "w", encoding="utf-8") as f:
        json.dump(ltns_data, f, ensure_ascii=False, indent=2)
    print(f"   List saved: {ltns_path}")


def cmd_followup(skip_scan=False, dry_run=False, no_prompts=False):
    """Score FollowUp candidates using interaction history + LinkedIn signals."""
    from config import ACTIVITY_ACCOUNTS, FOLLOWUP_SCORES_FILE, FOLLOWUP_TOP_N
    from auth import authenticate, authenticate_for_activity
    from interaction_scanner import InteractionScanner
    from followup_scorer import (
        load_linkedin_signals,
        score_contacts,
        build_followup_scores_json,
        upload_followup_scores_to_gcs,
    )

    print("🔄 FollowUp — AI-Powered Reconnect Scoring")
    print("=" * 50)
    print()

    if dry_run:
        print("ℹ️  DRY RUN — no groups, notes, or GCS will be updated")
        print()

    # Step 1: Get contacts
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print("📡 Fetching contacts...")

    def progress(fetched, total):
        print(f"\r   Fetched: {fetched} / ~{total}  ", end="", flush=True)

    contacts = client.get_all_contacts(progress_callback=progress)
    print()
    print(f"   Total contacts: {len(contacts)}")
    print()

    # Step 2: Authenticate activity accounts + scan
    scanner = InteractionScanner(contacts)
    if not skip_scan:
        account_credentials = []
        for account in ACTIVITY_ACCOUNTS:
            email = account["email"]
            print(f"🔐 Authenticating {email}...")
            try:
                acreds = authenticate_for_activity(email)
                account_credentials.append((email, acreds))
                print(f"   ✅ OK")
            except Exception as e:
                print(f"   ⚠️  Skipping {email}: {e}")
        print()

        for account_email, acreds in account_credentials:
            print(f"📧 Scanning {account_email}...")
            scanner.scan_gmail(acreds, account_email)
            scanner.scan_calendar(acreds, account_email)
        print()

    # Step 3: Load LinkedIn signals
    linkedin_signals = load_linkedin_signals()
    li_count = len(linkedin_signals)
    print(f"🔗 LinkedIn signals loaded: {li_count}")
    if li_count:
        types = {}
        for sig in linkedin_signals.values():
            t = sig.get("signal_type", "unknown")
            types[t] = types.get(t, 0) + 1
        print(f"   {types}")
    print()

    # Step 4: Score candidates
    print("📊 Scoring FollowUp candidates...")
    scored = score_contacts(
        contacts=contacts,
        interactions=scanner._interactions,
        contact_emails=scanner._contact_emails,
        linkedin_signals=linkedin_signals,
        top_n=FOLLOWUP_TOP_N,
    )

    if not scored:
        print("ℹ️  No FollowUp candidates found.")
        return

    # Step 5: Display results
    print()
    print("═══════════════════════════════════════════════════════════")
    print(f"       FOLLOWUP TOP {len(scored)} RECONNECT LIST")
    print("═══════════════════════════════════════════════════════════")
    for s in scored:
        li_tag = ""
        if s.linkedin_signal:
            li_tag = f" [LI:{s.linkedin_signal}]"
        org_info = f" @ {s.org}" if s.org else ""
        title_info = f" ({s.title})" if s.title else ""
        last = s.last_date or "never"
        print(
            f"  {s.rank:3d}. {s.name:<30s}{org_info}{title_info}"
            f"  last: {last}  gap: {s.months_gap:>5.1f}m"
            f"  score: {s.score_total:>7.1f}{li_tag}"
        )
    print("═══════════════════════════════════════════════════════════")

    # Score breakdown
    with_linkedin = sum(1 for s in scored if s.linkedin_signal)
    job_changes = sum(1 for s in scored if s.linkedin_signal == "job_change")
    print(f"  With LinkedIn signal: {with_linkedin} ({job_changes} job changes)")
    print(f"  Avg completeness: {sum(s.completeness for s in scored) / len(scored):.1f}/4")
    print()

    # Step 6: Create FollowUp group
    if not dry_run:
        print("👥 Updating FollowUp group...")
        scanner.create_followup_group(client, scored)

    # Step 7: Generate AI prompts
    if not no_prompts:
        print("🤖 Generating FollowUp prompts...")
        updated, prompts = scanner.generate_followup_prompts(client, scored, dry_run=dry_run)
        # Attach prompts to scored list for JSON output
        for s in scored:
            s.followup_prompt = prompts.get(s.resource_name)
        print(f"   Prompts {'generated' if dry_run else 'written'}: {updated}")

    # Step 8: Save scores to file + GCS
    scores_json = build_followup_scores_json(scored)
    FOLLOWUP_SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FOLLOWUP_SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores_json, f, ensure_ascii=False, indent=2)
    print(f"   Scores saved: {FOLLOWUP_SCORES_FILE}")

    if not dry_run:
        upload_followup_scores_to_gcs()

    print()
    print("✅ FollowUp scoring complete!")


def cmd_linkedin_scan(skip_scan=False, dry_run=False, limit=100, groups=None):
    """Scan LinkedIn profiles for social signals. Supports group-based filtering."""
    from config import ACTIVITY_ACCOUNTS
    from auth import authenticate, authenticate_for_activity
    from interaction_scanner import InteractionScanner
    from linkedin_scanner import LinkedInScanner

    print("🔗 LinkedIn Social Signals Scanner")
    print("=" * 50)
    print()

    if dry_run:
        print("ℹ️  DRY RUN — no notes will be updated")
        print()

    # Step 1: Get contacts
    creds = authenticate()
    client = PeopleAPIClient(creds)

    print("📡 Fetching contacts...")

    def progress(fetched, total):
        print(f"\r   Fetched: {fetched} / ~{total}  ", end="", flush=True)

    contacts = client.get_all_contacts(progress_callback=progress)
    print()
    print(f"   Total contacts: {len(contacts)}")
    print()

    # Step 2a: Group-based filtering (if --groups specified)
    group_members = None
    if groups:
        group_names = [g.strip() for g in groups.split(",")]
        print(f"🏷️  Filtering by groups: {', '.join(group_names)}")
        all_groups = client.get_all_contact_groups()
        group_members = set()
        matched_names = []
        for grp in all_groups:
            grp_name = grp.get("name") or grp.get("formattedName", "")
            if grp_name in group_names:
                members = client.get_contact_group_members(grp["resourceName"])
                print(f"   {grp_name}: {len(members)} members")
                group_members.update(members)
                matched_names.append(grp_name)
        unmatched = set(group_names) - set(matched_names)
        if unmatched:
            print(f"   ⚠️  Groups not found: {', '.join(sorted(unmatched))}")
        print(f"   Total unique members: {len(group_members)}")
        print()

    # Step 2b: Get LTNS list (skip if group filtering active)
    ltns_list = []
    if groups:
        print("   Skipping LTNS scan (group filter active)")
        print()
    elif not skip_scan:
        # Scan interactions to identify LTNS candidates
        account_credentials = []
        for account in ACTIVITY_ACCOUNTS:
            email = account["email"]
            print(f"🔐 Authenticating {email}...")
            try:
                acreds = authenticate_for_activity(email)
                account_credentials.append((email, acreds))
                print(f"   ✅ OK")
            except Exception as e:
                print(f"   ⚠️  Skipping {email}: {e}")
        print()

        scanner = InteractionScanner(contacts)
        for account_email, acreds in account_credentials:
            print(f"📧 Scanning {account_email}...")
            scanner.scan_gmail(acreds, account_email)
            scanner.scan_calendar(acreds, account_email)
        print()

        print("🔍 Identifying LTNS candidates...")
        from config import LTNS_TOP_N
        ltns_list = scanner.identify_ltns(client, top_n=LTNS_TOP_N, dry_run=True)
        print(f"   Found {len(ltns_list)} LTNS candidates")
        print()
    else:
        # Try loading cached LTNS list
        ltns_path = DATA_DIR / "ltns_list.json"
        if ltns_path.exists():
            data = json.loads(ltns_path.read_text(encoding="utf-8"))
            ltns_list = data.get("candidates", [])
            print(f"   Loaded {len(ltns_list)} cached LTNS candidates")
        else:
            print("   ⚠️  No cached LTNS list — scanning contacts with LinkedIn URLs only")
        print()

    # Step 3: Select targets
    li_scanner = LinkedInScanner(contacts)
    targets = li_scanner.select_targets(ltns_list=ltns_list, limit=limit, group_members=group_members)

    if not targets:
        print("ℹ️  No targets found for LinkedIn scanning.")
        return

    with_url = sum(1 for t in targets if t.get("linkedin_url"))
    without_url = len(targets) - with_url
    print(f"🎯 Selected {len(targets)} targets ({with_url} with LinkedIn URL, {without_url} need discovery)")
    print()

    # Step 4: Display targets and wait for confirmation
    print("═══════════════════════════════════════════")
    print(f"       LINKEDIN SCAN TARGETS ({len(targets)})")
    print("═══════════════════════════════════════════")
    for i, t in enumerate(targets[:20], 1):
        url_status = "✅" if t.get("linkedin_url") else "🔍"
        org_info = f" @ {t['org']}" if t.get("org") else ""
        print(f"  {i:3d}. {url_status} {t['name']:<30s}{org_info}")
    if len(targets) > 20:
        print(f"  ... and {len(targets) - 20} more")
    print("═══════════════════════════════════════════")
    print()

    print("⚠️  Browser automation will visit LinkedIn profiles.")
    print("   This requires Chrome with an active LinkedIn session.")
    print("   Rate: ~1 profile every 15 seconds.")
    print(f"   Estimated time: ~{len(targets) * 15 // 60} minutes")
    print()
    print("   To proceed, run the linkedin-scan from Claude Code")
    print("   which will use Chrome MCP tools for browser automation.")
    print()

    # Save targets for the browser automation step
    targets_path = DATA_DIR / "linkedin_scan_targets.json"
    with open(targets_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "count": len(targets),
            "targets": targets,
        }, f, ensure_ascii=False, indent=2)
    print(f"   Targets saved: {targets_path}")

    # Step 5: If not dry run, note writing will happen after browser automation
    if dry_run:
        print("\n   DRY RUN — skipping browser automation and note writing")
    else:
        print("\n   Next: Use Claude Code to run browser automation on these targets.")
        print("   Then call: python main.py linkedin-scan --write-notes")

    return targets


def cmd_crm_sync(dry_run=False):
    """Sync CRM notes and tags from dashboard to Google Contacts."""
    from crm_sync import run_crm_sync

    print("🔄 CRM Sync — Notes & Tags → Google Contacts")
    print("=" * 50)
    print()

    if dry_run:
        print("ℹ️  DRY RUN — no contacts or groups will be updated")
        print()

    result = run_crm_sync(dry_run=dry_run)

    notes = result["notes"]
    tags = result["tags"]

    print()
    print("📝 Notes sync:")
    print(f"   Synced: {notes['synced']}")
    print(f"   Skipped (unchanged): {notes['skipped']}")
    if notes["errors"]:
        print(f"   Errors: {notes['errors']}")

    print()
    print("🏷️  Tags sync:")
    print(f"   Groups created: {tags['groups_created']}")
    print(f"   Memberships added: {tags['memberships_added']}")
    if tags["errors"]:
        print(f"   Errors: {tags['errors']}")

    print()
    print("Done.")


def cmd_refresh_tables(table=None):
    """Refresh code tables from external sources."""
    from code_tables import tables

    print("🔄 Code Tables — Refresh")
    print("=" * 50)
    print()

    # Show current status
    info = tables.info()
    for name, status in info.items():
        source = "seed" if not status.get("has_cache") else "cached"
        count = status.get("count", "?")
        age = f"{status['age_days']}d ago" if "age_days" in status else "never refreshed"
        url = "🌐" if status.get("refresh_url") else "📋 manual"
        print(f"  {name:25s}  {count:>5} entries  ({source}, {age})  {url}")
    print()

    # Refresh
    print("Refreshing...")
    results = tables.refresh(name=table, force=True)
    print()

    for name, result in results.items():
        status = result["status"]
        if status == "updated":
            print(f"  ✅ {name}: {result['old_count']} → {result['new_count']} entries (+{result['added']})")
        elif status == "skipped":
            print(f"  ⏭  {name}: {result['reason']}")
        else:
            print(f"  ❌ {name}: {result.get('reason', 'unknown error')}")

    print()
    print("Done.")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Google Contacts Refiner — cleanup and fix contacts",
        usage="python main.py <command> [options]",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "auth", "backup", "analyze", "analyse", "fix", "ai-review",
            "verify", "rollback", "resume", "info",
            "auth-activity", "tag-activity", "ltns", "followup",
            "linkedin-match", "linkedin-scan", "crm-sync", "refresh-tables",
        ],
        help="Command to execute",
    )
    parser.add_argument("--auto", action="store_true", help="Automatic mode (no interaction)")
    parser.add_argument("--confidence", type=float, default=0.90, help="Min. confidence for auto-apply (default: 0.90)")
    parser.add_argument("--dry-run", action="store_true", help="Analysis only, no changes")
    parser.add_argument("--skip-scan", action="store_true", help="Skip Gmail/Calendar scan, use cache")
    parser.add_argument("--no-prompts", action="store_true", help="Skip AI reconnect prompt generation (LTNS)")
    parser.add_argument("--csv", type=str, help="Path to LinkedIn Connections.csv (for linkedin-match)")
    parser.add_argument("--limit", type=int, default=100, help="Max profiles to scan (for linkedin-scan)")
    parser.add_argument("--write-notes", action="store_true", help="Write cached scan results to notes (for linkedin-scan)")
    parser.add_argument("--groups", type=str, help="Comma-separated group names to filter targets (for linkedin-scan, e.g. Y2025,Y2026)")

    args = parser.parse_args()

    if not args.command:
        print(__doc__)
        sys.exit(0)

    command = args.command.lower()
    if command == "analyse":
        command = "analyze"

    simple_commands = {
        "auth": cmd_auth,
        "backup": cmd_backup,
        "analyze": cmd_analyze,
        "ai-review": cmd_ai_review,
        "verify": cmd_verify,
        "rollback": cmd_rollback,
        "resume": cmd_resume,
        "info": cmd_info,
        "auth-activity": cmd_auth_activity,
        "refresh-tables": cmd_refresh_tables,
    }

    try:
        if command == "fix":
            cmd_fix(
                auto_mode=args.auto,
                confidence_threshold=args.confidence,
                dry_run=args.dry_run,
            )
        elif command == "tag-activity":
            cmd_tag_activity(
                skip_scan=args.skip_scan,
                dry_run=args.dry_run,
            )
        elif command == "linkedin-match":
            if not args.csv:
                print("❌ --csv required: python main.py linkedin-match --csv <path-to-Connections.csv>")
                sys.exit(1)
            cmd_linkedin_match(csv_path=args.csv, dry_run=args.dry_run)
        elif command == "linkedin-scan":
            cmd_linkedin_scan(
                skip_scan=args.skip_scan,
                dry_run=args.dry_run,
                limit=args.limit,
                groups=args.groups,
            )
        elif command == "ltns":
            cmd_ltns(
                skip_scan=args.skip_scan,
                dry_run=args.dry_run,
                no_prompts=args.no_prompts,
            )
        elif command == "followup":
            cmd_followup(
                skip_scan=args.skip_scan,
                dry_run=args.dry_run,
                no_prompts=args.no_prompts,
            )
        elif command == "crm-sync":
            cmd_crm_sync(dry_run=args.dry_run)
        elif command in simple_commands:
            simple_commands[command]()
        else:
            print(f"❌ Unknown command: {command}")
            print(__doc__)
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⏸  Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

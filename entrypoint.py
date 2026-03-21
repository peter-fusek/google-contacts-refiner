#!/usr/bin/env python3
"""
Cloud Run Job entry point for Google Contacts Refiner.

Two-phase pipeline:
  Phase 1 (fast, ~5 min): backup → analyze (rule-based, NO AI) → auto-fix HIGH
  Phase 2 (slow, checkpointed): AI review of MEDIUM changes → auto-fix promoted

Resume logic (after timeout/crash):
  - AI review checkpoint exists → resume Phase 2
  - Fix checkpoint exists → resume fix
  - Otherwise → fresh Phase 1
"""
import logging
import os
import sys
import traceback
from datetime import datetime

# Configure logging for Cloud Logging (structured JSON to stdout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("contacts-refiner")


def _auto_export_sessions():
    """
    Auto-export review sessions that have decisions but haven't been exported.

    Replicates the dashboard export logic in Python so that Phase 0 can
    process sessions without requiring a manual "Export for Pipeline" click.
    """
    import hashlib
    import json
    from pathlib import Path

    from config import DATA_DIR

    session_dir = DATA_DIR / "review_sessions"
    if not session_dir.exists():
        return

    session_files = sorted(session_dir.glob("*.json"))
    if not session_files:
        return

    # Find the latest review file (skipped_changes source)
    review_files = sorted(
        f for f in DATA_DIR.glob("review_*.json")
        if "sessions" not in f.name and "decisions" not in f.name
    )
    if not review_files:
        logger.info("Phase 0 auto-export: No review file found, skipping")
        return

    latest_review = review_files[-1]
    with open(latest_review, encoding="utf-8") as f:
        review_data = json.load(f)

    # Build change lookup: changeId → metadata (same hash as dashboard)
    change_map = {}
    for item in review_data.get("items", []):
        rn = item.get("resourceName", "")
        dn = item.get("displayName", "")
        for change in item.get("skipped_changes", []):
            raw = f"{rn}|{change.get('field', '')}|{change.get('old', '')}|{change.get('new', '')}"
            change_id = hashlib.sha256(raw.encode()).hexdigest()[:12]
            change_map[change_id] = {
                "resourceName": rn,
                "displayName": dn,
                "field": change.get("field", ""),
                "old": change.get("old", ""),
                "new": change.get("new", ""),
                "confidence": change.get("confidence", 0),
                "reason": change.get("reason", ""),
            }

    exported_count = 0
    for session_file in session_files:
        try:
            with open(session_file, encoding="utf-8") as f:
                session = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        decisions = session.get("decisions", {})
        if not decisions or session.get("exportedAt"):
            continue

        # Build enriched changes list
        enriched = []
        for change_id, d in decisions.items():
            decision = d.get("decision", "")
            if decision in ("approved", "edited", "rejected"):
                meta = change_map.get(change_id, {})
                enriched.append({
                    "changeId": change_id,
                    "decision": decision,
                    "editedValue": d.get("editedValue"),
                    "decidedAt": d.get("decidedAt", ""),
                    "resourceName": meta.get("resourceName"),
                    "field": meta.get("field"),
                    "old": meta.get("old"),
                    "new": meta.get("new"),
                    "confidence": meta.get("confidence"),
                    "reason": meta.get("reason"),
                })

        if not enriched:
            continue

        # Write review_decisions file
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        sid = session.get("id", "unknown")
        decisions_path = DATA_DIR / f"review_decisions_{timestamp}.json"
        with open(decisions_path, "w", encoding="utf-8") as f:
            json.dump({
                "sessionId": sid,
                "exportedAt": datetime.now().isoformat(),
                "reviewFilePath": str(latest_review),
                "changes": enriched,
            }, f, ensure_ascii=False, indent=2)

        # Mark session as exported
        session["exportedAt"] = datetime.now().isoformat()
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

        exported_count += 1
        logger.info(f"Phase 0: Auto-exported {len(enriched)} decisions from session {sid}")

    if exported_count:
        logger.info(f"Phase 0: Auto-exported {exported_count} session(s)")
    else:
        logger.info("Phase 0: No unexported sessions found")


def _move_to_failed(filepath: str, reason: str):
    """Move a decision file to failed/ directory. Logs outcome."""
    import shutil
    from pathlib import Path
    from config import DATA_DIR

    # Path containment check — only process files from DATA_DIR
    src = Path(filepath).resolve()
    if not str(src).startswith(str(DATA_DIR.resolve())):
        logger.error(f"Phase 0: Refusing to move file outside DATA_DIR: {filepath}")
        return

    failed_dir = DATA_DIR / "failed"
    failed_dir.mkdir(exist_ok=True)
    failed_path = failed_dir / src.name
    # Truncate reason to prevent log injection from file content
    safe_reason = reason[:200].replace("\n", " ").replace("\r", "")
    try:
        shutil.move(str(src), failed_path)
        logger.error(f"Phase 0: {safe_reason} — {src.name} moved to failed/")
    except OSError as e:
        logger.error(f"Phase 0: {safe_reason} — {src.name} could not be moved: {e}")


def _process_review_feedback():
    """
    Phase 0: Process review decisions from the dashboard.

    Reads review_decisions_*.json files from GCS, applies approved/edited
    changes via People API, feeds all decisions into memory for learning,
    and archives processed files.
    """
    import glob as glob_module
    import json
    import shutil
    from collections import defaultdict
    from pathlib import Path

    from config import DATA_DIR
    from memory import MemoryManager

    # Auto-export any unexported sessions first
    try:
        _auto_export_sessions()
    except Exception as e:
        logger.warning(f"Phase 0: Auto-export failed (non-fatal): {e}")

    # Find unprocessed decision files
    pattern = str(DATA_DIR / "review_decisions_*.json")
    decision_files = sorted(glob_module.glob(pattern))

    if not decision_files:
        logger.info("Phase 0: No review decisions to process")
        return

    logger.info(f"Phase 0: Processing {len(decision_files)} review decision file(s)")

    memory = MemoryManager()
    feedback_entries = []
    # Collect approved/edited changes grouped by resourceName
    changes_by_contact: dict[str, list[dict]] = defaultdict(list)

    for filepath in decision_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)

            # New enriched format: list of change objects in "changes" key
            changes_list = data.get("changes", [])
            logger.info(f"Phase 0: {filepath} — {len(changes_list)} actionable changes")

            for change in changes_list:
                decision = change.get("decision", "")
                resource_name = change.get("resourceName")

                # Skip changes with missing metadata (unmatched changeIds)
                if not change.get("field") and not change.get("reason"):
                    continue

                # Collect feedback for memory learning (approvals, edits, AND rejections)
                if decision in ("approved", "edited", "rejected"):
                    dtype_map = {"approved": "approval", "edited": "edit", "rejected": "rejection"}
                    feedback_entries.append({
                        "type": dtype_map[decision],
                        "ruleCategory": memory.extract_rule_category(change.get("reason") or ""),
                        "field": change.get("field") or "",
                        "old": change.get("old") or "",
                        "suggested": change.get("new") or "",
                        "finalValue": change.get("editedValue") or change.get("new") or "",
                        "confidence": change.get("confidence", 0),
                    })

                    # Collect approved/edited for API application (not rejections)
                    if decision in ("approved", "edited") and resource_name and change.get("field"):
                        new_value = change.get("editedValue") or change.get("new", "")
                        changes_by_contact[resource_name].append({
                            "field": change["field"],
                            "old": change.get("old", ""),
                            "new": new_value,
                            "confidence": change.get("confidence", 0.65),
                            "reason": f"review:{change.get('reason', '')}",
                        })

            # Archive processed file (move to archive/ subdirectory)
            archive_dir = DATA_DIR / "archive"
            archive_dir.mkdir(exist_ok=True)
            archive_path = archive_dir / Path(filepath).name
            try:
                shutil.move(filepath, archive_path)
                logger.info(f"Phase 0: Archived {filepath} -> {archive_path}")
            except OSError as move_err:
                # Archive failed — try to quarantine so it doesn't retry forever
                _move_to_failed(filepath, f"Archive failed: {move_err}")

        except json.JSONDecodeError as e:
            _move_to_failed(filepath, f"Corrupt JSON: {e}")

        except Exception as e:
            logger.error(f"Phase 0: Failed to process {filepath}: {e}")

    # Feed all decisions into memory for learning
    if feedback_entries:
        memory.process_review_feedback(feedback_entries)
        memory.save()
        logger.info(f"Phase 0: Processed {len(feedback_entries)} feedback entries into memory")

    # Apply approved/edited changes via People API
    if changes_by_contact:
        _apply_review_changes(changes_by_contact)


def _apply_review_changes(changes_by_contact: dict[str, list[dict]]):
    """Apply approved review changes to contacts via People API."""
    import uuid

    from auth import authenticate
    from api_client import PeopleAPIClient
    from batch_processor import build_update_body
    from changelog import ChangeLog
    from utils import get_etag
    from googleapiclient.errors import HttpError

    creds = authenticate()
    client = PeopleAPIClient(creds)
    session_id = f"review_{uuid.uuid4().hex[:8]}"
    changelog = ChangeLog(session_id)

    total_contacts = len(changes_by_contact)
    applied = 0
    failed = 0
    skipped = 0

    logger.info(f"Phase 0: Applying review changes to {total_contacts} contacts")
    changelog.log_batch_start(0, total_contacts)

    for resource_name, changes in changes_by_contact.items():
        try:
            # Fetch fresh contact data for current etag
            try:
                person = client.get_contact(resource_name)
            except HttpError as e:
                status = e.resp.status if e.resp else 0
                if status == 404:
                    logger.info(f"Phase 0: Contact {resource_name} no longer exists (already deleted), skipping")
                    skipped += 1
                    continue
                raise
            etag = get_etag(person)

            # Build update payload
            result = build_update_body(person, changes)
            if not result:
                logger.warning(f"Phase 0: No valid update body for {resource_name}")
                continue

            update_body, update_fields = result

            if not update_fields:
                logger.warning(f"Phase 0: Empty update fields for {resource_name}, skipping")
                continue

            # Apply update
            client.update_contact(resource_name, etag, update_body, update_fields)

            # Log each change
            for change in changes:
                changelog.log_change(
                    resource_name=resource_name,
                    field=change["field"],
                    old_value=change["old"],
                    new_value=change["new"],
                    reason=change["reason"],
                    confidence=change["confidence"],
                    batch=0,
                )
            applied += 1

        except Exception as e:
            logger.error(f"Phase 0: Failed to update {resource_name}: {e}")
            failed += 1

    changelog.log_batch_end(0, applied, failed)
    logger.info(f"Phase 0: Applied review changes — {applied} ok, {failed} failed, {skipped} skipped (contact deleted)")


def _check_pause_flag() -> bool:
    """Check if pipeline is paused via dashboard emergency stop."""
    try:
        from config import DATA_DIR
        pause_file = DATA_DIR / "pipeline_paused.json"
        if pause_file.exists():
            import json
            data = json.loads(pause_file.read_text())
            if data.get("paused"):
                logger.warning("Pipeline is PAUSED (emergency stop from dashboard at %s). Exiting.", data.get("pausedAt", "?"))
                return True
    except Exception as e:
        logger.warning("Failed to check pause flag: %s", e)
    return False


def run():
    """Execute the contacts refiner pipeline."""
    # Check emergency stop flag before doing anything
    if _check_pause_flag():
        return

    start = datetime.now()
    dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
    skip_ai = os.getenv("SKIP_AI_REVIEW", "").lower() in ("1", "true", "yes")

    # Track run state for pipeline_runs.json
    run_state = {
        "phases_completed": [],
        "changes_applied": 0,
        "changes_failed": 0,
        "queue_size": 0,
        "errors": [],
    }

    from config import AI_REVIEW_CHECKPOINT
    from recovery import RecoveryManager

    # ── Pre-phase: Refresh stale code tables ──────────────────────────
    try:
        from code_tables import tables
        tables.refresh_if_stale()
    except Exception as e:
        logger.warning("Code table refresh failed (non-fatal): %s", e)

    # ── Phase 0: Process review feedback ─────────────────────────────
    try:
        _process_review_feedback()
        run_state["phases_completed"].append("phase0")
    except Exception as e:
        logger.warning(f"Phase 0 failed (non-fatal): {e}")
        run_state["errors"].append(f"Phase 0: {e}")

    # ── Resume routing ──────────────────────────────────────────────
    # Priority 1: AI review checkpoint (Phase 2 was interrupted)
    if AI_REVIEW_CHECKPOINT.exists():
        logger.info("AI review checkpoint found — resuming Phase 2")
        try:
            from main import cmd_ai_review, cmd_fix
            cmd_ai_review(resume=True)
            # After AI review, apply promoted changes
            logger.info("Phase 2: Auto-fix promoted changes")
            cmd_fix(auto_mode=True, confidence_threshold=0.90, dry_run=dry_run)
            run_state["phases_completed"].append("phase2_resume")
        except Exception as e:
            logger.error(f"AI review resume failed: {e}")
            run_state["errors"].append(f"Phase 2 resume: {e}")
            traceback.print_exc()
            sys.exit(1)
        _record_pipeline_run(run_state, start)
        _log_elapsed(start)
        return

    # Priority 2: Fix checkpoint (Phase 1 step 3 or Phase 2 fix was interrupted)
    if RecoveryManager.has_pending_session():
        logger.info("Fix checkpoint found — resuming")
        try:
            from main import cmd_resume
            cmd_resume()
            run_state["phases_completed"].append("fix_resume")
        except Exception as e:
            logger.error(f"Fix resume failed: {e}")
            run_state["errors"].append(f"Fix resume: {e}")
            traceback.print_exc()
            sys.exit(1)
        _record_pipeline_run(run_state, start)
        _log_elapsed(start)
        return

    # ── Phase 1: Fast mechanical pass ───────────────────────────────
    logger.info("Phase 1: Fast mechanical pass (no AI)")

    # Step 1: Backup
    logger.info("Step 1/3: Backup")
    try:
        from main import cmd_backup
        cmd_backup()
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    # Step 2: Analyze (rule-based only — NO AI, fast ~2 min)
    logger.info("Step 2/3: Analyze (rule-based)")
    original_ai = os.environ.get("AI_ENABLED")
    os.environ["AI_ENABLED"] = "false"
    try:
        from main import cmd_analyze
        cmd_analyze()
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Restore AI_ENABLED for Phase 2
        if original_ai is not None:
            os.environ["AI_ENABLED"] = original_ai
        else:
            os.environ.pop("AI_ENABLED", None)

    # Step 3: Auto-fix HIGH confidence changes (mechanical)
    logger.info(f"Step 3/3: Auto-fix HIGH {'(DRY RUN)' if dry_run else ''}")
    try:
        from main import cmd_fix
        fix_result = cmd_fix(auto_mode=True, confidence_threshold=0.90, dry_run=dry_run)
        if fix_result:
            run_state["changes_applied"] = fix_result.get("success", 0)
            run_state["changes_failed"] = fix_result.get("failed", 0)
    except Exception as e:
        logger.error(f"Auto-fix failed: {e}")
        run_state["errors"].append(f"Phase 1 auto-fix: {e}")
        traceback.print_exc()
        sys.exit(1)

    # Record queue stats after analysis
    try:
        queue_size = _record_queue_stats()
        run_state["queue_size"] = queue_size or 0
    except Exception as e:
        logger.warning(f"Queue stats failed (non-fatal): {e}")

    run_state["phases_completed"].append("phase1")
    phase1_elapsed = datetime.now() - start
    logger.info(f"Phase 1 completed in {phase1_elapsed}")

    # ── Phase 2: AI review (checkpointed) ───────────────────────────
    if skip_ai:
        logger.info("Phase 2 skipped (SKIP_AI_REVIEW=true)")
        _record_pipeline_run(run_state, start)
        _log_elapsed(start)
        return

    logger.info("Phase 2: AI review of MEDIUM changes")
    try:
        from main import cmd_ai_review
        promoted_count = cmd_ai_review()
    except Exception as e:
        logger.error(f"AI review failed: {e}")
        run_state["errors"].append(f"Phase 2 AI review: {e}")
        traceback.print_exc()
        sys.exit(1)

    # Apply promoted changes only if AI actually promoted something
    if promoted_count and promoted_count > 0:
        logger.info(f"Phase 2: Auto-fix {promoted_count} promoted changes")
        try:
            cmd_fix(auto_mode=True, confidence_threshold=0.90, dry_run=dry_run)
        except Exception as e:
            logger.error(f"Promoted fix failed: {e}")
            run_state["errors"].append(f"Phase 2 fix: {e}")
            traceback.print_exc()
            sys.exit(1)
    else:
        logger.info("Phase 2: No promoted changes, skipping fix")

    run_state["phases_completed"].append("phase2")

    # ── Phase 3 (optional): Activity Tagging ────────────────────────
    enable_activity = os.getenv("ENABLE_ACTIVITY_TAGGING", "").lower() in ("1", "true", "yes")
    if enable_activity:
        logger.info("Phase 3: Activity Tagging")
        try:
            from main import cmd_tag_activity
            cmd_tag_activity(dry_run=dry_run)
            run_state["phases_completed"].append("phase3")
        except Exception as e:
            logger.error(f"Activity tagging failed (non-fatal): {e}")
            run_state["errors"].append(f"Phase 3: {e}")
            traceback.print_exc()
    else:
        logger.info("Phase 3 skipped (ENABLE_ACTIVITY_TAGGING not set)")

    # ── Phase 4 (optional): FollowUp Scoring ────────────────────────
    enable_followup = os.getenv("ENABLE_FOLLOWUP_SCORING", "").lower() in ("1", "true", "yes")
    if enable_followup:
        logger.info("Phase 4: FollowUp Scoring")
        try:
            from main import cmd_followup
            cmd_followup(skip_scan=True, dry_run=dry_run, no_prompts=False)
            run_state["phases_completed"].append("phase4")
        except Exception as e:
            logger.error(f"FollowUp scoring failed (non-fatal): {e}")
            run_state["errors"].append(f"Phase 4: {e}")
            traceback.print_exc()
    else:
        logger.info("Phase 4 skipped (ENABLE_FOLLOWUP_SCORING not set)")

    # ── Record run & send digest ─────────────────────────────────
    _record_pipeline_run(run_state, start)

    try:
        from notifier import send_email_digest
        send_email_digest(run_state, start)
    except Exception as e:
        logger.warning(f"Email digest failed (non-fatal): {e}")

    _log_elapsed(start)


def _record_queue_stats() -> int:
    """Record current review queue size to queue_stats.json for trend tracking.

    Returns the total number of pending changes.
    """
    import json
    from config import DATA_DIR

    # Find the latest review file to count pending changes
    review_files = sorted(
        f for f in DATA_DIR.glob("review_*.json")
        if "sessions" not in f.name and "decisions" not in f.name
    )
    if not review_files:
        return 0

    try:
        with open(review_files[-1], encoding="utf-8") as f:
            review_data = json.load(f)

        total_changes = sum(
            len(item.get("skipped_changes", []))
            for item in review_data.get("items", [])
        )

        # Count by category (reuse rule extraction)
        from memory import MemoryManager
        mem = MemoryManager()
        by_category: dict[str, int] = {}
        for item in review_data.get("items", []):
            for change in item.get("skipped_changes", []):
                cat = mem.extract_rule_category(change.get("reason", ""))
                by_category[cat] = by_category.get(cat, 0) + 1

        # Load existing stats
        stats_path = DATA_DIR / "queue_stats.json"
        stats = []
        if stats_path.exists():
            try:
                with open(stats_path, encoding="utf-8") as f:
                    stats = json.load(f)
            except (json.JSONDecodeError, IOError):
                stats = []

        # Append today's entry (replace if same date)
        today = datetime.now().strftime("%Y-%m-%d")
        stats = [s for s in stats if s.get("date") != today]
        stats.append({
            "date": today,
            "totalChanges": total_changes,
            "byCategory": by_category,
        })

        # Keep last 90 days
        stats = stats[-90:]

        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        logger.info(f"Queue stats: {total_changes} pending changes recorded")
        return total_changes

    except Exception as e:
        logger.warning(f"Queue stats recording failed (non-fatal): {e}")
        return 0


def _record_pipeline_run(run_state: dict, start: datetime):
    """Record structured pipeline run metadata to pipeline_runs.json."""
    import json
    from config import DATA_DIR

    try:
        elapsed = datetime.now() - start
        entry = {
            "date": start.isoformat(),
            "duration_seconds": int(elapsed.total_seconds()),
            "phases_completed": run_state.get("phases_completed", []),
            "queue_size": run_state.get("queue_size", 0),
            "errors": run_state.get("errors", []),
        }

        runs_path = DATA_DIR / "pipeline_runs.json"
        runs = []
        if runs_path.exists():
            try:
                with open(runs_path, encoding="utf-8") as f:
                    runs = json.load(f)
            except (json.JSONDecodeError, IOError):
                runs = []

        runs.append(entry)
        runs = runs[-90:]  # Keep last 90 entries

        with open(runs_path, "w", encoding="utf-8") as f:
            json.dump(runs, f, ensure_ascii=False, indent=2)

        logger.info(f"Pipeline run recorded: {len(entry['phases_completed'])} phases, {entry['duration_seconds']}s")

    except Exception as e:
        logger.warning(f"Pipeline run recording failed (non-fatal): {e}")


def _log_elapsed(start):
    elapsed = datetime.now() - start
    logger.info(f"Pipeline completed in {elapsed}")


if __name__ == "__main__":
    run()

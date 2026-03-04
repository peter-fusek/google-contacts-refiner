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


def run():
    """Execute the contacts refiner pipeline."""
    start = datetime.now()
    dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
    skip_ai = os.getenv("SKIP_AI_REVIEW", "").lower() in ("1", "true", "yes")

    from config import AI_REVIEW_CHECKPOINT
    from recovery import RecoveryManager

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
        except Exception as e:
            logger.error(f"AI review resume failed: {e}")
            traceback.print_exc()
            sys.exit(1)
        _log_elapsed(start)
        return

    # Priority 2: Fix checkpoint (Phase 1 step 3 or Phase 2 fix was interrupted)
    if RecoveryManager.has_pending_session():
        logger.info("Fix checkpoint found — resuming")
        try:
            from main import cmd_resume
            cmd_resume()
        except Exception as e:
            logger.error(f"Fix resume failed: {e}")
            traceback.print_exc()
            sys.exit(1)
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
        cmd_fix(auto_mode=True, confidence_threshold=0.90, dry_run=dry_run)
    except Exception as e:
        logger.error(f"Auto-fix failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    phase1_elapsed = datetime.now() - start
    logger.info(f"Phase 1 completed in {phase1_elapsed}")

    # ── Phase 2: AI review (checkpointed) ───────────────────────────
    if skip_ai:
        logger.info("Phase 2 skipped (SKIP_AI_REVIEW=true)")
        _log_elapsed(start)
        return

    logger.info("Phase 2: AI review of MEDIUM changes")
    try:
        from main import cmd_ai_review
        cmd_ai_review()
    except Exception as e:
        logger.error(f"AI review failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    # Apply promoted changes
    logger.info("Phase 2: Auto-fix promoted changes")
    try:
        cmd_fix(auto_mode=True, confidence_threshold=0.90, dry_run=dry_run)
    except Exception as e:
        logger.error(f"Promoted fix failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    _log_elapsed(start)


def _log_elapsed(start):
    elapsed = datetime.now() - start
    logger.info(f"Pipeline completed in {elapsed}")


if __name__ == "__main__":
    run()

"""
Notification system for headless runs.

Supports macOS notifications (local), Cloud Logging (cloud),
and daily email digest via Resend.
"""
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR, ENVIRONMENT

logger = logging.getLogger(__name__)


def send_notification(title: str, message: str):
    """
    Send a notification — platform-aware.

    Cloud: logs to Cloud Logging (stdout → captured by Cloud Run).
    Local (macOS): sends native notification via osascript.
    """
    if ENVIRONMENT == "cloud":
        logger.info(f"[{title}] {message}")
    else:
        _send_macos_notification(title, message)


def _send_macos_notification(title: str, message: str):
    """Send a macOS notification via osascript with stdin to avoid injection."""
    try:
        script = (
            'on run argv\n'
            '  display notification (item 2 of argv) with title (item 1 of argv)\n'
            'end run'
        )
        subprocess.run(
            ["osascript", "-e", script, title, message],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        pass  # Silently fail if notifications aren't available


# Backward compat alias
send_macos_notification = send_notification


def generate_run_summary(
    changes_applied: int,
    changes_failed: int,
    changes_skipped: int,
    skipped_for_review: list[dict],
    ai_stats: Optional[dict] = None,
) -> str:
    """Generate a human-readable summary of an auto-mode run."""
    lines = [
        "═══════════════════════════════════════════",
        f"  AUTOMATIC RUN — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "═══════════════════════════════════════════",
        f"  Applied:     {changes_applied}",
        f"  Failed:      {changes_failed}",
        f"  Skipped:     {changes_skipped}",
        f"  For review:  {len(skipped_for_review)}",
    ]

    if ai_stats:
        lines.append(
            f"  AI tokeny:   {ai_stats.get('total_input_tokens', 0) + ai_stats.get('total_output_tokens', 0)}"
        )
        lines.append(f"  AI cost:     ${ai_stats.get('estimated_cost_usd', 0):.3f}")

    lines.append("═══════════════════════════════════════════")

    return "\n".join(lines)


def send_email_digest(run_state: dict, start: datetime) -> bool:
    """
    Send a daily email digest summarizing the pipeline run via Resend.

    Requires RESEND_API_KEY env var (or Secret Manager in cloud mode).
    Returns True if sent successfully.
    """
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        logger.info("Email digest skipped: RESEND_API_KEY not set")
        return False

    try:
        import resend
    except ImportError:
        logger.warning("Email digest skipped: 'resend' package not installed (pip install resend)")
        return False

    elapsed = datetime.now() - start
    duration_min = int(elapsed.total_seconds()) // 60
    duration_sec = int(elapsed.total_seconds()) % 60
    date_str = start.strftime("%Y-%m-%d")
    phases = ", ".join(run_state.get("phases_completed", [])) or "none"
    queue = run_state.get("queue_size", 0)
    errors = run_state.get("errors", [])

    body_lines = [
        "Contact Refiner — Daily Report",
        "=" * 35,
        f"Date:     {date_str}",
        f"Duration: {duration_min}m {duration_sec}s",
        f"Phases:   {phases}",
        "",
        f"Review Queue: {queue} pending",
        "",
    ]

    if errors:
        body_lines.append(f"Errors: {len(errors)}")
        for err in errors:
            body_lines.append(f"  - {err}")
    else:
        body_lines.append("Errors: none")

    body_lines.extend([
        "",
        "— Contact Refiner",
        "https://contactrefiner.com/dashboard",
    ])

    body = "\n".join(body_lines)
    subject = f"Contact Refiner — Daily Report {date_str}"

    try:
        resend.api_key = api_key
        result = resend.Emails.send({
            "from": "Contact Refiner <noreply@contactrefiner.com>",
            "to": ["peterfusek1980@gmail.com"],
            "subject": subject,
            "text": body,
        })
        logger.info(f"Email digest sent: {result.get('id', 'ok')}")
        return True

    except Exception as e:
        logger.warning(f"Email digest failed: {e}")
        return False


def write_review_file(skipped_changes: list[dict]) -> Optional[Path]:
    """
    Write changes that need manual review to a timestamped JSON file.

    Returns the path to the review file, or None if no changes to review.
    """
    if not skipped_changes:
        return None

    # Filter out no-change items (old == new) from AI review
    filtered = []
    for item in skipped_changes:
        real_changes = [
            c for c in item.get("skipped_changes", [])
            if c.get("old", "") != c.get("new", "")
        ]
        if real_changes:
            filtered.append({**item, "skipped_changes": real_changes})

    if not filtered:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    review_path = DATA_DIR / f"review_{timestamp}.json"

    review_data = {
        "generated": datetime.now().isoformat(),
        "total_items": len(filtered),
        "items": filtered,
    }

    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)

    return review_path

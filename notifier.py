"""
Notification system for headless runs.

Supports macOS notifications and generates run summaries.
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR


def send_macos_notification(title: str, message: str):
    """Send a macOS notification via osascript."""
    try:
        # Escape double quotes in message
        safe_title = title.replace('"', '\\"')
        safe_message = message.replace('"', '\\"')
        subprocess.run(
            [
                "osascript", "-e",
                f'display notification "{safe_message}" with title "{safe_title}"',
            ],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        pass  # Silently fail if notifications aren't available


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
        f"  AUTOMATICKÝ BEH — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "═══════════════════════════════════════════",
        f"  Aplikované:  {changes_applied}",
        f"  Zlyhané:     {changes_failed}",
        f"  Preskočené:  {changes_skipped}",
        f"  Na review:   {len(skipped_for_review)}",
    ]

    if ai_stats:
        lines.append(
            f"  AI tokeny:   {ai_stats.get('total_input_tokens', 0) + ai_stats.get('total_output_tokens', 0)}"
        )
        lines.append(f"  AI náklady:  ${ai_stats.get('estimated_cost_usd', 0):.3f}")

    lines.append("═══════════════════════════════════════════")

    return "\n".join(lines)


def write_review_file(skipped_changes: list[dict]) -> Optional[Path]:
    """
    Write changes that need manual review to a timestamped JSON file.

    Returns the path to the review file, or None if no changes to review.
    """
    if not skipped_changes:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    review_path = DATA_DIR / f"review_{timestamp}.json"

    review_data = {
        "generated": datetime.now().isoformat(),
        "total_items": len(skipped_changes),
        "items": skipped_changes,
    }

    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)

    return review_path

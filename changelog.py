"""
Append-only JSONL changelog for tracking all changes.
Each line is a JSON object describing one field change.
Used for rollback and audit.
"""
import json
from datetime import datetime
from pathlib import Path

from config import DATA_DIR


class ChangeLog:
    """
    Append-only JSONL log of changes made to contacts.

    Each entry:
    {
        "timestamp": "ISO8601",
        "resourceName": "people/c...",
        "field": "phoneNumbers[0].value",
        "old": "0903123456",
        "new": "+421 903 123 456",
        "reason": "SK mobile format normalization",
        "confidence": "high",
        "batch": 3,
        "session_id": "uuid"
    }
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = DATA_DIR / f"changelog_{timestamp}.jsonl"
        self._ensure_file()

    def _ensure_file(self):
        """Create log file if it doesn't exist."""
        if not self.log_path.exists():
            self.log_path.touch()

    def log_change(
        self,
        resource_name: str,
        field: str,
        old_value: str,
        new_value: str,
        reason: str,
        confidence: float,
        batch: int,
    ):
        """Append a change entry to the log."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "resourceName": resource_name,
            "field": field,
            "old": old_value,
            "new": new_value,
            "reason": reason,
            "confidence": self._confidence_label(confidence),
            "confidence_value": confidence,
            "batch": batch,
            "session_id": self.session_id,
        }

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_batch_start(self, batch_num: int, contact_count: int):
        """Log batch start marker."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "batch_start",
            "batch": batch_num,
            "contact_count": contact_count,
            "session_id": self.session_id,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_batch_end(self, batch_num: int, success: int, failed: int):
        """Log batch end marker."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "batch_end",
            "batch": batch_num,
            "success": success,
            "failed": failed,
            "session_id": self.session_id,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        if confidence >= 0.90:
            return "high"
        elif confidence >= 0.60:
            return "medium"
        else:
            return "low"

    def get_all_entries(self) -> list[dict]:
        """Read all entries from the log."""
        entries = []
        if not self.log_path.exists():
            return entries
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def get_changes_for_contact(self, resource_name: str) -> list[dict]:
        """Get all changes for a specific contact."""
        return [
            e for e in self.get_all_entries()
            if e.get("resourceName") == resource_name and "field" in e
        ]

    def get_rollback_entries(self) -> list[dict]:
        """
        Get entries needed for rollback, in reverse order.
        Only returns actual field changes (not markers).
        """
        entries = [
            e for e in self.get_all_entries()
            if "field" in e and "old" in e
        ]
        entries.reverse()
        return entries


def load_changelog(path: Path) -> list[dict]:
    """Load a changelog file and return all entries."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def find_latest_changelog() -> Path | None:
    """Find the most recent changelog file."""
    logs = sorted(DATA_DIR.glob("changelog_*.jsonl"), reverse=True)
    return logs[0] if logs else None


def summarize_changelog(entries: list[dict]) -> dict:
    """
    Summarize changelog entries.

    Returns:
        {
            'total_changes': int,
            'by_confidence': {'high': int, 'medium': int, 'low': int},
            'by_batch': {batch_num: {'changes': int, 'success': int, 'failed': int}},
            'contacts_modified': int,
        }
    """
    summary = {
        "total_changes": 0,
        "by_confidence": {"high": 0, "medium": 0, "low": 0},
        "by_batch": {},
        "contacts_modified": set(),
    }

    for entry in entries:
        if "field" in entry:
            summary["total_changes"] += 1
            conf = entry.get("confidence", "unknown")
            if conf in summary["by_confidence"]:
                summary["by_confidence"][conf] += 1
            rn = entry.get("resourceName", "")
            if rn:
                summary["contacts_modified"].add(rn)

        if entry.get("type") == "batch_end":
            batch = entry.get("batch", 0)
            summary["by_batch"][batch] = {
                "success": entry.get("success", 0),
                "failed": entry.get("failed", 0),
            }

    summary["contacts_modified"] = len(summary["contacts_modified"])
    return summary

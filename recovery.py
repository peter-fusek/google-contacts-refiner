"""
Crash recovery — checkpoint/resume support.
Saves progress after each batch so processing can resume after failure.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from config import DATA_DIR


CHECKPOINT_FILE = DATA_DIR / "checkpoint.json"


class RecoveryManager:
    """
    Manages session state and checkpoints for crash recovery.
    """

    def __init__(self, session_id: str = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.checkpoint_data = {
            "session_id": self.session_id,
            "started_at": datetime.now().isoformat(),
            "last_completed_batch": 0,
            "total_batches": 0,
            "contacts_processed": 0,
            "contacts_total": 0,
            "status": "initialized",
            "workplan_path": "",
            "changelog_path": "",
            "backup_path": "",
        }

    def set_session_info(
        self,
        total_batches: int,
        contacts_total: int,
        workplan_path: str = "",
        changelog_path: str = "",
        backup_path: str = "",
    ):
        """Set session information at the start."""
        self.checkpoint_data.update({
            "total_batches": total_batches,
            "contacts_total": contacts_total,
            "status": "in_progress",
            "workplan_path": workplan_path,
            "changelog_path": changelog_path,
            "backup_path": backup_path,
        })
        self._save()

    def save_checkpoint(self, batch_num: int, contacts_processed: int):
        """Save a checkpoint after completing a batch."""
        self.checkpoint_data.update({
            "last_completed_batch": batch_num,
            "contacts_processed": contacts_processed,
            "last_checkpoint_at": datetime.now().isoformat(),
        })
        self._save()

    def mark_completed(self):
        """Mark the session as completed."""
        self.checkpoint_data["status"] = "completed"
        self.checkpoint_data["completed_at"] = datetime.now().isoformat()
        self._save()

    def mark_failed(self, error: str):
        """Mark the session as failed."""
        self.checkpoint_data["status"] = "failed"
        self.checkpoint_data["error"] = error
        self.checkpoint_data["failed_at"] = datetime.now().isoformat()
        self._save()

    def _save(self):
        """Write checkpoint to file."""
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(self.checkpoint_data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def has_pending_session() -> bool:
        """Check if there's an in-progress session."""
        if not CHECKPOINT_FILE.exists():
            return False
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("status") == "in_progress"
        except (json.JSONDecodeError, KeyError):
            return False

    @staticmethod
    def load_checkpoint() -> dict | None:
        """Load the current checkpoint, if any."""
        if not CHECKPOINT_FILE.exists():
            return None
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def clear_checkpoint():
        """Remove the checkpoint file."""
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()

    @staticmethod
    def format_checkpoint_info(data: dict) -> str:
        """Format checkpoint info for display."""
        lines = [
            "═══ PENDING SESSION ═══",
            "",
            f"  Session ID:  {data.get('session_id', '?')}",
            f"  Started:     {data.get('started_at', '?')}",
            f"  Status:      {data.get('status', '?')}",
            f"  Last batch:  {data.get('last_completed_batch', 0)} / {data.get('total_batches', '?')}",
            f"  Contacts:    {data.get('contacts_processed', 0)} / {data.get('contacts_total', '?')}",
        ]

        if data.get("last_checkpoint_at"):
            lines.append(f"  Last checkpoint: {data['last_checkpoint_at']}")

        if data.get("error"):
            lines.append(f"  Error: {data['error']}")

        return "\n".join(lines)

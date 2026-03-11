"""
File-based memory system for cross-session learning.

Manages two files:
- instructions.md — human-editable rules (version controlled)
- memory.json — machine-learned patterns (gitignored)
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import APP_DIR, DATA_DIR


INSTRUCTIONS_PATH = APP_DIR / "instructions.md"  # Ships with code, not data
MEMORY_PATH = DATA_DIR / "memory.json"            # Persisted data (GCS in cloud)

# Rule category extraction from reason strings
RULE_CATEGORIES = {
    "diacritics": r"diakritik|diacritics",
    "title_case": r"veľkosti písmen|Title Case|letter casing",
    "phone_format": r"tel\. čísla|normalizácia tel|phone.*normalization|international format",
    "phone_type": r"typu tel|phone.*type",
    "phone_duplicate": r"duplicitné tel|duplicate phone",
    "email_normalize": r"normalizácia email|email.*normalization",
    "email_invalid": r"nevalidný.*email|invalid.*email",
    "email_duplicate": r"duplicitný email|duplicate email",
    "address_zip": r"PSČ|postal code",
    "address_country": r"krajin|country",
    "address_parse": r"parsovanie adres|address.*pars",
    "org_case": r"organizáci|organization",
    "name_extract": r"extrakcia.*mena|extrakcia.*Name|name.*extract|inferred.*name",
    "name_split": r"rozdelenie|name.*split",
    "name_title": r"extrakcia titul|title.*extract",
    "company_in_name": r"firma.*men|firmu|company.*name|strip.*company",
    "family_name_fix": r"priezvisko|family.*name",
    "x500_dn": r"X\.500 DN",
    "org_from_email": r"odhadnutá z email|inferred from email|organization.*email",
    "domain_case": r"domain|domén|doména",
    "event_from_note": r"udalosť z poznámky|dátum.*poznámky|from notes|extracted from notes",
}

# Default empty memory structure
_DEFAULT_MEMORY = {
    "version": "1.1",
    "last_updated": None,
    "diacritics_corrections": {},
    "merge_decisions": {},
    "enrichment_patterns": {"domain_to_org": {}},
    "rejected_changes": [],
    "session_history": [],
    "rule_stats": {},
}


class MemoryManager:
    """
    Manages persistent learning across sessions.

    Loads instructions.md (human rules) and memory.json (machine patterns)
    at initialization. Records approvals/rejections during processing,
    then saves updated memory at the end.
    """

    def __init__(self):
        self.instructions = self._load_instructions()
        self.memory = self._load_memory()
        self._dirty = False

    # ── Public API ────────────────────────────────────────────────

    def get_prompt_context(self) -> str:
        """Return combined instructions + memory summary for AI prompt context."""
        parts = []

        if self.instructions:
            parts.append("=== RULES (instructions.md) ===")
            parts.append(self.instructions)

        diacritics = self.memory.get("diacritics_corrections", {})
        if diacritics:
            # Only include high-confidence learned patterns
            learned = {
                k: v["corrected"] for k, v in diacritics.items()
                if v.get("times_approved", 0) > v.get("times_rejected", 0)
            }
            if learned:
                parts.append("\n=== LEARNED DIACRITICS PATTERNS ===")
                parts.append(json.dumps(learned, ensure_ascii=False))

        rejected = self.memory.get("rejected_changes", [])
        if rejected:
            recent = rejected[-20:]  # Last 20 rejections
            parts.append("\n=== RECENTLY REJECTED CHANGES ===")
            for r in recent:
                parts.append(
                    f"- {r.get('field', '?')}: "
                    f"\"{r.get('rejected_value', '')}\" rejected, "
                    f"kept \"{r.get('kept_value', '')}\""
                )

        return "\n".join(parts) if parts else ""

    def get_diacritics_preference(self, name: str) -> Optional[str]:
        """
        Check if memory has a learned diacritics preference for a name.

        Returns the corrected form if approved more than rejected,
        or None if no preference or if user rejected the correction.
        """
        diacritics = self.memory.get("diacritics_corrections", {})
        entry = diacritics.get(name)
        if not entry:
            return None

        approved = entry.get("times_approved", 0)
        rejected = entry.get("times_rejected", 0)

        if approved > rejected:
            return entry.get("corrected")
        return None

    def record_approval(self, change: dict):
        """Record that a change was approved by the user."""
        field = change.get("field", "")
        reason = change.get("reason", "")

        # Track diacritics approvals
        if "diakritik" in reason.lower() or "diacritics" in reason.lower():
            old_val = change.get("old", "")
            new_val = change.get("new", "")
            if old_val and new_val and old_val != new_val:
                self._record_diacritics(old_val, new_val, approved=True)

        # Track domain-to-org mappings from enrichment
        if "organizáci" in reason.lower() or "organization" in reason.lower() or "email" in reason.lower():
            extra = change.get("extra", {})
            if extra.get("domain") and change.get("new"):
                patterns = self.memory.setdefault("enrichment_patterns", {})
                domain_map = patterns.setdefault("domain_to_org", {})
                domain_map[extra["domain"]] = change["new"]
                self._dirty = True

    def record_rejection(self, change: dict):
        """Record that a change was rejected by the user."""
        field = change.get("field", "")
        reason = change.get("reason", "")

        # Track diacritics rejections
        if "diakritik" in reason.lower() or "diacritics" in reason.lower():
            old_val = change.get("old", "")
            new_val = change.get("new", "")
            if old_val and new_val:
                self._record_diacritics(old_val, new_val, approved=False)

        # Record all rejections for context
        self.memory.setdefault("rejected_changes", []).append({
            "field": field,
            "rejected_value": change.get("new", ""),
            "kept_value": change.get("old", ""),
            "reason": reason,
            "date": datetime.now().isoformat(),
        })
        # Keep only last 100 rejections
        self.memory["rejected_changes"] = self.memory["rejected_changes"][-100:]
        self._dirty = True

    def merge_learnings(self, learnings: list[dict]):
        """Merge AI-generated learnings into memory."""
        for learning in learnings:
            ltype = learning.get("type", "")
            if ltype == "diacritics_pattern":
                key = learning.get("key", "")
                value = learning.get("value", "")
                if key and value:
                    self._record_diacritics(key, value, approved=True)
            elif ltype == "domain_to_org":
                domain = learning.get("key", "")
                org = learning.get("value", "")
                if domain and org:
                    patterns = self.memory.setdefault("enrichment_patterns", {})
                    domain_map = patterns.setdefault("domain_to_org", {})
                    domain_map[domain] = org
                    self._dirty = True

    def extract_rule_category(self, reason: str) -> str:
        """Extract rule category from a reason string."""
        for category, pattern in RULE_CATEGORIES.items():
            if re.search(pattern, reason, re.IGNORECASE):
                return category
        return "other"

    def process_review_feedback(self, decisions: list[dict]):
        """
        Process review decisions from the dashboard.

        Each decision dict should have:
          - type: 'approval' | 'rejection' | 'edit'
          - ruleCategory: str
          - field: str
          - old: str
          - suggested: str
          - finalValue: str
          - confidence: float
        """
        rule_stats = self.memory.setdefault("rule_stats", {})

        for d in decisions:
            category = d.get("ruleCategory", "other")
            stats = rule_stats.setdefault(category, {
                "approved": 0, "rejected": 0, "edited": 0,
            })

            dtype = d.get("type", "")
            if dtype == "approval":
                stats["approved"] = stats.get("approved", 0) + 1
                # Also record as a standard approval for diacritics tracking
                self.record_approval({
                    "field": d.get("field", ""),
                    "old": d.get("old", ""),
                    "new": d.get("finalValue", d.get("suggested", "")),
                    "reason": d.get("ruleCategory", ""),
                })
            elif dtype == "rejection":
                stats["rejected"] = stats.get("rejected", 0) + 1
                self.record_rejection({
                    "field": d.get("field", ""),
                    "old": d.get("old", ""),
                    "new": d.get("suggested", ""),
                    "reason": d.get("ruleCategory", ""),
                })
            elif dtype == "edit":
                stats["edited"] = stats.get("edited", 0) + 1

            # Recalculate adjusted confidence
            total = stats.get("approved", 0) + stats.get("rejected", 0) + stats.get("edited", 0)
            if total >= 5:
                approved = stats.get("approved", 0) + stats.get("edited", 0)
                approval_rate = approved / total
                # Bayesian smoothing: prior weight of 10 at base confidence 0.75
                base = 0.75
                adjusted = (base * 10 + approval_rate * total) / (10 + total)
                stats["adjusted_confidence"] = max(0.30, min(0.98, round(adjusted, 3)))

        self._dirty = True

    def get_adjusted_confidence(self, rule_category: str, base_confidence: float) -> float:
        """
        Get confidence adjusted by user feedback history.

        Uses Bayesian smoothing: if enough feedback exists (>=5 decisions),
        blend the base confidence with the observed approval rate.
        """
        rule_stats = self.memory.get("rule_stats", {})
        stats = rule_stats.get(rule_category)
        if not stats:
            return base_confidence

        total = stats.get("approved", 0) + stats.get("rejected", 0) + stats.get("edited", 0)
        if total < 5:
            return base_confidence

        adjusted = stats.get("adjusted_confidence")
        if adjusted is not None:
            return adjusted

        # Fallback calculation
        approved = stats.get("approved", 0) + stats.get("edited", 0)
        approval_rate = approved / total
        blended = (base_confidence * 10 + approval_rate * total) / (10 + total)
        return max(0.30, min(0.98, blended))

    def record_session(self, contacts_processed: int, changes_applied: int):
        """Record session summary in history."""
        self.memory.setdefault("session_history", []).append({
            "date": datetime.now().isoformat(),
            "contacts_processed": contacts_processed,
            "changes_applied": changes_applied,
        })
        # Keep only last 50 sessions
        self.memory["session_history"] = self.memory["session_history"][-50:]
        self._dirty = True

    def save(self):
        """Persist memory.json to disk."""
        if not self._dirty:
            return

        self.memory["last_updated"] = datetime.now().isoformat()

        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(self.memory, f, ensure_ascii=False, indent=2)

        self._dirty = False

    # ── Private helpers ───────────────────────────────────────────

    def _record_diacritics(self, ascii_form: str, corrected: str, approved: bool):
        """Record a diacritics approval or rejection."""
        diacritics = self.memory.setdefault("diacritics_corrections", {})
        entry = diacritics.setdefault(ascii_form, {
            "corrected": corrected,
            "times_approved": 0,
            "times_rejected": 0,
        })

        if approved:
            entry["times_approved"] = entry.get("times_approved", 0) + 1
        else:
            entry["times_rejected"] = entry.get("times_rejected", 0) + 1

        # Update corrected form if this is an approval
        if approved:
            entry["corrected"] = corrected

        self._dirty = True

    def _load_instructions(self) -> str:
        """Load instructions.md as plain text."""
        if INSTRUCTIONS_PATH.exists():
            return INSTRUCTIONS_PATH.read_text(encoding="utf-8")
        return ""

    def _load_memory(self) -> dict:
        """Load memory.json or create default structure."""
        if MEMORY_PATH.exists():
            try:
                data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
                # Merge with defaults for any missing keys
                for key, default_val in _DEFAULT_MEMORY.items():
                    data.setdefault(key, default_val)
                return data
            except (json.JSONDecodeError, IOError):
                pass
        return dict(_DEFAULT_MEMORY)

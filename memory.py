"""
File-based memory system for cross-session learning.

Manages two files:
- instructions.md — human-editable rules (version controlled)
- memory.json — machine-learned patterns (gitignored)
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import APP_DIR, DATA_DIR

_logger = logging.getLogger(__name__)


INSTRUCTIONS_PATH = APP_DIR / "instructions.md"  # Ships with code, not data
MEMORY_PATH = DATA_DIR / "memory.json"            # Persisted data (GCS in cloud)

# Rule category extraction from reason strings
# NOTE: Order matters — first match wins. More specific patterns must come first.
RULE_CATEGORIES = {
    "diacritics_given": r"diacritics.*given",
    "diacritics_family": r"diacritics.*family",
    "diacritics": r"diacritics",  # fallback for unqualified diacritics
    "org_case": r"organization|letter casing \(org",
    "title_case": r"letter casing|Title Case",
    "phone_format": r"phone.*normalization|international format",
    "phone_type": r"phone.*type",
    "phone_duplicate": r"duplicate phone",
    "email_normalize": r"email.*normalization|email.*lowercase",
    "email_invalid": r"invalid.*email",
    "email_duplicate": r"duplicate email",
    "address_zip": r"postal code",
    "address_country": r"country",
    "address_parse": r"address.*pars",
    "name_extract": r"name.*extract|inferred.*name",
    "name_split": r"name.*split|split.*name",
    "name_title": r"title.*extract|prefix.*extract",
    "company_in_name": r"company.*name|company_in_name",
    "family_name_fix": r"family.*name|familyName",
    "x500_dn": r"X\.500 DN",
    "org_from_email": r"inferred from email|organization.*email",
    "phone_from_note": r"phone.*(?:found in|from) notes|phone.*extracted from notes",
    "email_from_note": r"email.*(?:found in|from) notes|email.*extracted from notes",
    "url_from_note": r"(?:URL|url|website).*(?:found in|from) notes|URL.*extracted from notes",
    "event_from_note": r"(?:birthday|anniversary|date|event).*(?:found in|from) notes|(?:from|extracted from) notes",
    "owner_email": r"owner email",
    "corporate_url": r"corporate.*(?:LinkedIn|website|directory|social media)",
    "shared_address": r"shared HQ|shared.*office.*address",
    "tobedeleted": r"low-value contact|deletion candidate",
}

# Migration map: old Slovak/stale rule_stats keys → current English category
_RULE_STATS_MIGRATION = {
    # Slovak keys from pre-translation era (prefix-matched)
    "URL nájdené v poznámke": "event_from_note",
    "email nájdený v poznámke": "event_from_note",
    "tel. číslo nájdené v poznámke": "event_from_note",
    "dátum narodenia z poznámky": "event_from_note",
    "meno odhadnuté z emailu": "name_extract",
    "URL extrahovaná z poznámky": "event_from_note",
    "mobilné číslo extrahované z poznámky": "event_from_note",
    # AI-generated Slovak one-off reasons → other
    "oprava typu": "other",
    "neistá oprava": "other",
    "poznámka uvádza": "other",
    "preusporiadanie": "other",
    # Removed category — org casing was never routed here
    "domain_case": "org_case",
}

# Default empty memory structure
_DEFAULT_MEMORY = {
    "version": "1.2",
    "last_updated": None,
    "diacritics_corrections": {},
    "merge_decisions": {},
    "enrichment_patterns": {"domain_to_org": {}},
    "rejected_changes": [],
    "rejected_specifics": {},  # {resourceName: {field: [newValue, ...]}}
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
        self._dirty = False
        self.instructions = self._load_instructions()
        self.memory = self._load_memory()

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

        Returns:
            corrected form if approved > rejected (use this form)
            original name if rejected >= approved (keep as-is, skip correction)
            None if no data exists for this name
        """
        diacritics = self.memory.get("diacritics_corrections", {})
        entry = diacritics.get(name)
        if not entry:
            return None

        approved = entry.get("times_approved", 0)
        rejected = entry.get("times_rejected", 0)

        if approved > rejected:
            return entry.get("corrected")
        # User rejected this correction — return original name to block re-proposal
        return name

    def record_approval(self, change: dict):
        """Record that a change was approved by the user."""
        field = change.get("field", "")
        reason = change.get("reason", "")

        # Track diacritics approvals
        if "diacritics" in reason.lower():
            old_val = change.get("old", "")
            new_val = change.get("new", "")
            if old_val and new_val and old_val != new_val:
                self._record_diacritics(old_val, new_val, approved=True)

        # Track domain-to-org mappings from enrichment
        if "organization" in reason.lower() or "email" in reason.lower():
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
        if "diacritics" in reason.lower():
            old_val = change.get("old", "")
            new_val = change.get("new", "")
            if old_val and new_val:
                self._record_diacritics(old_val, new_val, approved=False)

        # Record per-contact blocklist entry if resourceName is available
        resource_name = change.get("resourceName", "")
        new_val = change.get("new", "")
        if resource_name and field and new_val:
            self._record_rejected_specific(resource_name, field, new_val)

        # Record all rejections for context
        self.memory.setdefault("rejected_changes", []).append({
            "field": field,
            "rejected_value": new_val,
            "kept_value": change.get("old", ""),
            "reason": reason,
            "resourceName": resource_name,
            "date": datetime.now().isoformat(),
        })
        # Keep only last 100 rejections
        self.memory["rejected_changes"] = self.memory["rejected_changes"][-100:]
        self._dirty = True

    def _record_rejected_specific(self, resource_name: str, field: str, new_value: str):
        """Record a specific rejection in the blocklist (capped at 500 contacts)."""
        blocklist = self.memory.setdefault("rejected_specifics", {})
        contact = blocklist.setdefault(resource_name, {})
        values = contact.setdefault(field, [])
        if new_value not in values:
            values.append(new_value)
        # Cap blocklist size: evict oldest contacts if over 500
        if len(blocklist) > 500:
            oldest = next(iter(blocklist))
            del blocklist[oldest]
        self._dirty = True

    def is_rejected_specific(self, resource_name: str, field: str, new_value: str) -> bool:
        """Check if a specific (contact, field, value) triple was previously rejected."""
        blocklist = self.memory.get("rejected_specifics", {})
        contact = blocklist.get(resource_name, {})
        return new_value in contact.get(field, [])

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
          - resourceName: str (optional, needed for per-contact blocklist)
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
                    "resourceName": d.get("resourceName", ""),
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
                # Migrate stale rule_stats keys
                if self._migrate_rule_stats(data):
                    self._dirty = True
                return data
            except (json.JSONDecodeError, IOError) as e:
                # Backup corrupted file before falling back to defaults
                backup_path = MEMORY_PATH.with_suffix(".corrupted.json")
                try:
                    import shutil
                    shutil.copy2(MEMORY_PATH, backup_path)
                    _logger.warning("memory.json corrupted (%s), backed up to %s", e, backup_path)
                except OSError:
                    _logger.error("memory.json corrupted (%s), backup also failed", e)
        return dict(_DEFAULT_MEMORY)

    @staticmethod
    def _migrate_rule_stats(data: dict) -> bool:
        """
        Migrate old/stale rule_stats keys to current English categories.

        Merges counts (approved, rejected, edited) into the target key,
        recalculates adjusted_confidence, and removes the old key.
        Returns True if any migration occurred.
        """
        rule_stats = data.get("rule_stats", {})
        if not rule_stats:
            return False

        migrated = False
        for old_key, new_key in _RULE_STATS_MIGRATION.items():
            # Also match keys that start with the old key (e.g. Slovak variants
            # with parenthesized suffixes like "dátum narodenia z poznámky (birthday)")
            keys_to_migrate = [
                k for k in list(rule_stats.keys())
                if k == old_key or k.startswith(old_key)
            ]
            for k in keys_to_migrate:
                old_stats = rule_stats.pop(k)
                target = rule_stats.setdefault(new_key, {
                    "approved": 0, "rejected": 0, "edited": 0,
                })
                target["approved"] = target.get("approved", 0) + old_stats.get("approved", 0)
                target["rejected"] = target.get("rejected", 0) + old_stats.get("rejected", 0)
                target["edited"] = target.get("edited", 0) + old_stats.get("edited", 0)

                # Recalculate adjusted_confidence
                total = target["approved"] + target["rejected"] + target["edited"]
                if total >= 5:
                    approved = target["approved"] + target["edited"]
                    approval_rate = approved / total
                    base = 0.75
                    adjusted = (base * 10 + approval_rate * total) / (10 + total)
                    target["adjusted_confidence"] = max(0.30, min(0.98, round(adjusted, 3)))

                migrated = True

        # Seed diacritics sub-categories from the generic "diacritics" stats
        # so they inherit the learned confidence until they accumulate own feedback.
        diac = rule_stats.get("diacritics")
        if diac and diac.get("adjusted_confidence"):
            for sub in ("diacritics_given", "diacritics_family"):
                if sub not in rule_stats:
                    rule_stats[sub] = dict(diac)  # copy stats as seed
                    migrated = True

        # Seed note-extraction sub-categories from the old "event_from_note" bucket
        # so they inherit learned confidence until they accumulate own feedback.
        efn = rule_stats.get("event_from_note")
        if efn and efn.get("adjusted_confidence"):
            for sub in ("phone_from_note", "email_from_note", "url_from_note"):
                if sub not in rule_stats:
                    rule_stats[sub] = dict(efn)  # copy stats as seed
                    migrated = True

        return migrated

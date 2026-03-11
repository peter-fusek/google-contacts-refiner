"""
Contact analyzer — runs all normalization and enrichment checks,
aggregates results per contact with confidence scores.
"""
from typing import Optional

from normalizer import (
    normalize_name, normalize_phones, normalize_emails,
    normalize_addresses, normalize_organizations, normalize_urls,
)
from enricher import enrich_contact
from utils import get_display_name, get_resource_name
from config import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM

# Lazy-loaded memory manager for confidence adjustment
_memory = None


def _get_memory():
    global _memory
    if _memory is None:
        try:
            from memory import MemoryManager
            _memory = MemoryManager()
        except Exception:
            pass
    return _memory


def _adjust_confidence(changes: list[dict]) -> list[dict]:
    """Adjust confidence scores based on user feedback history."""
    memory = _get_memory()
    if not memory:
        return changes

    for change in changes:
        category = memory.extract_rule_category(change.get("reason", ""))
        base = change["confidence"]
        adjusted = memory.get_adjusted_confidence(category, base)
        if adjusted != base:
            change["confidence"] = adjusted
    return changes


def analyze_contact(person: dict, ai_analyzer=None) -> dict:
    """
    Analyze a single contact and collect all suggested changes.

    Returns:
        {
            'resourceName': str,
            'displayName': str,
            'changes': [
                {
                    'field': str,
                    'old': str,
                    'new': str,
                    'confidence': float,
                    'reason': str,
                    'extra': dict (optional),
                },
                ...
            ],
            'stats': {
                'high': int,
                'medium': int,
                'low': int,
                'total': int,
            }
        }
    """
    changes = []

    # Run all normalizers
    changes.extend(normalize_name(person))
    changes.extend(normalize_phones(person))
    changes.extend(normalize_emails(person))
    changes.extend(normalize_addresses(person))
    changes.extend(normalize_organizations(person))
    changes.extend(normalize_urls(person))

    # Run enrichment
    changes.extend(enrich_contact(person))

    # Remove changes where old == new (no-ops) or new is empty
    # Allow empty new for: middleName clearing, URL removal, email removal
    changes = [
        c for c in changes
        if c.get("old") != c.get("new") and (
            c.get("new") not in (None, "")
            or "middleName" in c.get("field", "")
            or c.get("field", "").startswith("urls[")
            or c.get("field", "").startswith("emailAddresses[")
        )
    ]

    # Adjust confidence based on user feedback history
    changes = _adjust_confidence(changes)

    # AI enhancement pass (if available and needed)
    if ai_analyzer and ai_analyzer.needs_ai_review(changes):
        try:
            changes = ai_analyzer.enhance_analysis(person, changes)
        except Exception:
            pass  # Fall back to rule-based changes silently

    # Filter out info-only markers
    actionable_changes = [
        c for c in changes
        if c.get("new") not in ("__DUPLICATE__", "__INVALID__")
    ]
    info_changes = [
        c for c in changes
        if c.get("new") in ("__DUPLICATE__", "__INVALID__")
    ]

    # Compute stats
    high = sum(1 for c in actionable_changes if c["confidence"] >= CONFIDENCE_HIGH)
    medium = sum(1 for c in actionable_changes if CONFIDENCE_MEDIUM <= c["confidence"] < CONFIDENCE_HIGH)
    low = sum(1 for c in actionable_changes if c["confidence"] < CONFIDENCE_MEDIUM)

    return {
        "resourceName": get_resource_name(person),
        "displayName": get_display_name(person),
        "etag": person.get("etag", ""),
        "changes": actionable_changes,
        "info": info_changes,
        "stats": {
            "high": high,
            "medium": medium,
            "low": low,
            "total": len(actionable_changes),
        },
    }


def analyze_all_contacts(contacts: list[dict], progress_callback=None, ai_analyzer=None) -> list[dict]:
    """
    Analyze all contacts and return results for those with changes.

    Args:
        contacts: List of person resources from API.
        progress_callback: Called with (done, total).
        ai_analyzer: Optional AIAnalyzer for AI-enhanced analysis.

    Returns:
        List of analysis results (only contacts with changes).
    """
    results = []
    total = len(contacts)

    for i, person in enumerate(contacts):
        analysis = analyze_contact(person, ai_analyzer=ai_analyzer)
        if analysis["changes"] or analysis["info"]:
            results.append(analysis)

        if progress_callback and (i + 1) % 50 == 0:
            progress_callback(i + 1, total)

    if progress_callback:
        progress_callback(total, total)

    return results


def summarize_analysis(results: list[dict]) -> dict:
    """
    Create a summary of analysis results.

    Returns:
        {
            'total_contacts_with_changes': int,
            'total_changes': int,
            'by_confidence': {'high': int, 'medium': int, 'low': int},
            'by_field_type': {'names': int, 'phones': int, 'emails': int, ...},
            'info_items': {'duplicates': int, 'invalid': int},
        }
    """
    summary = {
        "total_contacts_with_changes": len(results),
        "total_changes": 0,
        "by_confidence": {"high": 0, "medium": 0, "low": 0},
        "by_field_type": {
            "names": 0,
            "phones": 0,
            "emails": 0,
            "addresses": 0,
            "organizations": 0,
            "enrichment_notes": 0,
            "enrichment_email": 0,
            "other": 0,
        },
        "info_items": {"duplicates": 0, "invalid": 0},
    }

    for result in results:
        for change in result["changes"]:
            summary["total_changes"] += 1

            # By confidence
            conf = change["confidence"]
            if conf >= CONFIDENCE_HIGH:
                summary["by_confidence"]["high"] += 1
            elif conf >= CONFIDENCE_MEDIUM:
                summary["by_confidence"]["medium"] += 1
            else:
                summary["by_confidence"]["low"] += 1

            # By field type
            field = change["field"]
            if field.startswith("names"):
                summary["by_field_type"]["names"] += 1
            elif field.startswith("phoneNumbers"):
                summary["by_field_type"]["phones"] += 1
            elif field.startswith("emailAddresses"):
                summary["by_field_type"]["emails"] += 1
            elif field.startswith("addresses"):
                summary["by_field_type"]["addresses"] += 1
            elif field.startswith("organizations"):
                summary["by_field_type"]["organizations"] += 1
            elif field.startswith("urls"):
                summary["by_field_type"].setdefault("urls", 0)
                summary["by_field_type"]["urls"] += 1
            elif "note" in change.get("reason", "").lower() or "poznámk" in change.get("reason", "").lower():
                summary["by_field_type"]["enrichment_notes"] += 1
            elif "email" in change.get("reason", "").lower():
                summary["by_field_type"]["enrichment_email"] += 1
            else:
                summary["by_field_type"]["other"] += 1

        for info in result.get("info", []):
            if info.get("new") == "__DUPLICATE__":
                summary["info_items"]["duplicates"] += 1
            elif info.get("new") == "__INVALID__":
                summary["info_items"]["invalid"] += 1

    return summary


def confidence_emoji(confidence: float) -> str:
    """Return emoji for confidence level."""
    if confidence >= CONFIDENCE_HIGH:
        return "🟢"
    elif confidence >= CONFIDENCE_MEDIUM:
        return "🟡"
    else:
        return "🔴"


def format_contact_changes(result: dict, index: int = 0) -> str:
    """
    Format a single contact's changes for display.
    """
    lines = []
    display = result["displayName"]
    rn = result["resourceName"]

    # Build header showing name transformation if applicable
    name_changes = [c for c in result["changes"] if c["field"].startswith("names")]
    if name_changes:
        new_name_parts = {}
        for c in name_changes:
            if "givenName" in c["field"]:
                new_name_parts["given"] = c["new"]
            elif "familyName" in c["field"]:
                new_name_parts["family"] = c["new"]

        if new_name_parts:
            new_display = f"{new_name_parts.get('given', '')} {new_name_parts.get('family', '')}".strip()
            if new_display and new_display != display:
                lines.append(f"[{index}] {display} → {new_display}")
            else:
                lines.append(f"[{index}] {display}")
        else:
            lines.append(f"[{index}] {display}")
    else:
        lines.append(f"[{index}] {display}")

    # List changes
    for change in result["changes"]:
        emoji = confidence_emoji(change["confidence"])
        field_short = change["field"].split(".")[-1] if "." in change["field"] else change["field"]
        if change["old"]:
            lines.append(f"  {emoji} {field_short}: \"{change['old']}\" → \"{change['new']}\" ({change['reason']})")
        else:
            lines.append(f"  {emoji} {field_short}: → \"{change['new']}\" ({change['reason']})")

    # List info items
    for info in result.get("info", []):
        if info["new"] == "__DUPLICATE__":
            lines.append(f"  ℹ️  DUPLICATE: {info['field']} = \"{info['old']}\"")
        elif info["new"] == "__INVALID__":
            lines.append(f"  ⚠️  INVALID: {info['field']} = \"{info['old']}\"")

    return "\n".join(lines)

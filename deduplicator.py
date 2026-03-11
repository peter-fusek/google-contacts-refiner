"""
Duplicate detection for contacts.
Uses fuzzy name matching + exact phone/email matching.
Reports only — never auto-merges.
"""
import re
from rapidfuzz import fuzz
from unidecode import unidecode
from utils import get_display_name, get_resource_name


def _normalize_for_compare(s: str) -> str:
    """Normalize string for comparison: lowercase, strip diacritics, collapse whitespace."""
    if not s:
        return ""
    return " ".join(unidecode(s).lower().split())


def _get_phone_digits(person: dict) -> set[str]:
    """Get set of normalized phone digit strings for a contact."""
    digits = set()
    for phone in person.get("phoneNumbers", []):
        value = phone.get("value", "")
        d = re.sub(r'\D', '', value)
        # Normalize: strip leading country code variants
        if d.startswith("00421"):
            d = "421" + d[5:]
        elif d.startswith("00420"):
            d = "420" + d[5:]
        if len(d) >= 9:
            digits.add(d[-9:])  # Last 9 digits as fingerprint
    return digits


def _get_emails(person: dict) -> set[str]:
    """Get set of lowercase emails for a contact."""
    return {
        e.get("value", "").lower().strip()
        for e in person.get("emailAddresses", [])
        if e.get("value", "").strip()
    }


def find_duplicates(
    contacts: list[dict],
    name_threshold: float = 80.0,
    progress_callback=None,
) -> list[dict]:
    """
    Find potential duplicate contacts.

    Strategy:
    1. Exact match on phone number (last 9 digits) → high confidence
    2. Exact match on email → high confidence
    3. Fuzzy match on name (>threshold) → medium confidence

    Args:
        contacts: List of person resources.
        name_threshold: Minimum fuzzy ratio for name match.
        progress_callback: Called with (done, total).

    Returns:
        List of duplicate groups:
        [
            {
                'contacts': [resourceName1, resourceName2, ...],
                'names': [displayName1, displayName2, ...],
                'match_type': 'phone' | 'email' | 'name',
                'confidence': float,
                'detail': str,
            },
            ...
        ]
    """
    # Build indexes
    phone_index: dict[str, list[int]] = {}  # last9digits → [contact_idx]
    email_index: dict[str, list[int]] = {}  # email → [contact_idx]

    for i, person in enumerate(contacts):
        for digits in _get_phone_digits(person):
            phone_index.setdefault(digits, []).append(i)
        for email in _get_emails(person):
            email_index.setdefault(email, []).append(i)

    # Find duplicate groups
    seen_pairs = set()  # (min_idx, max_idx) to avoid reporting same pair twice
    groups = []

    # ── Phone-based duplicates ────────────────────────────────────
    for digits, indices in phone_index.items():
        if len(indices) > 1:
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    pair = (min(indices[a], indices[b]), max(indices[a], indices[b]))
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        groups.append({
                            "contacts": [
                                get_resource_name(contacts[indices[a]]),
                                get_resource_name(contacts[indices[b]]),
                            ],
                            "names": [
                                get_display_name(contacts[indices[a]]),
                                get_display_name(contacts[indices[b]]),
                            ],
                            "match_type": "phone",
                            "confidence": 0.90,
                            "detail": f"same phone number (…{digits[-4:]})",
                        })

    # ── Email-based duplicates ────────────────────────────────────
    for email, indices in email_index.items():
        if len(indices) > 1:
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    pair = (min(indices[a], indices[b]), max(indices[a], indices[b]))
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        groups.append({
                            "contacts": [
                                get_resource_name(contacts[indices[a]]),
                                get_resource_name(contacts[indices[b]]),
                            ],
                            "names": [
                                get_display_name(contacts[indices[a]]),
                                get_display_name(contacts[indices[b]]),
                            ],
                            "match_type": "email",
                            "confidence": 0.90,
                            "detail": f"same email ({email})",
                        })

    # ── Name-based fuzzy duplicates ───────────────────────────────
    # Only compare contacts that haven't been matched already
    # Use blocking: group by first 3 characters of normalized name
    name_blocks: dict[str, list[int]] = {}
    for i, person in enumerate(contacts):
        name_norm = _normalize_for_compare(get_display_name(person))
        if len(name_norm) >= 3:
            block_key = name_norm[:3]
            name_blocks.setdefault(block_key, []).append(i)

    total_blocks = len(name_blocks)
    done_blocks = 0

    for block_key, indices in name_blocks.items():
        done_blocks += 1
        if progress_callback and done_blocks % 100 == 0:
            progress_callback(done_blocks, total_blocks)

        if len(indices) < 2:
            continue

        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                pair = (min(indices[a], indices[b]), max(indices[a], indices[b]))
                if pair in seen_pairs:
                    continue

                name_a = _normalize_for_compare(get_display_name(contacts[indices[a]]))
                name_b = _normalize_for_compare(get_display_name(contacts[indices[b]]))

                if not name_a or not name_b:
                    continue

                ratio = fuzz.ratio(name_a, name_b)
                if ratio >= name_threshold:
                    seen_pairs.add(pair)
                    groups.append({
                        "contacts": [
                            get_resource_name(contacts[indices[a]]),
                            get_resource_name(contacts[indices[b]]),
                        ],
                        "names": [
                            get_display_name(contacts[indices[a]]),
                            get_display_name(contacts[indices[b]]),
                        ],
                        "match_type": "name",
                        "confidence": ratio / 100.0,
                        "detail": f"similar name (match {ratio:.0f}%)",
                    })

    if progress_callback:
        progress_callback(total_blocks, total_blocks)

    # Sort by confidence descending
    groups.sort(key=lambda g: -g["confidence"])

    return groups


def format_duplicates(groups: list[dict]) -> str:
    """Format duplicate groups for display."""
    if not groups:
        return "✅ No potential duplicates found."

    lines = [
        f"🔍 Found {len(groups)} potential duplicate groups:",
        "",
    ]

    for i, group in enumerate(groups, 1):
        names = " ↔ ".join(group["names"])
        conf_pct = f"{group['confidence'] * 100:.0f}%"
        match_type = group["match_type"]
        detail = group["detail"]

        lines.append(f"  [{i}] {names}")
        lines.append(f"      Type: {match_type} | Confidence: {conf_pct} | {detail}")
        lines.append("")

    lines.append("ℹ️  Duplicates are not merged automatically. Review and merge manually in Google Contacts.")

    return "\n".join(lines)

"""
Helper/utility functions used across modules.
"""
import re
import unicodedata
from typing import Optional
from unidecode import unidecode


def strip_whitespace(s: str) -> str:
    """Remove leading/trailing whitespace and collapse internal whitespace."""
    if not s:
        return s
    return " ".join(s.split())


def normalize_unicode(s: str) -> str:
    """Normalize unicode to NFC form."""
    if not s:
        return s
    return unicodedata.normalize("NFC", s)


def to_ascii(s: str) -> str:
    """Convert string to ASCII (strip diacritics)."""
    if not s:
        return s
    return unidecode(s)


def title_case_sk(s: str) -> str:
    """
    Title case with Slovak/Czech awareness.
    Handles particles like 'von', 'van', 'de' etc.
    """
    if not s:
        return s

    lowercase_particles = {"von", "van", "de", "di", "du", "da", "le", "la", "el", "al"}
    words = s.split()
    result = []
    for i, word in enumerate(words):
        if i > 0 and word.lower() in lowercase_particles:
            result.append(word.lower())
        else:
            # Capitalize first letter, lowercase rest
            if len(word) > 1:
                result.append(word[0].upper() + word[1:].lower())
            elif word:
                result.append(word.upper())
    return " ".join(result)


def is_all_caps(s: str) -> bool:
    """Check if string is ALL CAPS (at least 2 alpha chars)."""
    if not s:
        return False
    alpha_chars = [c for c in s if c.isalpha()]
    return len(alpha_chars) >= 2 and all(c.isupper() for c in alpha_chars)


def is_all_lower(s: str) -> bool:
    """Check if string is all lowercase (at least 2 alpha chars)."""
    if not s:
        return False
    alpha_chars = [c for c in s if c.isalpha()]
    return len(alpha_chars) >= 2 and all(c.islower() for c in alpha_chars)


def extract_emails_from_text(text: str) -> list[str]:
    """Extract email addresses from free text."""
    if not text:
        return []
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return re.findall(pattern, text)


def extract_phones_from_text(text: str) -> list[str]:
    """Extract phone numbers from free text."""
    if not text:
        return []
    # Match various phone formats
    patterns = [
        r'\+\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{3}[\s.-]?\d{2,4}',   # +421 903 123 456
        r'\b0\d{2,3}[\s./-]?\d{3}[\s./-]?\d{3,4}\b',              # 0903 123 456 / 02/1234 5678
        r'\b\d{4}[\s.-]\d{3}[\s.-]\d{3}\b',                        # 0903 123 456
        r'\+\d{10,14}',                                             # +421903123456
        r'\b09\d{8}\b',                                              # 0903123456
    ]
    found = []
    for pat in patterns:
        matches = re.findall(pat, text)
        found.extend(matches)

    # Deduplicate — keep longer matches if overlapping
    unique = []
    for phone in found:
        cleaned = re.sub(r'[\s./-]', '', phone)
        if not any(re.sub(r'[\s./-]', '', u) == cleaned for u in unique):
            unique.append(phone)
    return unique


def extract_urls_from_text(text: str) -> list[str]:
    """Extract URLs from free text."""
    if not text:
        return []
    pattern = r'https?://[^\s<>\"\']+|www\.[^\s<>\"\']+\.[^\s<>\"\']+'
    return re.findall(pattern, text)


def extract_dates_from_text(text: str) -> list[dict]:
    """
    Extract dates from text. Returns list of {'raw': ..., 'parsed': 'YYYY-MM-DD', 'context': ...}
    """
    if not text:
        return []

    results = []

    # Slovak/Czech date patterns
    patterns = [
        # DD.MM.YYYY or DD. MM. YYYY
        (r'(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})', 'dmy'),
        # DD/MM/YYYY
        (r'(\d{1,2})/(\d{1,2})/(\d{4})', 'dmy'),
        # DD-MM-YYYY
        (r'(\d{1,2})-(\d{1,2})-(\d{4})', 'dmy'),
        # YYYY-MM-DD (ISO)
        (r'(\d{4})-(\d{1,2})-(\d{1,2})', 'ymd'),
    ]

    for pattern, fmt in patterns:
        for match in re.finditer(pattern, text):
            raw = match.group(0)
            try:
                if fmt == 'dmy':
                    d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
                elif fmt == 'ymd':
                    y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                else:
                    continue

                if 1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100:
                    parsed = f"{y:04d}-{m:02d}-{d:02d}"

                    # Try to detect context (birthday, nameday, etc.)
                    context_window = text[max(0, match.start() - 40):match.end() + 20].lower()
                    context = "unknown"
                    if any(w in context_window for w in ["narod", "born", "birthday", "nar.", "dátum narod"]):
                        context = "birthday"
                    elif any(w in context_window for w in ["menin", "nameday", "sviatok"]):
                        context = "nameday"
                    elif any(w in context_window for w in ["výroč", "anniversary", "výročie"]):
                        context = "anniversary"

                    results.append({"raw": raw, "parsed": parsed, "context": context})
            except (ValueError, IndexError):
                continue

    return results


def extract_company_from_email(email: str) -> Optional[str]:
    """
    Try to extract company name from email domain.
    Returns None for free email providers.
    """
    from config import FREE_EMAIL_DOMAINS

    if not email or "@" not in email:
        return None

    domain = email.split("@")[1].lower().strip()

    if domain in FREE_EMAIL_DOMAINS:
        return None

    # Strip common TLDs to get company name
    company = domain.split(".")[0]
    if len(company) < 2:
        return None

    return domain


def parse_name_from_email(email: str) -> Optional[tuple[str, str]]:
    """
    Try to parse first/last name from email local part.
    Returns (givenName, familyName) or None.

    Handles: meno.priezvisko@, meno_priezvisko@, menopriezvisko@ (less confident)
    """
    if not email or "@" not in email:
        return None

    local = email.split("@")[0].lower().strip()

    # Remove numbers, dots at start/end
    local = re.sub(r'^\d+', '', local)
    local = re.sub(r'\d+$', '', local)

    # Try separator patterns
    for sep in [".", "_", "-"]:
        if sep in local:
            parts = local.split(sep)
            if len(parts) == 2:
                first, last = parts
                if len(first) >= 2 and len(last) >= 2 and first.isalpha() and last.isalpha():
                    return (first.capitalize(), last.capitalize())

    return None


def get_display_name(person: dict) -> str:
    """Get a display name for a contact (for logging/display)."""
    names = person.get("names", [])
    if names:
        dn = names[0].get("displayName")
        if dn:
            return dn
        given = names[0].get("givenName", "")
        family = names[0].get("familyName", "")
        if given or family:
            return f"{given} {family}".strip()

    # Fallback to email
    emails = person.get("emailAddresses", [])
    if emails:
        return emails[0].get("value", "(bez mena)")

    # Fallback to phone
    phones = person.get("phoneNumbers", [])
    if phones:
        return phones[0].get("value", "(bez mena)")

    return "(bez mena)"


def get_resource_name(person: dict) -> str:
    """Get resourceName from a person dict."""
    return person.get("resourceName", "")


def get_etag(person: dict) -> str:
    """Get etag from a person dict."""
    metadata = person.get("metadata", {})
    return person.get("etag", metadata.get("etag", ""))


def safe_get_nested(d: dict, *keys, default=None):
    """Safely navigate nested dict/list."""
    current = d
    for key in keys:
        try:
            if isinstance(current, list):
                current = current[int(key)]
            elif isinstance(current, dict):
                current = current[key]
            else:
                return default
        except (KeyError, IndexError, TypeError, ValueError):
            return default
    return current

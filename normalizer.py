"""
Normalization of contact fields:
- Names (diacritics, casing, prefix extraction, field splitting)
- Phone numbers (international format with phonenumbers)
- Emails (lowercase, validation)
- Addresses (PSČ formatting, country detection)
- Organizations (title case, unification)
"""
import re
from typing import Optional

import phonenumbers
from email_validator import validate_email, EmailNotValidError
from unidecode import unidecode

from urllib.parse import urlparse

from config import (
    SK_CZ_NAMES_DIACRITICS, SURNAME_SUFFIX_PATTERNS,
    NAME_PREFIXES, DEFAULT_REGION, SUPPORTED_REGIONS,
    FREE_EMAIL_DOMAINS,
)
from utils import (
    title_case_sk, is_all_caps, is_all_lower, strip_whitespace,
    normalize_unicode,
)


# ══════════════════════════════════════════════════════════════════════
# NAME NORMALIZATION
# ══════════════════════════════════════════════════════════════════════

def fix_diacritics(name: str, memory=None) -> tuple[str, float]:
    """
    Fix missing diacritics in Slovak/Czech names.

    Returns:
        (fixed_name, confidence)
        confidence: 1.0 = exact dictionary match, 0.7 = pattern-based
    """
    if not name:
        return name, 0.0

    # Check memory first (learned preferences override dictionary)
    if memory:
        pref = memory.get_diacritics_preference(name)
        if pref and pref != name:
            return pref, 0.97

    # Check exact dictionary match (case-insensitive key lookup)
    # Build a lookup from stripped version
    ascii_name = unidecode(name).strip()

    # Already has diacritics? Check if it matches itself
    if name != ascii_name:
        # Name already has diacritics — probably fine
        return name, 1.0

    # Exact match in dictionary
    for key, value in SK_CZ_NAMES_DIACRITICS.items():
        if unidecode(key).lower() == ascii_name.lower():
            # Preserve original casing pattern
            if name[0].isupper():
                return value, 0.95
            return value, 0.92

    # Pattern-based suffix matching for surnames
    lower = ascii_name.lower()
    for suffix, replacement in sorted(SURNAME_SUFFIX_PATTERNS.items(), key=lambda x: -len(x[0])):
        if lower.endswith(suffix) and len(lower) > len(suffix) + 1:
            base = ascii_name[:-len(suffix)]
            fixed = base + replacement
            # Apply title case
            if ascii_name[0].isupper():
                fixed = fixed[0].upper() + fixed[1:]
            return fixed, 0.65

    return name, 0.0


def extract_prefix(full_name: str) -> tuple[str, str]:
    """
    Extract academic/professional prefix from a name.

    Returns:
        (prefix, remaining_name)
    """
    if not full_name:
        return "", full_name

    prefixes_found = []
    remaining = full_name.strip()

    # Sort prefixes by length (longest first) to avoid partial matches
    sorted_prefixes = sorted(NAME_PREFIXES, key=len, reverse=True)

    for prefix in sorted_prefixes:
        # Check at start of string
        pattern = re.compile(r'^' + re.escape(prefix) + r'[\s,]+', re.IGNORECASE)
        match = pattern.match(remaining)
        if match:
            prefixes_found.append(prefix)
            remaining = remaining[match.end():].strip()
            continue

        # Check with comma separation
        pattern2 = re.compile(r',?\s*' + re.escape(prefix) + r'\.?\s*$', re.IGNORECASE)
        match2 = pattern2.search(remaining)
        if match2:
            prefixes_found.append(prefix)
            remaining = remaining[:match2.start()].strip()

    prefix_str = " ".join(prefixes_found)
    return prefix_str, remaining


def split_name_fields(name_str: str) -> dict:
    """
    Split a full name string into givenName and familyName.
    Handles: "Peter Novák", "Novák Peter", "Ing. Peter Novák PhD."

    Returns:
        dict with 'givenName', 'familyName', 'prefix', 'suffix'
    """
    if not name_str:
        return {}

    result = {"givenName": "", "familyName": "", "prefix": "", "suffix": ""}

    # Extract prefix/suffix first
    prefix, cleaned = extract_prefix(name_str)
    result["prefix"] = prefix

    parts = cleaned.split()
    if len(parts) == 0:
        return result
    elif len(parts) == 1:
        # Single word — assume it's a family name
        result["familyName"] = parts[0]
    elif len(parts) == 2:
        # Two words — givenName familyName (standard Slovak order)
        result["givenName"] = parts[0]
        result["familyName"] = parts[1]
    else:
        # Multiple words — first is given, rest is family
        result["givenName"] = parts[0]
        result["familyName"] = " ".join(parts[1:])

    return result


_COMPANY_LEGAL_FORMS_RE = re.compile(
    r'(s\.?\s*r\.?\s*o\.?|a\.?\s*s\.?|spol\.|k\.?\s*s\.?|gmbh|ltd|inc|corp|ag)\b',
    re.IGNORECASE,
)


def _is_company_or_affiliation(text: str, org_names: set[str]) -> bool:
    """Check if text looks like a company name or organizational affiliation."""
    normalized = text.lower().replace('.', '').replace(',', '').replace(' ', '')
    # LinkedIn junk patterns
    if re.match(r'^\d+\+?\s*(connections?|kontaktov|kontakty)', text, re.IGNORECASE):
        return True
    # Matches an existing org
    for org in org_names:
        org_norm = org.replace('.', '').replace(',', '').replace(' ', '')
        if normalized == org_norm or normalized in org_norm or org_norm in normalized:
            return True
    # Contains a legal form suffix
    if _COMPANY_LEGAL_FORMS_RE.search(text):
        return True
    # Contains common org indicators
    org_indicators = ['bank', 'group', 'universit', 'facult', 'ministerst', 'institut',
                      'consulting', 'solution', 'technolog', 'software', 'school']
    text_lower = text.lower()
    if any(ind in text_lower for ind in org_indicators):
        return True
    return False


def _detect_company_in_name(person: dict) -> Optional[dict]:
    """
    Detect company/org name stuck in name fields and suggest fixes.

    Common patterns:
    D) displayName ends with "(Company)" — strip parenthesized part, re-parse name
    A) familyName in parentheses with company: "(ČPS.a.s.)" — real surname in middleName
    B) familyName is just a legal suffix: "S.r.o.)" — surname in displayName
    C) familyName matches an org name: "Instarea" — surname in displayName

    Returns dict with 'changes', 'given', 'family', 'middle' or None.
    """
    names = person.get("names", [])
    if not names:
        return None

    n = names[0]
    given = n.get("givenName", "")
    family = n.get("familyName", "")
    middle = n.get("middleName", "")
    display = n.get("displayName", "")
    unstructured = n.get("unstructuredName", "")
    orgs = person.get("organizations", [])
    org_names = {o.get("name", "").strip("() ").lower() for o in orgs if o.get("name")}

    changes = []

    # ── Pattern D: displayName/unstructuredName ends with "(Company)" ─
    # e.g. "Bocko Marek (DELL. a.s.)" → strip "(DELL. a.s.)" from name fields
    # Strategy: don't re-parse the name, instead clean company junk from existing fields
    source = unstructured or display
    paren_end = re.search(r'\s*\(([^)]+)\)\s*$', source)
    if paren_end:
        paren_content = paren_end.group(1).strip()
        clean_name = source[:paren_end.start()].strip().rstrip(',')

        # Skip if parenthesized part looks like a maiden name (female surname)
        is_maiden = bool(re.search(
            r'(?i)^(ex\s+)?[A-ZÁ-Ž][a-zá-ž]+(ov[áa]|ín[áa]|sk[áa])$',
            paren_content,
        ))

        if not is_maiden and clean_name and _is_company_or_affiliation(paren_content, org_names):
            # Clean company junk from givenName (e.g. "Karbanová (Galileo" → "Karbanová")
            clean_given = re.sub(r'\s*\(.*$', '', given).strip() if given else ""
            # Clean company junk from familyName (e.g. "A.s.)" → extract real surname)
            clean_family = family
            family_has_junk = bool(re.search(r'[()]', family)) or _COMPANY_LEGAL_FORMS_RE.fullmatch(
                re.sub(r'^[(\s]+|[)\s]+$', '', family)
            ) if family else False
            # Clean company junk from middleName (e.g. "Marek (DELL." → "Marek")
            clean_middle = re.sub(r'\s*\(.*$', '', middle).strip() if middle else ""

            if family_has_junk:
                # familyName is broken — real surname is in middleName or clean_name
                if clean_middle and clean_middle != clean_given:
                    clean_family = clean_middle
                    clean_middle = ""
                else:
                    # Extract surname from clean_name
                    parts = clean_name.split()
                    if len(parts) >= 2:
                        clean_family = parts[-1]
                    elif parts:
                        clean_family = parts[0]
                        clean_given = ""

            # Emit changes for fields that differ
            new_given = clean_given
            new_family = clean_family
            new_middle = clean_middle if clean_middle != new_family else ""

            if new_given != given:
                changes.append({
                    "field": "names[0].givenName",
                    "old": given,
                    "new": new_given,
                    "confidence": 0.90,
                    "reason": "company_in_name: given name cleaned after removing company (%s)" % paren_content,
                })
            if new_family != family:
                changes.append({
                    "field": "names[0].familyName",
                    "old": family,
                    "new": new_family,
                    "confidence": 0.90,
                    "reason": "company_in_name: family name cleaned after removing company (%s)" % paren_content,
                })
            if new_middle != middle:
                changes.append({
                    "field": "names[0].middleName",
                    "old": middle,
                    "new": new_middle,
                    "confidence": 0.90,
                    "reason": "company_in_name: cleared middleName (contained company name)",
                })
            # Clear unstructuredName
            if unstructured and re.search(r'\s*\([^)]+\)\s*$', unstructured):
                changes.append({
                    "field": "names[0].unstructuredName",
                    "old": unstructured,
                    "new": clean_name,
                    "confidence": 0.90,
                    "reason": "company_in_name: removed company from name (%s)" % paren_content,
                })
            if changes:
                return {"changes": changes, "given": new_given, "family": new_family, "middle": new_middle}

    # ── Pattern A: familyName in parentheses ──────────────────────
    parens_match = re.match(r'^\((.+)\)$', family.strip())
    if parens_match:
        parens_content = parens_match.group(1).strip()
        is_company = (
            parens_content.lower() in org_names
            or _COMPANY_LEGAL_FORMS_RE.search(parens_content)
        )
        # If not clearly a company, check if it looks like a maiden name
        # Maiden names: Slovak/Czech female surnames ending in -ová, -ova, -á
        is_maiden_name = bool(re.search(
            r'(?i)(ov[áa]|[áa]|ín[áa]|sk[áa])$', parens_content
        ))
        if not is_company and not is_maiden_name:
            # Doesn't look like a surname — likely a company/affiliation
            is_company = True
        if is_company and middle:
            # middleName is the real surname — move to familyName, keep middleName
            changes.append({
                "field": "names[0].familyName",
                "old": family,
                "new": middle,
                "confidence": 0.90,
                "reason": "surname was in middleName, familyName contained company (%s)" % parens_content,
            })
            # Add company to organizations if not already there
            if parens_content.lower() not in org_names:
                changes.append({
                    "field": "organizations[+].name",
                    "old": "",
                    "new": parens_content,
                    "confidence": 0.90,
                    "reason": "company from name (%s) added to organizations" % parens_content,
                })
            return {"changes": changes, "given": given, "family": middle, "middle": middle}

    # ── Pattern B: familyName is just a legal form suffix ─────────
    # e.g. "S.r.o.)", "A.s.)" — the company name was split across fields
    cleaned_family = family.strip()
    stripped_family = re.sub(r'^[(\s]+|[)\s]+$', '', cleaned_family)
    if stripped_family and re.fullmatch(
        r'(?i)s\.?\s*r\.?\s*o\.?|a\.?\s*s\.?|spol\.?|k\.?\s*s\.?|gmbh|ltd\.?|inc\.?|corp\.?|ag\.?',
        stripped_family,
    ):
        # Extract real surname from displayName
        real_surname = _extract_surname_from_display(display, given)
        if real_surname:
            changes.append({
                "field": "names[0].familyName",
                "old": family,
                "new": real_surname,
                "confidence": 0.92,
                "reason": "familyName contained only legal form suffix, surname from displayName",
            })
            # Extract full company name from displayName and add to orgs
            company_name = _extract_company_from_display(display)
            if company_name and company_name.lower() not in org_names:
                changes.append({
                    "field": "organizations[+].name",
                    "old": "",
                    "new": company_name,
                    "confidence": 0.92,
                    "reason": "company from name (%s) added to organizations" % company_name,
                })
            return {"changes": changes, "given": given, "family": real_surname, "middle": middle}

    # ── Pattern C: familyName matches an org name ─────────────────
    if family and family.strip().lower() in org_names:
        # Check if displayName has a pipe/dash separator suggesting "Name | Company"
        real_surname = _extract_surname_from_display(display, given)
        if real_surname and real_surname.lower() != family.lower():
            changes.append({
                "field": "names[0].familyName",
                "old": family,
                "new": real_surname,
                "confidence": 0.85,
                "reason": "familyName contained company name (%s), surname from displayName" % family,
            })
            return {"changes": changes, "given": given, "family": real_surname, "middle": middle}

    return None


def _extract_company_from_display(display: str) -> Optional[str]:
    """Extract company name from displayName parentheses, e.g. 'Peter (Acme s.r.o.)' → 'Acme s.r.o.'"""
    match = re.search(r'\(([^)]+)\)', display)
    if match:
        return match.group(1).strip()
    return None


def _extract_surname_from_display(display: str, given: str) -> Optional[str]:
    """Extract the real surname from displayName, removing company parts."""
    if not display:
        return None

    # Remove parenthesized parts: "Peter Marhoffer (ČPS.a.s.)" → "Peter Marhoffer"
    cleaned = re.sub(r'\s*\(.*?\)', '', display).strip()
    # Remove pipe-separated parts: "Jan Zelinka | Instarea" → "Jan Zelinka"
    cleaned = re.split(r'\s*[|]\s*', cleaned)[0].strip()

    # Remove the given name to get the surname
    if given:
        # Handle multi-word given names
        for g in given.split():
            cleaned = re.sub(r'(?i)^' + re.escape(g) + r'\s+', '', cleaned).strip()

    # Remove any title prefixes
    _, cleaned = extract_prefix(cleaned)

    parts = cleaned.split()
    if parts:
        return parts[-1] if len(parts) == 1 else " ".join(parts)

    return None


def normalize_name(person: dict) -> list[dict]:
    """
    Analyze and suggest name normalizations for a contact.

    Returns list of changes: [{'field': ..., 'old': ..., 'new': ..., 'confidence': ..., 'reason': ...}]
    """
    changes = []
    names = person.get("names", [])
    if not names:
        return changes

    name_data = names[0]
    given = name_data.get("givenName", "")
    family = name_data.get("familyName", "")
    middle = name_data.get("middleName", "")
    display = name_data.get("displayName", "")
    prefix = name_data.get("honorificPrefix", "")
    suffix = name_data.get("honorificSuffix", "")

    # ── Parse X.500 Distinguished Name format ─────────────────────
    # e.g. "CN=Dasa MATUSIKOVA/O=RIDB/C=AT"
    dn_source = display or given or family
    if dn_source and re.match(r'^[A-Z]{1,3}=', dn_source):
        dn_parts = {}
        for segment in re.split(r'[/,]', dn_source):
            if '=' in segment:
                key, val = segment.split('=', 1)
                dn_parts[key.strip().upper()] = val.strip()
        cn = dn_parts.get("CN", "")
        if cn:
            parsed = split_name_fields(cn)
            new_given = parsed.get("givenName", "")
            new_family = parsed.get("familyName", "")
            if new_given and new_given != given:
                changes.append({
                    "field": "names[0].givenName",
                    "old": given,
                    "new": title_case_sk(new_given),
                    "confidence": 0.95,
                    "reason": "given name extracted from X.500 DN format",
                })
            if new_family and new_family != family:
                changes.append({
                    "field": "names[0].familyName",
                    "old": family,
                    "new": title_case_sk(new_family),
                    "confidence": 0.95,
                    "reason": "family name extracted from X.500 DN format",
                })
            # Add org from O= if present and not already in organizations
            dn_org = dn_parts.get("O", "")
            if dn_org:
                existing_orgs = {
                    o.get("name", "").lower()
                    for o in person.get("organizations", [])
                }
                if dn_org.lower() not in existing_orgs:
                    changes.append({
                        "field": "organizations[+].name",
                        "old": "",
                        "new": dn_org,
                        "confidence": 0.90,
                        "reason": "organization extracted from X.500 DN format",
                    })
            given = title_case_sk(new_given) if new_given else given
            family = title_case_sk(new_family) if new_family else family

    # ── Fix company name stuck in name fields ─────────────────────
    company_fix = _detect_company_in_name(person)
    if company_fix:
        changes.extend(company_fix["changes"])
        given = company_fix.get("given", given)
        family = company_fix.get("family", family)
        middle = company_fix.get("middle", middle)

    # ── If name is only in one field or display name ──────────────
    if not given and not family and display:
        parsed = split_name_fields(display)
        if parsed.get("givenName"):
            changes.append({
                "field": "names[0].givenName",
                "old": "",
                "new": parsed["givenName"],
                "confidence": 0.85,
                "reason": "givenName extracted from displayName",
            })
        if parsed.get("familyName"):
            changes.append({
                "field": "names[0].familyName",
                "old": "",
                "new": parsed["familyName"],
                "confidence": 0.85,
                "reason": "familyName extracted from displayName",
            })
        if parsed.get("prefix") and not prefix:
            changes.append({
                "field": "names[0].honorificPrefix",
                "old": "",
                "new": parsed["prefix"],
                "confidence": 0.90,
                "reason": "title/prefix extracted from displayName",
            })
        given = parsed.get("givenName", "")
        family = parsed.get("familyName", "")

    # If full name in familyName only
    if not given and family and " " in family:
        parsed = split_name_fields(family)
        if parsed.get("givenName"):
            changes.append({
                "field": "names[0].givenName",
                "old": "",
                "new": parsed["givenName"],
                "confidence": 0.80,
                "reason": "split given name from familyName",
            })
            changes.append({
                "field": "names[0].familyName",
                "old": family,
                "new": parsed["familyName"],
                "confidence": 0.80,
                "reason": "split family name from familyName",
            })
            given = parsed["givenName"]
            family = parsed["familyName"]

    # If full name in givenName only
    if given and not family and " " in given:
        parsed = split_name_fields(given)
        if parsed.get("familyName"):
            changes.append({
                "field": "names[0].givenName",
                "old": given,
                "new": parsed["givenName"],
                "confidence": 0.80,
                "reason": "split given name from givenName",
            })
            changes.append({
                "field": "names[0].familyName",
                "old": "",
                "new": parsed["familyName"],
                "confidence": 0.80,
                "reason": "split family name from givenName",
            })
            given = parsed["givenName"]
            family = parsed["familyName"]

    # ── Extract prefix from name if not separate ──────────────────
    if not prefix:
        full = f"{given} {family}".strip()
        extracted_prefix, remaining = extract_prefix(full)
        if extracted_prefix:
            changes.append({
                "field": "names[0].honorificPrefix",
                "old": "",
                "new": extracted_prefix,
                "confidence": 0.90,
                "reason": f"title/prefix '{extracted_prefix}' extracted from name",
            })
            # Re-parse remaining
            parsed = split_name_fields(remaining)
            if given and parsed.get("givenName") != given:
                changes.append({
                    "field": "names[0].givenName",
                    "old": given,
                    "new": parsed.get("givenName", given),
                    "confidence": 0.85,
                    "reason": "given name adjusted after title extraction",
                })
                given = parsed.get("givenName", given)
            if family and parsed.get("familyName") != family:
                changes.append({
                    "field": "names[0].familyName",
                    "old": family,
                    "new": parsed.get("familyName", family),
                    "confidence": 0.85,
                    "reason": "family name adjusted after title extraction",
                })
                family = parsed.get("familyName", family)

    # ── Fix casing ────────────────────────────────────────────────
    if given and (is_all_caps(given) or is_all_lower(given)):
        fixed = title_case_sk(given)
        if fixed != given:
            changes.append({
                "field": "names[0].givenName",
                "old": given,
                "new": fixed,
                "confidence": 0.95,
                "reason": "fix letter casing (Title Case)",
            })
            given = fixed

    if family and (is_all_caps(family) or is_all_lower(family)):
        fixed = title_case_sk(family)
        if fixed != family:
            changes.append({
                "field": "names[0].familyName",
                "old": family,
                "new": fixed,
                "confidence": 0.95,
                "reason": "fix letter casing (Title Case)",
            })
            family = fixed

    # ── Fix diacritics ────────────────────────────────────────────
    # Lazy-load memory for learned diacritics preferences
    try:
        from memory import MemoryManager
        _diac_memory = MemoryManager()
    except Exception:
        _diac_memory = None

    if given:
        fixed, conf = fix_diacritics(given, memory=_diac_memory)
        if fixed != given and conf > 0.0:
            changes.append({
                "field": "names[0].givenName",
                "old": given,
                "new": fixed,
                "confidence": conf,
                "reason": "diacritics restoration (given name)",
            })

    if family:
        fixed, conf = fix_diacritics(family, memory=_diac_memory)
        if fixed != family and conf > 0.0:
            changes.append({
                "field": "names[0].familyName",
                "old": family,
                "new": fixed,
                "confidence": conf,
                "reason": "diacritics restoration (family name)",
            })

    return changes


# ══════════════════════════════════════════════════════════════════════
# PHONE NORMALIZATION
# ══════════════════════════════════════════════════════════════════════

def normalize_phone(phone_str: str, region: str = DEFAULT_REGION) -> tuple[str, float, str]:
    """
    Normalize a phone number to international format.

    Args:
        phone_str: Raw phone number string.
        region: Default region hint (SK, CZ).

    Returns:
        (normalized, confidence, phone_type)
        phone_type: 'mobile', 'fixed_line', 'unknown'
    """
    if not phone_str:
        return phone_str, 0.0, "unknown"

    # Clean up
    cleaned = phone_str.strip()

    # Try parsing with phonenumbers
    for try_region in [region] + [r for r in SUPPORTED_REGIONS if r != region]:
        try:
            parsed = phonenumbers.parse(cleaned, try_region)
            if phonenumbers.is_valid_number(parsed):
                # Format with spaces
                formatted = phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
                )

                # Determine type
                num_type = phonenumbers.number_type(parsed)
                if num_type == phonenumbers.PhoneNumberType.MOBILE:
                    ptype = "mobile"
                elif num_type == phonenumbers.PhoneNumberType.FIXED_LINE:
                    ptype = "fixed_line"
                elif num_type == phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE:
                    ptype = "mobile"  # In SK/CZ context, usually mobile
                else:
                    ptype = "unknown"

                return formatted, 0.95, ptype
        except phonenumbers.NumberParseException:
            continue

    # Could not parse — return original
    return phone_str, 0.0, "unknown"


def normalize_phones(person: dict) -> list[dict]:
    """
    Analyze and suggest phone normalization for a contact.

    Returns list of changes.
    """
    changes = []
    phones = person.get("phoneNumbers", [])
    if not phones:
        return changes

    seen_numbers = set()  # Track normalized numbers to detect duplicates

    for i, phone in enumerate(phones):
        value = phone.get("value", "")
        ptype = phone.get("type", "")

        normalized, confidence, detected_type = normalize_phone(value)

        if normalized != value and confidence > 0:
            changes.append({
                "field": f"phoneNumbers[{i}].value",
                "old": value,
                "new": normalized,
                "confidence": confidence,
                "reason": "phone number normalization to international format",
            })

        # Suggest type if missing
        if not ptype and detected_type != "unknown":
            changes.append({
                "field": f"phoneNumbers[{i}].type",
                "old": "",
                "new": detected_type,
                "confidence": 0.80,
                "reason": f"phone type detection ({detected_type})",
            })

        # Track for duplicate detection
        norm_digits = re.sub(r'\D', '', normalized)
        if norm_digits in seen_numbers:
            changes.append({
                "field": f"phoneNumbers[{i}]",
                "old": value,
                "new": "__DUPLICATE__",
                "confidence": 0.90,
                "reason": "duplicate phone number within contact",
            })
        seen_numbers.add(norm_digits)

    return changes


# ══════════════════════════════════════════════════════════════════════
# EMAIL NORMALIZATION
# ══════════════════════════════════════════════════════════════════════

def normalize_email_address(email_str: str) -> tuple[str, float, bool]:
    """
    Normalize an email address.

    Returns:
        (normalized, confidence, is_valid)
    """
    if not email_str:
        return email_str, 0.0, False

    # Strip whitespace and invisible chars
    cleaned = email_str.strip()
    cleaned = re.sub(r'[\u200b\u200c\u200d\ufeff\xa0]', '', cleaned)

    # Lowercase
    lowered = cleaned.lower()

    # Validate
    try:
        result = validate_email(lowered, check_deliverability=False)
        normalized = result.normalized
        return normalized, 0.95, True
    except EmailNotValidError:
        return lowered, 0.50, False


def normalize_emails(person: dict) -> list[dict]:
    """
    Analyze and suggest email normalization for a contact.
    """
    changes = []
    emails = person.get("emailAddresses", [])
    if not emails:
        return changes

    from config import OWNER_EMAILS

    seen_emails = set()

    for i, email in enumerate(emails):
        value = email.get("value", "")
        normalized, confidence, is_valid = normalize_email_address(value)

        if normalized != value and confidence > 0:
            changes.append({
                "field": f"emailAddresses[{i}].value",
                "old": value,
                "new": normalized,
                "confidence": confidence,
                "reason": "email normalization (lowercase, trim)",
            })

        if not is_valid:
            changes.append({
                "field": f"emailAddresses[{i}]",
                "old": value,
                "new": "__INVALID__",
                "confidence": 0.70,
                "reason": "invalid email format",
            })

        # Duplicate check
        if normalized.lower() in seen_emails:
            changes.append({
                "field": f"emailAddresses[{i}]",
                "old": value,
                "new": "__DUPLICATE__",
                "confidence": 0.90,
                "reason": "duplicate email within contact",
            })
        seen_emails.add(normalized.lower())

    # Owner email check — flag owner's email on non-owner contacts
    # A contact is considered "owner's own card" if ALL its emails are owner emails
    all_normalized = [e.get("value", "").lower().strip() for e in emails]
    non_owner_emails = [e for e in all_normalized if e and e not in OWNER_EMAILS]
    is_owner_card = len(non_owner_emails) == 0 and len(all_normalized) > 0

    if not is_owner_card:
        for i, email in enumerate(emails):
            value = email.get("value", "").lower().strip()
            if value in OWNER_EMAILS:
                changes.append({
                    "field": f"emailAddresses[{i}]",
                    "old": email.get("value", ""),
                    "new": "",
                    "confidence": 0.95,
                    "reason": f"owner email ({value}) incorrectly on non-owner contact",
                })

    return changes


# ══════════════════════════════════════════════════════════════════════
# ADDRESS NORMALIZATION
# ══════════════════════════════════════════════════════════════════════

def normalize_psc(psc_str: str) -> tuple[str, str]:
    """
    Normalize postal code and detect country.

    Returns:
        (formatted_psc, detected_country)
    """
    digits = re.sub(r'\D', '', psc_str)
    if len(digits) != 5:
        return psc_str, ""

    formatted = f"{digits[:3]} {digits[3:]}"

    # Detect country from PSČ
    first_digit = int(digits[0])
    if first_digit in (0, 8, 9):
        country = "SK"
    elif 1 <= first_digit <= 7:
        country = "CZ"
    else:
        country = ""

    return formatted, country


def normalize_addresses(person: dict) -> list[dict]:
    """
    Analyze and suggest address normalization for a contact.
    """
    changes = []
    addresses = person.get("addresses", [])
    if not addresses:
        return changes

    for i, addr in enumerate(addresses):
        postal = addr.get("postalCode", "")
        country = addr.get("country", "")
        country_code = addr.get("countryCode", "")
        street = addr.get("streetAddress", "")
        city = addr.get("city", "")

        # Normalize PSČ
        if postal:
            formatted, detected = normalize_psc(postal)
            if formatted != postal:
                changes.append({
                    "field": f"addresses[{i}].postalCode",
                    "old": postal,
                    "new": formatted,
                    "confidence": 0.95,
                    "reason": "postal code formatting (XXX XX)",
                })

            # Add country if missing — but only if address has other useful fields
            # (street or city). Country-only enrichment adds noise with little value.
            if detected and not country and not country_code and (street or city):
                country_name = "Slovensko" if detected == "SK" else "Česko"
                changes.append({
                    "field": f"addresses[{i}].country",
                    "old": "",
                    "new": country_name,
                    "confidence": 0.75,
                    "reason": f"country inferred from postal code ({detected})",
                })
                changes.append({
                    "field": f"addresses[{i}].countryCode",
                    "old": "",
                    "new": detected,
                    "confidence": 0.75,
                    "reason": "country code inferred from postal code",
                })

        # Try to parse unstructured address
        formatted_value = addr.get("formattedValue", "")

        if formatted_value and not street and not city:
            parsed = _try_parse_address(formatted_value)
            if parsed:
                for field_name, field_value in parsed.items():
                    if field_value and not addr.get(field_name):
                        changes.append({
                            "field": f"addresses[{i}].{field_name}",
                            "old": "",
                            "new": field_value,
                            "confidence": 0.60,
                            "reason": "address parsed from formattedValue",
                        })

    return changes


def _try_parse_address(addr_str: str) -> Optional[dict]:
    """
    Try to parse a Slovak/Czech address string into components.
    Very basic — handles common patterns.
    """
    result = {}

    # Try to find PSČ
    psc_match = re.search(r'\b(\d{3})\s*(\d{2})\b', addr_str)
    if not psc_match:
        psc_match = re.search(r'\b(\d{5})\b', addr_str)

    if psc_match:
        raw_psc = psc_match.group(0)
        formatted_psc, country = normalize_psc(raw_psc)
        result["postalCode"] = formatted_psc
        if country:
            result["countryCode"] = country
            result["country"] = "Slovensko" if country == "SK" else "Česko"

    return result if result else None


# ══════════════════════════════════════════════════════════════════════
# ORGANIZATION NORMALIZATION
# ══════════════════════════════════════════════════════════════════════

def normalize_organizations(person: dict) -> list[dict]:
    """
    Analyze and suggest organization normalization for a contact.
    """
    changes = []
    orgs = person.get("organizations", [])
    if not orgs:
        return changes

    for i, org in enumerate(orgs):
        name = org.get("name", "")
        title = org.get("title", "")

        # Fix casing for company name — skip domain names (e.g. swan.sk, gfk.com)
        if name and (is_all_caps(name) or is_all_lower(name)):
            if not re.match(r'^[a-z0-9.-]+\.[a-z]{2,}$', name, re.IGNORECASE):
                fixed = _title_case_company(name)
                if fixed != name:
                    changes.append({
                        "field": f"organizations[{i}].name",
                        "old": name,
                        "new": fixed,
                        "confidence": 0.70,
                        "reason": "fix letter casing (organization)",
                    })

        # Fix casing for title/position
        if title and (is_all_caps(title) or is_all_lower(title)):
            fixed = _title_case_title(title)
            if fixed != title:
                changes.append({
                    "field": f"organizations[{i}].title",
                    "old": title,
                    "new": fixed,
                    "confidence": 0.70,
                    "reason": "fix letter casing (job title)",
                })

    return changes


def _title_case_company(name: str) -> str:
    """
    Title case for company names, preserving legal forms and acronyms.
    """
    # Legal form abbreviations to preserve
    legal_forms = {
        "s.r.o.": "s.r.o.", "s. r. o.": "s.r.o.",
        "a.s.": "a.s.", "a. s.": "a.s.",
        "sro": "s.r.o.", "spol.": "spol.",
        "k.s.": "k.s.", "v.o.s.": "v.o.s.",
        "z.s.": "z.s.", "o.z.": "o.z.",
        "n.o.": "n.o.",
    }

    # Known company/industry acronyms to keep uppercase
    KNOWN_ACRONYMS = {
        "IBM", "SAP", "HP", "GE", "ABB", "DHL", "BMW", "VW", "UPC",
        "CSOB", "SLSP", "VUB", "OTP", "ING", "PPF", "ESET",
        "IT", "EU", "SK", "CZ", "USA", "UK", "NATO", "FIFA",
        "CEO", "CFO", "CTO", "COO", "CMO", "CIO",
    }

    words = name.split()
    result_words = []
    for i, word in enumerate(words):
        # Strip non-alpha to check against acronyms
        alpha = re.sub(r'[^a-zA-Z]', '', word)
        if alpha.upper() in KNOWN_ACRONYMS:
            # Preserve as uppercase, keep non-alpha chars in place
            result_words.append(word.upper())
        elif len(alpha) <= 2 and alpha.isalpha():
            # Very short words (AG, SE, AB) — likely acronyms
            result_words.append(word.upper())
        else:
            result_words.append(
                word[0].upper() + word[1:].lower() if len(word) > 1 else word.upper()
            )

    result = " ".join(result_words)

    # Restore legal form abbreviations
    lower_result = result.lower()
    for form_lower, form_proper in legal_forms.items():
        if form_lower in lower_result:
            idx = lower_result.find(form_lower)
            if idx >= 0:
                result = result[:idx] + form_proper + result[idx + len(form_lower):]

    return result


# Common job title acronyms that should stay uppercase
_JOB_TITLE_ACRONYMS = {
    "CEO", "CFO", "CTO", "COO", "CMO", "CIO", "CSO", "CPO", "CDO", "CISO",
    "VP", "SVP", "EVP", "AVP",
    "HR", "IT", "PR", "QA", "PM", "BA",
    "MBA", "CPA", "CFA",
}


# ══════════════════════════════════════════════════════════════════════
# URL NORMALIZATION — corporate URL detection
# ══════════════════════════════════════════════════════════════════════

# LinkedIn company page patterns (vs personal /in/ profiles)
_LINKEDIN_COMPANY_PATHS = ("/company/", "/school/", "/showcase/")

# Social media domains where we distinguish personal vs corporate pages
_SOCIAL_DOMAINS = {
    "linkedin.com", "www.linkedin.com",
    "facebook.com", "www.facebook.com", "fb.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "instagram.com", "www.instagram.com",
    "youtube.com", "www.youtube.com",
}

# Business directory / corporate-only domains (never personal)
_CORPORATE_ONLY_DOMAINS = {
    "glassdoor.com", "www.glassdoor.com",
    "crunchbase.com", "www.crunchbase.com",
    "zoominfo.com", "www.zoominfo.com",
    "dnb.com", "www.dnb.com",
    "bloomberg.com", "www.bloomberg.com",
}


def _normalize_domain(domain: str) -> str:
    """Strip www. prefix for comparison."""
    return domain.lower().removeprefix("www.")


def _org_matches_domain(org_names: set[str], domain: str) -> bool:
    """Check if any org name matches the URL domain."""
    # e.g. org "Instarea s.r.o." should match domain "instarea.sk"
    domain_base = _normalize_domain(domain).split(".")[0]
    if len(domain_base) < 3:
        return False
    for org in org_names:
        org_clean = re.sub(r'\b(s\.?r\.?o\.?|a\.?s\.?|spol\.?|ltd\.?|inc\.?|gmbh|corp\.?)\b', '', org,
                           flags=re.IGNORECASE).strip().strip("., ")
        if org_clean.lower() == domain_base:
            return True
        # Also check if domain base is contained in multi-word org name
        org_words = [w.lower() for w in org_clean.split() if len(w) > 2]
        if domain_base in org_words:
            return True
    return False


def normalize_urls(person: dict) -> list[dict]:
    """
    Detect and flag corporate URLs on personal contacts for removal.

    Corporate URL types:
    - LinkedIn company/school/showcase pages
    - Company websites matching org name
    - Business directory listings (Glassdoor, Crunchbase, etc.)
    - Corporate social media pages

    Personal URLs are kept:
    - LinkedIn /in/ profiles
    - Personal websites not matching any org
    """
    changes = []
    urls = person.get("urls", [])
    if not urls:
        return changes

    # Build set of org names for matching
    org_names = {
        o.get("name", "").strip()
        for o in person.get("organizations", [])
        if o.get("name", "").strip()
    }

    for i, url_entry in enumerate(urls):
        value = url_entry.get("value", "").strip()
        if not value:
            continue

        # Ensure URL has a scheme for parsing
        parse_url = value if "://" in value else f"https://{value}"
        try:
            parsed = urlparse(parse_url)
        except Exception:
            continue

        domain = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower().rstrip("/")

        if not domain:
            continue

        # ── LinkedIn: company page vs personal profile ──
        if _normalize_domain(domain) == "linkedin.com":
            # Personal profile — keep
            if path.startswith("/in/"):
                continue
            # Company/school/showcase page — remove
            if any(path.startswith(p) for p in _LINKEDIN_COMPANY_PATHS):
                changes.append({
                    "field": f"urls[{i}]",
                    "old": value,
                    "new": "",
                    "confidence": 0.95,
                    "reason": "corporate LinkedIn page (not a personal profile)",
                })
                continue
            # Ambiguous LinkedIn URL (no /in/ or /company/) — skip
            continue

        # ── Corporate-only domains (Glassdoor, Crunchbase, etc.) ──
        if _normalize_domain(domain) in {_normalize_domain(d) for d in _CORPORATE_ONLY_DOMAINS}:
            changes.append({
                "field": f"urls[{i}]",
                "old": value,
                "new": "",
                "confidence": 0.90,
                "reason": f"corporate directory listing ({_normalize_domain(domain)})",
            })
            continue

        # ── Social media: check if it's a company page ──
        if _normalize_domain(domain) in {_normalize_domain(d) for d in _SOCIAL_DOMAINS}:
            # If the URL path matches an org name, it's likely a corporate page
            path_slug = path.strip("/").split("/")[0] if path.strip("/") else ""
            if path_slug and _org_matches_domain(org_names, path_slug + ".com"):
                changes.append({
                    "field": f"urls[{i}]",
                    "old": value,
                    "new": "",
                    "confidence": 0.80,
                    "reason": "corporate social media page (matches organization name)",
                })
                continue
            # Otherwise keep — could be personal social profile
            continue

        # ── Company website: domain matches an org name ──
        if org_names and _org_matches_domain(org_names, domain):
            # Check if it's a generic homepage (no deep path or just /)
            is_homepage = not path or path == "/"
            if is_homepage:
                changes.append({
                    "field": f"urls[{i}]",
                    "old": value,
                    "new": "",
                    "confidence": 0.85,
                    "reason": "corporate website homepage (matches organization)",
                })
            else:
                # Deep link on company site — could be a personal profile page
                changes.append({
                    "field": f"urls[{i}]",
                    "old": value,
                    "new": "",
                    "confidence": 0.65,
                    "reason": "corporate website link (matches organization)",
                })
            continue

    return changes


# ══════════════════════════════════════════════════════════════════════
# SHARED ADDRESS DETECTION (cross-contact)
# ══════════════════════════════════════════════════════════════════════

def _normalize_address_key(addr: dict) -> str:
    """Build a normalized key from address components for dedup comparison."""
    parts = []
    for field in ("streetAddress", "city", "postalCode"):
        val = addr.get(field, "").strip().lower()
        if val:
            parts.append(re.sub(r'\s+', ' ', val))
    # Fallback to formattedValue if no structured fields
    if not parts:
        fv = addr.get("formattedValue", "").strip().lower()
        if fv:
            parts.append(re.sub(r'\s+', ' ', fv))
    return "|".join(parts) if parts else ""


def build_shared_address_index(contacts: list[dict], min_count: int = 3) -> dict[str, int]:
    """
    Scan all contacts and find addresses shared by multiple contacts.

    Returns:
        {normalized_address_key: contact_count} for addresses with >= min_count contacts
    """
    from collections import Counter
    address_counts: Counter = Counter()

    for person in contacts:
        seen_keys = set()  # Avoid counting same contact twice for same address
        for addr in person.get("addresses", []):
            key = _normalize_address_key(addr)
            if key and key not in seen_keys:
                address_counts[key] += 1
                seen_keys.add(key)

    return {k: v for k, v in address_counts.items() if v >= min_count}


def detect_shared_addresses(person: dict, shared_index: dict[str, int]) -> list[dict]:
    """
    Flag addresses that appear on many contacts (likely HQ/office addresses).

    Args:
        person: Contact person dict.
        shared_index: {normalized_address_key: count} from build_shared_address_index.

    Returns:
        List of change dicts flagging shared addresses for removal.
    """
    if not shared_index:
        return []

    changes = []
    addresses = person.get("addresses", [])

    for i, addr in enumerate(addresses):
        key = _normalize_address_key(addr)
        if not key:
            continue

        count = shared_index.get(key)
        if count is None:
            continue

        # Build a readable address for the reason string
        display_addr = (
            addr.get("formattedValue")
            or f"{addr.get('streetAddress', '')}, {addr.get('city', '')}".strip(", ")
        )

        # Higher confidence for addresses shared by more contacts
        if count >= 10:
            confidence = 0.90
        elif count >= 5:
            confidence = 0.80
        else:  # 3-4
            confidence = 0.70

        changes.append({
            "field": f"addresses[{i}]",
            "old": display_addr,
            "new": "",
            "confidence": confidence,
            "reason": f"shared HQ/office address (found on {count} contacts)",
        })

    return changes


def _title_case_title(title: str) -> str:
    """Title case for job titles, preserving common acronyms."""
    words = title.split()
    result = []
    for word in words:
        alpha = re.sub(r'[^a-zA-Z]', '', word)
        if alpha.upper() in _JOB_TITLE_ACRONYMS:
            result.append(word.upper())
        else:
            result.append(
                word[0].upper() + word[1:].lower() if len(word) > 1 else word.upper()
            )
    return " ".join(result)

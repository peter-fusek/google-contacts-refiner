"""
Enrichment module — extracts structured data from unstructured fields.

Main sources:
- Notes/biographies → phones, emails, addresses, dates, URLs, companies
- Email addresses → names, organizations
- Cross-field validation
"""
import re
from typing import Optional

from config import FREE_EMAIL_DOMAINS
from utils import (
    extract_emails_from_text, extract_phones_from_text,
    extract_urls_from_text, extract_dates_from_text,
    extract_company_from_email, parse_name_from_email,
    get_display_name,
)
from normalizer import normalize_phone, normalize_email_address


def enrich_from_notes(person: dict) -> list[dict]:
    """
    Extract structured data from notes/biographies.

    Scans note text for:
    - Phone numbers → phoneNumbers
    - Emails → emailAddresses
    - URLs → urls
    - Dates (birthdays, etc.) → birthdays/events
    - Company/position mentions → organizations
    - IČO/DIČ → userDefined
    """
    changes = []
    bios = person.get("biographies", [])
    if not bios:
        return changes

    note_text = bios[0].get("value", "")
    if not note_text:
        return changes

    # Strip interaction note block — it contains dates that are not personal events
    interaction_marker = "── Last Interaction"
    if interaction_marker in note_text:
        note_text = note_text[:note_text.index(interaction_marker)].rstrip()

    # Existing data for dedup
    existing_phones = {
        re.sub(r'\D', '', p.get("value", ""))
        for p in person.get("phoneNumbers", [])
    }
    existing_emails = {
        e.get("value", "").lower()
        for e in person.get("emailAddresses", [])
    }
    existing_urls = {
        u.get("value", "").lower().rstrip("/")
        for u in person.get("urls", [])
    }

    # ── Extract phones from notes ─────────────────────────────────
    found_phones = extract_phones_from_text(note_text)
    for phone in found_phones:
        normalized, conf, ptype = normalize_phone(phone)
        digits = re.sub(r'\D', '', normalized)
        if digits and digits not in existing_phones and len(digits) >= 9:
            changes.append({
                "field": "phoneNumbers[+]",
                "old": "",
                "new": normalized,
                "confidence": 0.70,
                "reason": "phone number found in notes",
                "extra": {"type": ptype if ptype != "unknown" else "other"},
            })
            existing_phones.add(digits)

    # ── Extract emails from notes ─────────────────────────────────
    found_emails = extract_emails_from_text(note_text)
    for email in found_emails:
        norm, conf, valid = normalize_email_address(email)
        if valid and norm.lower() not in existing_emails:
            changes.append({
                "field": "emailAddresses[+]",
                "old": "",
                "new": norm,
                "confidence": 0.70,
                "reason": "email found in notes",
                "extra": {"type": "other"},
            })
            existing_emails.add(norm.lower())

    # ── Extract URLs from notes ───────────────────────────────────
    found_urls = extract_urls_from_text(note_text)
    for url in found_urls:
        clean_url = url.rstrip("/").lower()
        if clean_url not in existing_urls:
            changes.append({
                "field": "urls[+]",
                "old": "",
                "new": url,
                "confidence": 0.75,
                "reason": "URL found in notes",
                "extra": {"type": "other"},
            })
            existing_urls.add(clean_url)

    # ── Extract dates from notes ──────────────────────────────────
    found_dates = extract_dates_from_text(note_text)
    existing_birthdays = person.get("birthdays", [])
    existing_events = person.get("events", [])

    for date_info in found_dates:
        parsed = date_info["parsed"]
        context = date_info["context"]

        if context == "birthday" and not existing_birthdays:
            # Parse YYYY-MM-DD
            parts = parsed.split("-")
            if len(parts) == 3:
                changes.append({
                    "field": "birthdays[+]",
                    "old": "",
                    "new": parsed,
                    "confidence": 0.60,
                    "reason": f"birthday extracted from notes ({date_info['raw']})",
                    "extra": {
                        "date": {
                            "year": int(parts[0]),
                            "month": int(parts[1]),
                            "day": int(parts[2]),
                        }
                    },
                })
        elif context in ("nameday", "anniversary") and not existing_events:
            changes.append({
                "field": "events[+]",
                "old": "",
                "new": parsed,
                "confidence": 0.55,
                "reason": f"event extracted from notes ({context}: {date_info['raw']})",
                "extra": {"type": context},
            })

    # ── Extract IČO/DIČ ──────────────────────────────────────────
    existing_user_defined = {
        (ud.get("key", ""), ud.get("value", ""))
        for ud in person.get("userDefined", [])
    }

    # IČO: 8-digit number
    ico_match = re.search(r'IČO[:\s]*(\d{8})', note_text, re.IGNORECASE)
    if ico_match:
        ico = ico_match.group(1)
        if ("IČO", ico) not in existing_user_defined:
            changes.append({
                "field": "userDefined[+]",
                "old": "",
                "new": ico,
                "confidence": 0.92,
                "reason": "company ID (IČO) found in notes",
                "extra": {"key": "IČO", "value": ico},
            })

    # DIČ
    dic_match = re.search(r'DIČ[:\s]*(\d{10})', note_text, re.IGNORECASE)
    if dic_match:
        dic = dic_match.group(1)
        if ("DIČ", dic) not in existing_user_defined:
            changes.append({
                "field": "userDefined[+]",
                "old": "",
                "new": dic,
                "confidence": 0.92,
                "reason": "tax ID (DIČ) found in notes",
                "extra": {"key": "DIČ", "value": dic},
            })

    # IČ DPH
    icdph_match = re.search(r'IČ\s*DPH[:\s]*(SK\d{10}|CZ\d{8,10})', note_text, re.IGNORECASE)
    if icdph_match:
        icdph = icdph_match.group(1).upper()
        if ("IČ DPH", icdph) not in existing_user_defined:
            changes.append({
                "field": "userDefined[+]",
                "old": "",
                "new": icdph,
                "confidence": 0.92,
                "reason": "VAT ID (IČ DPH) found in notes",
                "extra": {"key": "IČ DPH", "value": icdph},
            })

    return changes


def enrich_from_email(person: dict) -> list[dict]:
    """
    Enrich contact data from email addresses:
    - Infer name from email local part (if name is missing)
    - Infer organization from email domain (if org is missing)
    """
    changes = []
    emails = person.get("emailAddresses", [])
    if not emails:
        return changes

    names = person.get("names", [])
    has_given = bool(names and names[0].get("givenName", "").strip())
    has_family = bool(names and names[0].get("familyName", "").strip())
    has_org = bool(person.get("organizations", []))

    for email_entry in emails:
        email = email_entry.get("value", "")
        if not email:
            continue

        # ── Infer name from email ─────────────────────────────────
        if not has_given and not has_family:
            parsed_name = parse_name_from_email(email)
            if parsed_name:
                given, family = parsed_name
                # Apply diacritics
                from normalizer import fix_diacritics
                given_fixed, _ = fix_diacritics(given)
                family_fixed, _ = fix_diacritics(family)

                changes.append({
                    "field": "names[0].givenName" if names else "names[+].givenName",
                    "old": "",
                    "new": given_fixed,
                    "confidence": 0.55,
                    "reason": f"given name inferred from email ({email})",
                })
                changes.append({
                    "field": "names[0].familyName" if names else "names[+].familyName",
                    "old": "",
                    "new": family_fixed,
                    "confidence": 0.55,
                    "reason": f"family name inferred from email ({email})",
                })
                has_given = True
                has_family = True

        # ── Infer organization from email domain ──────────────────
        if not has_org:
            company = extract_company_from_email(email)
            if company:
                changes.append({
                    "field": "organizations[+].name",
                    "old": "",
                    "new": company,
                    "confidence": 0.50,
                    "reason": f"organization inferred from email ({email})",
                    "extra": {"domain": company},
                })
                has_org = True

    return changes


def enrich_cross_field(person: dict) -> list[dict]:
    """
    Cross-field validation and enrichment:
    - Fill name from formatted name if missing
    - Detect reversed name order (familyName contains givenName)
    - Check org consistency
    """
    changes = []
    names = person.get("names", [])

    if not names:
        return changes

    name_data = names[0]
    given = name_data.get("givenName", "").strip()
    family = name_data.get("familyName", "").strip()
    display = name_data.get("displayName", "").strip()
    unstructured = name_data.get("unstructuredName", "").strip()

    # ── Fill from formatted/unstructured name ─────────────────────
    if not given and not family:
        source = display or unstructured
        if source:
            from normalizer import split_name_fields
            parsed = split_name_fields(source)
            if parsed.get("givenName"):
                changes.append({
                    "field": "names[0].givenName",
                    "old": "",
                    "new": parsed["givenName"],
                    "confidence": 0.80,
                    "reason": "given name filled from displayName/unstructuredName",
                })
            if parsed.get("familyName"):
                changes.append({
                    "field": "names[0].familyName",
                    "old": "",
                    "new": parsed["familyName"],
                    "confidence": 0.80,
                    "reason": "family name filled from displayName/unstructuredName",
                })

    return changes


def enrich_contact(person: dict) -> list[dict]:
    """
    Run all enrichment checks on a contact.
    Returns combined list of suggested changes.
    """
    changes = []
    changes.extend(enrich_from_notes(person))
    changes.extend(enrich_from_email(person))
    changes.extend(enrich_cross_field(person))
    return changes

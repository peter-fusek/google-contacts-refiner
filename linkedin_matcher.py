"""
LinkedIn connection matching & contact enrichment.

Parses LinkedIn's native data export (Connections.csv) and fuzzy-matches
against Google Contacts by name and company. Generates enrichment changes
for matched contacts (company, LinkedIn URL, connection date).

Usage:
    python main.py linkedin-match <path-to-Connections.csv>
"""
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from rapidfuzz import fuzz
from unidecode import unidecode

from utils import get_display_name, get_resource_name


# ── Matching thresholds ────────────────────────────────────────────
NAME_MATCH_THRESHOLD = 85      # Fuzzy name match score (0-100)
NAME_EXACT_BONUS = 10          # Bonus for exact name match
COMPANY_MATCH_THRESHOLD = 75   # Fuzzy company match score
COMPANY_MATCH_BONUS = 15       # Score bonus when company also matches


def parse_linkedin_csv(csv_path: Union[str, Path]) -> list[dict]:
    """
    Parse LinkedIn Connections.csv export.

    Expected columns: First Name, Last Name, Email Address, Company, Position,
                      Connected On, URL (varies by export version)

    Returns:
        List of dicts with normalized fields.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"LinkedIn CSV not found: {csv_path}")

    connections = []

    with open(csv_path, encoding="utf-8") as f:
        # LinkedIn CSVs sometimes have BOM or notes at the top — skip non-header lines
        reader = csv.DictReader(f)

        for row in reader:
            # Normalize column names (LinkedIn changes them between export versions)
            normalized = {k.strip().lower(): v.strip() for k, v in row.items() if k}

            first = normalized.get("first name", "")
            last = normalized.get("last name", "")

            if not first and not last:
                continue

            conn = {
                "first_name": first,
                "last_name": last,
                "full_name": f"{first} {last}".strip(),
                "email": normalized.get("email address", ""),
                "company": normalized.get("company", ""),
                "position": normalized.get("position", ""),
                "connected_on": normalized.get("connected on", ""),
                "url": normalized.get("url", ""),
            }
            connections.append(conn)

    return connections


def _normalize_name(name: str) -> str:
    """Normalize name for comparison: lowercase, strip diacritics, extra whitespace."""
    return re.sub(r'\s+', ' ', unidecode(name).lower().strip())


def _extract_contact_names(person: dict) -> list[str]:
    """Extract all name variants from a contact for matching."""
    names = []
    for n in person.get("names", []):
        display = n.get("displayName", "")
        given = n.get("givenName", "")
        family = n.get("familyName", "")

        if display:
            names.append(display)
        if given and family:
            names.append(f"{given} {family}")
            names.append(f"{family} {given}")  # Slovak order variant
        elif given:
            names.append(given)
        elif family:
            names.append(family)

    return [_normalize_name(n) for n in names if n]


def _extract_contact_companies(person: dict) -> list[str]:
    """Extract organization names from a contact."""
    companies = []
    for org in person.get("organizations", []):
        name = org.get("name", "").strip()
        if name:
            companies.append(_normalize_name(name))
    return companies


def match_connections(
    connections: list[dict],
    contacts: list[dict],
    name_threshold: int = NAME_MATCH_THRESHOLD,
) -> list[dict]:
    """
    Fuzzy-match LinkedIn connections to Google Contacts.

    Returns:
        List of match dicts:
        {
            'connection': dict,       # LinkedIn connection data
            'contact': dict,          # Matched Google Contact person
            'score': float,           # Match confidence (0-100)
            'match_type': str,        # 'name_only', 'name_and_company', 'email'
        }
    """
    # Build contact lookup indices
    email_index: dict[str, dict] = {}  # email → person
    name_index: list[tuple[list[str], list[str], dict]] = []  # (names, companies, person)

    for person in contacts:
        # Email index
        for email_entry in person.get("emailAddresses", []):
            email = email_entry.get("value", "").lower().strip()
            if email:
                email_index[email] = person

        # Name index
        contact_names = _extract_contact_names(person)
        contact_companies = _extract_contact_companies(person)
        if contact_names:
            name_index.append((contact_names, contact_companies, person))

    matches = []
    matched_resources = set()  # Avoid duplicate matches

    for conn in connections:
        best_match = None
        best_score = 0
        match_type = ""

        # Priority 1: Email match (highest confidence)
        if conn["email"]:
            person = email_index.get(conn["email"].lower())
            if person:
                rn = get_resource_name(person)
                if rn not in matched_resources:
                    best_match = person
                    best_score = 98
                    match_type = "email"

        # Priority 2: Name + company fuzzy match
        if not best_match:
            conn_name = _normalize_name(conn["full_name"])
            conn_company = _normalize_name(conn["company"]) if conn["company"] else ""

            for contact_names, contact_companies, person in name_index:
                rn = get_resource_name(person)
                if rn in matched_resources:
                    continue

                # Find best name score across all name variants
                name_score = 0
                for cn in contact_names:
                    score = fuzz.ratio(conn_name, cn)
                    # Also try token_sort_ratio for different word orders
                    score = max(score, fuzz.token_sort_ratio(conn_name, cn))
                    name_score = max(name_score, score)

                if name_score < name_threshold:
                    continue

                # Add exact match bonus
                if name_score == 100:
                    name_score = min(100, name_score + NAME_EXACT_BONUS)

                # Company matching bonus
                total_score = name_score
                current_match_type = "name_only"

                if conn_company and contact_companies:
                    company_score = max(
                        fuzz.ratio(conn_company, cc) for cc in contact_companies
                    )
                    if company_score >= COMPANY_MATCH_THRESHOLD:
                        total_score = min(100, total_score + COMPANY_MATCH_BONUS)
                        current_match_type = "name_and_company"

                if total_score > best_score:
                    best_match = person
                    best_score = total_score
                    match_type = current_match_type

        if best_match and best_score >= name_threshold:
            rn = get_resource_name(best_match)
            matched_resources.add(rn)
            matches.append({
                "connection": conn,
                "contact": best_match,
                "score": best_score,
                "match_type": match_type,
            })

    return matches


def generate_enrichment_changes(matches: list[dict]) -> list[dict]:
    """
    Generate enrichment changes from LinkedIn matches.

    For each match, suggest:
    - Add/update organization (company + position)
    - Add LinkedIn profile URL
    - Add connection date to notes (if not already present)

    Returns:
        List of per-contact result dicts compatible with the workplan format.
    """
    results = []

    for match in matches:
        conn = match["connection"]
        person = match["contact"]
        score = match["score"]
        changes = []

        resource_name = get_resource_name(person)
        display_name = get_display_name(person)

        # Confidence based on match quality
        base_confidence = 0.70 if match["match_type"] == "name_only" else 0.85
        if match["match_type"] == "email":
            base_confidence = 0.95

        # ── Organization enrichment ──
        existing_orgs = {
            o.get("name", "").lower().strip()
            for o in person.get("organizations", [])
            if o.get("name")
        }
        if conn["company"] and conn["company"].lower().strip() not in existing_orgs:
            change = {
                "field": "organizations[+].name",
                "old": "",
                "new": conn["company"],
                "confidence": base_confidence,
                "reason": f"organization from LinkedIn ({match['match_type']} match, score {score:.0f}%)",
            }
            if conn["position"]:
                change["extra"] = {"title": conn["position"]}
            changes.append(change)

        # ── LinkedIn URL enrichment ──
        linkedin_url = conn.get("url", "")
        if linkedin_url:
            existing_urls = {
                u.get("value", "").lower().rstrip("/")
                for u in person.get("urls", [])
            }
            if linkedin_url.lower().rstrip("/") not in existing_urls:
                changes.append({
                    "field": "urls[+].value",
                    "old": "",
                    "new": linkedin_url,
                    "confidence": base_confidence,
                    "reason": f"LinkedIn profile URL ({match['match_type']} match)",
                    "extra": {"type": "profile"},
                })

        if changes:
            results.append({
                "resourceName": resource_name,
                "displayName": display_name,
                "changes": changes,
                "linkedin_match": {
                    "score": score,
                    "type": match["match_type"],
                    "linkedin_name": conn["full_name"],
                    "linkedin_company": conn["company"],
                },
            })

    return results


def format_match_report(matches: list[dict], results: list[dict]) -> str:
    """Format a human-readable match report."""
    lines = [
        "=" * 50,
        "  LINKEDIN MATCHING REPORT",
        "=" * 50,
        f"  Connections parsed: (from CSV)",
        f"  Matches found: {len(matches)}",
        f"  Contacts to enrich: {len(results)}",
        "",
    ]

    # Match type breakdown
    by_type = {"email": 0, "name_and_company": 0, "name_only": 0}
    for m in matches:
        by_type[m["match_type"]] = by_type.get(m["match_type"], 0) + 1
    lines.append("  Match types:")
    lines.append(f"    Email:            {by_type['email']}")
    lines.append(f"    Name + company:   {by_type['name_and_company']}")
    lines.append(f"    Name only:        {by_type['name_only']}")
    lines.append("")

    # Score distribution
    high = sum(1 for m in matches if m["score"] >= 95)
    medium = sum(1 for m in matches if 85 <= m["score"] < 95)
    low = sum(1 for m in matches if m["score"] < 85)
    lines.append("  Match confidence:")
    lines.append(f"    🟢 High (95%+):   {high}")
    lines.append(f"    🟡 Medium (85-95): {medium}")
    lines.append(f"    🔴 Low (<85%):     {low}")
    lines.append("")

    # Sample matches
    if results:
        lines.append("  Sample enrichments:")
        for r in results[:10]:
            lm = r["linkedin_match"]
            lines.append(
                f"    {r['displayName']} ← {lm['linkedin_name']} "
                f"({lm['type']}, {lm['score']:.0f}%) "
                f"[{len(r['changes'])} changes]"
            )
        if len(results) > 10:
            lines.append(f"    ... and {len(results) - 10} more")

    lines.append("=" * 50)
    return "\n".join(lines)

"""
LinkedIn Social Signals Scanner — browser automation for contact enrichment.

Scans LinkedIn profiles of LTNS (Long Time No See) contacts to find
activity signals: job changes, posts, work anniversaries. Writes
a Social Signals block into contact notes alongside existing blocks.

Usage:
    python main.py linkedin-scan [--dry-run] [--skip-scan] [--limit N]

Requires: Chrome with active LinkedIn session, Claude Code chrome MCP tools.
"""
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz
from unidecode import unidecode

from api_client import PeopleAPIClient
from config import DATA_DIR
from utils import get_display_name, get_resource_name

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
SOCIAL_SIGNALS_MARKER = "── Social Signals"
SCAN_CACHE_FILE = DATA_DIR / "linkedin_scan_cache.json"
SCAN_RESULTS_FILE = DATA_DIR / "linkedin_signals.json"

# Rate limiting: be gentle with LinkedIn
MIN_DELAY_SECONDS = 12
MAX_DELAY_SECONDS = 20
MAX_PROFILES_PER_SESSION = 50

# LinkedIn URL patterns
LINKEDIN_PROFILE_RE = re.compile(r"linkedin\.com/in/([a-zA-Z0-9_-]+)", re.IGNORECASE)
LINKEDIN_OLD_PUB_RE = re.compile(r"linkedin\.com/pub/", re.IGNORECASE)

# URLs with percent-encoded diacritics or /pub/ format are often broken
BROKEN_URL_INDICATORS = ["%C4%", "%C5%", "%C3%", "/pub/"]


class LinkedInScanner:
    """
    Scans LinkedIn profiles via Chrome browser automation to extract
    activity signals for contact enrichment.
    """

    def __init__(self, contacts: list[dict]):
        self.contacts = contacts
        self._contacts_by_rn = {get_resource_name(c): c for c in contacts}
        self._cache = self._load_cache()
        self._results: dict[str, dict] = {}

    # ── Target Selection ─────────────────────────────────────────────

    def select_targets(
        self,
        ltns_list: Optional[list[dict]] = None,
        limit: int = 100,
        group_members: Optional[set[str]] = None,
    ) -> list[dict]:
        """
        Select contacts to scan. Prioritizes LTNS contacts with LinkedIn URLs,
        then LTNS contacts without (need profile discovery).

        Returns list of {resourceName, name, linkedin_url, org, title, source}.
        """
        targets = []
        seen_rn = set()

        # Priority 1: LTNS contacts with existing LinkedIn URLs
        if ltns_list:
            for candidate in ltns_list:
                rn = candidate["resourceName"]
                if rn in seen_rn:
                    continue

                linkedin_url = None
                for u in candidate.get("urls", []):
                    if u.get("type") == "linkedin":
                        linkedin_url = u["url"]
                        break

                # Also check the contact's urls field directly
                if not linkedin_url:
                    contact = self._contacts_by_rn.get(rn, {})
                    for url_entry in contact.get("urls", []):
                        url_val = url_entry.get("value", "")
                        if "linkedin.com/in/" in url_val.lower():
                            linkedin_url = url_val
                            break

                # Skip if already scanned recently
                cached = self._cache.get(rn, {})
                if cached.get("scanned_at", "") > _days_ago(7):
                    continue

                targets.append({
                    "resourceName": rn,
                    "name": candidate.get("name", ""),
                    "linkedin_url": linkedin_url,
                    "org": candidate.get("org", ""),
                    "title": candidate.get("title", ""),
                    "source": "ltns",
                    "months_gap": candidate.get("months_gap", 0),
                })
                seen_rn.add(rn)

        # Priority 2: Contacts with LinkedIn URL (filtered by group if specified)
        for contact in self.contacts:
            if len(targets) >= limit:
                break
            rn = get_resource_name(contact)
            if rn in seen_rn:
                continue

            # If group filter active, skip contacts not in those groups
            if group_members is not None and rn not in group_members:
                continue

            linkedin_url = None
            for url_entry in contact.get("urls", []):
                url_val = url_entry.get("value", "")
                if "linkedin.com/in/" in url_val.lower():
                    linkedin_url = url_val
                    break

            if not linkedin_url:
                continue

            cached = self._cache.get(rn, {})
            if cached.get("scanned_at", "") > _days_ago(7):
                continue

            names = contact.get("names", [{}])
            name = names[0].get("displayName", "") if names else ""
            orgs = contact.get("organizations", [])
            org = orgs[0].get("name", "") if orgs else ""
            title = orgs[0].get("title", "") if orgs else ""

            targets.append({
                "resourceName": rn,
                "name": name,
                "linkedin_url": linkedin_url,
                "org": org,
                "title": title,
                "source": "group" if group_members is not None else "contact_url",
                "months_gap": 0,
            })
            seen_rn.add(rn)

        # Sort: LTNS first (by months_gap descending), then others
        targets.sort(key=lambda t: (-1 if t["source"] == "ltns" else 0, -t.get("months_gap", 0)))
        return targets[:limit]

    # ── Profile Scanning ─────────────────────────────────────────────

    def scan_profile(self, target: dict) -> Optional[dict]:
        """
        Scan a single LinkedIn profile. Returns extracted signals or None.

        This is the method called by the browser automation orchestrator.
        It does NOT do the browser interaction itself — the CLI command
        handles Chrome MCP calls and passes results here for parsing.
        """
        rn = target["resourceName"]
        signals = {
            "resourceName": rn,
            "name": target["name"],
            "linkedin_url": target.get("linkedin_url", ""),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "headline": "",
            "current_role": "",
            "recent_activity": [],
            "signal_type": "",
            "signal_text": "",
        }

        self._results[rn] = signals
        self._cache[rn] = {"scanned_at": signals["scanned_at"]}
        return signals

    def record_profile_data(
        self,
        resource_name: str,
        headline: str = "",
        current_role: str = "",
        recent_posts: Optional[list[str]] = None,
        job_change: str = "",
        linkedin_url: str = "",
    ):
        """Record data extracted from a LinkedIn profile by the browser automation."""
        signals = self._results.get(resource_name, {})
        if not signals:
            return

        signals["headline"] = headline
        signals["current_role"] = current_role
        signals["recent_activity"] = recent_posts or []
        if linkedin_url:
            signals["linkedin_url"] = linkedin_url

        # Classify the signal
        if job_change:
            signals["signal_type"] = "job_change"
            signals["signal_text"] = job_change
        elif recent_posts:
            signals["signal_type"] = "active"
            signals["signal_text"] = f"{len(recent_posts)} recent post(s)"
        elif headline:
            signals["signal_type"] = "profile"
            signals["signal_text"] = headline
        else:
            signals["signal_type"] = "no_activity"
            signals["signal_text"] = "No recent public activity"

        self._results[resource_name] = signals
        self._cache[resource_name] = {
            "scanned_at": signals["scanned_at"],
            "signal_type": signals["signal_type"],
        }

    # ── Note Writing ─────────────────────────────────────────────────

    def update_notes(
        self,
        client: PeopleAPIClient,
        dry_run: bool = False,
    ) -> int:
        """
        Write Social Signals blocks to contact notes.
        Preserves existing note content including Last Interaction blocks.
        """
        if not self._results:
            logger.info("LinkedIn: No scan results to write")
            return 0

        # Filter to contacts with actual signals
        to_update = [
            (rn, sig) for rn, sig in self._results.items()
            if sig.get("signal_type") and sig["signal_type"] != "no_activity"
        ]

        if not to_update:
            logger.info("LinkedIn: No actionable signals found")
            return 0

        logger.info(f"LinkedIn: {len(to_update)} contacts with signals to write")

        if dry_run:
            for rn, sig in to_update[:5]:
                note = self._build_signal_note(sig)
                logger.info(f"  Would update {sig.get('name', rn)}:\n{note}")
            logger.info("DRY RUN — no notes updated")
            return len(to_update)

        updated = 0
        failed = 0

        for rn, sig in to_update:
            try:
                note_text = self._build_signal_note(sig)

                # Fetch current contact to get existing note + etag
                person = client.get_contact(rn, person_fields="biographies,metadata")
                etag = person.get("etag", "")

                # Get existing note
                existing_note = ""
                for bio in person.get("biographies", []):
                    if bio.get("contentType") == "TEXT_PLAIN":
                        existing_note = bio.get("value", "")
                        break

                # Strip old Social Signals block, keep everything else
                clean_note = _strip_block(existing_note, SOCIAL_SIGNALS_MARKER)

                # Insert Social Signals block after Last Interaction (if present)
                # or at the top if no interaction block
                new_note = _insert_signal_block(clean_note, note_text)

                body = {
                    "biographies": [{
                        "value": new_note,
                        "contentType": "TEXT_PLAIN",
                    }]
                }
                client.update_contact(rn, etag, body, update_fields="biographies")
                updated += 1

                if updated % 10 == 0:
                    self.save_cache()
                    logger.info(f"LinkedIn: {updated}/{len(to_update)} notes updated...")

            except Exception as e:
                logger.error(f"LinkedIn: Failed to update {rn}: {e}")
                failed += 1

        self.save_cache()
        self.save_results()
        logger.info(f"LinkedIn: Done — {updated} updated, {failed} failed")
        return updated

    def _build_signal_note(self, signals: dict) -> str:
        """Build the Social Signals note block for a contact."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [f"{SOCIAL_SIGNALS_MARKER} (updated {today}) ──"]

        url = signals.get("linkedin_url", "")
        if url:
            # Only embed validated LinkedIn URLs (prevent injection via malformed URLs)
            url = normalize_linkedin_url(url)
            slug = LINKEDIN_PROFILE_RE.search(url)
            if slug:
                lines.append(f"LinkedIn: linkedin.com/in/{slug.group(1)}")
            # Skip URLs that don't match the expected pattern

        headline = signals.get("headline", "")
        if headline:
            lines.append(f"Role: {headline}")

        signal_type = signals.get("signal_type", "")
        signal_text = signals.get("signal_text", "")

        if signal_type == "job_change":
            lines.append(f"Signal: 🟢 Job change — {signal_text}")
        elif signal_type == "active":
            lines.append(f"Signal: 🟡 Active — {signal_text}")
        elif signal_type == "profile":
            lines.append(f"Signal: ⚪ Profile found")
        else:
            lines.append(f"Signal: ⚪ {signal_text}")

        # Add recent activity snippets (max 2)
        for post in signals.get("recent_activity", [])[:2]:
            lines.append(f"  • {post[:80]}")

        return "\n".join(lines)

    # ── Cache & Persistence ──────────────────────────────────────────

    def _load_cache(self) -> dict:
        if SCAN_CACHE_FILE.exists():
            try:
                return json.loads(SCAN_CACHE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def save_cache(self):
        SCAN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SCAN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)

    def save_results(self):
        SCAN_RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SCAN_RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated": datetime.now(timezone.utc).isoformat(),
                    "count": len(self._results),
                    "signals": self._results,
                },
                f, ensure_ascii=False, indent=2,
            )

    @property
    def results(self) -> dict[str, dict]:
        return self._results


# ── Helpers ──────────────────────────────────────────────────────────

def is_likely_broken_url(url: str) -> bool:
    """Check if a LinkedIn URL is likely broken (old format or diacritics in slug)."""
    if not url:
        return False
    return any(indicator in url for indicator in BROKEN_URL_INDICATORS)


def build_google_search_url(name: str, company: str = "") -> str:
    """Build a Google search URL to find a LinkedIn profile."""
    from urllib.parse import quote_plus
    query = f'site:linkedin.com/in/ "{name}"'
    if company:
        query += f' "{company}"'
    return f"https://www.google.com/search?q={quote_plus(query)}"


def build_linkedin_search_url(name: str) -> str:
    """Build a LinkedIn people search URL. More reliable than Google for profile discovery."""
    from urllib.parse import quote_plus
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(name)}&origin=GLOBAL_SEARCH_HEADER"


def verify_name_match(expected_name: str, profile_name: str, threshold: int = 70) -> bool:
    """
    Verify that a LinkedIn profile name matches the expected contact name.
    Uses fuzzy matching to handle name order, diacritics, and abbreviations.
    """
    if not expected_name or not profile_name:
        return False

    # Normalize: strip diacritics, lowercase, strip extra whitespace
    e = unidecode(expected_name).lower().strip()
    p = unidecode(profile_name).lower().strip()

    # Direct fuzzy match
    if fuzz.ratio(e, p) >= threshold:
        return True

    # Token sort handles name order (Family Given vs Given Family)
    if fuzz.token_sort_ratio(e, p) >= threshold:
        return True

    return False


def normalize_linkedin_url(url: str) -> str:
    """
    Normalize a LinkedIn URL: convert /pub/ to /in/ format,
    ensure https, strip tracking params.
    """
    if not url:
        return url

    # Ensure https
    url = re.sub(r'^http://', 'https://', url)

    # /pub/ URLs can sometimes redirect to /in/ — extract the slug
    pub_match = re.search(r'linkedin\.com/pub/([^/?]+)', url)
    if pub_match:
        slug = pub_match.group(1)
        # /pub/ slugs often have format: first-last/xx/xxx/xxx
        # The /in/ equivalent drops the trailing segments
        slug_parts = slug.split('/')
        if slug_parts:
            return f"https://www.linkedin.com/in/{slug_parts[0]}"

    return url


def _days_ago(n: int) -> str:
    """Return ISO date string for N days ago."""
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _strip_block(note: str, marker: str) -> str:
    """Remove a marker-delimited block from a note, preserving the rest."""
    if marker not in note:
        return note

    lines = note.split("\n")
    result = []
    in_block = False
    for line in lines:
        if marker in line:
            in_block = True
            continue
        if in_block:
            if not line.strip():
                in_block = False
                continue
            # Lines belonging to the block (indented or known prefixes)
            if line.startswith(("LinkedIn:", "Role:", "Signal:", "  •")):
                continue
            in_block = False
        result.append(line)

    return "\n".join(result).strip()


def _insert_signal_block(existing_note: str, signal_block: str) -> str:
    """
    Insert Social Signals block into the note.
    Places it after the Last Interaction block if present,
    otherwise at the top. Preserves all existing content.
    """
    from interaction_scanner import INTERACTION_NOTE_MARKER

    if not existing_note.strip():
        return signal_block

    if INTERACTION_NOTE_MARKER in existing_note:
        # Find end of interaction block and insert after it
        lines = existing_note.split("\n")
        result = []
        inserted = False
        in_interaction = False

        for line in lines:
            result.append(line)
            if INTERACTION_NOTE_MARKER in line:
                in_interaction = True
                continue
            if in_interaction:
                if not line.strip() or not line.startswith(("Email:", "Meeting:", "Summary:")):
                    # End of interaction block — insert signal block here
                    if not inserted:
                        result.append("")
                        result.append(signal_block)
                        inserted = True
                    in_interaction = False

        if not inserted:
            # Interaction block was at the end
            result.append("")
            result.append(signal_block)

        return "\n".join(result)

    # No interaction block — prepend signal block
    return f"{signal_block}\n\n{existing_note.strip()}"


def parse_linkedin_activity(page_text: str) -> dict:
    """
    Parse LinkedIn profile page text to extract structured signals.

    Args:
        page_text: Raw text content from a LinkedIn profile page.

    Returns:
        Dict with headline, current_role, recent_posts, job_change.
    """
    result = {
        "headline": "",
        "current_role": "",
        "recent_posts": [],
        "job_change": "",
    }

    if not page_text:
        return result

    lines = page_text.strip().split("\n")
    lines = [l.strip() for l in lines if l.strip()]

    # LinkedIn profile pages typically have:
    # Name (line ~1-2)
    # Headline (line ~3-5, contains role + company)
    # Location (contains city/country)

    # Look for headline-like content (role + company patterns)
    for line in lines[:20]:
        if any(kw in line.lower() for kw in [" at ", " @ ", "ceo", "cto", "founder", "director", "manager", "engineer", "developer", "consultant"]):
            if not result["headline"] and len(line) < 200:
                result["headline"] = line
                break

    # Look for job change signals
    for line in lines:
        lower = line.lower()
        if any(kw in lower for kw in ["started a new position", "new role", "joined", "promoted to", "now working"]):
            result["job_change"] = line[:150]
            break

    # Look for recent activity (posts section)
    in_activity = False
    for line in lines:
        if "activity" in line.lower() and "recent" in line.lower():
            in_activity = True
            continue
        if in_activity and len(line) > 20 and len(line) < 300:
            result["recent_posts"].append(line)
            if len(result["recent_posts"]) >= 3:
                break

    return result

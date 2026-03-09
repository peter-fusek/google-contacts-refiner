"""
Contact Activity Tagging — scan Gmail and Calendar to determine last interaction per contact.

Scans both personal and work accounts, caches results, and assigns year-based labels
(Y2025, Y2024, ..., "Never in touch") to contacts via People API contact groups.

Also updates contact notes with last email/meeting details (raw subject + AI summary).
"""
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from api_client import PeopleAPIClient, RateLimiter
from config import (
    ACTIVITY_ACCOUNTS,
    ACTIVITY_LABEL_PREFIX,
    CALENDAR_EVENTS_SINCE,
    GMAIL_RATE_LIMIT,
    INTERACTIONS_CACHE,
    NEVER_IN_TOUCH_LABEL,
    RESCAN_INTERVAL_DAYS,
)

logger = logging.getLogger("contacts-refiner.activity")

INTERACTION_NOTE_MARKER = "── Last Interaction"


class InteractionScanner:
    """
    Scans Gmail and Calendar to find the most recent interaction date per contact.

    Workflow:
    1. Build email→resourceName index from contacts
    2. Scan Gmail (per unique email: query from/to, get latest date + subject + snippet)
    3. Scan Calendar (fetch all events, build attendee index with event titles)
    4. Merge results: last_interaction = max(last_email, last_meeting)
    5. Assign year-based labels via People API
    6. Update contact notes with last interaction details
    """

    def __init__(self, contacts: list[dict]):
        """
        Args:
            contacts: List of People API person resources.
        """
        self.contacts = contacts
        self._email_to_contacts: dict[str, set[str]] = defaultdict(set)
        self._contact_emails: dict[str, set[str]] = {}
        # email → {last_email: {date, subject, snippet}, last_meeting: {date, title}}
        self._interactions: dict[str, dict] = {}
        self._last_noted: dict[str, dict] = {}  # resourceName → {email_date, meeting_date}
        self._gmail_limiter = RateLimiter(GMAIL_RATE_LIMIT)
        self._cache_loaded = False

        self._build_email_index()
        self._load_cache()

    def _build_email_index(self):
        """Build mapping of email addresses to contact resourceNames."""
        for contact in self.contacts:
            rn = contact.get("resourceName", "")
            if not rn:
                continue

            emails = set()
            for email_entry in contact.get("emailAddresses", []):
                email = email_entry.get("value", "").strip().lower()
                if email:
                    emails.add(email)
                    self._email_to_contacts[email].add(rn)

            if emails:
                self._contact_emails[rn] = emails

        total_emails = len(self._email_to_contacts)
        total_contacts_with_email = len(self._contact_emails)
        logger.info(
            f"Email index: {total_emails} unique emails "
            f"from {total_contacts_with_email} contacts"
        )

    def _load_cache(self):
        """Load cached interaction data from disk. Migrates old format if needed."""
        if INTERACTIONS_CACHE.exists():
            try:
                data = json.loads(INTERACTIONS_CACHE.read_text(encoding="utf-8"))
                raw = data.get("interactions", {})

                # Migrate old format: {email: "date"} → {email: {last_email: {date}}}
                for key, val in raw.items():
                    if isinstance(val, str):
                        raw[key] = {"last_email": {"date": val, "subject": "", "snippet": ""}}

                self._interactions = raw
                self._last_noted = data.get("last_noted", {})
                self._cache_loaded = True
                logger.info(f"Cache loaded: {len(self._interactions)} email interactions")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load cache: {e}")
                self._interactions = {}

    def save_cache(self):
        """Persist interaction data to disk."""
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "interactions": self._interactions,
            "last_noted": self._last_noted,
        }
        INTERACTIONS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        INTERACTIONS_CACHE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"Cache saved: {len(self._interactions)} interactions")

    def _should_rescan(self, email: str) -> bool:
        """Check if an email needs rescanning based on cache age."""
        if email not in self._interactions:
            return True

        if not INTERACTIONS_CACHE.exists():
            return True

        mtime = datetime.fromtimestamp(INTERACTIONS_CACHE.stat().st_mtime, tz=timezone.utc)
        age = datetime.now(timezone.utc) - mtime
        return age > timedelta(days=RESCAN_INTERVAL_DAYS)

    # ── Gmail Scanning ──────────────────────────────────────────────────

    def scan_gmail(self, credentials: Credentials, account_email: str):
        """
        Scan Gmail for the latest email interaction per contact email address.

        Fetches subject and snippet alongside date for note generation.
        """
        service = build("gmail", "v1", credentials=credentials)

        emails_to_scan = [
            email for email in self._email_to_contacts
            if self._should_rescan(email)
        ]

        if not emails_to_scan:
            logger.info(f"Gmail ({account_email}): All emails cached, nothing to scan")
            return

        logger.info(
            f"Gmail ({account_email}): Scanning {len(emails_to_scan)} emails "
            f"(skipping {len(self._email_to_contacts) - len(emails_to_scan)} cached)"
        )

        scanned = 0
        found = 0
        errors = 0

        for i, email in enumerate(emails_to_scan):
            try:
                info = self._get_latest_gmail_info(service, email)
                if info:
                    existing = self._interactions.get(email, {})
                    existing_date = existing.get("last_email", {}).get("date", "")
                    if not existing_date or info["date"] > existing_date:
                        if not isinstance(existing, dict):
                            existing = {}
                        existing["last_email"] = info
                        self._interactions[email] = existing
                    found += 1
                scanned += 1

            except Exception as e:
                logger.debug(f"Gmail error for {email}: {e}")
                errors += 1
                scanned += 1

            # Progress + incremental save every 200 emails
            if (i + 1) % 200 == 0:
                logger.info(
                    f"Gmail ({account_email}): {i + 1}/{len(emails_to_scan)} "
                    f"({found} found, {errors} errors)"
                )
                self.save_cache()

        self.save_cache()
        logger.info(
            f"Gmail ({account_email}): Done — {scanned} scanned, "
            f"{found} with interactions, {errors} errors"
        )

    def _get_latest_gmail_info(self, service, email: str) -> Optional[dict]:
        """
        Get date, subject, and snippet of the most recent email to/from an address.

        Returns {date, subject, snippet} or None.
        """
        query = f"from:{email} OR to:{email}"

        self._gmail_limiter.wait()
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=1,
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return None

        msg_id = messages[0]["id"]
        self._gmail_limiter.wait()
        msg = service.users().messages().get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["Subject"],
        ).execute()

        internal_date = msg.get("internalDate")
        if not internal_date:
            return None

        dt = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)

        # Extract subject from headers
        subject = ""
        for header in msg.get("payload", {}).get("headers", []):
            if header.get("name", "").lower() == "subject":
                subject = header.get("value", "")
                break

        return {
            "date": dt.strftime("%Y-%m-%d"),
            "subject": subject[:200],  # Truncate long subjects
            "snippet": (msg.get("snippet") or "")[:300],
        }

    # ── Calendar Scanning ───────────────────────────────────────────────

    def scan_calendar(self, credentials: Credentials, account_email: str):
        """
        Scan Google Calendar to find latest meeting per attendee email.

        Stores event title alongside date for note generation.
        """
        service = build("calendar", "v3", credentials=credentials)

        logger.info(f"Calendar ({account_email}): Fetching events since {CALENDAR_EVENTS_SINCE}")

        events_processed = 0
        attendees_found = 0
        page_token = None

        while True:
            kwargs = {
                "calendarId": "primary",
                "timeMin": CALENDAR_EVENTS_SINCE,
                "maxResults": 2500,
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if page_token:
                kwargs["pageToken"] = page_token

            try:
                result = service.events().list(**kwargs).execute()
            except Exception as e:
                logger.error(f"Calendar ({account_email}): API error: {e}")
                break

            events = result.get("items", [])

            for event in events:
                event_date = self._get_event_date(event)
                if not event_date:
                    continue

                event_title = (event.get("summary") or "")[:200]

                attendees = event.get("attendees", [])
                for attendee in attendees:
                    email = attendee.get("email", "").strip().lower()
                    if not email or email == account_email.lower():
                        continue

                    if email in self._email_to_contacts:
                        existing = self._interactions.get(email, {})
                        if not isinstance(existing, dict):
                            existing = {"last_email": {"date": existing, "subject": "", "snippet": ""}}

                        existing_date = existing.get("last_meeting", {}).get("date", "")
                        if not existing_date or event_date > existing_date:
                            existing["last_meeting"] = {
                                "date": event_date,
                                "title": event_title,
                            }
                            self._interactions[email] = existing
                            attendees_found += 1

                events_processed += 1

            page_token = result.get("nextPageToken")
            if not page_token:
                break

            logger.info(
                f"Calendar ({account_email}): {events_processed} events processed..."
            )

        self.save_cache()
        logger.info(
            f"Calendar ({account_email}): Done — {events_processed} events, "
            f"{attendees_found} attendee interactions updated"
        )

    def _get_event_date(self, event: dict) -> Optional[str]:
        """Extract date from a calendar event."""
        start = event.get("start", {})
        date_str = start.get("dateTime") or start.get("date")
        if not date_str:
            return None

        try:
            if "T" in date_str:
                dt = datetime.fromisoformat(date_str)
                return dt.strftime("%Y-%m-%d")
            else:
                return date_str
        except (ValueError, TypeError):
            return None

    # ── Contact Activity Resolution ─────────────────────────────────────

    def _get_interaction_date(self, email_data: dict) -> Optional[str]:
        """Get the latest interaction date from enriched interaction data."""
        if isinstance(email_data, str):
            return email_data  # Legacy format
        email_date = email_data.get("last_email", {}).get("date", "")
        meeting_date = email_data.get("last_meeting", {}).get("date", "")
        dates = [d for d in [email_date, meeting_date] if d]
        return max(dates) if dates else None

    def get_contact_activity(self) -> dict[str, Optional[str]]:
        """
        Resolve the latest interaction date for each contact.

        Returns:
            Dict mapping resourceName → latest interaction date (YYYY-MM-DD)
            or None if no interaction found.
        """
        result: dict[str, Optional[str]] = {}

        for rn, emails in self._contact_emails.items():
            latest = None
            for email in emails:
                data = self._interactions.get(email)
                if data:
                    date = self._get_interaction_date(data)
                    if date and (not latest or date > latest):
                        latest = date
            result[rn] = latest

        # Contacts without email addresses → None
        for contact in self.contacts:
            rn = contact.get("resourceName", "")
            if rn and rn not in result:
                result[rn] = None

        return result

    def get_contact_interaction_details(self, rn: str) -> dict:
        """
        Get the best last_email and last_meeting for a contact across all its emails.

        Returns: {last_email: {date, subject, snippet} | None, last_meeting: {date, title} | None}
        """
        best_email = None
        best_meeting = None

        emails = self._contact_emails.get(rn, set())
        for email in emails:
            data = self._interactions.get(email, {})
            if isinstance(data, str):
                data = {"last_email": {"date": data, "subject": "", "snippet": ""}}

            le = data.get("last_email")
            if le and le.get("date"):
                if not best_email or le["date"] > best_email["date"]:
                    best_email = le

            lm = data.get("last_meeting")
            if lm and lm.get("date"):
                if not best_meeting or lm["date"] > best_meeting["date"]:
                    best_meeting = lm

        return {"last_email": best_email, "last_meeting": best_meeting}

    # ── Note Update ─────────────────────────────────────────────────────

    def update_notes(
        self,
        client: PeopleAPIClient,
        dry_run: bool = False,
        use_ai: bool = True,
    ) -> int:
        """
        Update contact notes with last email/meeting details.

        Only updates contacts whose interaction changed since last noted.

        Args:
            client: PeopleAPIClient for People API calls.
            dry_run: If True, compute but don't apply.
            use_ai: If True, generate AI summaries via Haiku.

        Returns:
            Number of contacts updated.
        """
        # Find contacts that need note updates
        contacts_to_update = []

        for rn, emails in self._contact_emails.items():
            details = self.get_contact_interaction_details(rn)
            if not details["last_email"] and not details["last_meeting"]:
                continue

            # Check if interaction changed since last noted
            current_sig = {
                "email_date": (details["last_email"] or {}).get("date", ""),
                "meeting_date": (details["last_meeting"] or {}).get("date", ""),
            }
            prev_sig = self._last_noted.get(rn, {})

            if current_sig == prev_sig:
                continue

            contacts_to_update.append((rn, details, current_sig))

        if not contacts_to_update:
            logger.info("Notes: No contacts need updating")
            return 0

        logger.info(f"Notes: {len(contacts_to_update)} contacts need note updates")

        if dry_run:
            for rn, details, _ in contacts_to_update[:5]:
                note = self._build_note_text(details)
                logger.info(f"  Would update {rn}:\n{note}")
            logger.info("DRY RUN — no notes updated")
            return len(contacts_to_update)

        # Generate AI summaries in batch if enabled
        ai_summaries = {}
        if use_ai:
            ai_summaries = self._generate_ai_summaries(contacts_to_update)

        updated = 0
        failed = 0

        for rn, details, sig in contacts_to_update:
            try:
                note_text = self._build_note_text(details, ai_summaries.get(rn))

                # Fetch current contact to get existing note + etag
                person = client.get_contact(rn, person_fields="biographies,metadata")
                etag = person.get("etag", "")

                # Get existing note, strip old interaction block
                existing_note = ""
                bios = person.get("biographies", [])
                for bio in bios:
                    if bio.get("contentType") == "TEXT_PLAIN":
                        existing_note = bio.get("value", "")
                        break

                clean_note = self._strip_interaction_block(existing_note)
                if clean_note and not clean_note.endswith("\n"):
                    clean_note += "\n"

                new_note = f"{clean_note}\n{note_text}" if clean_note else note_text

                # Update via People API
                body = {
                    "biographies": [{
                        "value": new_note,
                        "contentType": "TEXT_PLAIN",
                    }]
                }
                client.update_contact(rn, etag, body, update_fields="biographies")

                self._last_noted[rn] = sig
                updated += 1

                if updated % 50 == 0:
                    self.save_cache()
                    logger.info(f"Notes: {updated}/{len(contacts_to_update)} updated...")

            except Exception as e:
                logger.error(f"Notes: Failed to update {rn}: {e}")
                failed += 1

        self.save_cache()
        logger.info(f"Notes: Done — {updated} updated, {failed} failed")
        return updated

    def _build_note_text(self, details: dict, ai_summary: Optional[str] = None) -> str:
        """Build the interaction note block."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [f"{INTERACTION_NOTE_MARKER} (updated {today}) ──"]

        le = details.get("last_email")
        if le and le.get("date"):
            subject = le.get("subject", "").strip() or "(no subject)"
            lines.append(f"Email: {le['date']} | {subject}")

        lm = details.get("last_meeting")
        if lm and lm.get("date"):
            title = lm.get("title", "").strip() or "(no title)"
            lines.append(f"Meeting: {lm['date']} | {title}")

        if ai_summary:
            lines.append(f"Summary: {ai_summary}")

        return "\n".join(lines)

    def _strip_interaction_block(self, note: str) -> str:
        """Remove existing interaction block from a note."""
        if INTERACTION_NOTE_MARKER not in note:
            return note

        # Remove from marker to end of block (consecutive non-empty lines)
        lines = note.split("\n")
        result = []
        in_block = False
        for line in lines:
            if INTERACTION_NOTE_MARKER in line:
                in_block = True
                continue
            if in_block:
                # Block ends at empty line or end of text
                if not line.strip():
                    in_block = False
                    continue
                # Still in block (Email:, Meeting:, Summary: lines)
                if line.startswith(("Email:", "Meeting:", "Summary:")):
                    continue
                in_block = False
            result.append(line)

        # Strip trailing whitespace
        text = "\n".join(result).rstrip()
        return text

    def _generate_ai_summaries(
        self,
        contacts: list[tuple[str, dict, dict]],
    ) -> dict[str, str]:
        """Generate AI summaries for contacts that have snippets."""
        summaries = {}

        # Collect contacts with snippets
        to_summarize = []
        for rn, details, _ in contacts:
            snippet = (details.get("last_email") or {}).get("snippet", "")
            subject = (details.get("last_email") or {}).get("subject", "")
            meeting = (details.get("last_meeting") or {}).get("title", "")
            if snippet or subject:
                to_summarize.append((rn, subject, snippet, meeting))

        if not to_summarize:
            return summaries

        logger.info(f"AI: Generating summaries for {len(to_summarize)} contacts")

        try:
            import anthropic
            from config import ENVIRONMENT
            import os

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                logger.warning("AI: No ANTHROPIC_API_KEY, skipping summaries")
                return summaries

            client = anthropic.Anthropic(api_key=api_key)

            # Batch in groups of 20 to reduce API calls
            for batch_start in range(0, len(to_summarize), 20):
                batch = to_summarize[batch_start:batch_start + 20]

                prompt_parts = []
                for i, (rn, subject, snippet, meeting) in enumerate(batch):
                    part = f"[{i+1}] Subject: {subject}"
                    if snippet:
                        part += f"\nSnippet: {snippet}"
                    if meeting:
                        part += f"\nLast meeting: {meeting}"
                    prompt_parts.append(part)

                prompt = (
                    "For each numbered contact interaction below, write exactly ONE short sentence "
                    "(max 15 words) summarizing the relationship context. "
                    "Reply with numbered lines only, e.g.:\n"
                    "[1] Discussed project timeline\n"
                    "[2] Regular client meetings\n\n"
                    + "\n\n".join(prompt_parts)
                )

                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}],
                )

                # Parse response
                text = response.content[0].text
                for line in text.strip().split("\n"):
                    match = re.match(r"\[(\d+)\]\s*(.+)", line.strip())
                    if match:
                        idx = int(match.group(1)) - 1
                        if 0 <= idx < len(batch):
                            rn = batch[idx][0]
                            summaries[rn] = match.group(2).strip()[:100]

                logger.info(
                    f"AI: Batch {batch_start // 20 + 1} — "
                    f"{len([r for r in batch if batch[batch.index(r)][0] in summaries])} summaries"
                )

        except Exception as e:
            logger.error(f"AI summary generation failed: {e}")

        return summaries

    # ── Year-Label Assignment ────────────────────────────────────────────

    def assign_labels(
        self,
        client: PeopleAPIClient,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """
        Assign year-based labels to contacts based on interaction data.

        Labels: Y2025, Y2024, Y2023, ..., "Never in touch"
        Additive only — never removes contacts from groups.
        """
        activity = self.get_contact_activity()

        label_assignments: dict[str, list[str]] = defaultdict(list)

        current_year = str(datetime.now(timezone.utc).year)

        for rn, last_date in activity.items():
            if last_date:
                year = last_date[:4]
                if year > current_year:
                    year = current_year
                label = f"{ACTIVITY_LABEL_PREFIX}{year}"
            else:
                label = NEVER_IN_TOUCH_LABEL
            label_assignments[label].append(rn)

        logger.info("Activity label assignments:")
        for label in sorted(label_assignments.keys()):
            count = len(label_assignments[label])
            logger.info(f"  {label}: {count} contacts")

        if dry_run:
            logger.info("DRY RUN — no labels applied")
            return {k: len(v) for k, v in label_assignments.items()}

        existing_groups = client.get_all_contact_groups()
        group_map: dict[str, str] = {}
        for g in existing_groups:
            name = g.get("name", "")
            grn = g.get("resourceName", "")
            if name and grn:
                group_map[name] = grn

        existing_members: dict[str, set[str]] = {}
        stats: dict[str, int] = {}

        for label, contact_rns in label_assignments.items():
            if label not in group_map:
                logger.info(f"Creating group: {label}")
                try:
                    group = client.create_contact_group(label)
                    group_map[label] = group["resourceName"]
                except Exception as e:
                    logger.error(f"Failed to create group '{label}': {e}")
                    continue

            group_rn = group_map[label]

            if group_rn not in existing_members:
                try:
                    members = client.get_contact_group_members(group_rn)
                    existing_members[group_rn] = set(members)
                except Exception as e:
                    logger.warning(f"Failed to get members for {label}: {e}")
                    existing_members[group_rn] = set()

            new_contacts = [
                rn for rn in contact_rns
                if rn not in existing_members[group_rn]
            ]

            if not new_contacts:
                logger.info(f"{label}: All {len(contact_rns)} contacts already in group")
                stats[label] = 0
                continue

            added = 0
            for batch_start in range(0, len(new_contacts), 500):
                batch = new_contacts[batch_start:batch_start + 500]
                try:
                    client.add_contact_to_group(group_rn, batch)
                    added += len(batch)
                except Exception as e:
                    logger.error(f"Failed to add {len(batch)} contacts to {label}: {e}")

            stats[label] = added
            logger.info(f"{label}: Added {added} contacts (skipped {len(contact_rns) - len(new_contacts)} existing)")

        return stats

    # ── Full Scan Pipeline ──────────────────────────────────────────────

    def run_full_scan(
        self,
        account_credentials: list[tuple[str, Credentials]],
        client: PeopleAPIClient,
        skip_scan: bool = False,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """
        Run the complete activity tagging pipeline.

        Args:
            account_credentials: List of (email, credentials) tuples.
            client: PeopleAPIClient for label operations.
            skip_scan: Skip Gmail/Calendar scan, use cached data only.
            dry_run: Compute assignments but don't apply labels.

        Returns:
            Dict mapping label name → count of contacts assigned.
        """
        if not skip_scan:
            for account_email, creds in account_credentials:
                logger.info(f"Scanning {account_email}...")
                self.scan_gmail(creds, account_email)
                self.scan_calendar(creds, account_email)

        # Resolve and assign labels
        stats = self.assign_labels(client, dry_run=dry_run)

        # Update contact notes with interaction details
        notes_updated = self.update_notes(client, dry_run=dry_run)
        logger.info(f"Notes updated: {notes_updated}")

        return stats

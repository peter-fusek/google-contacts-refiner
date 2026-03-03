"""
Google People API wrapper with retry logic, rate limiting, and pagination.
"""
import time
import threading
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

from config import (
    PERSON_FIELDS, UPDATE_PERSON_FIELDS, PAGE_SIZE,
    READ_RATE_LIMIT, MUTATION_RATE_LIMIT,
    RETRY_MAX_ATTEMPTS, RETRY_BASE_DELAY,
)


class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self.interval = 60.0 / max_per_minute
        self.last_call = 0.0
        self._lock = threading.Lock()

    def wait(self):
        """Block until we can make another call."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_call
            if elapsed < self.interval:
                sleep_time = self.interval - elapsed
                time.sleep(sleep_time)
            self.last_call = time.monotonic()


class PeopleAPIClient:
    """
    Wrapper around Google People API with:
    - Automatic pagination
    - Rate limiting (separate for reads and writes)
    - Retry with exponential backoff
    - Convenience methods for common operations
    """

    def __init__(self, credentials: Credentials):
        self.service = build("people", "v1", credentials=credentials)
        self.people = self.service.people()
        self.contact_groups = self.service.contactGroups()

        self._read_limiter = RateLimiter(READ_RATE_LIMIT)
        self._write_limiter = RateLimiter(MUTATION_RATE_LIMIT)

    def _retry(self, func, is_write: bool = False, **kwargs):
        """
        Execute an API call with retry and rate limiting.
        """
        limiter = self._write_limiter if is_write else self._read_limiter

        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            limiter.wait()
            try:
                return func(**kwargs).execute()
            except HttpError as e:
                status = e.resp.status if e.resp else 0

                # Rate limit exceeded
                if status == 429:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    print(f"  ⏳ Rate limit, čakám {delay:.0f}s (pokus {attempt}/{RETRY_MAX_ATTEMPTS})")
                    time.sleep(delay)
                    continue

                # Server error — retry
                if status >= 500:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    print(f"  ⚠️  Server error {status}, retry za {delay:.0f}s (pokus {attempt}/{RETRY_MAX_ATTEMPTS})")
                    time.sleep(delay)
                    continue

                # Client error — don't retry
                raise

            except Exception as e:
                if attempt < RETRY_MAX_ATTEMPTS:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    print(f"  ⚠️  Chyba: {e}, retry za {delay:.0f}s (pokus {attempt}/{RETRY_MAX_ATTEMPTS})")
                    time.sleep(delay)
                else:
                    raise

        raise RuntimeError(f"Zlyhalo po {RETRY_MAX_ATTEMPTS} pokusoch")

    # ── Read Operations ────────────────────────────────────────────────

    def get_all_contacts(
        self,
        person_fields: str = PERSON_FIELDS,
        progress_callback=None,
    ) -> list[dict]:
        """
        Fetch ALL contacts with pagination.

        Args:
            person_fields: Comma-separated fields to fetch.
            progress_callback: Called with (fetched_so_far, total_estimate).

        Returns:
            List of person resources.
        """
        all_contacts = []
        page_token = None
        page_num = 0

        while True:
            page_num += 1
            kwargs = {
                "resourceName": "people/me",
                "pageSize": PAGE_SIZE,
                "personFields": person_fields,
                "sortOrder": "LAST_MODIFIED_DESCENDING",
            }
            if page_token:
                kwargs["pageToken"] = page_token

            result = self._retry(
                self.people.connections().list,
                is_write=False,
                **kwargs,
            )

            connections = result.get("connections", [])
            all_contacts.extend(connections)

            total = result.get("totalPeople", 0) or result.get("totalItems", 0) or "?"

            if progress_callback:
                progress_callback(len(all_contacts), total)

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return all_contacts

    def get_contact(self, resource_name: str, person_fields: str = PERSON_FIELDS) -> dict:
        """Fetch a single contact by resourceName."""
        return self._retry(
            self.people.get,
            is_write=False,
            resourceName=resource_name,
            personFields=person_fields,
        )

    def get_all_contact_groups(self, group_fields: str = "name,groupType,memberCount,metadata") -> list[dict]:
        """Fetch all contact groups/labels."""
        all_groups = []
        page_token = None

        while True:
            kwargs = {
                "pageSize": 100,
                "groupFields": group_fields,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            result = self._retry(
                self.contact_groups.list,
                is_write=False,
                **kwargs,
            )

            groups = result.get("contactGroups", [])
            all_groups.extend(groups)

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return all_groups

    def get_contact_group_members(self, group_resource_name: str) -> list[str]:
        """
        Get member resourceNames for a contact group.
        """
        result = self._retry(
            self.contact_groups.get,
            is_write=False,
            resourceName=group_resource_name,
            maxMembers=1000,
            groupFields="name,groupType,memberCount",
        )
        return result.get("memberResourceNames", [])

    # ── Write Operations ───────────────────────────────────────────────

    def update_contact(
        self,
        resource_name: str,
        etag: str,
        person_body: dict,
        update_fields: str = UPDATE_PERSON_FIELDS,
    ) -> dict:
        """
        Update a contact.

        Args:
            resource_name: e.g. "people/c1234567890"
            etag: Current etag for optimistic locking.
            person_body: Dict with fields to update.
            update_fields: Comma-separated field mask.

        Returns:
            Updated person resource.
        """
        person_body["etag"] = etag

        return self._retry(
            self.people.updateContact,
            is_write=True,
            resourceName=resource_name,
            updatePersonFields=update_fields,
            body=person_body,
        )

    def batch_update_contacts(
        self,
        contacts: list[dict],
        update_fields: str = UPDATE_PERSON_FIELDS,
        progress_callback=None,
    ) -> tuple[list[dict], list[dict]]:
        """
        Update multiple contacts sequentially with rate limiting.

        Args:
            contacts: List of dicts with 'resourceName', 'etag', 'body'.
            update_fields: Field mask.
            progress_callback: Called with (done, total, success_bool, error_msg).

        Returns:
            (successes, failures) — each a list of result dicts.
        """
        successes = []
        failures = []

        for i, contact in enumerate(contacts):
            try:
                result = self.update_contact(
                    resource_name=contact["resourceName"],
                    etag=contact["etag"],
                    person_body=contact["body"],
                    update_fields=update_fields,
                )
                successes.append({
                    "resourceName": contact["resourceName"],
                    "result": result,
                })
                if progress_callback:
                    progress_callback(i + 1, len(contacts), True, None)

            except Exception as e:
                error_msg = str(e)
                failures.append({
                    "resourceName": contact["resourceName"],
                    "error": error_msg,
                })
                if progress_callback:
                    progress_callback(i + 1, len(contacts), False, error_msg)

        return successes, failures

    # ── Label Operations ───────────────────────────────────────────────

    def add_contact_to_group(self, group_resource_name: str, contact_resource_names: list[str]) -> dict:
        """Add contacts to a contact group."""
        body = {
            "resourceNamesToAdd": contact_resource_names,
        }
        return self._retry(
            self.contact_groups.members().modify,
            is_write=True,
            resourceName=group_resource_name,
            body=body,
        )

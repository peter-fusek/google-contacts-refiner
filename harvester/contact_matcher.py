"""
Contact matcher — resolve InteractionRecord.matchCandidates to a Google
People `resourceName`.

Matching precedence per docs/schemas/interaction.md:
  1. Exact email match (Google People `emailAddresses.value`, lowercase)
  2. E.164 phone match (Google People `phoneNumbers.value`, normalized)
  3. Handle cache (seeded from Beeper contacts-list + prior matches)
  4. Fuzzy display-name match (rapidfuzz ≥ 92) — applied only when the
     record has a resolved participant name (macOS AddressBook gives us
     resolved names for ~53% of iMessage handles per chat.db diagnostic)

Deps: `phonenumbers`, `rapidfuzz` — already in requirements.txt. No kernel
dependencies, so safe to land ahead of `beeper_client.py`.

Run inline self-test against synthetic fixtures:
    python -m harvester.contact_matcher

Consumed by:
- `harvester/pipeline.py` — once kernel lands, each reader's output goes
  through this matcher before writing to `data/interactions/*.jsonl`
- `main.py harvest-messages` wiring — see Sprint 3.33 S2 plan
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import phonenumbers
from rapidfuzz import fuzz, process
from unidecode import unidecode

logger = logging.getLogger("contacts-refiner.contact_matcher")

# Default phone region used by `phonenumbers.parse` when the raw number has
# no country prefix. Aligned with the project's SK-primary contact base.
DEFAULT_PHONE_REGION = "SK"

# rapidfuzz score threshold for fuzzy name matches. Calibrated by manual
# inspection: 92 catches diacritic variants (Kristína vs Kristina) and
# middle-name drop-outs ("Anna Kolegová" vs "Anna Marie Kolegová") without
# collapsing unrelated people (Peter Fusek vs Peter Fischer = 78).
FUZZY_NAME_THRESHOLD = 92


# ── normalization helpers ─────────────────────────────────────────────────

def normalize_phone(raw: str, default_region: str = DEFAULT_PHONE_REGION) -> Optional[str]:
    """Normalize a phone string to E.164 (e.g. `+421903123456`).

    Returns None if `phonenumbers` can't parse it or the result is invalid.
    Tries the default region first, falls back to prefix-aware parsing.
    """
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, default_region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164,
            )
    except phonenumbers.NumberParseException:
        pass
    # Fallback when the raw string already has `+` or an international prefix.
    try:
        parsed = phonenumbers.parse(raw, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164,
            )
    except phonenumbers.NumberParseException:
        pass
    return None


def normalize_email(raw: str) -> Optional[str]:
    """Lowercase + strip. Returns None for empty / non-email-looking input."""
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if "@" not in cleaned:
        return None
    return cleaned


# ── match cache on disk ───────────────────────────────────────────────────

@dataclass
class MatchCache:
    """Persistent handle → resourceName map.

    Populated from three sources:
      - seed from Beeper `/v1/accounts/{id}/contacts/list` (once kernel wired)
      - `record_match()` after any successful match — so subsequent runs
        short-circuit before re-scanning indexes
      - manual dashboard "link to contact X" action (future)
    """
    by_handle: dict[str, str] = field(default_factory=dict)
    schema_version: int = 1

    def record(self, handle: str, resource_name: str) -> None:
        if handle and resource_name:
            self.by_handle[handle] = resource_name

    def lookup(self, handle: str) -> Optional[str]:
        return self.by_handle.get(handle)

    @classmethod
    def load(cls, path: Path) -> "MatchCache":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1:
                logger.warning(
                    f"MatchCache schema mismatch at {path}; ignoring"
                )
                return cls()
            return cls(by_handle=dict(data.get("by_handle", {})))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"MatchCache load failed at {path}: {e}")
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "updated": datetime.now(timezone.utc).isoformat(),
            "count": len(self.by_handle),
            "by_handle": self.by_handle,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


# ── matcher ───────────────────────────────────────────────────────────────

class ContactMatcher:
    """Resolve InteractionRecord match candidates to Google People resourceNames.

    Built once per harvest run from a Google People snapshot + optional
    LinkedIn signals + persistent match cache. Matching is O(1) for
    email/phone, O(N) for fuzzy name where N = contact count.
    """

    def __init__(
        self,
        contacts: list[dict],
        *,
        linkedin_signals: Optional[dict[str, dict]] = None,
        match_cache: Optional[MatchCache] = None,
        default_phone_region: str = DEFAULT_PHONE_REGION,
        fuzzy_threshold: int = FUZZY_NAME_THRESHOLD,
    ):
        self.default_phone_region = default_phone_region
        self.fuzzy_threshold = fuzzy_threshold
        self.match_cache = match_cache or MatchCache()
        self.linkedin_signals = linkedin_signals or {}

        # Build the three fast indexes plus a name list for fuzzy match.
        self.email_index: dict[str, str] = {}
        self.phone_index: dict[str, str] = {}
        self.name_index: dict[str, str] = {}
        self._build_indexes(contacts)

        logger.info(
            f"ContactMatcher ready: "
            f"{len(contacts)} contacts, "
            f"{len(self.email_index)} emails, "
            f"{len(self.phone_index)} phones, "
            f"{len(self.name_index)} names, "
            f"{len(self.match_cache.by_handle)} cached handles, "
            f"{len(self.linkedin_signals)} linkedin signals"
        )

    # ── public API ──────────────────────────────────────────────────────
    def match(self, record: dict) -> Optional[str]:
        """Resolve this interaction record to a `people/cXXXXX` or None.

        Does not mutate the record. Caller is responsible for writing the
        result back into `record["contactId"]` if desired.

        Also records successful matches into the cache so subsequent
        handle-based lookups short-circuit.
        """
        cands = record.get("matchCandidates") or {}

        # 1. Email
        for raw_email in cands.get("emails") or []:
            norm = normalize_email(raw_email)
            if norm and norm in self.email_index:
                rn = self.email_index[norm]
                self.match_cache.record(f"email:{norm}", rn)
                return rn

        # 2. Phone (E.164)
        for raw_phone in cands.get("phones") or []:
            e164 = normalize_phone(raw_phone, self.default_phone_region)
            if e164 and e164 in self.phone_index:
                rn = self.phone_index[e164]
                self.match_cache.record(f"phone:{e164}", rn)
                return rn

        # 3. Handle cache (Beeper contacts-list, prior matches, manual links)
        for raw_handle in cands.get("handles") or []:
            rn = self.match_cache.lookup(raw_handle)
            if rn:
                return rn

        # 4. Fuzzy name — only when the record carries a resolved participant
        #    name. macOS AddressBook resolves ~53% of iMessage handles, so
        #    this fires for the iMessage/SMS/RCS reader output mostly.
        fuzzy = self._fuzzy_match_participants(record.get("participants") or [])
        if fuzzy:
            # Seed the handle cache so next interaction from the same handle
            # resolves in O(1) without re-running fuzzy.
            for raw_handle in cands.get("handles") or []:
                self.match_cache.record(raw_handle, fuzzy)
            return fuzzy

        # 5. LinkedIn signal reverse lookup — handles LinkedIn DMs where
        #    the DM source gave us a LinkedIn vanity URL fragment.
        li_match = self._linkedin_signal_match(cands.get("handles") or [])
        if li_match:
            return li_match

        return None

    def save_cache(self, path: Path) -> None:
        self.match_cache.save(path)

    # ── internals ───────────────────────────────────────────────────────
    def _build_indexes(self, contacts: list[dict]) -> None:
        for c in contacts:
            rn = c.get("resourceName")
            if not rn:
                continue

            for e in c.get("emailAddresses") or []:
                norm = normalize_email(e.get("value") or "")
                if norm:
                    # First contact with this email wins. In practice
                    # duplicates are rare post-deduplicator.
                    self.email_index.setdefault(norm, rn)

            for p in c.get("phoneNumbers") or []:
                e164 = normalize_phone(
                    p.get("value") or "", self.default_phone_region,
                )
                if e164:
                    self.phone_index.setdefault(e164, rn)

            for n in c.get("names") or []:
                display = (n.get("displayName") or "").strip()
                if display:
                    self.name_index.setdefault(display, rn)

    def _fuzzy_match_participants(self, participants: list[dict]) -> Optional[str]:
        if not self.name_index:
            return None
        # Build an ascii-folded view of the index once (cached on instance) so
        # "Kristina Gomoryova" matches "Kristína Gomoryová" without false
        # positives from lowering the fuzz threshold.
        if not hasattr(self, "_ascii_name_index"):
            self._ascii_name_index: dict[str, str] = {}
            for display, rn in self.name_index.items():
                key = unidecode(display).lower().strip()
                if key:
                    self._ascii_name_index.setdefault(key, rn)
            self._ascii_names_list = list(self._ascii_name_index.keys())

        for p in participants:
            name = (p.get("name") or "").strip()
            if not name:
                continue
            probe = unidecode(name).lower().strip()
            if not probe:
                continue
            best = process.extractOne(
                probe, self._ascii_names_list,
                scorer=fuzz.WRatio, score_cutoff=self.fuzzy_threshold,
            )
            if best:
                matched_key, score, _ = best
                rn = self._ascii_name_index[matched_key]
                logger.debug(
                    f"fuzzy match '{name}' → '{matched_key}' "
                    f"(score={score}, resource={rn})"
                )
                return rn
        return None

    def _linkedin_signal_match(self, handles: Iterable[str]) -> Optional[str]:
        """Cross-reference handles against linkedin_signals.json.

        Handles that look like `linkedin:<vanity-id>` or `@linkedin/<id>` get
        reverse-mapped via the linkedin_url stored per signal.
        """
        if not self.linkedin_signals:
            return None
        # Build lightweight reverse index on first call and cache on instance
        if not hasattr(self, "_li_url_to_rn"):
            rev: dict[str, str] = {}
            for rn, sig in self.linkedin_signals.items():
                url = (sig.get("linkedin_url") or "").lower()
                if url:
                    rev[url] = rn
                    # Also index the vanity segment after /in/
                    if "/in/" in url:
                        vanity = url.split("/in/", 1)[1].rstrip("/")
                        if vanity:
                            rev[f"linkedin:{vanity}"] = rn
                            rev[f"@linkedin/{vanity}"] = rn
            self._li_url_to_rn = rev
        for h in handles:
            key = (h or "").lower().strip()
            if key in self._li_url_to_rn:
                return self._li_url_to_rn[key]
        return None


# ── self-test ─────────────────────────────────────────────────────────────

def _run_self_test() -> None:
    print("Running contact_matcher self-test…")

    # Fixture: a mini Google People snapshot covering the common shapes.
    contacts = [
        {
            "resourceName": "people/c1001",
            "names": [{"displayName": "Badr Almarshoud"}],
            "emailAddresses": [{"value": "Badr@example.KSA"}],
            "phoneNumbers": [{"value": "+966 58 346 95 491"}],
        },
        {
            "resourceName": "people/c1002",
            "names": [{"displayName": "Kristína Gomoryová"}],
            "emailAddresses": [{"value": "k.gomoryova@recetox.cz"}],
            "phoneNumbers": [{"value": "0903 290 609"}],  # Slovak short form
        },
        {
            "resourceName": "people/c1003",
            "names": [{"displayName": "Miloslava Burikova"}],
            "emailAddresses": [],
            "phoneNumbers": [{"value": "+421910511197"}],
        },
        {
            "resourceName": "people/c1004",
            "names": [{"displayName": "Franklin Nkrumah"}],
            "emailAddresses": [{"value": "franklin@terbigen.co.za"}],
            "phoneNumbers": [],
        },
    ]

    linkedin_signals = {
        "people/c1004": {
            "linkedin_url": "https://www.linkedin.com/in/franklin-nkrumah/",
            "signal_type": "active",
        },
    }

    matcher = ContactMatcher(contacts, linkedin_signals=linkedin_signals)

    # Case 1: exact email (case-insensitive)
    rec_email = {
        "channel": "gmail",
        "matchCandidates": {"emails": ["BADR@example.ksa"], "phones": [], "handles": []},
        "participants": [],
    }
    assert matcher.match(rec_email) == "people/c1001", "email case-insensitive"
    print("  ✓ Case 1: email (case-insensitive)")

    # Case 2: phone normalized from Slovak short form
    rec_phone = {
        "channel": "whatsapp",
        "matchCandidates": {"emails": [], "phones": ["0903290609"], "handles": []},
        "participants": [],
    }
    assert matcher.match(rec_phone) == "people/c1002", "phone SK short form"
    print("  ✓ Case 2: phone (Slovak short form → E.164)")

    # Case 3: phone already E.164
    rec_phone_e164 = {
        "channel": "imessage",
        "matchCandidates": {"emails": [], "phones": ["+421910511197"], "handles": []},
        "participants": [],
    }
    assert matcher.match(rec_phone_e164) == "people/c1003"
    print("  ✓ Case 3: phone (already E.164)")

    # Case 4: fuzzy name match — participant has resolved name from AddressBook
    rec_fuzzy = {
        "channel": "imessage",
        "matchCandidates": {"emails": [], "phones": ["+9999999999"], "handles": []},
        "participants": [{"kind": "phone", "value": "+9999999999",
                          "name": "Kristina Gomoryova",   # no diacritics
                          "self": False}],
    }
    assert matcher.match(rec_fuzzy) == "people/c1002", "fuzzy strips diacritics"
    print("  ✓ Case 4: fuzzy name (diacritic variant)")

    # Case 5: LinkedIn URL handle
    rec_li = {
        "channel": "linkedin_dm",
        "matchCandidates": {
            "emails": [], "phones": [],
            "handles": ["linkedin:franklin-nkrumah"],
        },
        "participants": [],
    }
    assert matcher.match(rec_li) == "people/c1004", "linkedin handle match"
    print("  ✓ Case 5: LinkedIn handle reverse lookup")

    # Case 6: no match
    rec_no = {
        "channel": "whatsapp",
        "matchCandidates": {"emails": [], "phones": ["+15555551234"], "handles": []},
        "participants": [{"name": "Unknown Person", "self": False}],
    }
    assert matcher.match(rec_no) is None, "unknown should return None"
    print("  ✓ Case 6: no-match returns None")

    # Case 7: match cache round-trip — prior match resolves via handle
    matcher.match_cache.record("beeper:!room123:beeper.com", "people/c1001")
    rec_cache = {
        "channel": "whatsapp",
        "matchCandidates": {"emails": [], "phones": [],
                            "handles": ["beeper:!room123:beeper.com"]},
        "participants": [],
    }
    assert matcher.match(rec_cache) == "people/c1001"
    print("  ✓ Case 7: match cache handle lookup")

    # Case 8: persistent cache save/load
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        matcher.save_cache(tmp_path)
        loaded = MatchCache.load(tmp_path)
        assert loaded.lookup("beeper:!room123:beeper.com") == "people/c1001"
        assert loaded.schema_version == 1
        print("  ✓ Case 8: cache save/load round-trip")
    finally:
        tmp_path.unlink(missing_ok=True)

    print("All self-tests passed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _run_self_test()

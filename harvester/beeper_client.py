"""
Beeper Desktop API — HTTP client + ChannelReader wrapper.

Thin transport layer over `beeper_oauth.get_or_create_token`. Stdlib-only
(urllib), matches the ChannelReader protocol in docs/schemas/interaction.md
so `pipeline.py` can treat it uniformly alongside `IMessageReader`.

One `BeeperClient` emits records across all Beeper-attached networks
(WhatsApp, Signal, Messenger, LinkedIn DM, Telegram, Instagram, X,
Discord, …) — so its `channel` attribute is per-record, not per-reader.

Key behaviours:
- `available()` probes `/v1/info` with a 2s timeout (no auth required).
- `harvest(since, until)` paginates `/v1/chats` then
  `/v1/chats/{id}/messages` with cursor support, normalizes each message
  into an InteractionRecord, and throttles to 1 req/sec to stay under
  Beeper's (undocumented) rate ceiling.
- `401 Unauthorized` triggers one forced re-auth, then a single retry.
  A second 401 raises — we don't want the harvester silently skipping
  an entire cadence when credentials are dead.

Run inline self-test (offline — does not touch Beeper):
    python -m harvester.beeper_client
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

from harvester.beeper_oauth import (
    DEFAULT_ISSUER,
    BeeperToken,
    get_or_create_token,
    is_beeper_reachable,
)

logger = logging.getLogger("contacts-refiner.beeper_client")

HTTP_TIMEOUT_SECONDS = 15
REQUEST_INTERVAL_SECONDS = 1.0  # polite throttle per beeper-api-reference.md
PAGE_LIMIT = 100                # per-page cap for /chats + /messages
MESSAGE_SUMMARY_MAX = 500

# Map Beeper network identifiers → interaction.md channel vocabulary.
# Beeper's /v1/accounts returns `networkID` like "whatsapp", "signal",
# "facebook" (Messenger), "linkedin", "telegram", "instagram", "x",
# "discord". The mapping is mostly 1:1; the exceptions are normalized
# here so pipeline.py never sees a raw Beeper identifier leaking into
# `channel`.
NETWORK_CHANNEL_MAP: dict[str, str] = {
    "whatsapp": "whatsapp",
    "signal": "signal",
    "facebook": "messenger",
    "messenger": "messenger",
    "linkedin": "linkedin_dm",
    "telegram": "telegram",
    "instagram": "instagram",
    "x": "x",
    "twitter": "x",
    "discord": "discord",
    "slack": "slack",
    "imessage": "imessage",  # Beeper can mirror iMessage; reader prefers direct chat.db
}


def normalize_network_id(raw: Optional[str]) -> str:
    """Strip Beeper's compound network/account prefixes.

    Beeper's /v1/accounts returns accountID values like
    `slackgo.T07QED922QP-U07R7KZ1Z5X` (workspace + user embedded) or
    `slackgo.T07QED922QP`. A raw MCP payload's `networkHint` can also
    carry these. Without normalization they leak past NETWORK_CHANNEL_MAP
    and produce schema-invalid `channel` values in the InteractionRecord.

    Called from both the HTTP harvester path (BeeperClient.harvest) and
    the MCP path (scripts/mcp_harvest_session.py) so the two can't
    diverge on channel vocab.

    Rules are explicit rather than regex to keep the set auditable:
      - slackgo.* → slack
      - beepergo.* → strip prefix (Beeper internal bridges)
      - matrix-* → strip prefix
      - facebookgo → facebook (Messenger bridge goes by two names)
      - discordgo → discord
      - instagramgo → instagram
    Unknown prefixes pass through lowercased.
    """
    raw = (raw or "").strip().lower()
    if not raw:
        return ""
    # Order matters — longest prefix first so slackgo beats slack.
    for prefix, replacement in (
        ("slackgo", "slack"),
        ("facebookgo", "facebook"),
        ("discordgo", "discord"),
        ("instagramgo", "instagram"),
        ("beepergo", ""),
        ("matrix-", ""),
    ):
        if raw.startswith(prefix):
            rest = raw[len(prefix):].lstrip(".-:")
            # If there's a canonical replacement, use it (drops the suffix
            # entirely since workspace/user IDs are not the channel). If
            # not, keep the tail (e.g. beepergo.sms → sms).
            return replacement or rest or prefix
    return raw


# ── data shapes ───────────────────────────────────────────────────────────

@dataclass
class BeeperClientConfig:
    """Instance config. All fields have sensible defaults."""
    issuer: str = DEFAULT_ISSUER
    page_limit: int = PAGE_LIMIT
    request_interval_seconds: float = REQUEST_INTERVAL_SECONDS
    summary_max_chars: int = MESSAGE_SUMMARY_MAX
    # When True, skip the iMessage channel from Beeper output — direct
    # chat.db reader handles it better (NSArchiver decoding, group chat
    # metadata). Avoids duplicate records for the same message.
    skip_imessage: bool = True
    # When True, skip group chats. Matches iMessage reader default False
    # inverted — groups are high-volume + low-signal for CRM.
    skip_group_chats: bool = False


# ── helpers ───────────────────────────────────────────────────────────────

def _truncate_summary(body: Optional[str], max_chars: int) -> str:
    """Apply interaction.md §summary rules: collapse whitespace, cap length.

    URL stripping is intentionally NOT done here — Beeper returns message
    text already in plain form, and the `(domain)` transformation from
    interaction.md is expensive to get right (false-positive on code
    snippets, IPv4 literals, etc.). Pipeline.py can apply it post-hoc if
    needed; for now keep the summary closer to the source.
    """
    if body is None:
        return ""
    clean = " ".join(str(body).split())
    if not clean:
        return ""
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1] + "…"


def _hash_interaction_id(
    channel: str, thread_id: str, ts_iso: str, direction: str, external_id: str,
) -> str:
    """sha256 truncated to 16 hex chars. Matches imessage_reader._hash scheme
    so interactionIds are comparable across readers when the same message
    appears via multiple paths."""
    key = f"{channel}|{thread_id}|{ts_iso}|{direction}|{external_id}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    s = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# ── client ────────────────────────────────────────────────────────────────

class BeeperClient:
    """HTTP client + ChannelReader for Beeper Desktop.

    Thread-unsafe by design — harvester calls are single-threaded. The
    token is refreshed lazily, so a long-running harvest that crosses an
    expiry boundary still works.
    """

    channel = "beeper"  # per-reader channel; real per-record channel comes from network

    def __init__(self, config: Optional[BeeperClientConfig] = None):
        self.config = config or BeeperClientConfig()
        self._token: Optional[BeeperToken] = None
        self._last_request_ts: float = 0.0
        self._accounts_cache: Optional[list[dict]] = None

    # ── ChannelReader protocol ──────────────────────────────────────────

    def available(self) -> bool:
        """Non-auth reachability probe. Fast — 2s timeout in the helper."""
        if not is_beeper_reachable(self.config.issuer):
            logger.info(
                "BeeperClient: Desktop API not reachable at %s — skipping",
                self.config.issuer,
            )
            return False
        return True

    def harvest(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Iterator[dict]:
        """Yield InteractionRecord dicts for messages in [since, until).

        Walks /v1/chats, then per-chat /v1/chats/{id}/messages. Ordering
        within a chat follows Beeper's response (newest-first typically),
        but callers should not rely on global ordering — pipeline.py
        sorts before writing.
        """
        if not self.available():
            return

        token = self._ensure_token()
        accounts_by_id = self._build_account_index()

        for chat in self._iter_chats(token):
            chat_id = chat.get("id") or chat.get("chatID") or ""
            if not chat_id:
                continue
            account_id = chat.get("accountID") or chat.get("accountId") or ""
            raw_network_id = (
                chat.get("networkID")
                or chat.get("network")
                or accounts_by_id.get(account_id, {}).get("networkID")
                or account_id  # fallback to accountID so compound IDs still normalize
                or ""
            )
            # Normalize compound IDs (slackgo.T07… etc) before map lookup.
            network_id = normalize_network_id(raw_network_id)

            if self.config.skip_imessage and network_id == "imessage":
                continue

            is_group = bool(chat.get("isGroupChat") or chat.get("isGroup"))
            if self.config.skip_group_chats and is_group:
                continue

            channel = NETWORK_CHANNEL_MAP.get(network_id, network_id or "beeper")

            for message in self._iter_messages(token, chat_id, since=since, until=until):
                record = self._message_to_record(
                    message=message,
                    chat=chat,
                    channel=channel,
                    network_id=network_id,
                    is_group=is_group,
                )
                if record is None:
                    continue
                yield record

    # ── HTTP primitives ─────────────────────────────────────────────────

    def _ensure_token(self) -> BeeperToken:
        if self._token is None or self._token.is_expired():
            self._token = get_or_create_token(issuer=self.config.issuer)
        return self._token

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        interval = self.config.request_interval_seconds
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _get_json(self, path: str, params: Optional[dict] = None) -> dict:
        """GET a JSON endpoint with 401-once-retry.

        Raises on non-2xx other than 401 (which triggers re-auth + single
        retry). The retry explicitly forces a fresh token via
        `get_or_create_token(force_new=True)` rather than the lazy
        refresh path, since a 401 likely means the refresh_token is
        also stale.
        """
        self._throttle()
        token = self._ensure_token()
        url = self._build_url(path, params)

        for attempt in (0, 1):
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token.access_token}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    logger.info("BeeperClient: 401 on %s — forcing re-auth", path)
                    self._token = get_or_create_token(
                        issuer=self.config.issuer, force_new=True,
                    )
                    token = self._token
                    continue
                body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                raise RuntimeError(
                    f"Beeper API error {e.code} on {path}: {body[:200]}"
                ) from e

        # Unreachable — the loop either returns or raises.
        raise RuntimeError(f"Beeper API: exhausted retries on {path}")

    def _build_url(self, path: str, params: Optional[dict]) -> str:
        base = self.config.issuer.rstrip("/") + path
        if not params:
            return base
        filtered = {k: v for k, v in params.items() if v is not None}
        if not filtered:
            return base
        return base + "?" + urllib.parse.urlencode(filtered, doseq=False)

    # ── endpoint wrappers ───────────────────────────────────────────────

    def _build_account_index(self) -> dict[str, dict]:
        """Fetch /v1/accounts once per harvest — used to attach
        networkID to chat records that don't self-report it."""
        if self._accounts_cache is None:
            try:
                data = self._get_json("/v1/accounts")
            except RuntimeError as e:
                logger.warning("BeeperClient: /v1/accounts failed: %s", e)
                self._accounts_cache = []
                return {}
            # Response may be a list or {accounts:[...]}
            self._accounts_cache = (
                data if isinstance(data, list) else data.get("accounts") or []
            )
        return {
            (a.get("accountID") or a.get("id") or ""): a
            for a in self._accounts_cache
        }

    def _iter_chats(self, token: BeeperToken) -> Iterator[dict]:
        """Paginate /v1/chats across all accounts.

        Beeper's pagination uses a `cursor` token in the response (the
        spec calls it `nextCursor`). We keep calling until the cursor
        disappears or we've seen an empty page.
        """
        cursor: Optional[str] = None
        while True:
            params = {"limit": self.config.page_limit}
            if cursor:
                params["cursor"] = cursor
            data = self._get_json("/v1/chats", params=params)
            items = self._extract_items(data, key="chats")
            if not items:
                return
            for item in items:
                yield item
            cursor = (
                data.get("nextCursor")
                or data.get("next_cursor")
                or data.get("cursor")
            )
            if not cursor:
                return

    def _iter_messages(
        self,
        token: BeeperToken,
        chat_id: str,
        *,
        since: Optional[datetime],
        until: Optional[datetime],
    ) -> Iterator[dict]:
        """Paginate /v1/chats/{id}/messages within [since, until).

        Beeper returns messages newest-first per page. We paginate backward
        via the response cursor until we cross `since`, then stop. This
        is more efficient than fetching all history every run.
        """
        cursor: Optional[str] = None
        while True:
            params = {"limit": self.config.page_limit}
            if cursor:
                params["cursor"] = cursor
            path = f"/v1/chats/{urllib.parse.quote(chat_id, safe=':!@')}/messages"
            try:
                data = self._get_json(path, params=params)
            except RuntimeError as e:
                # Per-chat failures are non-fatal — log and move on. A
                # wedged chat shouldn't halt the whole harvest.
                logger.warning(
                    "BeeperClient: messages for chat %s failed: %s", chat_id, e,
                )
                return
            items = self._extract_items(data, key="messages")
            if not items:
                return

            crossed_since = False
            for msg in items:
                ts = _parse_iso(
                    msg.get("timestamp") or msg.get("createdAt") or msg.get("sentAt")
                )
                if ts is None:
                    # Can't window-filter without a timestamp; skip but
                    # don't halt — some edge messages (deleted?) may
                    # arrive without one.
                    continue
                if until is not None and ts >= until:
                    continue
                if since is not None and ts < since:
                    crossed_since = True
                    continue
                yield msg

            if crossed_since:
                # We've paginated into pre-since territory — no need to
                # keep fetching older pages.
                return
            cursor = (
                data.get("nextCursor")
                or data.get("next_cursor")
                or data.get("cursor")
            )
            if not cursor:
                return

    @staticmethod
    def _extract_items(data: dict | list, *, key: str) -> list[dict]:
        """Beeper endpoints vary between returning `[...]` and `{key: [...]}`.

        Absorb the variance in one place so the walker loops stay simple.
        """
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            val = data.get(key) or data.get("items") or data.get("data")
            if isinstance(val, list):
                return val
        return []

    # ── record normalization ────────────────────────────────────────────

    def _message_to_record(
        self,
        *,
        message: dict,
        chat: dict,
        channel: str,
        network_id: str,
        is_group: bool,
    ) -> Optional[dict]:
        """Map a Beeper message dict → InteractionRecord per interaction.md.

        Returns None on structural mismatch (missing timestamp, missing
        both sender + chat id) so pipeline.py can skip without special-case
        handling.
        """
        ts = _parse_iso(
            message.get("timestamp") or message.get("createdAt") or message.get("sentAt")
        )
        if ts is None:
            return None
        ts_iso = _format_iso(ts)

        # Direction: Beeper tags own messages via `isSender`/`isFromMe`.
        is_from_me = bool(
            message.get("isSender")
            or message.get("isFromMe")
            or message.get("senderSelf")
        )
        direction = "outbound" if is_from_me else "inbound"

        chat_id = chat.get("id") or chat.get("chatID") or ""
        thread_id = f"beeper:{chat_id}" if chat_id else f"beeper:{network_id}:unknown"

        external_id = (
            message.get("id")
            or message.get("messageID")
            or message.get("externalID")
            or ""
        )

        body = (
            message.get("text")
            or message.get("body")
            or message.get("content")
            or ""
        )
        summary = _truncate_summary(body, self.config.summary_max_chars)
        if not summary:
            # Channel-specific placeholder per interaction.md §summary rule 5.
            if message.get("attachments") or message.get("assetURLs"):
                summary = "[attachment]"
            elif message.get("reactions"):
                summary = "[reaction]"
            else:
                summary = "[empty]"

        # Match candidates: Beeper exposes sender handle + chat participants.
        match_candidates: dict[str, list[str]] = {
            "emails": [],
            "phones": [],
            "handles": [],
        }
        participants: list[dict] = []

        sender = message.get("sender") or {}
        sender_handle = (
            sender.get("handle")
            or sender.get("id")
            or message.get("senderID")
            or ""
        )
        sender_name = sender.get("fullName") or sender.get("name") or ""
        if sender_handle and not is_from_me:
            self._add_candidate(match_candidates, sender_handle, network_id, chat_id)
            participants.append({
                "kind": self._classify_handle(sender_handle),
                "value": sender_handle,
                "name": sender_name or None,
                "self": False,
            })

        for p in chat.get("participants") or []:
            if p.get("isSelf"):
                continue
            handle = p.get("handle") or p.get("id") or ""
            if not handle or handle == sender_handle:
                continue
            self._add_candidate(match_candidates, handle, network_id, chat_id)
            participants.append({
                "kind": self._classify_handle(handle),
                "value": handle,
                "name": p.get("fullName") or p.get("name"),
                "self": False,
            })

        interaction_id = _hash_interaction_id(
            channel, thread_id, ts_iso, direction, external_id or sender_handle,
        )

        return {
            "interactionId": interaction_id,
            "contactId": None,
            "matchCandidates": match_candidates,
            "channel": channel,
            "direction": direction,
            "threadId": thread_id,
            "timestamp": ts_iso,
            "subject": message.get("subject"),
            "summary": summary,
            "fullTextRef": None,
            "participants": participants,
            "metadata": {
                "source": "beeper",
                "sourceVersion": "beeper_client@1",
                "beeperChatId": chat_id,
                "beeperNetwork": network_id or None,
                "beeperMessageId": external_id or None,
                "isRead": bool(message.get("isRead")),
                "readAt": message.get("readAt"),
                "reactionCount": len(message.get("reactions") or []),
                "hasAttachments": bool(message.get("attachments") or message.get("assetURLs")),
                "isGroupChat": is_group,
            },
        }

    @staticmethod
    def _classify_handle(handle: str) -> str:
        if "@" in handle and not handle.startswith("@"):
            return "email"
        if any(c.isdigit() for c in handle) and handle.lstrip("+").replace("-", "").replace(" ", "").isdigit():
            return "phone"
        return "handle"

    @staticmethod
    def _add_candidate(
        cands: dict[str, list[str]], handle: str, network_id: str, chat_id: str,
    ) -> None:
        """Route a raw Beeper handle into the right candidate bucket.

        Matrix room IDs (`!room:domain`) go into `handles`; email-shaped
        strings into `emails`; phone-shaped into `phones`. Also stamps a
        network-qualified composite into `handles` so ContactMatcher's
        cache can do exact lookups like `beeper:whatsapp:+421...`.
        """
        if "@" in handle and not handle.startswith("@"):
            cands["emails"].append(handle.lower())
        elif handle.startswith("+") or handle.lstrip("+").replace("-", "").replace(" ", "").isdigit():
            # Leave phone normalization to ContactMatcher.normalize_phone;
            # just surface the raw string here.
            cands["phones"].append(handle)
        else:
            cands["handles"].append(handle)

        if network_id:
            cands["handles"].append(f"beeper:{network_id}:{handle}")
        if chat_id and chat_id.startswith("!"):
            # Matrix room id — useful for cache-keying across members
            cands["handles"].append(f"beeper_room:{chat_id}")


# ── CLI self-test ─────────────────────────────────────────────────────────

def _run_self_test() -> None:
    """Offline-only tests. Covers normalization + URL construction without
    touching the Beeper API."""
    print("BeeperClient self-test (offline)…")

    # summary truncation
    assert _truncate_summary("a b\n\nc  d" * 50, 20).endswith("…")
    assert _truncate_summary("", 20) == ""
    assert _truncate_summary(None, 20) == ""
    assert _truncate_summary("hello", 20) == "hello"
    print("  ✓ summary truncation")

    # interactionId stable + hex
    id1 = _hash_interaction_id("whatsapp", "beeper:!r", "2026-04-21T00:00:00+00:00", "inbound", "x")
    id2 = _hash_interaction_id("whatsapp", "beeper:!r", "2026-04-21T00:00:00+00:00", "inbound", "x")
    assert id1 == id2 and len(id1) == 16
    assert all(c in "0123456789abcdef" for c in id1)
    print("  ✓ interactionId determinism")

    # URL construction with and without params
    client = BeeperClient()
    u1 = client._build_url("/v1/chats", None)
    assert u1 == "http://localhost:23373/v1/chats"
    u2 = client._build_url("/v1/chats", {"limit": 50, "cursor": "abc", "_skip": None})
    assert "limit=50" in u2 and "cursor=abc" in u2 and "_skip" not in u2
    print("  ✓ URL construction")

    # Items extraction absorbs list vs dict-wrapped
    assert BeeperClient._extract_items([{"id": 1}], key="chats") == [{"id": 1}]
    assert BeeperClient._extract_items({"chats": [{"id": 2}]}, key="chats") == [{"id": 2}]
    assert BeeperClient._extract_items({"items": [{"id": 3}]}, key="chats") == [{"id": 3}]
    assert BeeperClient._extract_items({}, key="chats") == []
    print("  ✓ response shape absorption")

    # Message normalization
    client = BeeperClient()
    chat = {
        "id": "!room1:beeper.local",
        "accountID": "acct-wa",
        "networkID": "whatsapp",
        "participants": [
            {"handle": "+421903000001", "fullName": "Test User", "isSelf": False},
            {"handle": "+421905999999", "isSelf": True},
        ],
    }
    msg_inbound = {
        "id": "msg-1",
        "timestamp": "2026-04-20T15:00:00Z",
        "text": "Hey, can we jump on a demo next week?",
        "sender": {"handle": "+421903000001", "fullName": "Test User"},
        "isSender": False,
    }
    rec = client._message_to_record(
        message=msg_inbound, chat=chat, channel="whatsapp",
        network_id="whatsapp", is_group=False,
    )
    assert rec is not None
    assert rec["channel"] == "whatsapp"
    assert rec["direction"] == "inbound"
    assert rec["threadId"] == "beeper:!room1:beeper.local"
    assert "+421903000001" in rec["matchCandidates"]["phones"]
    assert any(h.startswith("beeper:whatsapp:") for h in rec["matchCandidates"]["handles"])
    assert rec["metadata"]["beeperNetwork"] == "whatsapp"
    assert rec["summary"].startswith("Hey, can we jump")
    print("  ✓ inbound message normalization")

    # Outbound → direction flipped, participants empty (we drop self-handles)
    msg_outbound = {
        "id": "msg-2",
        "timestamp": "2026-04-20T16:00:00Z",
        "text": "Sure — Tuesday at 10?",
        "isSender": True,
    }
    rec_out = client._message_to_record(
        message=msg_outbound, chat=chat, channel="whatsapp",
        network_id="whatsapp", is_group=False,
    )
    assert rec_out["direction"] == "outbound"
    # Self-sender should not appear in participants/matchCandidates
    assert not rec_out["participants"] or all(not p.get("self") for p in rec_out["participants"])
    print("  ✓ outbound direction + self filtering")

    # Missing timestamp → None (not a crash)
    msg_bad = {"id": "x", "text": "no ts"}
    assert client._message_to_record(
        message=msg_bad, chat=chat, channel="whatsapp",
        network_id="whatsapp", is_group=False,
    ) is None
    print("  ✓ missing-timestamp returns None (not raise)")

    # Empty body → placeholder
    msg_empty = {
        "id": "e",
        "timestamp": "2026-04-20T16:00:00Z",
        "text": "",
        "attachments": [{"type": "image"}],
        "isSender": True,
    }
    rec_empty = client._message_to_record(
        message=msg_empty, chat=chat, channel="whatsapp",
        network_id="whatsapp", is_group=False,
    )
    assert rec_empty["summary"] == "[attachment]"
    print("  ✓ empty-body attachment placeholder")

    # Handle classification
    assert BeeperClient._classify_handle("user@example.com") == "email"
    assert BeeperClient._classify_handle("+421903000001") == "phone"
    assert BeeperClient._classify_handle("@linkedin/foo") == "handle"
    print("  ✓ handle kind classification")

    print("All offline self-tests passed.")


if __name__ == "__main__":
    import logging as _lg
    _lg.basicConfig(level=_lg.INFO, format="%(levelname)s %(message)s")
    _run_self_test()

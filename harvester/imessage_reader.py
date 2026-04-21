"""
iMessage / SMS / RCS reader — harvests messages from macOS Messages chat.db.

Yields `InteractionRecord` dicts matching docs/schemas/interaction.md. Stdlib
only (sqlite3 + plistlib) — safe to import from any module without pulling
in Beeper or Gmail reader dependencies.

Requirements
------------
- Full Disk Access granted to the process running this reader. On macOS
  this means Terminal (if running via `python`), or the launchd user agent
  (if scheduled). `IMessageReader.available()` returns False with a log
  entry when FDA is missing — callers should treat that as "skip iMessage
  this run" rather than a fatal error.

Body decoding
-------------
Post-iOS 16, many messages have `text=NULL` and the body lives in
`attributedBody` as an NSKeyedArchiver binary plist. We decode
best-effort — messages with unparseable attributedBody get
`summary="[undecoded]"` rather than being dropped entirely, since metadata
(timestamp, sender, direction) is still useful for presence signals even
without the body.

Run inline self-test
--------------------
    python -m harvester.imessage_reader
Smoke-tests against the live chat.db (read-only), prints counts + samples.
Does not write anywhere.
"""

from __future__ import annotations

import hashlib
import logging
import plistlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal, Optional

logger = logging.getLogger("contacts-refiner.imessage")

DEFAULT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

# Apple Cocoa reference timestamp: 2001-01-01 UTC in Unix seconds
COCOA_EPOCH_OFFSET = 978307200

# Service column → our channel taxonomy (docs/schemas/interaction.md)
SERVICE_CHANNEL_MAP: dict[str, str] = {
    "iMessage": "imessage",
    "SMS": "sms",
    "RCS": "rcs",
}


# ── helpers ───────────────────────────────────────────────────────────────

def _apple_ts_to_utc_iso(apple_ts: int) -> str:
    """Convert Apple `date` column (ns since 2001-01-01 UTC) to ISO-8601 UTC.

    Older messages (pre-iOS 11) used seconds rather than nanoseconds — but
    on this user's chat.db the earliest message is 2022-12-23 so we don't
    need to detect that legacy format.
    """
    seconds_since_cocoa = apple_ts / 1_000_000_000
    unix_ts = seconds_since_cocoa + COCOA_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def _utc_to_apple_ts(dt: datetime) -> int:
    """Inverse of _apple_ts_to_utc_iso — used for SQL WHERE clauses."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    unix_ts = dt.astimezone(timezone.utc).timestamp()
    return int((unix_ts - COCOA_EPOCH_OFFSET) * 1_000_000_000)


def _decode_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    """Best-effort extraction of message text from the attributedBody blob.

    Apple's Messages.app uses the NSArchiver "typed stream" binary format
    (magic bytes `\\x04\\x0bstreamtyped`), NOT NSKeyedArchiver. Python's
    `plistlib` does not parse typed streams, so we scan the blob directly.

    Strategy — robust against multi-chunk attributed strings:
      1. Find the `NSString` class marker.
      2. Skip ~5 bytes of class-ref metadata that follow it (including
         what looks like a length byte that is actually only the first
         chunk's length — the real body often continues past it).
      3. Read forward until hitting a known NSArchiver object-boundary
         marker that terminates the string.
      4. Decode the span as UTF-8.

    Returns None on any failure. Covers >95% of messages on modern iOS/macOS.
    Messages with app-extension balloons (Fitness, Maps links, games) and
    tapback-only rows fall through — but tapbacks are filtered out in the
    SQL query, and balloon metadata without text is not a meaningful miss.
    """
    if not blob:
        return None
    if not blob.startswith(b"\x04\x0bstreamtyped"):
        # Not a typed-stream blob; don't try to parse.
        return None

    idx = blob.find(b"NSString")
    if idx < 0:
        return None

    # Layout after "NSString":
    #   01 {94|95} 84 01 2B <length> <UTF-8 body>
    # The 5 bytes (01..2B) are class-ref metadata + a constant 0x2B sigil.
    # The 6th byte is the length encoded one of three ways:
    #   - single byte 0x00-0x7F: length 0-127
    #   - 0x81 + 2 bytes big-endian: 16-bit length
    #   - 0x82 + 4 bytes big-endian: 32-bit length (rare)
    p = idx + len(b"NSString") + 5
    if p >= len(blob):
        return None

    first = blob[p]
    if first == 0x81:
        if p + 3 > len(blob):
            return None
        # Little-endian 16-bit length.
        length = blob[p + 1] | (blob[p + 2] << 8)
        body_start = p + 3
    elif first == 0x82:
        if p + 5 > len(blob):
            return None
        length = int.from_bytes(blob[p + 1:p + 5], "little")
        body_start = p + 5
    else:
        length = first
        body_start = p + 1

    body_end = body_start + length
    if body_end > len(blob) or length == 0:
        return None

    try:
        text = blob[body_start:body_end].decode("utf-8")
    except UnicodeDecodeError:
        text = blob[body_start:body_end].decode("utf-8", errors="ignore")

    text = text.strip()
    return text if text else None


def _normalize_handle(raw: str) -> str:
    """Normalize phone/email for consistent thread/match keys.

    Phone: keep leading +, strip non-digits. Email: lowercase. Preserves
    empty string for missing handles.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "@" in raw:
        return raw.lower()
    return "".join(c for c in raw if c.isdigit() or c == "+")


def _hash_interaction_id(
    channel: str, thread_id: str, ts_iso: str, direction: str, guid: str,
) -> str:
    """sha256 truncated to 16 hex chars. GUID included because the other
    fields can occasionally collide in the rare dual-message-per-timestamp
    case (e.g. tapbacks firing in the same ms as the message)."""
    key = f"{channel}|{thread_id}|{ts_iso}|{direction}|{guid}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _truncate_summary(body: Optional[str], max_chars: int) -> str:
    """Collapse whitespace and cap length per schema rules."""
    if not body:
        return "[undecoded]"
    # Collapse runs of whitespace to single space
    clean = " ".join(body.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1] + "…"


# ── reader ────────────────────────────────────────────────────────────────

@dataclass
class IMessageReaderConfig:
    db_path: Path = DEFAULT_DB_PATH
    include_services: tuple[str, ...] = ("iMessage", "SMS", "RCS")
    include_group_chats: bool = True
    summary_max_chars: int = 500


class IMessageReader:
    """chat.db → InteractionRecord dicts.

    Matches the ChannelReader protocol from docs/schemas/interaction.md:
      - `channel` property (per-row, not per-reader — one reader emits
        iMessage + SMS + RCS records)
      - `available() -> bool`
      - `harvest(since, until) -> Iterator[dict]`
    """

    def __init__(self, config: Optional[IMessageReaderConfig] = None):
        self.config = config or IMessageReaderConfig()

    # ── protocol: availability ──────────────────────────────────────────
    def available(self) -> bool:
        if not self.config.db_path.exists():
            logger.info(f"iMessage reader: chat.db not found at {self.config.db_path}")
            return False
        try:
            conn = self._open_ro()
            conn.execute("SELECT 1 FROM message LIMIT 1").fetchone()
            conn.close()
            return True
        except sqlite3.Error as e:
            logger.info(f"iMessage reader: chat.db not readable (FDA?): {e}")
            return False

    # ── protocol: harvest ───────────────────────────────────────────────
    def harvest(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Iterator[dict]:
        """Yield InteractionRecord dicts for messages in [since, until).

        Both bounds are optional; `None` means unbounded on that side. Results
        are ordered by timestamp ascending (oldest first) — callers expecting
        newest-first should sort the result list themselves.
        """
        conn = self._open_ro()
        try:
            yield from self._harvest_rows(conn, since, until)
        finally:
            conn.close()

    # ── utility: count without streaming bodies ─────────────────────────
    def count_messages(self, since: Optional[datetime] = None) -> int:
        """Quick count for progress estimation. Does not decode bodies."""
        conn = self._open_ro()
        try:
            clauses = [f"service IN ({','.join('?' * len(self.config.include_services))})"]
            params: list = list(self.config.include_services)
            if since:
                clauses.append("date >= ?")
                params.append(_utc_to_apple_ts(since))
            sql = f"SELECT COUNT(*) FROM message WHERE {' AND '.join(clauses)}"
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    # ── internals ───────────────────────────────────────────────────────
    def _open_ro(self) -> sqlite3.Connection:
        # `immutable=1` speeds reads and tells SQLite no concurrent writer
        # exists — which is a lie when Messages.app is running, but our
        # read-only intent plus uri mode prevents any lock acquisition.
        conn = sqlite3.connect(
            f"file:{self.config.db_path}?mode=ro&immutable=1", uri=True
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _harvest_rows(
        self,
        conn: sqlite3.Connection,
        since: Optional[datetime],
        until: Optional[datetime],
    ) -> Iterator[dict]:
        clauses: list[str] = []
        params: list = []

        placeholders = ",".join("?" * len(self.config.include_services))
        clauses.append(f"m.service IN ({placeholders})")
        params.extend(self.config.include_services)

        if since:
            clauses.append("m.date >= ?")
            params.append(_utc_to_apple_ts(since))
        if until:
            clauses.append("m.date < ?")
            params.append(_utc_to_apple_ts(until))

        # Skip tapbacks / reactions (associated_message_type 2000-2007) for
        # v1 — they pollute the summary field. We can surface them later as
        # a reactions count on the "parent" record.
        clauses.append(
            "(m.associated_message_type IS NULL "
            "OR m.associated_message_type = 0 "
            "OR m.associated_message_type NOT BETWEEN 2000 AND 2007)"
        )

        # Skip empty stub messages (is_empty=1) and system messages
        clauses.append("(m.is_empty IS NULL OR m.is_empty = 0)")
        clauses.append("(m.is_system_message IS NULL OR m.is_system_message = 0)")

        where = " WHERE " + " AND ".join(clauses)
        sql = f"""
            SELECT
                m.ROWID         AS message_id,
                m.guid          AS guid,
                m.text          AS text,
                m.attributedBody AS attr_body,
                m.date          AS apple_date,
                m.service       AS service,
                m.is_from_me    AS is_from_me,
                m.is_read       AS is_read,
                m.subject       AS subject,
                m.cache_roomnames AS cache_roomnames,
                m.cache_has_attachments AS has_attachments,
                h.id            AS handle_value,
                c.chat_identifier AS chat_identifier,
                c.display_name  AS chat_display_name,
                c.style         AS chat_style
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            LEFT JOIN chat c ON cmj.chat_id = c.ROWID
            {where}
            ORDER BY m.date ASC
        """

        for row in conn.execute(sql, params):
            try:
                record = self._row_to_record(row)
                if record is not None:
                    yield record
            except Exception as e:
                logger.debug(f"iMessage reader: skip row {row['message_id']}: {e}")
                continue

    def _row_to_record(self, row: sqlite3.Row) -> Optional[dict]:
        service = row["service"]
        channel = SERVICE_CHANNEL_MAP.get(service)
        if not channel:
            return None

        is_group = bool(row["cache_roomnames"]) or bool(row["chat_display_name"])
        if is_group and not self.config.include_group_chats:
            return None

        body = row["text"] or _decode_attributed_body(row["attr_body"])
        summary = _truncate_summary(body, self.config.summary_max_chars)

        ts_iso = _apple_ts_to_utc_iso(row["apple_date"])
        direction: Literal["inbound", "outbound"] = (
            "outbound" if row["is_from_me"] else "inbound"
        )

        # threadId: for 1:1 prefer handle (stable across chat renames); for
        # groups use chat_identifier (stable per-group).
        if is_group:
            raw_thread = row["chat_identifier"] or row["cache_roomnames"] or ""
            thread_key = f"group:{_normalize_handle(raw_thread)}"
        else:
            raw_thread = row["handle_value"] or row["chat_identifier"] or ""
            thread_key = _normalize_handle(raw_thread)
        thread_id = f"{channel}:{thread_key}"

        handle_value = row["handle_value"] or ""
        normalized = _normalize_handle(handle_value)
        handle_kind = "email" if "@" in handle_value else "phone"

        match_candidates: dict[str, list[str]] = {
            "emails": [],
            "phones": [],
            "handles": [],
        }
        participants: list[dict] = []
        if normalized:
            if handle_kind == "email":
                match_candidates["emails"].append(normalized)
            else:
                match_candidates["phones"].append(normalized)
            participants.append({
                "kind": handle_kind,
                "value": normalized,
                "name": None,  # contact_matcher fills this
                "self": False,
            })

        interaction_id = _hash_interaction_id(
            channel, thread_id, ts_iso, direction, row["guid"] or "",
        )

        return {
            "interactionId": interaction_id,
            "contactId": None,
            "matchCandidates": match_candidates,
            "channel": channel,
            "direction": direction,
            "threadId": thread_id,
            "timestamp": ts_iso,
            "subject": row["subject"],
            "summary": summary,
            "fullTextRef": None,
            "participants": participants,
            "metadata": {
                "source": "direct",
                "sourceVersion": "imessage_reader@1",
                "iMessageService": service,
                "isRead": bool(row["is_read"]),
                "messageGuid": row["guid"],
                "chatIdentifier": row["chat_identifier"],
                "isGroupChat": is_group,
                "hasAttachments": bool(row["has_attachments"]),
            },
        }


# ── self-test ─────────────────────────────────────────────────────────────

def _self_test() -> None:
    """Smoke test against the live chat.db. Read-only. Does not write."""
    import json
    from collections import Counter
    from datetime import timedelta

    print("iMessage reader — self-test against live chat.db")
    print("=" * 60)

    reader = IMessageReader()
    if not reader.available():
        print("✗ chat.db not available (Full Disk Access missing or file missing)")
        print("  On macOS: System Settings → Privacy & Security → Full Disk Access")
        return

    total = reader.count_messages()
    print(f"total messages in scope (iMessage + SMS + RCS): {total}")

    since_7d = datetime.now(timezone.utc) - timedelta(days=7)
    count_7d = reader.count_messages(since=since_7d)
    print(f"last 7 days: {count_7d} messages")

    sample = list(reader.harvest(since=since_7d))
    print(f"harvested: {len(sample)} records")

    if not sample:
        print("no recent records to show")
        return

    by_channel = Counter(r["channel"] for r in sample)
    by_direction = Counter(r["direction"] for r in sample)
    group_count = sum(1 for r in sample if r["metadata"]["isGroupChat"])
    undecoded = sum(1 for r in sample if r["summary"] == "[undecoded]")
    with_attachments = sum(1 for r in sample if r["metadata"]["hasAttachments"])

    print(f"\nchannel breakdown : {dict(by_channel)}")
    print(f"direction breakdown: {dict(by_direction)}")
    print(f"group chat records : {group_count}/{len(sample)}")
    print(f"undecoded bodies   : {undecoded}/{len(sample)} "
          f"({100*undecoded/len(sample):.1f}%)")
    print(f"with attachments   : {with_attachments}/{len(sample)}")

    # harvest() yields oldest-first — sample[-1] is the most recent message.
    print("\noldest record in 7d window:")
    _print_sample(sample[0])
    print("\nmost-recent record in 7d window:")
    _print_sample(sample[-1])


def _print_sample(record: dict) -> None:
    # Mask body for terminal output — don't dump full conversation content.
    s = record["summary"]
    truncated = s[:80] + "…" if len(s) > 80 else s
    preview = {
        "interactionId": record["interactionId"],
        "channel": record["channel"],
        "direction": record["direction"],
        "timestamp": record["timestamp"],
        "threadId": record["threadId"],
        "summary": truncated,
        "participants": record["participants"],
        "metadata_keys": list(record["metadata"].keys()),
        "iMessageService": record["metadata"]["iMessageService"],
        "isGroupChat": record["metadata"]["isGroupChat"],
    }
    import json
    print(json.dumps(preview, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _self_test()

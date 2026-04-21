"""
Omnichannel harvester — pipeline orchestrator.

Pulls InteractionRecords from channel readers (iMessage chat.db + Beeper
Desktop API today; Gmail added in a later sprint), matches each record
to a Google People resourceName, writes monthly-partitioned JSONL to
`data/interactions/YYYY-MM.jsonl` locally, then uploads to GCS.

Append-only semantics: re-runs dedupe by `interactionId` against the
existing partition, so overlapping windows are safe.

Entry points:
- `run_harvest(mode="incremental")` — default cadence, since=cursor, until=now
- `run_harvest(mode="reconcile", since_timedelta=timedelta(hours=24))` — re-pull
  the last N hours to catch late-delivery / reactions that an earlier
  incremental run missed
- `run_harvest(mode="backfill", backfill_sources=("beeper",))` — full history
  for a specific reader; used once per install to seed initial data

State files (all under `data/interactions/`):
- `cursor.json`             — last successful `until` per reader
- `interaction_match_cache.json` — handle → resourceName from ContactMatcher
- `interaction_unknowns.jsonl`   — records with no match; append-only review queue
- `YYYY-MM.jsonl`           — monthly partition, newest first within file
- `contact_kpis.json`       — ContactKPI rollup (written by score_interactions())

Emergency stop: honours `data/pipeline_paused.json` per the pattern used by
`entrypoint.py:_check_pause_flag` — same file, same shape, so the dashboard's
emergency-stop button stops this harvester too.

Run inline self-test (synthetic fixtures, no Beeper / chat.db needed):
    python -m harvester.pipeline
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Literal, Optional, Protocol

from config import DATA_DIR
from utils import upload_file_to_gcs
from harvester.contact_matcher import ContactMatcher, MatchCache, log_phone_parse_summary

logger = logging.getLogger("contacts-refiner.harvester.pipeline")


# ── constants ─────────────────────────────────────────────────────────────

INTERACTIONS_DIR = DATA_DIR / "interactions"
CURSOR_FILE = INTERACTIONS_DIR / "cursor.json"
MATCH_CACHE_FILE = INTERACTIONS_DIR / "interaction_match_cache.json"
UNKNOWNS_FILE = INTERACTIONS_DIR / "interaction_unknowns.jsonl"
CURSOR_SCHEMA_VERSION = 1
INCREMENTAL_OVERLAP_MINUTES = 5  # re-scan last N minutes to catch race-window edits
RECONCILE_DEFAULT_HOURS = 24
BACKFILL_EARLIEST = datetime(2015, 1, 1, tzinfo=timezone.utc)

Mode = Literal["incremental", "reconcile", "backfill"]


# ── reader protocol ───────────────────────────────────────────────────────

class ChannelReader(Protocol):
    """Matches the contract in docs/schemas/interaction.md §Channel reader."""

    def available(self) -> bool: ...
    def harvest(
        self, since: Optional[datetime], until: Optional[datetime],
    ) -> Iterator[dict]: ...


# ── cursor state ──────────────────────────────────────────────────────────

@dataclass
class CursorState:
    """Persistent `reader_name → last_until_ts` map.

    A successful harvest updates the cursor for each reader to its new
    `until`. Failures leave the previous cursor in place — so the next
    run replays from there.
    """
    cursors: dict[str, str] = field(default_factory=dict)
    schema_version: int = CURSOR_SCHEMA_VERSION

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CursorState":
        # Resolve at call time — tests rebind the module-level CURSOR_FILE.
        if path is None:
            path = CURSOR_FILE
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("CursorState load failed at %s: %s", path, e)
            return cls()
        if data.get("schema_version") != CURSOR_SCHEMA_VERSION:
            logger.warning(
                "CursorState schema mismatch at %s (file=%s, code=%s) — resetting",
                path, data.get("schema_version"), CURSOR_SCHEMA_VERSION,
            )
            return cls()
        return cls(cursors=dict(data.get("cursors", {})))

    def save(self, path: Optional[Path] = None) -> None:
        if path is None:
            path = CURSOR_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "updated": datetime.now(timezone.utc).isoformat(),
            "cursors": self.cursors,
        }
        path.write_text(json.dumps(payload, indent=2))

    def get(self, reader_name: str) -> Optional[datetime]:
        raw = self.cursors.get(reader_name)
        if not raw:
            return None
        try:
            s = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def set(self, reader_name: str, until: datetime) -> None:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        self.cursors[reader_name] = until.astimezone(timezone.utc).isoformat()


# ── pause flag ────────────────────────────────────────────────────────────

def is_harvester_paused() -> bool:
    """Same convention as entrypoint.py:_check_pause_flag.

    Fail-safe: if the pause file exists but is unreadable, assume PAUSED.
    Returns False only when the file is absent or explicitly `{"paused":false}`.
    """
    pause_file = DATA_DIR / "pipeline_paused.json"
    if not pause_file.exists():
        return False
    try:
        data = json.loads(pause_file.read_text(encoding="utf-8"))
        return bool(data.get("paused"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error(
            "Harvester: pipeline_paused.json unreadable (fail-safe PAUSED): %s", e,
        )
        return True


# ── partition writer ──────────────────────────────────────────────────────

# Public seam for out-of-module consumers (e.g. scripts/mcp_harvest_session.py).
# The leading-underscore names below remain the primary implementation so
# internal call sites don't churn; the public aliases promise a stable
# contract and show up as importable symbols. If a signature changes, update
# the alias at the same time or both paths will silently drift.

def _partition_path(ts: datetime, base: Optional[Path] = None) -> Path:
    if base is None:
        base = INTERACTIONS_DIR
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    utc_ts = ts.astimezone(timezone.utc)
    return base / f"{utc_ts.strftime('%Y-%m')}.jsonl"


def _existing_ids(partition: Path) -> set[str]:
    """Load interactionIds already present in `partition` (for dedup).

    Reads line-by-line so a malformed trailing line doesn't blow up the
    whole file. The worst case (malformed line) is: we re-append a
    record with a new id, not a silent drop.
    """
    if not partition.exists():
        return set()
    ids: set[str] = set()
    with partition.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = obj.get("interactionId")
            if iid:
                ids.add(iid)
    return ids


def _append_records(
    records_by_partition: dict[Path, list[dict]],
) -> dict[Path, int]:
    """Append each list of records to its monthly partition.

    Returns a `{partition: records_written}` map for logging.
    """
    written: dict[Path, int] = {}
    for partition, records in records_by_partition.items():
        if not records:
            continue
        partition.parent.mkdir(parents=True, exist_ok=True)
        with partition.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        written[partition] = len(records)
    return written


# ── unknowns queue ────────────────────────────────────────────────────────

def _append_unknown(record: dict, path: Optional[Path] = None) -> None:
    """Write one no-match record to the review queue."""
    if path is None:
        path = UNKNOWNS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# Public re-exports — see block comment at top of section.
partition_path_for = _partition_path
existing_partition_ids = _existing_ids
append_records = _append_records
append_unknown_record = _append_unknown


def process_record(
    record: dict,
    *,
    matcher: ContactMatcher,
    records_by_partition: dict[Path, list[dict]],
    existing_ids_cache: dict[Path, set[str]],
    seen_in_run: set[str],
    on_unknown: Callable[[dict], None] = _append_unknown,
) -> Literal["new", "dupe_in_run", "dupe_on_disk", "skipped_no_ts", "skipped_no_id"]:
    """Process one normalized InteractionRecord into a harvest run.

    Shared by `_run_single_reader` (HTTP path) and scripts/mcp_harvest_session.py
    (MCP path). Handles: intra-run dedup, disk dedup, timestamp window,
    contact match, unknown-queue routing, partition bucket append.

    Caller must: (a) count the returned state into its own stats; (b) pass
    the same `records_by_partition`, `existing_ids_cache`, `seen_in_run`
    across calls so dedup works; (c) invoke `append_records` at end to
    flush pending writes.

    This is the one place to change if the normalization→persistence
    contract evolves — both paths must stay in sync by going through here.
    """
    iid = record.get("interactionId")
    if not iid:
        return "skipped_no_id"
    if iid in seen_in_run:
        return "dupe_in_run"
    seen_in_run.add(iid)

    ts_raw = record.get("timestamp")
    if not ts_raw:
        return "skipped_no_ts"
    ts = _parse_ts(ts_raw)
    if ts is None:
        return "skipped_no_ts"

    partition = _partition_path(ts)
    if partition not in existing_ids_cache:
        existing_ids_cache[partition] = _existing_ids(partition)
    if iid in existing_ids_cache[partition]:
        return "dupe_on_disk"

    resolved = matcher.match(record)
    if resolved:
        record["contactId"] = resolved
    else:
        record["contactId"] = None
        on_unknown(record)

    records_by_partition.setdefault(partition, []).append(record)
    existing_ids_cache[partition].add(iid)
    return "new"


# ── contact snapshot loader ───────────────────────────────────────────────

def _load_latest_contacts() -> list[dict]:
    """Contacts snapshot for the matcher — use the latest backup if any.

    Falls back to the People API if no local backup exists. Matcher
    performance is fine on both.
    """
    from backup import get_latest_backup, load_backup
    backup_path = get_latest_backup()
    if backup_path:
        data = load_backup(backup_path)
        logger.info("Harvester: contacts from backup %s (n=%d)",
                    backup_path.name, len(data["contacts"]))
        return data["contacts"]
    # No backup — fetch live
    logger.warning("Harvester: no local backup, fetching contacts live")
    from auth import authenticate
    from api_client import PeopleAPIClient
    client = PeopleAPIClient(authenticate())
    contacts = client.get_all_contacts()
    logger.info("Harvester: contacts live (n=%d)", len(contacts))
    return contacts


def _load_linkedin_signals() -> dict[str, dict]:
    """Feed LinkedIn signals to the matcher so linkedin-handle rows resolve."""
    try:
        from followup_scorer import load_linkedin_signals
        return load_linkedin_signals()
    except Exception as e:
        logger.info("Harvester: no LinkedIn signals loaded: %s", e)
        return {}


# ── main run function ─────────────────────────────────────────────────────

@dataclass
class HarvestSummary:
    mode: Mode
    since: Optional[str]
    until: str
    readers_ran: list[str] = field(default_factory=list)
    readers_skipped: list[str] = field(default_factory=list)
    records_seen: int = 0
    records_new: int = 0
    records_matched: int = 0
    records_unmatched: int = 0
    records_by_channel: dict[str, int] = field(default_factory=dict)
    partitions_written: dict[str, int] = field(default_factory=dict)
    paused: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "since": self.since,
            "until": self.until,
            "readers_ran": self.readers_ran,
            "readers_skipped": self.readers_skipped,
            "records_seen": self.records_seen,
            "records_new": self.records_new,
            "records_matched": self.records_matched,
            "records_unmatched": self.records_unmatched,
            "records_by_channel": self.records_by_channel,
            "partitions_written": self.partitions_written,
            "paused": self.paused,
            "errors": self.errors,
        }


def run_harvest(
    *,
    mode: Mode = "incremental",
    since_timedelta: Optional[timedelta] = None,
    backfill_sources: Optional[Iterable[str]] = None,
    readers: Optional[dict[str, ChannelReader]] = None,
    contacts: Optional[list[dict]] = None,
    linkedin_signals: Optional[dict[str, dict]] = None,
    upload_to_gcs: bool = True,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> HarvestSummary:
    """Drive one harvest across all available readers.

    Pure business logic — no argparse. Callable from `main.py` or tests.

    Modes:
      - `incremental`: since = last cursor (or 24h ago if first run),
        until = now. Overlaps the last INCREMENTAL_OVERLAP_MINUTES to
        catch edits/reactions.
      - `reconcile`: since = now - since_timedelta (default 24h),
        until = now. Ignores cursors. Used by the daily launchd job.
      - `backfill`: since = BACKFILL_EARLIEST, until = now. Only runs
        readers in `backfill_sources`.
    """
    summary = HarvestSummary(mode=mode, since=None, until="")

    if is_harvester_paused():
        logger.warning("Harvester: paused via pipeline_paused.json — exiting cleanly")
        summary.paused = True
        summary.until = now_fn().isoformat()
        return summary

    # Resolve readers lazily — tests inject, prod builds live.
    if readers is None:
        readers = _build_default_readers()

    now = now_fn()
    cursors = CursorState.load()

    # Resolve contacts + LinkedIn signals once, share across readers.
    contacts = contacts if contacts is not None else _load_latest_contacts()
    linkedin_signals = (
        linkedin_signals
        if linkedin_signals is not None
        else _load_linkedin_signals()
    )

    match_cache = MatchCache.load(MATCH_CACHE_FILE)
    matcher = ContactMatcher(
        contacts, linkedin_signals=linkedin_signals, match_cache=match_cache,
    )

    # Accumulate per-partition so we do one file open per month per run.
    records_by_partition: dict[Path, list[dict]] = {}
    existing_ids_cache: dict[Path, set[str]] = {}

    for reader_name, reader in readers.items():
        if mode == "backfill" and backfill_sources and reader_name not in backfill_sources:
            continue

        if not reader.available():
            logger.info("Harvester: reader '%s' unavailable — skipping", reader_name)
            summary.readers_skipped.append(reader_name)
            continue

        since, until = _resolve_window(
            mode=mode, reader_name=reader_name, cursors=cursors,
            since_timedelta=since_timedelta, now=now,
        )
        if summary.since is None:
            summary.since = since.isoformat() if since else None
        summary.until = until.isoformat()

        logger.info(
            "Harvester: running reader=%s since=%s until=%s mode=%s",
            reader_name,
            since.isoformat() if since else "null",
            until.isoformat(),
            mode,
        )

        try:
            new_records = _run_single_reader(
                reader=reader,
                reader_name=reader_name,
                since=since,
                until=until,
                matcher=matcher,
                records_by_partition=records_by_partition,
                existing_ids_cache=existing_ids_cache,
                summary=summary,
            )
        except Exception as e:
            # Never let one reader's crash halt the whole harvest — log
            # and move on. The next run's cursor is unchanged for this
            # reader, so we'll retry the same window.
            logger.exception("Harvester: reader '%s' raised: %s", reader_name, e)
            summary.errors.append(f"{reader_name}: {e}")
            continue

        # Only advance the cursor on success.
        cursors.set(reader_name, until)
        summary.readers_ran.append(reader_name)
        logger.info(
            "Harvester: reader '%s' produced %d new records", reader_name, new_records,
        )

    # Flush accumulated records to disk + GCS.
    written = _append_records(records_by_partition)
    for partition, count in written.items():
        summary.partitions_written[partition.name] = count
        if upload_to_gcs:
            blob = f"data/interactions/{partition.name}"
            try:
                upload_file_to_gcs(partition, blob, "harvester")
            except Exception as e:
                logger.warning("Harvester: GCS upload failed for %s: %s", partition.name, e)
                summary.errors.append(f"gcs_upload[{partition.name}]: {e}")

    matcher.save_cache(MATCH_CACHE_FILE)
    cursors.save()
    log_phone_parse_summary()
    logger.info("Harvester summary: %s", summary.to_dict())
    return summary


def _resolve_window(
    *,
    mode: Mode,
    reader_name: str,
    cursors: CursorState,
    since_timedelta: Optional[timedelta],
    now: datetime,
) -> tuple[Optional[datetime], datetime]:
    """Compute [since, until) for one reader under the given mode.

    Returns `(since, until)`. `since=None` in backfill mode means the
    reader should go as far back as it can.
    """
    if mode == "backfill":
        return BACKFILL_EARLIEST, now

    if mode == "reconcile":
        delta = since_timedelta or timedelta(hours=RECONCILE_DEFAULT_HOURS)
        return now - delta, now

    # incremental
    last_cursor = cursors.get(reader_name)
    if last_cursor is None:
        # First run — look back 24h by default. Too short and we miss
        # history; too long and the first run takes forever.
        return now - timedelta(hours=24), now

    # Overlap N minutes to catch edits/reactions/read-receipts that
    # arrived between our last `until` and the message's actual final
    # state on the other end.
    overlap = timedelta(minutes=INCREMENTAL_OVERLAP_MINUTES)
    return last_cursor - overlap, now


def _run_single_reader(
    *,
    reader: ChannelReader,
    reader_name: str,
    since: Optional[datetime],
    until: datetime,
    matcher: ContactMatcher,
    records_by_partition: dict[Path, list[dict]],
    existing_ids_cache: dict[Path, set[str]],
    summary: HarvestSummary,
) -> int:
    """Harvest one reader, match+dedup each record, add to pending writes.

    Returns the number of new records (post-dedup) queued for this reader.
    Delegates per-record handling to the public `process_record` seam so
    the MCP path in scripts/mcp_harvest_session.py and this HTTP path
    can't drift on dedup / match / unknowns policy.
    """
    new_count = 0
    seen_in_run: set[str] = set()

    for record in reader.harvest(since=since, until=until):
        summary.records_seen += 1
        ch = record.get("channel") or reader_name
        summary.records_by_channel[ch] = summary.records_by_channel.get(ch, 0) + 1

        outcome = process_record(
            record,
            matcher=matcher,
            records_by_partition=records_by_partition,
            existing_ids_cache=existing_ids_cache,
            seen_in_run=seen_in_run,
        )
        if outcome == "new":
            new_count += 1
            summary.records_new += 1
            if record.get("contactId"):
                summary.records_matched += 1
            else:
                summary.records_unmatched += 1

    return new_count


def _parse_ts(value: str) -> Optional[datetime]:
    try:
        s = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_default_readers() -> dict[str, ChannelReader]:
    """Production readers — iMessage (chat.db) + Beeper (Desktop API).

    Gmail reader lands in a later sprint; the map lets us add it without
    touching run_harvest.
    """
    from harvester.beeper_client import BeeperClient
    from harvester.imessage_reader import IMessageReader
    return {
        "imessage": IMessageReader(),
        "beeper": BeeperClient(),
    }


# ── scoring bridge ────────────────────────────────────────────────────────

def score_interactions_cli(
    *,
    partitions_glob: str = "*.jsonl",
    out_path: Optional[Path] = None,
    upload_to_gcs: bool = True,
) -> dict:
    """Load all interaction partitions, derive ContactKPIs, persist JSON.

    Used by `main.py score-interactions`. Loads every `YYYY-MM.jsonl` in
    `data/interactions/`, groups by contactId, runs
    `scoring_signals.derive_all_kpis`, and writes
    `data/interactions/contact_kpis.json`.

    Unmatched records (contactId=null) are ignored — they have no
    resourceName to roll up under.
    """
    from harvester.scoring_signals import (
        derive_all_kpis, save_kpis_to_json,
    )
    from config import FOLLOWUP_BEEPER_KPI_FILE

    if out_path is None:
        out_path = FOLLOWUP_BEEPER_KPI_FILE

    records_by_contact: dict[str, list[dict]] = {}
    total = 0
    unmatched = 0
    for partition in sorted(INTERACTIONS_DIR.glob(partitions_glob)):
        # Skip contact_kpis.json / cursor.json / match_cache.json — they
        # match *.jsonl only, but be defensive.
        if not partition.name.endswith(".jsonl"):
            continue
        if partition.name in ("interaction_unknowns.jsonl",):
            continue
        with partition.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                rn = rec.get("contactId")
                if not rn:
                    unmatched += 1
                    continue
                records_by_contact.setdefault(rn, []).append(rec)

    logger.info(
        "score-interactions: loaded %d records across %d contacts (%d unmatched)",
        total, len(records_by_contact), unmatched,
    )

    kpis = derive_all_kpis(records_by_contact)
    save_kpis_to_json(kpis, out_path)

    if upload_to_gcs:
        try:
            upload_file_to_gcs(out_path, "data/interactions/contact_kpis.json", "harvester")
        except Exception as e:
            logger.warning("score-interactions: GCS upload failed: %s", e)

    return {
        "total_records": total,
        "unmatched_records": unmatched,
        "contacts_scored": len(kpis),
        "out_path": str(out_path),
    }


# ── CLI self-test ─────────────────────────────────────────────────────────

class _FakeReader:
    """Test reader that yields preset records deterministically."""

    def __init__(self, records: list[dict], available: bool = True):
        self._records = records
        self._available = available

    def available(self) -> bool:
        return self._available

    def harvest(
        self, since: Optional[datetime], until: Optional[datetime],
    ) -> Iterator[dict]:
        for r in self._records:
            ts = _parse_ts(r.get("timestamp") or "")
            if since and ts and ts < since:
                continue
            if until and ts and ts >= until:
                continue
            yield r


def _run_self_test() -> None:
    import tempfile
    print("pipeline self-test (offline, synthetic fixtures)…")

    # Redirect DATA_DIR for this test — can't easily monkey-patch config,
    # so use the global INTERACTIONS_DIR override via temp dir.
    with tempfile.TemporaryDirectory() as tmp:
        global INTERACTIONS_DIR, CURSOR_FILE, MATCH_CACHE_FILE, UNKNOWNS_FILE
        saved = (INTERACTIONS_DIR, CURSOR_FILE, MATCH_CACHE_FILE, UNKNOWNS_FILE)
        INTERACTIONS_DIR = Path(tmp) / "interactions"
        CURSOR_FILE = INTERACTIONS_DIR / "cursor.json"
        MATCH_CACHE_FILE = INTERACTIONS_DIR / "interaction_match_cache.json"
        UNKNOWNS_FILE = INTERACTIONS_DIR / "interaction_unknowns.jsonl"
        INTERACTIONS_DIR.mkdir(parents=True)

        try:
            _assert_basic_run()
            _assert_dedup()
            _assert_pause()
            _assert_cursor_advance()
            _assert_reader_crash_isolated()
        finally:
            INTERACTIONS_DIR, CURSOR_FILE, MATCH_CACHE_FILE, UNKNOWNS_FILE = saved

    print("All pipeline self-tests passed.")


def _assert_basic_run() -> None:
    contacts = [
        {"resourceName": "people/c1",
         "names": [{"displayName": "Alice Test"}],
         "emailAddresses": [{"value": "alice@example.com"}],
         "phoneNumbers": []},
        {"resourceName": "people/c2",
         "names": [{"displayName": "Bob Test"}],
         "emailAddresses": [],
         "phoneNumbers": [{"value": "+421903111111"}]},
    ]
    ts = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    fake_records = [
        {
            "interactionId": "aa11bb22cc33dd44",
            "contactId": None,
            "matchCandidates": {"emails": ["alice@example.com"], "phones": [], "handles": []},
            "channel": "gmail", "direction": "inbound",
            "threadId": "gmail:t1", "timestamp": ts.isoformat(),
            "subject": "demo", "summary": "hi",
            "participants": [], "metadata": {"source": "gmail"},
        },
        {
            "interactionId": "ee55ff66aa77bb88",
            "contactId": None,
            "matchCandidates": {"emails": [], "phones": ["+421903111111"], "handles": []},
            "channel": "whatsapp", "direction": "outbound",
            "threadId": "beeper:!room1", "timestamp": ts.isoformat(),
            "subject": None, "summary": "ping",
            "participants": [], "metadata": {"source": "beeper"},
        },
    ]
    readers = {"fake": _FakeReader(fake_records)}
    summary = run_harvest(
        mode="incremental", readers=readers,
        contacts=contacts, linkedin_signals={},
        upload_to_gcs=False,
    )
    assert summary.records_new == 2, summary
    assert summary.records_matched == 2, summary
    assert summary.readers_ran == ["fake"], summary
    partition = _partition_path(ts)
    assert partition.exists(), partition
    lines = partition.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, lines
    first = json.loads(lines[0])
    assert first["contactId"] in ("people/c1", "people/c2")
    print("  ✓ basic run writes matched records to monthly partition")


def _assert_dedup() -> None:
    contacts = [{"resourceName": "people/c1",
                 "names": [{"displayName": "Alice Test"}],
                 "emailAddresses": [{"value": "alice@example.com"}],
                 "phoneNumbers": []}]
    # Use a timestamp that lands inside a 1h reconcile window anchored at `now`.
    ts = datetime.now(timezone.utc) - timedelta(minutes=5)
    rec = {
        "interactionId": "dedup1234567890a",
        "contactId": None,
        "matchCandidates": {"emails": ["alice@example.com"], "phones": [], "handles": []},
        "channel": "gmail", "direction": "inbound",
        "threadId": "gmail:t2", "timestamp": ts.isoformat(),
        "subject": "x", "summary": "y",
        "participants": [], "metadata": {"source": "gmail"},
    }
    readers = {"fake": _FakeReader([rec, rec])}  # duplicate intra-run
    summary = run_harvest(
        mode="reconcile", since_timedelta=timedelta(hours=1), readers=readers,
        contacts=contacts, linkedin_signals={}, upload_to_gcs=False,
    )
    assert summary.records_new == 1, f"intra-run dedup: {summary}"

    # Now re-run — should dedup against existing partition
    readers2 = {"fake": _FakeReader([rec])}
    summary2 = run_harvest(
        mode="reconcile", since_timedelta=timedelta(hours=1), readers=readers2,
        contacts=contacts, linkedin_signals={}, upload_to_gcs=False,
    )
    assert summary2.records_new == 0, f"cross-run dedup: {summary2}"
    print("  ✓ dedup (intra-run + cross-run)")


def _assert_pause() -> None:
    pause_path = DATA_DIR / "pipeline_paused.json"
    pause_path.write_text(json.dumps({"paused": True}))
    try:
        summary = run_harvest(
            mode="incremental", readers={}, contacts=[],
            linkedin_signals={}, upload_to_gcs=False,
        )
        assert summary.paused is True, summary
        print("  ✓ pause flag honoured")
    finally:
        pause_path.unlink(missing_ok=True)


def _assert_cursor_advance() -> None:
    contacts = [{"resourceName": "people/c1",
                 "names": [{"displayName": "Alice"}],
                 "emailAddresses": [{"value": "alice@example.com"}],
                 "phoneNumbers": []}]
    ts = datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc)
    readers = {"fake": _FakeReader([{
        "interactionId": "cursor1234567890x",
        "contactId": None,
        "matchCandidates": {"emails": ["alice@example.com"], "phones": [], "handles": []},
        "channel": "gmail", "direction": "inbound",
        "threadId": "gmail:c1", "timestamp": ts.isoformat(),
        "subject": None, "summary": "",
        "participants": [], "metadata": {"source": "gmail"},
    }])}
    fixed_now = datetime(2026, 4, 21, 11, 0, tzinfo=timezone.utc)
    run_harvest(
        mode="incremental", readers=readers,
        contacts=contacts, linkedin_signals={}, upload_to_gcs=False,
        now_fn=lambda: fixed_now,
    )
    state = CursorState.load(CURSOR_FILE)
    assert state.get("fake") == fixed_now, state.cursors
    print("  ✓ cursor advanced to `until`")


def _assert_reader_crash_isolated() -> None:
    class _CrashReader:
        def available(self):
            return True
        def harvest(self, since, until):
            yield {"interactionId": "x", "timestamp": "2026-04-21T12:00:00+00:00",
                   "channel": "x", "direction": "inbound", "threadId": "x",
                   "matchCandidates": {"emails": [], "phones": [], "handles": []},
                   "participants": [], "metadata": {}}
            raise RuntimeError("simulated reader crash")

    contacts = []
    summary = run_harvest(
        mode="incremental", readers={"crash": _CrashReader()},
        contacts=contacts, linkedin_signals={}, upload_to_gcs=False,
    )
    assert "crash" in [e.split(":")[0] for e in summary.errors], summary.errors
    assert "crash" not in summary.readers_ran
    print("  ✓ reader crash isolated (logged + recorded, pipeline continues)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _run_self_test()

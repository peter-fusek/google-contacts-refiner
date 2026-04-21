#!/usr/bin/env python3
"""
One-shot harvest from Beeper MCP tool output into the interaction pipeline.

Bridge for the "supervised in-session" harvest pattern: a Claude Code
operator (or anyone with access to Beeper Desktop's MCP server) calls
`mcp__beeper__search_chats` + `mcp__beeper__list_messages`, saves the
results as a single JSON payload in the shape below, then runs this
script to normalize → match → write → upload.

No scheduled jobs; no background daemon; every run is operator-visible.
Complements `harvester/pipeline.py`'s `run_harvest()` — same destination
artifacts (`data/interactions/YYYY-MM.jsonl`, GCS), same dedup
semantics. A full `harvest-messages --incremental` run over Beeper HTTP
would produce compatible records, so the two paths can coexist.

Input payload shape:
    {
      "harvestedAt": "ISO-8601 UTC",
      "source": "string",
      "chats": [
        {
          "chatID": "!room:beeper.local",
          "title": "Display name",
          "isGroup": bool,
          "participants": [{"handle": "...", "name": "...", "isSelf": false}, ...],
          "networkHint": "whatsapp" | "linkedin" | "signal" | ...,
          "messages": [
            {"id": "...", "timestamp": "ISO", "isSender": bool,
             "senderName": "...", "text": "..."},
            ...
          ]
        },
        ...
      ]
    }

Usage:
    uv run python scripts/mcp_harvest_session.py /tmp/mcp_harvest_2026-04-21.json
    uv run python scripts/mcp_harvest_session.py --dry-run /tmp/....json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR  # noqa: E402
from harvester.beeper_client import (  # noqa: E402
    NETWORK_CHANNEL_MAP,
    BeeperClient,
    BeeperClientConfig,
)
from harvester.contact_matcher import (  # noqa: E402
    ContactMatcher,
    MatchCache,
    log_phone_parse_summary,
)
from harvester.pipeline import (  # noqa: E402
    MATCH_CACHE_FILE,
    UNKNOWNS_FILE,
    _append_records,
    _append_unknown,
    _existing_ids,
    _parse_ts,
    _partition_path,
    is_harvester_paused,
)
from utils import upload_file_to_gcs  # noqa: E402


def _load_contacts() -> list[dict]:
    from backup import get_latest_backup, load_backup
    path = get_latest_backup()
    if not path:
        raise SystemExit("No backup found — run `python main.py backup` first")
    data = load_backup(path)
    print(f"  contacts: {len(data['contacts'])} from {path.name}")
    return data["contacts"]


def _load_linkedin_signals() -> dict[str, dict]:
    try:
        from followup_scorer import load_linkedin_signals
        return load_linkedin_signals()
    except Exception:
        return {}


def mcp_message_to_record(
    *,
    message: dict,
    chat: dict,
    client: BeeperClient,
) -> dict | None:
    """Adapt a Beeper MCP message into the shape BeeperClient._message_to_record expects.

    Reusing the HTTP client's normalizer keeps a single source of truth
    for InteractionRecord shape — no chance of MCP-path records drifting
    from HTTP-path records.
    """
    is_from_me = bool(message.get("isSender"))

    sender_handle = ""
    sender_name = message.get("senderName", "")
    if not is_from_me:
        # Pick the first non-self participant as sender fallback when MCP
        # doesn't surface a raw handle.
        for p in chat.get("participants", []):
            if not p.get("isSelf"):
                sender_handle = p.get("handle", "")
                sender_name = sender_name or p.get("name", "")
                break

    http_shaped = {
        "id": message.get("id"),
        "timestamp": message.get("timestamp"),
        "text": message.get("text"),
        "sender": {"handle": sender_handle, "fullName": sender_name},
        "isSender": is_from_me,
    }

    chat_shaped = {
        "id": chat.get("chatID"),
        "accountID": chat.get("networkHint", "") or "",
        "networkID": chat.get("networkHint", "") or "",
        "participants": chat.get("participants") or [],
        "isGroupChat": chat.get("isGroup", False),
    }

    network_id = (chat.get("networkHint") or "").lower()
    channel = NETWORK_CHANNEL_MAP.get(network_id, network_id or "beeper")

    record = client._message_to_record(
        message=http_shaped,
        chat=chat_shaped,
        channel=channel,
        network_id=network_id,
        is_group=bool(chat.get("isGroup")),
    )
    if record is None:
        return None

    # Tag so rollbacks and Session 3 reviewers can tell MCP-sourced rows
    # apart from future HTTP-sourced rows without parsing the id.
    record["metadata"]["source"] = "beeper-mcp-session"
    record["metadata"]["sourceVersion"] = "mcp_harvest_session@1"
    return record


def harvest(payload: dict, *, dry_run: bool, upload: bool) -> dict:
    if is_harvester_paused():
        print("⏸  pipeline_paused.json is set — exiting without writes")
        return {"paused": True}

    print(f"payload from: {payload.get('source', '?')}  "
          f"harvested_at: {payload.get('harvestedAt', '?')}")

    contacts = _load_contacts()
    linkedin_signals = _load_linkedin_signals()
    match_cache = MatchCache.load(MATCH_CACHE_FILE)
    matcher = ContactMatcher(
        contacts, linkedin_signals=linkedin_signals, match_cache=match_cache,
    )

    client = BeeperClient(BeeperClientConfig())

    records_by_partition: dict[Path, list[dict]] = {}
    existing_ids_cache: dict[Path, set[str]] = {}

    seen_in_run: set[str] = set()
    stats = {
        "chats": len(payload.get("chats", [])),
        "messages_in": 0,
        "records_normalized": 0,
        "skipped_already_exists": 0,
        "skipped_intra_run_dupe": 0,
        "skipped_missing_ts": 0,
        "matched": 0,
        "unmatched": 0,
        "by_channel": {},
    }

    for chat in payload.get("chats", []):
        for message in chat.get("messages", []):
            stats["messages_in"] += 1

            record = mcp_message_to_record(
                message=message, chat=chat, client=client,
            )
            if record is None:
                stats["skipped_missing_ts"] += 1
                continue

            stats["records_normalized"] += 1
            ch = record.get("channel") or "unknown"
            stats["by_channel"][ch] = stats["by_channel"].get(ch, 0) + 1

            iid = record["interactionId"]
            if iid in seen_in_run:
                stats["skipped_intra_run_dupe"] += 1
                continue
            seen_in_run.add(iid)

            ts = _parse_ts(record["timestamp"])
            partition = _partition_path(ts)
            if partition not in existing_ids_cache:
                existing_ids_cache[partition] = _existing_ids(partition)
            if iid in existing_ids_cache[partition]:
                stats["skipped_already_exists"] += 1
                continue

            resolved = matcher.match(record)
            if resolved:
                record["contactId"] = resolved
                stats["matched"] += 1
            else:
                record["contactId"] = None
                stats["unmatched"] += 1
                if not dry_run:
                    _append_unknown(record)

            records_by_partition.setdefault(partition, []).append(record)
            existing_ids_cache[partition].add(iid)

    if dry_run:
        print("\n-- dry-run: not writing or uploading --")
        for partition, records in records_by_partition.items():
            print(f"  would write {len(records)} records to {partition.name}")
            for r in records[:3]:
                print(f"    · [{r['channel']}] [{r['direction']}] "
                      f"{r['timestamp'][:19]} → {r.get('contactId') or 'unmatched'}"
                      f"  {r['summary'][:60]!r}")
        return {"dry_run": True, **stats}

    written = _append_records(records_by_partition)
    for partition, count in written.items():
        print(f"  wrote {count} records to {partition}")
        if upload:
            try:
                upload_file_to_gcs(
                    partition, f"data/interactions/{partition.name}", "mcp-harvest",
                )
            except Exception as e:
                print(f"  ! GCS upload failed for {partition.name}: {e}")

    matcher.save_cache(MATCH_CACHE_FILE)
    if upload:
        try:
            upload_file_to_gcs(
                MATCH_CACHE_FILE,
                "data/interactions/interaction_match_cache.json",
                "mcp-harvest",
            )
        except Exception as e:
            print(f"  ! match_cache upload failed: {e}")

    log_phone_parse_summary()
    return {"dry_run": False, **stats, "partitions_written": {
        p.name: c for p, c in written.items()
    }}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("payload", help="Path to JSON payload from MCP collection")
    ap.add_argument("--dry-run", action="store_true",
                    help="Normalize + match but don't write or upload")
    ap.add_argument("--no-upload", action="store_true",
                    help="Write locally but skip GCS upload")
    args = ap.parse_args()

    payload_path = Path(args.payload)
    if not payload_path.exists():
        print(f"error: payload not found at {payload_path}", file=sys.stderr)
        return 1

    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    print("═══════════════════════════════════════════════════════════════")
    print("  Beeper MCP Session Harvest")
    print(f"  mode: {'dry-run' if args.dry_run else 'apply'}  "
          f"upload: {'off' if args.no_upload else 'on'}")
    print(f"  time: {datetime.now(timezone.utc).isoformat()}")
    print("═══════════════════════════════════════════════════════════════")

    result = harvest(
        payload, dry_run=args.dry_run, upload=not args.no_upload,
    )

    print()
    print("── summary ──")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

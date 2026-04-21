"""
Omnichannel biography block — Phase 5 CRM sync extension.

Consumes `ContactKPI` rollups (from `harvester/scoring_signals.py`) and
renders a compact, metadata-only block that lives inside Google Contacts
biographies alongside existing `── CRM Notes`, `── Last Interaction`,
`── FollowUp Prompt` blocks.

Privacy red lines (non-negotiable):
  - **No message content** — ever. Only counts, dates, channel, side.
  - Block has a hard upper bound (~250 chars) to avoid bloating contacts.
  - Skipped for own-company (Instarea) and personal/family contacts in
    the integration patch (see `docs/patches/crm-sync-omnichannel.md`).

Operation:
  1. `build_block(kpi)` — pure, returns the multi-line block.
  2. `strip_block(bio)` — removes any existing Omnichannel block.
  3. `merge_into_biography(bio, block)` — produces new biography.
  4. `should_update(bio, new_block)` — diff check; returns False when
     no-op (skip API call entirely).
  5. `backup_biographies(...)` / `restore_biographies(...)` — for safe
     rollback, honouring the global deletion policy (soft-only).

Zero kernel dependencies. Self-test runs on synthetic `ContactKPI` data,
no Google People API calls.

Run inline self-test:
    python -m harvester.crm_omnichannel
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from harvester.scoring_signals import ContactKPI

logger = logging.getLogger("contacts-refiner.crm_omnichannel")

# Marker prefix for detection. Full header is built with date stamp:
#   ── Omnichannel (auto · 2026-04-21) ──
# Using a prefix lets `strip_block` match any prior Omnichannel block
# regardless of when it was written, keeping diff-checks precise.
OMNICHANNEL_MARKER = "── Omnichannel (auto"
OMNICHANNEL_END = "── End Omnichannel ──"

# Compact channel labels for the biography — scannable at a glance in the
# Google Contacts UI, mobile Contacts app, etc. Keep ≤4 chars each.
CHANNEL_ABBREV: dict[str, str] = {
    "whatsapp": "WA",
    "signal": "Sig",
    "messenger": "Msg",
    "instagram": "IG",
    "telegram": "TG",
    "linkedin_dm": "LI",
    "imessage": "iMsg",
    "sms": "SMS",
    "rcs": "RCS",
    "slack": "Sl",
    "discord": "Dc",
    "twitter": "X",
    "gmail": "Email",
    "calendar": "Cal",
    "call_facetime": "FT",
    "call_cellular": "Call",
}

# Hard cap on block char count — defensive against KPI bugs producing
# long strings. Biography field cap is ~10k on Google People, but polluting
# with a giant auto-block is rude to the user.
MAX_BLOCK_CHARS = 250


# ── block render ──────────────────────────────────────────────────────────

def _abbrev(channel: Optional[str]) -> str:
    if not channel:
        return "?"
    return CHANNEL_ABBREV.get(channel, channel[:4])


def _format_date(iso_ts: Optional[str]) -> Optional[str]:
    if not iso_ts:
        return None
    # Parse flexibly and render as YYYY-MM-DD (compact, locale-neutral).
    try:
        s = iso_ts.replace("Z", "+00:00") if iso_ts.endswith("Z") else iso_ts
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _days_since(iso_ts: Optional[str], as_of: datetime) -> Optional[int]:
    if not iso_ts:
        return None
    try:
        s = iso_ts.replace("Z", "+00:00") if iso_ts.endswith("Z") else iso_ts
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = as_of.astimezone(timezone.utc) - dt.astimezone(timezone.utc)
    return max(0, delta.days)


def build_block(
    kpi: ContactKPI, *, as_of: Optional[datetime] = None,
) -> str:
    """Render a ContactKPI as a fenced Omnichannel block.

    Block shape (example):

        ── Omnichannel (auto · 2026-04-21) ──
        Primary: WA · 30d: 12 msgs · 4 channels
        Last heard: 2026-04-20 WA · Awaiting: my reply (2d)
        ── End Omnichannel ──

    Empty-activity contacts get a terse two-line block so the marker is
    still discoverable and the diff-checker works symmetrically:

        ── Omnichannel (auto · 2026-04-21) ──
        No recent activity across messaging channels.
        ── End Omnichannel ──
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    date_stamp = as_of.strftime("%Y-%m-%d")

    header = f"{OMNICHANNEL_MARKER} · {date_stamp}) ──"
    # (OMNICHANNEL_MARKER already ends with an unmatched `(` — the closing
    # paren + date + trailing dashes complete the fence here.)

    # Detect "no activity" — either no windows at all, or all windows empty
    w30 = kpi.windows.get("30d") if kpi.windows else None
    activity_count = (w30.messages_in + w30.messages_out) if w30 else 0
    if activity_count == 0 and not kpi.last_inbound_ever_ts and not kpi.last_outbound_ever_ts:
        body = "No recent activity across messaging channels."
    else:
        body = _compose_active_body(kpi, w30, as_of)

    block = "\n".join([header, body, OMNICHANNEL_END])

    # Defensive: truncate if we somehow overshoot the cap.
    if len(block) > MAX_BLOCK_CHARS:
        truncated_body = body[: MAX_BLOCK_CHARS - len(header) - len(OMNICHANNEL_END) - 20]
        block = "\n".join([header, truncated_body + "…", OMNICHANNEL_END])
    return block


def _compose_active_body(kpi: ContactKPI, w30, as_of: datetime) -> str:
    """Two-line body for contacts with activity signals."""
    primary = _abbrev(kpi.channel_primary)
    total_30d = (w30.messages_in + w30.messages_out) if w30 else 0
    channels_30d = len(w30.channels) if w30 else 0

    line1_parts = [f"Primary: {primary}"]
    if total_30d > 0:
        line1_parts.append(f"30d: {total_30d} msgs")
    if channels_30d >= 1:
        line1_parts.append(f"{channels_30d} channel{'s' if channels_30d > 1 else ''}")
    line1 = " · ".join(line1_parts)

    # Line 2: last-heard + awaiting-reply
    line2_parts = []

    last_in_date = _format_date(kpi.last_inbound_ever_ts)
    if last_in_date:
        ch_hint = f" {primary}" if kpi.channel_primary else ""
        line2_parts.append(f"Last heard: {last_in_date}{ch_hint}")

    if kpi.last_awaiting_reply_side == "mine":
        # They wrote me. How long ago?
        days = _days_since(kpi.last_inbound_ever_ts, as_of) if kpi.last_inbound_ever_ts else None
        suffix = f" ({days}d)" if days is not None else ""
        line2_parts.append(f"Awaiting: my reply{suffix}")
    elif kpi.last_awaiting_reply_side == "theirs":
        days = _days_since(kpi.last_outbound_ever_ts, as_of) if kpi.last_outbound_ever_ts else None
        suffix = f" ({days}d)" if days is not None else ""
        line2_parts.append(f"Awaiting: their reply{suffix}")

    if kpi.stale_sent_count >= 3:
        line2_parts.append(f"Stale sent: {kpi.stale_sent_count}")

    if not line2_parts:
        return line1
    line2 = " · ".join(line2_parts)
    return f"{line1}\n{line2}"


# ── biography merge ───────────────────────────────────────────────────────

def strip_block(biography: str) -> str:
    """Remove any existing Omnichannel block from a biography string.

    Block identified by `OMNICHANNEL_MARKER` header; terminated by explicit
    `OMNICHANNEL_END`, or defensively by the next `──` header, or a blank
    line. Preserves all surrounding content (other marker blocks, user
    free-text) verbatim.
    """
    if OMNICHANNEL_MARKER not in biography:
        return biography

    lines = biography.split("\n")
    out: list[str] = []
    in_block = False
    for line in lines:
        if OMNICHANNEL_MARKER in line and not in_block:
            in_block = True
            continue
        if in_block:
            if OMNICHANNEL_END in line:
                in_block = False
                continue
            # Defensive: if we hit the next marker block without seeing End
            if line.strip().startswith("──"):
                in_block = False
                out.append(line)
                continue
            # Defensive: blank line also ends the block
            if not line.strip():
                in_block = False
                continue
            # Skip content lines inside the block
            continue
        out.append(line)

    # Normalize trailing whitespace but preserve leading content
    return "\n".join(out).rstrip() + ("\n" if biography.endswith("\n") else "")


def merge_into_biography(biography: str, block: str) -> str:
    """Produce a new biography with `block` inserted / replacing prior block.

    Insertion position: after the last existing marker block (same rule as
    crm_sync._insert_crm_block) so user free-text stays at the bottom.
    """
    base = strip_block(biography)

    if not base.strip():
        return block

    lines = base.split("\n")
    last_marker_line = -1
    in_block = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("──"):
            # A marker header line (or an end marker like "── End X ──").
            # Track as the last-seen marker line regardless of role.
            last_marker_line = i
            in_block = not in_block and "End" not in s
        elif in_block:
            # Content line within a marker block counts as part of it.
            last_marker_line = i
        elif s.startswith("──") is False and last_marker_line >= 0 and in_block:
            # Non-marker line within a block
            last_marker_line = i

    if last_marker_line >= 0:
        before = "\n".join(lines[: last_marker_line + 1])
        after_lines = lines[last_marker_line + 1:]
        after = "\n".join(after_lines).strip()
        if after:
            return f"{before}\n\n{block}\n\n{after}"
        return f"{before}\n\n{block}"

    # No existing markers — prepend
    return f"{block}\n\n{base.strip()}"


def should_update(existing: str, new_block: str) -> bool:
    """True iff applying `new_block` would change the biography.

    Diff-based: if the existing biography already has an equivalent
    Omnichannel block, the API call is a waste and we skip it. Returns
    False only when the resulting biography would be identical.
    """
    if not existing and not new_block:
        return False
    proposed = merge_into_biography(existing, new_block)
    return proposed.strip() != (existing or "").strip()


# ── backup / restore ──────────────────────────────────────────────────────

def backup_biographies(
    contacts: list[dict], path: Path, *, note: Optional[str] = None,
) -> None:
    """Snapshot the current biography of every contact to a JSON file.

    Written before any biography write. Supports `restore_biographies` for
    rollback. Honours the global deletion policy — this is the reversibility
    mechanism.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    biographies: dict[str, str] = {}
    for c in contacts:
        rn = c.get("resourceName")
        if not rn:
            continue
        bios = c.get("biographies") or []
        if bios:
            biographies[rn] = bios[0].get("value", "")
        else:
            biographies[rn] = ""
    payload = {
        "schema_version": 1,
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "count": len(biographies),
        "biographies": biographies,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info(f"Backed up {len(biographies)} biographies → {path}")


def load_backup(path: Path) -> dict[str, str]:
    """Inverse of backup_biographies. Returns resourceName → biography."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError(
            f"Biography backup schema mismatch at {path} "
            f"(got {data.get('schema_version')}, expected 1)"
        )
    return dict(data.get("biographies", {}))


# ── self-test ─────────────────────────────────────────────────────────────

def _run_self_test() -> None:
    from harvester.scoring_signals import ContactKPI, WindowStats

    print("Running crm_omnichannel self-test…")
    as_of = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)

    # Case A: business-hot multi-channel contact, awaiting my reply
    kpi_a = ContactKPI(
        resourceName="people/cA",
        windows={"30d": WindowStats(
            messages_in=8, messages_out=3,
            channels=["whatsapp", "linkedin_dm", "imessage"],
            last_inbound_ts="2026-04-20T14:00:00+00:00",
            last_outbound_ts="2026-04-19T09:00:00+00:00",
            business_hours_ratio=0.8, business_keyword_hits=4,
        )},
        last_awaiting_reply_side="mine",
        channel_primary="whatsapp",
        last_inbound_ever_ts="2026-04-20T14:00:00+00:00",
        last_outbound_ever_ts="2026-04-19T09:00:00+00:00",
        stale_sent_count=0,
    )
    block_a = build_block(kpi_a, as_of=as_of)
    print("Case A (business-hot, awaiting my reply):")
    print(block_a)
    assert OMNICHANNEL_MARKER in block_a
    assert OMNICHANNEL_END in block_a
    assert "WA" in block_a   # primary channel abbreviated
    assert "11 msgs" in block_a   # 8+3
    assert "3 channels" in block_a
    assert "Awaiting: my reply" in block_a
    # Inbound was 22 hours before as_of → "(0d)" semantically = "today".
    assert "(0d)" in block_a
    assert len(block_a) <= MAX_BLOCK_CHARS
    print(f"  ✓ {len(block_a)} chars, within cap {MAX_BLOCK_CHARS}\n")

    # Case B: no recent activity
    kpi_b = ContactKPI(resourceName="people/cB")
    block_b = build_block(kpi_b, as_of=as_of)
    assert "No recent activity" in block_b
    assert OMNICHANNEL_MARKER in block_b and OMNICHANNEL_END in block_b
    print("Case B (empty KPI): OK — compact 'no activity' block")

    # Case C: stale-sent contact (their reply owed)
    kpi_c = ContactKPI(
        resourceName="people/cC",
        windows={"30d": WindowStats(
            messages_in=0, messages_out=4, channels=["imessage"],
        )},
        last_awaiting_reply_side="theirs",
        channel_primary="imessage",
        stale_sent_count=4,
        last_outbound_ever_ts="2026-04-10T10:00:00+00:00",
    )
    block_c = build_block(kpi_c, as_of=as_of)
    assert "theirs" not in block_c  # human-readable, not machine-ish
    assert "their reply" in block_c
    assert "Stale sent: 4" in block_c
    print(f"Case C (stale-sent): OK — shows 'their reply' + stale count")

    # Case D: strip block
    bio_with_block = """── CRM Notes (updated 2026-04-19) ──
Reached out via LinkedIn, awaiting reply

── Omnichannel (auto · 2026-04-20) ──
Primary: WA · 30d: 5 msgs · 2 channels
Last heard: 2026-04-20 WA · Awaiting: my reply (1d)
── End Omnichannel ──

Free-text notes about this person."""
    stripped = strip_block(bio_with_block)
    assert OMNICHANNEL_MARKER not in stripped
    assert "CRM Notes" in stripped
    assert "Free-text notes" in stripped
    print("Case D (strip): removes only omnichannel block, preserves others")

    # Case E: merge into empty biography
    assert merge_into_biography("", block_a).strip() == block_a.strip()
    print("Case E (merge into empty): block becomes the entire bio")

    # Case F: merge replaces prior omnichannel block (diff gone)
    merged = merge_into_biography(bio_with_block, block_a)
    # Should have exactly one Omnichannel block
    assert merged.count(OMNICHANNEL_MARKER) == 1
    assert merged.count(OMNICHANNEL_END) == 1
    assert "CRM Notes" in merged
    assert "Free-text notes" in merged
    print("Case F (merge replaces): old block gone, new block in, other content preserved")

    # Case G: should_update false for no-op
    merged_twice = merge_into_biography(merged, block_a)
    assert not should_update(merged, block_a), "should_update returns False for no-op"
    assert merged_twice.strip() == merged.strip()
    print("Case G (no-op detection): should_update=False, content unchanged")

    # Case H: should_update true for different block
    kpi_changed = ContactKPI(
        resourceName="people/cA",
        windows={"30d": WindowStats(messages_in=99, messages_out=99, channels=["whatsapp"])},
        channel_primary="whatsapp",
    )
    block_changed = build_block(kpi_changed, as_of=as_of)
    assert should_update(merged, block_changed), "should_update True for changed KPI"
    print("Case H (change detection): should_update=True when KPI changed")

    # Case I: backup / restore round-trip
    import tempfile
    contacts_fixture = [
        {"resourceName": "people/c1001",
         "biographies": [{"value": "Hello world", "contentType": "TEXT_PLAIN"}]},
        {"resourceName": "people/c1002",
         "biographies": []},
    ]
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        backup_biographies(contacts_fixture, tmp_path, note="self-test")
        loaded = load_backup(tmp_path)
        assert loaded["people/c1001"] == "Hello world"
        assert loaded["people/c1002"] == ""
        print("Case I (backup/restore): round-trip intact")
    finally:
        tmp_path.unlink(missing_ok=True)

    print("All self-tests passed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _run_self_test()

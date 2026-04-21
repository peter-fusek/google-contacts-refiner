# Scoring Signals Schema — Multi-channel ContactKPI

**Status:** design — drafted 2026-04-21 for [#150](https://github.com/peter-fusek/contactrefiner/issues/150)  
**Parent epic:** [#149](https://github.com/peter-fusek/contactrefiner/issues/149)

Derived per-contact rollups computed from `data/interactions/*.jsonl` by `harvester/scoring_signals.py`. Written to `data/interactions/contact_kpis.json`. Consumed by `followup_scorer.py` (Phase 4) and `server/utils/lead-signals.ts` (/signals page).

## The record

```jsonc
{
  "resourceName": "people/c12345",
  "windows": {
    "30d": {
      "messages_in": 12,
      "messages_out": 5,
      "channels": ["whatsapp", "linkedin_dm", "imessage"],
      "last_inbound_ts": "2026-04-20T14:50:51Z",
      "last_outbound_ts": "2026-04-19T09:12:33Z",
      "median_response_lag_hours_mine":  2.1,    // how fast I reply to inbound
      "median_response_lag_hours_theirs": 18.4,  // how fast they reply to my outbound
      "business_hours_ratio": 0.83,              // fraction of msgs during 09-18 local
      "business_keyword_hits": 4                 // "meeting"|"demo"|"price"|"proposal"|...
    },
    "90d":  { /* same shape */ },
    "365d": { /* same shape */ }
  },
  "last_awaiting_reply_side": "mine",      // "mine" | "theirs" | null
  "stale_sent_count": 2,                   // my outbound msgs >7d without reply
  "channel_primary": "whatsapp",           // network with most exchange in 90d
  "first_seen_ts": "2024-11-03T08:44:01Z", // earliest interaction across any channel
  "computedAt": "2026-04-21T06:00:00Z"
}
```

## Computation rules

### Windows

Three fixed windows: `30d`, `90d`, `365d`, computed at harvest time (`now - window`).

### Response-lag medians

- For each of my outbound messages: find the next inbound in the same thread. Lag = `inbound_ts - outbound_ts`. Collect all pairs in the window. Take median.
- Symmetric for `_mine` (inbound → my next outbound).
- Exclude pairs > 14 days apart (treat as "never replied").
- If fewer than 3 pairs in the window, report `null`.

### business_hours_ratio

- "Business hours" = Mon-Fri, 09:00-18:00 local time (`Europe/Bratislava`).
- Ratio of messages (both directions) in window that fall inside that range.
- Unreliable for windows < 10 messages — callers should gate on volume.

### business_keyword_hits

- Substring match against a curated list: `meeting, demo, price, pricing, proposal, quote, invoice, contract, payment, deal, kickoff, timeline, scope, SOW, RFP, PO`.
- Case-insensitive, word-boundary aware.
- Count per-message hits (one hit per keyword per message, not per occurrence).

### last_awaiting_reply_side

- Look at the most recent message in any channel involving this contact.
- If it's inbound, `side = "mine"` (I owe reply).
- If it's outbound and has no response yet, `side = "theirs"` (they owe reply).
- If the most recent is outbound and was already followed by inbound from someone else in the thread, `side = null` (handled).

### stale_sent_count

- Count my outbound messages to this contact in the 30d window that have no reply and are older than 7 days.

### channel_primary

- Sum (`messages_in` + `messages_out`) per channel in the 90d window.
- `channel_primary` = argmax. Tie-break by recency.

### first_seen_ts

- Minimum timestamp across all interactions with this contact, all channels, all time. Populated on first computation and preserved.

## Score formula additions

Extend `followup_scorer._score_contact()` with a new `beeper_bonus` component. The existing signals (LinkedIn, interaction, completeness, exec_bonus, personal_penalty, months_gap) remain intact.

```python
def _compute_beeper_bonus(kpi: ContactKPI) -> float:
    """Cross-channel engagement bonus. Capped at +40."""
    bonus = 0.0
    w30 = kpi.windows.get("30d", {})

    # Most actionable — they wrote to me, I haven't replied
    if kpi.last_awaiting_reply_side == "mine":
        bonus += 15.0

    # Multi-channel engagement signals depth of relationship
    channels_30d = len(w30.get("channels", []))
    if channels_30d >= 2:
        bonus += 10.0

    # Clear business intent
    if w30.get("business_keyword_hits", 0) >= 1:
        bonus += 20.0

    # Work-context conversation
    if w30.get("business_hours_ratio", 0) >= 0.7:
        bonus += 5.0

    # Heavy inbound from them
    in_out_delta = w30.get("messages_in", 0) - w30.get("messages_out", 0)
    if in_out_delta > 5:
        bonus += 8.0

    # De-prioritise my over-sending
    if kpi.stale_sent_count > 3:
        bonus -= 10.0

    # Long silence
    last_in = w30.get("last_inbound_ts")
    if last_in and (now_utc() - last_in).days > 180:
        bonus -= 15.0

    # Cap so Beeper doesn't dominate long-term LinkedIn signals
    return max(-20.0, min(bonus, 40.0))
```

## `/signals` — new signal types

Extend `server/utils/lead-signals.ts` with three new derivations. All respect the existing 100 actionable/week cap.

### `dm_awaiting_reply`

- Trigger: `last_awaiting_reply_side == "mine"` AND `(now - last_inbound_ts).days >= 3`
- Action suggested: "Reply on {channel_primary}"
- Priority: `high` — visible in candidates tab

### `multichannel_active`

- Trigger: `len(windows.30d.channels) >= 3`
- Action suggested: "Deeper relationship — consider moving to in_conversation/opportunity"
- Priority: `medium`

### `silent_after_hot`

- Trigger: `(messages_in+out in 30d-90d window) >= 5` AND `(messages_in+out in 30d window) == 0`
- Action suggested: "Re-engage — was active {days} days ago, now silent"
- Priority: `medium`

### Dismiss reasons

Add to existing preset reasons: `"replied elsewhere"`, `"not relevant"`, `"wrong contact"`, `"spam/bot"`.

## Integration points

| Consumer | File | Hook |
|--|--|--|
| FollowUp scorer | `followup_scorer.py:_score_contact` | read `contact_kpis.json`, call `_compute_beeper_bonus()`, add to score |
| /signals page | `dashboard/server/utils/lead-signals.ts` | three new derivation functions |
| CRM kanban | read via existing `/api/crm` — score changes surface automatically | no code changes required |
| Biography write-back | `crm_sync.py` | read `channel_primary`, `messages_30d`, `last_awaiting_reply_side` for omnichannel block |

## Testing

- Fixtures in `tests/fixtures/interactions/` — synthetic `.jsonl` sets covering: single-channel, multi-channel, long-silent, business-hot, personal-noise, own-company-Instarea.
- Unit tests per computation rule — expected `ContactKPI` for each fixture.
- Integration: feed fixture `contact_kpis.json` through `followup_scorer`, snapshot top-20 ranking, compare against committed golden output.

## Observability

- Per-run summary emitted to `data/scoring_run_summary.json`:
  - Contacts scored, median/p95 score, top-10 score composition breakdown (how much came from each signal category).
  - KPI coverage: how many contacts had enough data to produce 30d stats vs fell back.
- Dashboard existing `/runs` view can surface this without changes.

## Version stamping

Bump `ContactKPI.schema_version` any time the computation changes. Scorer refuses to consume mismatched versions, triggering full KPI recompute instead of silent drift.

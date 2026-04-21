# Interaction Schema (Sprint 3.32)

Source of truth for the Omnichannel Harvester (epic [#149](https://github.com/peter-fusek/contactrefiner/issues/149)). All channel readers write into this shape; the dashboard, contact matcher, and action proposer read from it.

## Goals

1. One row per logical message / call / meeting, regardless of channel.
2. Every row is linkable back to a Google People `resourceName` once the contact matcher runs.
3. No channel-specific fields leak into the core — that goes into `metadata`.
4. Privacy-first: summary is always present, raw body is optional and opt-in per channel.

## Record

```jsonc
{
  // deterministic: sha256("{channel}|{threadId}|{timestampISO}|{direction}|{externalId}")
  "interactionId": "ab12...",

  // null until contact_matcher.py links via email/phone/handle
  "contactId": "people/c12345" | null,

  // signals the matcher uses — kept even after match for re-runs
  "matchCandidates": {
    "emails": ["badr@example.com"],
    "phones": ["+421903123456"],      // E.164 normalized
    "handles": ["@linkedin/badr-x", "beeper:!room123:matrix.beeper.com"]
  },

  // controlled vocabulary — extend via this doc, not ad-hoc
  "channel": "imessage" | "sms" | "rcs"
           | "whatsapp" | "signal" | "messenger" | "instagram" | "telegram"
           | "linkedin_dm" | "gmail" | "calendar" | "call_facetime" | "call_cellular"
           | "slack" | "discord" | "x",

  "direction": "inbound" | "outbound",

  // network-specific thread identifier, stable across our re-runs
  "threadId": "iMessage:+421903123456" | "beeper:!room123" | "gmail:thread-id",

  "timestamp": "2026-04-20T14:50:51Z",        // UTC ISO-8601

  "subject": "Re: Technical Service Hub" | null,  // email only
  "summary": "First 500 chars, stripped of URLs and >2 consecutive whitespace",

  // pointer to raw body on GCS, only set when retention opted in
  "fullTextRef": "gs://bucket/data/interactions/raw/2026-04/ab12.txt" | null,

  "participants": [
    { "kind": "email|phone|handle", "value": "+421903123456", "name": "Badr A.", "self": false }
  ],

  // channel-specific — never indexed or CRM-matched on
  "metadata": {
    "source": "beeper" | "direct",        // which reader produced this row
    "sourceVersion": "beeper@1.2",
    "beeperChatId": "!room123:matrix.beeper.com",
    "beeperNetwork": "whatsapp",          // when source=beeper, the actual network
    "iMessageService": "iMessage|SMS|RCS",
    "isRead": true,
    "readAt": "2026-04-20T14:52:54Z",
    "reactionCount": 0,
    "hasAttachments": false,
    "attachmentTypes": []                 // ["image/heic", "video/mov"]
  }
}
```

## Storage layout

All on GCS under `gs://contactrefiner-data/`.

```
data/interactions/
    2026-04.jsonl           # monthly partition, one record per line
    2026-03.jsonl
    raw/
        2026-04/
            ab12...txt      # full body, only when retention policy = raw
    interaction_match_cache.json    # handle → resourceName cache (read-heavy)
    interaction_unknowns.jsonl      # rows with no match, user review queue
    interaction_actions.json        # Phase 7 LLM action proposals
```

- **Partitioning**: by calendar month of `timestamp` (UTC). Keeps files under ~50 MB even at heavy volume, aligns with existing changelog pattern.
- **Append-only** with dedupe on `interactionId` — re-running a reader never duplicates, just fills gaps.
- **Raw bodies are off by default**. Per-channel opt-in via env var (e.g. `INTERACTION_RAW_EMAIL=1`, `INTERACTION_RAW_IMESSAGE=1`). Default is summary-only.

## Direction, from the CRM owner's POV

- `outbound` = from Peter.
- `inbound` = from the counterparty.
- For group chats with 3+ participants, one row per logical message, `direction=inbound` unless Peter is the sender. The contact matcher may leave `contactId` null for multi-party chats; those surface in a separate "group chats" tab, not the 1:1 inbox.

## Summary generation rules

Applied at read-time, in the channel reader:

1. Decode native body (iMessage `attributedBody` NSKeyedArchiver, Gmail `text/plain` preferred over `text/html` with HTML fallback stripped).
2. Collapse runs of whitespace to one space.
3. Strip URLs but keep the domain in parentheses: `https://news.ycombinator.com/item?id=1` → `(news.ycombinator.com)`.
4. Truncate at 500 chars; if truncated, append `…`.
5. If resulting summary is empty, use a channel-specific placeholder: `"[image]"`, `"[voice note]"`, `"[tapback ❤️]"`, `"[deleted]"`.

## Contact matching precedence

The matcher walks candidates in this order and stops at first hit:

1. Exact email match → Google People `emailAddresses.value`.
2. E.164 phone match → Google People `phoneNumbers.value` normalized via `phonenumbers` lib (SK/CZ default region).
3. macOS AddressBook name → Google People `displayName` fuzzy (rapidfuzz ≥ 92) — **only for iMessage** where handles are already resolved by the OS (53% coverage per diagnostics).
4. Beeper contacts-list handle → `metadata.beeperContactId` seen before → cached `resourceName`.
5. LinkedIn handle → existing `linkedin_signals.json` name/headline reverse lookup.

On miss, write one record to `interaction_unknowns.jsonl` with all candidates. Dashboard surfaces this queue for manual linking; user's manual link writes back to `interaction_match_cache.json` so future rows auto-resolve.

## Channel reader contract

Every reader (`harvester/beeper_client.py`, `harvester/imessage_reader.py`, `harvester/gmail_reader.py`, etc.) exposes:

```python
class ChannelReader(Protocol):
    channel: Literal["imessage", "gmail", ...]

    def available(self) -> bool: ...
    # Returns False with a log line if prerequisites missing
    # (Beeper not running, no FDA, expired OAuth). Harvester continues with other readers.

    def harvest(self, since: datetime, until: datetime) -> Iterator[InteractionRecord]: ...
    # Yields records. Must be deterministic (same inputs → same interactionIds).
    # Must be resumable (safe to call with overlapping windows).
```

Shared post-processing (deduplication by `interactionId`, summary truncation, GCS write) lives in `harvester/pipeline.py` — readers stay dumb.

## Privacy defaults

- **Summary-only** storage by default. `fullTextRef` null.
- **Phone numbers** stored E.164, not hashed — we need exact match against Google People. Access-control via GCS IAM + service-account rotation, not per-field encryption.
- **Raw bodies** (when opted in) stored as plaintext in GCS under a lifecycle rule: delete after 90 days. Short-lived so Anthropic summarisation has something to chew on, but not a permanent archive.
- **Deletion policy** (global CLAUDE.md): soft-delete only. Dashboard "delete interaction" moves the record to `interaction_trash/YYYY-MM.jsonl` with a 30-day restore window before hard-delete.
- **Demo mode** (`isDemoMode(event)`): all PII fields (`summary`, `participants[*].value`, `matchCandidates.*`) are blanked before any API response.

## What belongs here vs not

| Lives in interaction record | Lives elsewhere |
|--|--|
| Per-message facts (who/when/what) | Per-contact scores (stays in `followup_scores.json`) |
| Match candidates | CRM stage (stays in `crm_state.json`) |
| Raw body pointer | The raw body itself (GCS `raw/` prefix) |
| Channel metadata | Channel auth credentials (stays in Secret Manager) |

## Change log

| Date | Change | Why |
|--|--|--|
| 2026-04-21 | Initial draft | Sprint 3.32 Session 1 |

---

Once this doc is agreed, Session 2 builds the readers and the matcher against this contract.

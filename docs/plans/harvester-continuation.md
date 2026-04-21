# Harvester Continuation Plan — Sprints 3.33 → 3.35

**Parent epic:** [#149](https://github.com/peter-fusek/contactrefiner/issues/149)  
**Child meta-issue:** #150  
**Author:** drafted 2026-04-21 following user expansion request

## What changed

Original #149 was: spike (done), readers, matcher, `/inbox` page, Phase 7 LLM proposer. Future-section parked continuous harvesting, full backfill, multi-channel scoring, biography write-back.

User expansion 2026-04-21: **promote the Future section to in-scope.** From now on:

1. Harvest all Beeper signals continuously, not just on monthly runs.
2. Load full Beeper history — one-time exhaustive backfill across all 10 networks.
3. Enhance every contact's history with cross-channel interaction metadata.
4. Write that enrichment back to Google Contacts biographies.
5. Feed all of it into the FollowUp scorer + `/signals` lead discovery.
6. Run as a regular scheduled job on the Mac (launchd).

**Architectural implication:** harvester flips from "new Phase 6" to a cross-cutting feedstock for existing Phases 4 (scoring) and 5 (CRM sync), plus new Phase 6 (ingest) and Phase 7 (proposer). Good — we reuse proven sync machinery instead of paralleling it.

## Updated architecture

```
    [Beeper Desktop API]
         │       │          ▲
         │       │          │
   launchd       │      backfill-beeper
   hourly        │      one-shot CLI
   incremental   │
         ▼       ▼
    harvester/
      ├─ beeper_client.py        OAuth DCR, paginated, rate-capped 1 req/s
      ├─ imessage_reader.py      chat.db + attributedBody decoder
      ├─ gmail_reader.py         extended from Phase 3 interaction_scanner
      ├─ contact_matcher.py      cross-channel dedup, handle → resourceName
      ├─ scoring_signals.py      derives ContactKPI windows 30/90/365d
      └─ pipeline.py             orchestration, resumable, checkpointed
         │
         ▼
    data/
      interactions/
        2026-04.jsonl            monthly partition, unified records
        raw/2026-04/             opt-in raw bodies
      contact_kpis.json          per-contact rollups (feeds scorer)
      match_cache.json           handle → resourceName cache
      backfill_state.json        (account, chatID, cursor) checkpoints
         │
         ├─► Phase 4 FollowUp scorer   reads contact_kpis.json + existing signals
         ├─► Phase 5 CRM sync          writes compact omnichannel block to biographies
         ├─► Phase 7 Action proposer   LLM digest for /inbox proposed-actions queue
         └─► /inbox dashboard          unified feed, CRM drawer timeline
```

## 1. Full-history backfill (one-time operation)

### CLI

```bash
python main.py backfill-beeper [--account=linkedin] [--before=2026-04-01] [--resume] [--dry-run]
```

- Default: all accounts, from today backward to Beeper's earliest available.
- `--resume` picks up from `data/backfill_state.json` after crash/interrupt.
- `--dry-run` counts chats + messages without writing.

### Pacing

- 1 request/sec ceiling (configurable `HARVEST_RPS`).
- Adaptive backoff on any 429 / 5xx.
- Hard stop at 10k msgs/min to protect Beeper's local cache from thrashing.

### Expected volume

- Need to sample `GET /v1/chats?limit=1&total=true` first before committing to runtime estimate.
- Back-of-envelope: 100k-300k messages across 3 years of Beeper use. At 1 req/s with 50 msgs/page → 2000-6000 page calls → 30-100 min per account → ~10 hours for a full exhaustive sweep.
- Runs in background. User can leave laptop running overnight. Progress printed every 500 msgs + checkpointed every 50.

### Edge cases

- **Beeper-side history depth varies per network**: WhatsApp + Signal only backfill from link time (protocol limit). iMessage + Telegram + LinkedIn typically full. We take what Beeper has, log which windows are available per account.
- **Deep group chats**: 10k+ msg group chats paginate cleanly; hold only one page in memory at a time.
- **Matrix event edits / redactions**: record the canonical state at harvest time; don't chase edit history unless `--preserve-edits` flag (defer).
- **Sleep during run**: launchd won't fire the backfill, but a CLI invocation sleeps with Mac. Resume on next `python main.py backfill-beeper --resume`.

### Storage

- Writes retroactively into `data/interactions/YYYY-MM.jsonl` partitions by message timestamp.
- All records go through the same dedup-by-`interactionId` path as incremental harvest — safe to re-run.

### Cost

- $0 Anthropic (no LLM). GCS storage ~50-150 MB for the full archive.

## 2. Continuous harvest (regular job)

### Cadences

| Cadence | Cron (local) | What it does |
|--|--|--|
| Hourly | `:17` past every hour | Incremental pull since last successful timestamp per account |
| Daily | 04:00 | Reconciliation — re-pull last 24h to catch delivery delays, edits |
| Weekly | Monday 05:00 | Score recomputation (per-contact KPI refresh), /signals derivation |
| Monthly | 1st 06:00 | Phase 5 CRM sync incl. biography write-back; backfill gap repair |

### Runtime location

**Mac only** — Beeper API is localhost-bound. Cloud Run cannot reach it. Scheduled via **launchd** user agents in `~/Library/LaunchAgents/com.contactrefiner.harvester.*.plist`.

Template plists live in-repo under `launchagents/` for reference; install via `scripts/install-launchd.sh` (writes to `~/Library/LaunchAgents/` and `launchctl load`s them). Uninstall via matching script.

### Guards

- Before any work: `curl -s --max-time 2 localhost:23373/v1/info` — abort gracefully if unreachable.
- Honor existing emergency stop: `data/pipeline_paused.json` → skip and log.
- Honor global deletion policy: never delete interaction records; soft-delete to `data/interactions/trash/YYYY-MM.jsonl` with 30-day TTL.

### Logs + notifications

- Per-run log: `~/Library/Logs/contactrefiner/harvester-{hourly,daily,weekly,monthly}.log`
- Rotated at 10 MB, 7 files kept.
- Email on failure only (via existing Resend integration). Success is silent.

### Sleep + missed runs

- launchd `StartCalendarInterval` fires on next wake if a slot was missed during sleep.
- Incremental harvest is idempotent per timestamp — catches up naturally next run.
- Daily reconciliation window (24h) compensates for any incremental drift.

## 3. Multi-channel scoring integration

See [docs/schemas/scoring-signals.md](../schemas/scoring-signals.md) for the full ContactKPI schema + formula additions.

### New signals folded into `followup_scorer._score_contact()`

| Signal | Weight | Direction |
|--|--|--|
| They're awaiting my reply (30d) | +15 | high — most actionable |
| ≥2 channels active in 30d | +10 | high — deeper relationship |
| Business-keyword hits in 30d | +20 | clear business intent |
| Business-hours ratio ≥ 0.7 | +5 | work-context conversation |
| Inbound minus outbound > +5 | +8 | heavy inbound from them |
| Stale-sent count > 3 | −10 | I'm spamming without reply; demote |
| Last inbound > 180d | −15 | long silence |

### `/signals` new signal types

- `dm_awaiting_reply` — they wrote, I haven't replied 3+d
- `multichannel_active` — 3+ distinct channels in 30d  
- `silent_after_hot` — active (>=5 msgs) in 30-90d window but 0 in last 30d

Caps at 100 actionable items/week unchanged; new types compete with existing scoring for slots.

## 4. Google Contacts biography write-back

### The block

Append to each contact's biography, additively, idempotently. Diff-based — only touch when content changes.

```
── Omnichannel (auto) ──
Primary: WhatsApp. 30d: 12 msgs · 4 channels.
Last heard: 2026-04-20 WA · Awaiting: my reply (2d).
── End Omnichannel ──
```

### Rules

- **Never message content** — metadata only. Content stays in GCS.
- ~200 chars per contact ceiling. Google People API biography field cap TBD; measured in spike.
- Additive block: existing `── Last Interaction` block from Phase 3 coexists.
- Diff-based write: skip API call when computed block equals existing block on contact.
- Backup before any write: `data/contacts_biography_backup_YYYY-MM-DD.json` in GCS. 30-day retention.
- Skip contacts tagged as personal/family to avoid pollution.
- Skip own-company (Instarea) — consistent with business-first scoring philosophy.

### Rate limits

- Google People API `people.updateContact`: 90 RPM.
- Batch 50 updates per `batchUpdateContacts` call where schema allows.
- Reuse existing pipeline's exponential backoff.

### Emergency rollback

- `python main.py restore-biographies --from=2026-04-15` → reads backup JSON → restores biographies to that date's state.
- Deletion policy compliance: we **never** blank a biography; only overwrite the `── Omnichannel` fenced block.

## 5. Phasing

### Sprint 3.33 Session 1 — kernel (in flight, shared with parallel session)

- `harvester/` package scaffold
- `beeper_client.py` — typed client with OAuth DCR + rate cap
- `imessage_reader.py` — chat.db + attributedBody decoder
- `contact_matcher.py` — first pass (email + phone + handle → resourceName)
- `main.py harvest-messages --incremental` — writes first real JSONL to GCS
- Unit tests against fixtures

### Sprint 3.33 Session 2 — backfill + launchd

- `main.py backfill-beeper` (checkpoints, pacing, `--resume`)
- `launchagents/` plist templates + install/uninstall scripts
- Sample run end-to-end on user's Mac, capture volume numbers
- Populate `data/interactions/` with real historical data

### Sprint 3.33 Session 3 — scoring

- `scoring_signals.py` — ContactKPI computation
- `contact_kpis.json` writer
- Extend `followup_scorer._score_contact()` to read KPIs and add new weights
- Extend `server/utils/lead-signals.ts` with new signal types
- Dashboard `/signals` regression test

### Sprint 3.34 Session 1 — write-back

- Extend Phase 5 CRM sync with omnichannel biography block (diff-based)
- Biography backup + restore command
- Google People API rate limiter check
- Dry-run mode for full-contact-book preview before first real write

### Sprint 3.34 Session 2 — dashboard + proposer

- `server/api/inbox/index.get.ts` + `app/pages/inbox.vue`
- CRM card drawer interaction timeline
- Phase 7 LLM action proposer (Sonnet 4.6, prompt-cached, $1/run cap)
- Proposed-actions queue mirroring `/signals` UX

### Sprint 3.35+ — operational hardening

- Observability dashboard (per-account success rates, latencies)
- Call history integration (`osx-callhistory-decryptor`)
- Self-hosted mautrix bridges as Beeper alternative (only if reliability drops)

## 6. Cost + privacy envelope

| Item | Cost |
|--|--|
| Anthropic — harvest + scoring | $0 (deterministic code) |
| Anthropic — Phase 7 proposer | $1/run cap, monthly = ~$1/mo |
| GCS storage — interactions | ~50-150 MB one-time, <5 MB/mo growth |
| GCS storage — raw bodies (opt-in) | 200-500 MB if enabled; default off |
| Google People API | free tier; well under 90 RPM |

| Privacy red line | Enforcement |
|--|--|
| Message content ↛ Google Contacts | hard-coded: omnichannel block contains no bodies |
| Message content ↛ GCS unless opted in | per-channel env flag `INTERACTION_RAW_{channel}=1` |
| No cross-account sharing | all reads scoped to peter.fusek@instarea.sk |
| Deletion: soft-only | trash partitions with 30d TTL |

## 7. Risk register

| Risk | Mitigation |
|--|--|
| Mac sleep misses scheduled runs | Next wake fires catch-up; daily reconciliation window covers drift |
| Beeper bridge suspended (network ban) | `/v1/accounts/{id}/status` check; skip account, log, email |
| chat.db locked by Messages app | Exponential backoff 3 attempts, then skip cycle |
| Biography growth across runs | Diff-based update avoids churn; backup before every write |
| OAuth token expiry / revoke | Refresh flow; email alert on refresh failure |
| Scoring weight drift | Hold baseline weights in `config.py`; changes require commit + review |
| Accidental biography wipe | `restore-biographies` command + daily backup; never blank, only replace block |
| Cross-session merge conflicts | Work split in #150 coordination comment; branches per session |

## 8. Open questions

1. **Full history volume** — sample via `GET /v1/chats?limit=1` before committing to backfill ETA.
2. **Biography cap** — empirical test once People API scope confirmed.
3. **Group chats** — 1:1 matching trivial; groups need per-participant fan-out or "group:{chatId}" virtual contact. Decision deferred to scoring session.
4. **Scoring weights cap** — set maximum Beeper bonus at +40 so long-term LinkedIn context isn't drowned.
5. **Personal contact detection** — current heuristic (no org/title, personal email) may need refinement once biography write-back hits friends/family.

## 9. Rollback

Every new capability is reversible:

- Backfill: delete `data/interactions/` partitions; no upstream state was touched.
- Continuous harvest: `launchctl unload` the plists; processes stop.
- Scoring additions: remove Beeper-signal weights from `config.py`; followup_scorer reverts to LinkedIn+Gmail baseline.
- Write-back: `restore-biographies` with a date preceding first omnichannel write; clears all `── Omnichannel` blocks.

---

Track execution on #150. Discussion + decisions via PR reviews on branches named `feat/harvester-s{session}-{scope}`.

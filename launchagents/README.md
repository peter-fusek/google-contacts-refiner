# LaunchAgents — scheduled harvester on macOS

These plists schedule the Beeper/iMessage/Gmail harvester on the user's Mac. Beeper Desktop is localhost-bound, so the harvester cannot run on Cloud Run — it runs locally via launchd.

## Files

| Plist | Cadence | Job |
|--|--|--|
| `com.contactrefiner.harvester.hourly.plist` | `:17` past each hour | Incremental harvest since last run |
| `com.contactrefiner.harvester.daily.plist` | 04:00 daily | 24h reconciliation re-pull |
| `com.contactrefiner.harvester.weekly.plist` | Mon 05:00 | Score recomputation + /signals derivation |
| `com.contactrefiner.harvester.monthly.plist` | 1st 06:00 | Phase 5 CRM sync + biography write-back + backfill gap repair |

## Install

```bash
./scripts/install-launchd.sh    # copies plists to ~/Library/LaunchAgents/ + loads
./scripts/uninstall-launchd.sh  # unloads + removes
```

## Behavior during Mac sleep

- `StartCalendarInterval` fires on next wake if the slot was missed.
- Incremental harvest is idempotent by timestamp — catches up naturally.
- Daily reconciliation (24h window) covers any drift.

## Logs

`~/Library/Logs/contactrefiner/harvester-{hourly,daily,weekly,monthly}.log`  
Rotated at 10 MB, 7 files retained.

## Emergency stop

Drop `data/pipeline_paused.json` into GCS — every cadence checks it and skips if present. Same mechanism the Cloud Run pipeline uses.

## Status: templates only

These are not auto-loaded. Planned for Sprint 3.33 Session 2 alongside the `backfill-beeper` CLI.

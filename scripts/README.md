# scripts/

Operational scripts. Two categories.

## Launchd (bash)

Install / uninstall the macOS launchd agents that run the harvester on your Mac. See [`launchagents/README.md`](../launchagents/README.md) for the schedule.

```bash
./scripts/install-launchd.sh     # hourly/daily/weekly/monthly agents
./scripts/uninstall-launchd.sh   # leaves log files in ~/Library/Logs/contactrefiner/
```

## Rollback (Python)

Safety-net operations against Google Contacts biographies after an omnichannel write-back (Sprint 3.34 S1). All scripts:

- Authenticate via existing `auth.authenticate()` — OAuth or service account, same as the main pipeline.
- Use `api_client.PeopleAPIClient` with its existing rate limiter + 409 retry.
- Require `--dry-run` on the first invocation (or `--yes` for unattended runs).
- Print progress + final stats.

### restore_biographies.py

Restore biographies from a backup JSON file written by `harvester.crm_omnichannel.backup_biographies`. Backups live at `data/biography_backups/biographies_YYYY-MM-DD.json`.

```bash
# Dry-run first (always)
python scripts/restore_biographies.py \
    --backup data/biography_backups/biographies_2026-04-21.json \
    --dry-run

# Full restore (overwrite biography with backup value)
python scripts/restore_biographies.py \
    --backup data/biography_backups/biographies_2026-04-21.json

# Selective — only roll back the Omnichannel block, keep everything else
python scripts/restore_biographies.py \
    --backup data/biography_backups/biographies_2026-04-21.json \
    --omnichannel-only
```

### strip_omnichannel_blocks.py

Remove the `── Omnichannel (auto · …) ──` block from every contact's biography. Preserves all other content. Use when you need to clear the auto-block across the whole book without restoring from a specific backup.

```bash
# Dry-run first
python scripts/strip_omnichannel_blocks.py --dry-run

# Apply
python scripts/strip_omnichannel_blocks.py

# Cron-safe (no interactive prompt)
python scripts/strip_omnichannel_blocks.py --yes

# Test on a small slice
python scripts/strip_omnichannel_blocks.py --dry-run --limit 20
```

## Combined rollback runbook

The ideal path if an Omnichannel write goes wrong:

1. **Stop the bleeding** — remove `ENABLE_OMNICHANNEL_WRITEBACK` from the Cloud Run env. Next scheduled run skips the pass.
2. **Clear existing artifacts** — `strip_omnichannel_blocks.py --dry-run` to preview, then real run.
3. **If user free text was accidentally clobbered** — `restore_biographies.py --backup ...` (full mode) to restore from the latest pre-write backup.
4. **Debug the root cause** on the local checkpoint `contact_kpis.json` file, fix in `harvester/crm_omnichannel.build_block` or scoring pipeline, land via PR with new self-test cases.
5. **Re-enable write-back** after fix lands — set the env var back, next scheduled run starts producing the correct blocks.

## Global deletion policy

All of these scripts are consistent with the project rule: **never permanently delete data without a recovery path**. Specifically:

- `restore_biographies.py` has a dry-run gate and interactive confirmation before writing.
- `strip_omnichannel_blocks.py` only removes the fenced Omnichannel block; other biography content (other marker blocks + user free text) is preserved verbatim by `harvester.crm_omnichannel.strip_block`.
- Both scripts log every action; progress is checkpointed so partial runs can be retried idempotently.

# Patch — integrate omnichannel block into `crm_sync.py`

**Target sprint:** 3.34 Session 1 (biography write-back)  
**Prereq:** `harvester/crm_omnichannel.py` + `harvester/scoring_signals.py` + `FOLLOWUP_BEEPER_KPI_FILE` constant — all landed on `main`.  
**Parent issues:** [#149](https://github.com/peter-fusek/contactrefiner/issues/149), [#150](https://github.com/peter-fusek/contactrefiner/issues/150)

Concrete, line-accurate diff. Apply in one commit after Session 3 scoring is live and `data/interactions/contact_kpis.json` is being written on every weekly run.

## Overview

Extend the existing Phase 5 CRM sync (`crm_sync.sync_notes`) with a second pass that syncs each contact's `ContactKPI` rollup into a fenced `── Omnichannel (auto · YYYY-MM-DD) ──` block inside the biography. Additive, diff-based, idempotent. Reuses the existing backup/rate-limit/dry-run machinery.

**No message content ever enters the biography.** This rule is enforced inside `harvester/crm_omnichannel.build_block` — the patch does not add any code that could leak content; it only plumbs the already-safe block into the write path.

## File diff

### `crm_sync.py`

**Imports (after line 16):**

```diff
 import json
 import logging
 from datetime import datetime, timezone
 from pathlib import Path

-from config import DATA_DIR
+from config import (
+    DATA_DIR,
+    FOLLOWUP_BEEPER_KPI_FILE,
+    FOLLOWUP_OWN_COMPANY_DOMAINS,
+    FOLLOWUP_OWN_COMPANY_ORG_KEYWORDS,
+)
+from harvester.crm_omnichannel import (
+    backup_biographies,
+    build_block,
+    merge_into_biography,
+    should_update,
+)
+from harvester.scoring_signals import load_kpis_from_json
```

**New function `sync_omnichannel` (place after `sync_notes`, before `sync_tags`):**

```python
def sync_omnichannel(
    client,
    contacts_by_rn: dict[str, dict],
    *,
    dry_run: bool = False,
    backup_dir: Optional[Path] = None,
) -> dict:
    """Sync ContactKPI rollups into Google Contacts biographies.

    Reads data/interactions/contact_kpis.json, renders a fenced
    Omnichannel block per contact, merges into existing biography, writes
    back via People API with diff-based skip.

    Gates:
      - Runs only if ENABLE_OMNICHANNEL_WRITEBACK env var is truthy.
      - Skips own-company contacts (FOLLOWUP_OWN_COMPANY_*).
      - Writes biography backup under backup_dir before any API call.
    """
    import os
    if not os.getenv("ENABLE_OMNICHANNEL_WRITEBACK", "").lower() in ("1", "true", "yes"):
        logger.info("sync_omnichannel: disabled via ENABLE_OMNICHANNEL_WRITEBACK")
        return {"status": "disabled", "synced": 0, "skipped": 0}

    kpis = load_kpis_from_json(FOLLOWUP_BEEPER_KPI_FILE)
    if not kpis:
        logger.info(
            "sync_omnichannel: no ContactKPI data at %s — nothing to write",
            FOLLOWUP_BEEPER_KPI_FILE,
        )
        return {"status": "empty", "synced": 0, "skipped": 0}

    # Pre-write backup — reversibility is the global deletion policy.
    if backup_dir is None:
        backup_dir = DATA_DIR / "biography_backups"
    date_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_path = backup_dir / f"biographies_{date_stamp}.json"
    backup_biographies(
        list(contacts_by_rn.values()), backup_path,
        note="pre sync_omnichannel",
    )

    stats = {"synced": 0, "skipped_no_kpi": 0, "skipped_own_co": 0,
             "skipped_no_change": 0, "errors": 0}

    for rn, kpi in kpis.items():
        contact = contacts_by_rn.get(rn)
        if not contact:
            stats["skipped_no_kpi"] += 1
            continue

        if _is_own_company_contact(contact):
            stats["skipped_own_co"] += 1
            continue

        try:
            # Fetch latest biography — may have drifted since backup was taken.
            person = client.get_contact(rn, person_fields="biographies,metadata")
            etag = person.get("etag")
            bios = person.get("biographies", [])
            existing_bio = bios[0].get("value", "") if bios else ""

            new_block = build_block(kpi)
            if not should_update(existing_bio, new_block):
                stats["skipped_no_change"] += 1
                continue

            new_bio = merge_into_biography(existing_bio, new_block)

            if not dry_run:
                body = {"biographies": [{"value": new_bio, "contentType": "TEXT_PLAIN"}]}
                client.update_contact(rn, etag, body, update_fields="biographies")

            stats["synced"] += 1
        except Exception as e:
            logger.warning(f"sync_omnichannel failed for {rn}: {e}")
            stats["errors"] += 1

    logger.info(f"sync_omnichannel stats: {stats}")
    return {"status": "ok", **stats, "backup": str(backup_path)}


def _is_own_company_contact(contact: dict) -> bool:
    """Reuse the followup_scorer convention — don't sync omnichannel into
    Peter's own Instarea teammates' contact cards."""
    orgs = contact.get("organizations", []) or []
    org_name = orgs[0].get("name", "").lower() if orgs else ""
    if any(kw in org_name for kw in FOLLOWUP_OWN_COMPANY_ORG_KEYWORDS):
        return True
    emails = {
        (e.get("value") or "").lower()
        for e in contact.get("emailAddresses", []) or []
    }
    for email in emails:
        if "@" in email:
            domain = email.split("@")[-1]
            if domain in FOLLOWUP_OWN_COMPANY_DOMAINS:
                return True
    return False
```

**Wire into the existing `run_crm_sync` orchestrator (around line 252):**

```diff
 def run_crm_sync(client, dry_run: bool = False):
     """
     Loads crm_state.json, syncs notes to biographies and tags to groups.
     """
     state = load_crm_state()
     contacts_by_rn = _fetch_contacts_for_sync(client, state)
     notes_result = sync_notes(client, state, dry_run=dry_run, ...)
+    omni_result = sync_omnichannel(client, contacts_by_rn, dry_run=dry_run)
     tags_result = sync_tags(client, state, dry_run=dry_run, ...)
     return {
         "notes": notes_result,
+        "omnichannel": omni_result,
         "tags": tags_result,
     }
```

### `main.py`

No-op code changes — the existing `cmd_crm_sync` entry point routes to `run_crm_sync` which now includes the omnichannel pass. Add an opt-in flag for manual dry-run testing:

```diff
 def cmd_crm_sync(dry_run: bool = False, ...):
     ...
+    # Check env gate explicitly so users see why the pass is / isn't running
+    if not os.getenv("ENABLE_OMNICHANNEL_WRITEBACK"):
+        print("  Omnichannel writeback: skipped (ENABLE_OMNICHANNEL_WRITEBACK not set)")
```

### Cloud Run deploy update (`cloudbuild.yaml`)

No code change needed — the env var is per-Cloud-Run-Job-execution and gated as documented. Update env list once the feature is validated:

```
--update-env-vars ENABLE_OMNICHANNEL_WRITEBACK=true
```

Default: **off**. Turn on explicitly after the first dry-run review on a full contact book.

## Safety: what this patch does NOT do

- **Does not write message content** — `build_block` renders metadata only; the block is bounded to `MAX_BLOCK_CHARS=250`.
- **Does not remove existing biography content** — `merge_into_biography` preserves everything outside the Omnichannel block.
- **Does not run without a pre-write backup** — `backup_biographies` runs before any `update_contact` call.
- **Does not skip own-company contacts silently** — stats report `skipped_own_co` count.
- **Does not retry aggressively on API failures** — one failure per contact, logged, counted, moves on. The next weekly run retries naturally.

## Rollback

1. Set `ENABLE_OMNICHANNEL_WRITEBACK=` (empty) in Cloud Run env vars. The pass becomes a no-op immediately.
2. If a bad block got written, run `python main.py restore-biographies --from=YYYY-MM-DD` — reads the backup JSON at `data/biography_backups/biographies_YYYY-MM-DD.json` and restores each contact's biography to that date's snapshot.
3. To rip the marker block without a full restore: run `python main.py strip-omnichannel-blocks` (helper command — add to `main.py` in the same PR). It applies `harvester.crm_omnichannel.strip_block` to every contact and writes the result back. Additive rollback — never touches non-Omnichannel content.

## Sanity test before first run

1. Dry-run against your full contact book:  `ENABLE_OMNICHANNEL_WRITEBACK=true python main.py crm-sync --dry-run`
2. Inspect the produced `new_bio` for 5 sample contacts (Kristína, Miloslava, Badr, Franklin, plus one family contact). Verify:
   - No message content leaked.
   - Marker block is in the expected location.
   - Existing content preserved.
   - Family contact correctly gates via personal-contact detection in a future patch (this patch doesn't gate — Session 3.34 S2 adds `_is_likely_personal` gate).
3. Apply to real run, watch Cloud Run logs for `sync_omnichannel stats:`.
4. Spot-check 3 contacts in Google Contacts UI to confirm the block renders cleanly and respects deduplication across re-runs.

## Future patches this unlocks

- Sprint 3.34 S2: `_is_likely_personal` gate (skip family/friends)
- Sprint 3.34 S2: Contact groups `Active:{channel}` (additive, never removed)
- Sprint 3.35: Per-contact preference file (opt-out list)

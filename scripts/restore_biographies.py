#!/usr/bin/env python3
"""
Restore Google Contacts biographies from a backup JSON file.

Backups are written by harvester.crm_omnichannel.backup_biographies before
every `sync_omnichannel` run (Sprint 3.34 S1+), as `data/biography_backups/
biographies_YYYY-MM-DD.json`. This tool reads one of those files and
restores each contact's biography via the Google People API.

Two modes:
  --full (default): overwrite each contact's entire biography with the
      backup value. Destructive to any intermediate user edits made after
      the backup was taken.
  --omnichannel-only: keep the contact's current biography except for the
      fenced ── Omnichannel (auto) ── block, which gets replaced with the
      block from the backup. Safer — doesn't touch user free text or other
      marker blocks.

--dry-run is REQUIRED on the first invocation with any given backup file.
After dry-run, re-run without --dry-run to apply.

Usage
-----
    # Show what would change without writing
    python scripts/restore_biographies.py \\
        --backup data/biography_backups/biographies_2026-04-21.json \\
        --dry-run

    # Full restore (interactive confirmation)
    python scripts/restore_biographies.py \\
        --backup data/biography_backups/biographies_2026-04-21.json

    # Selective: only roll back the Omnichannel block
    python scripts/restore_biographies.py \\
        --backup data/biography_backups/biographies_2026-04-21.json \\
        --omnichannel-only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow `python scripts/restore_biographies.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import PeopleAPIClient
from auth import authenticate
from harvester.crm_omnichannel import (
    OMNICHANNEL_MARKER,
    load_backup,
    merge_into_biography,
    strip_block,
)

logger = logging.getLogger("restore_biographies")


def _extract_omnichannel_block(bio: str) -> str:
    """Return just the Omnichannel block from a biography, or empty string."""
    if OMNICHANNEL_MARKER not in bio:
        return ""
    lines = bio.split("\n")
    out = []
    in_block = False
    for line in lines:
        if OMNICHANNEL_MARKER in line:
            in_block = True
            out.append(line)
            continue
        if in_block:
            out.append(line)
            if "── End Omnichannel" in line:
                break
    return "\n".join(out).strip()


class _AuthAbort(RuntimeError):
    """Raised when People API returns 401 — the whole run should abort so
    the operator refreshes credentials rather than silently failing every
    contact with the same auth error."""


def _classify_api_error(exc: Exception) -> str:
    """Bucket an API exception into one of: auth / permanent / transient.

    auth      → 401/403 with auth message → abort whole run
    permanent → 404, 410, most 400s → log + skip this contact
    transient → 429, 5xx, network → log + count retry candidate
    """
    text = str(exc).lower()
    if "401" in text or "unauthenticated" in text or "invalid credentials" in text:
        return "auth"
    if "403" in text or "permission" in text or "forbidden" in text:
        return "auth"
    if "404" in text or "410" in text or "not found" in text or "deleted" in text:
        return "permanent"
    if "429" in text or "rate" in text or "quota" in text:
        return "transient"
    if "500" in text or "502" in text or "503" in text or "504" in text:
        return "transient"
    if "timed out" in text or "timeout" in text or "connection" in text:
        return "transient"
    return "permanent"


_PEOPLE_API_MIN_INTERVAL_SECONDS = 1.1
_last_api_call: list[float] = [0.0]  # mutable singleton — avoid a global `nonlocal`-less closure gotcha


def _throttle() -> None:
    """Sleep enough to keep at or under Google People's 60 QPM write ceiling.

    Each `get_contact`/`update_contact` counts as one quota unit. Without
    a throttle a bulk restore burns the minute-bucket in ~5 seconds, then
    blocks for the rest of the minute under server-side backoff that
    looks like transient errors in our logs. 1.1s interval keeps us at
    ~55 QPM — under the cap with a safety margin.
    """
    import time as _time
    elapsed = _time.monotonic() - _last_api_call[0]
    if elapsed < _PEOPLE_API_MIN_INTERVAL_SECONDS:
        _time.sleep(_PEOPLE_API_MIN_INTERVAL_SECONDS - elapsed)
    _last_api_call[0] = _time.monotonic()


def restore_one(
    client: PeopleAPIClient,
    rn: str,
    backup_bio: str,
    *,
    omnichannel_only: bool,
    dry_run: bool,
) -> str:
    """Restore a single contact's biography. Returns a status string.

    Raises `_AuthAbort` on 401/403 so the caller can bail out for the
    whole run — silently proceeding through auth failures would produce
    thousands of misleading "skipped" rows.
    """
    _throttle()
    try:
        person = client.get_contact(rn, person_fields="biographies,metadata")
    except Exception as e:
        kind = _classify_api_error(e)
        if kind == "auth":
            raise _AuthAbort(f"auth failure on {rn}: {e}") from e
        # permanent/transient → skip with classified tag
        return f"skipped-{kind}:{type(e).__name__}"

    etag = person.get("etag")
    bios = person.get("biographies", [])
    current = bios[0].get("value", "") if bios else ""

    if omnichannel_only:
        # Preserve current biography; only replace the Omnichannel block
        # with the one from the backup (or remove it if backup had none).
        backup_block = _extract_omnichannel_block(backup_bio)
        if backup_block:
            new_bio = merge_into_biography(strip_block(current), backup_block)
        else:
            new_bio = strip_block(current)
    else:
        new_bio = backup_bio

    if new_bio.strip() == (current or "").strip():
        return "no-change"

    if dry_run:
        return f"would-change (current={len(current)}c → new={len(new_bio)}c)"

    _throttle()
    body = {"biographies": [{"value": new_bio, "contentType": "TEXT_PLAIN"}]}
    client.update_contact(rn, etag, body, update_fields="biographies")
    return "restored"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backup", required=True, type=Path,
                        help="Path to biographies_YYYY-MM-DD.json backup file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without calling the API")
    parser.add_argument("--omnichannel-only", action="store_true",
                        help="Only restore the Omnichannel block; keep other biography content")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation prompt (requires --dry-run was run previously)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.backup.exists():
        print(f"error: backup file not found: {args.backup}", file=sys.stderr)
        return 2

    backups = load_backup(args.backup)
    print(f"Loaded backup: {len(backups)} contacts from {args.backup}")
    mode = "omnichannel-only" if args.omnichannel_only else "full"
    print(f"Mode: {mode}  |  Dry-run: {args.dry_run}")

    if not args.dry_run and not args.yes:
        print()
        print(f"⚠️  About to RESTORE {len(backups)} biographies.")
        print(f"    Mode: {mode}")
        print(f"    This will call Google People API and MUTATE your contacts.")
        response = input("    Continue? (type 'yes' to proceed): ")
        if response.strip().lower() != "yes":
            print("Aborted by user.")
            return 1

    creds = authenticate()
    client = PeopleAPIClient(creds)

    # Classified counters — conflating auth + transient + permanent into
    # a single "errors" bucket hid entire-run auth failures under a friendly
    # summary. Each class has distinct remediation.
    stats = {
        "restored": 0, "no-change": 0, "would-change": 0,
        "skipped-permanent": 0, "skipped-transient": 0,
        "update-errors": 0,
    }
    auth_aborted = False

    for i, (rn, backup_bio) in enumerate(backups.items(), 1):
        try:
            status = restore_one(
                client, rn, backup_bio,
                omnichannel_only=args.omnichannel_only,
                dry_run=args.dry_run,
            )
            if status == "restored":
                stats["restored"] += 1
            elif status == "no-change":
                stats["no-change"] += 1
            elif status.startswith("would-change"):
                stats["would-change"] += 1
            elif status.startswith("skipped-permanent"):
                stats["skipped-permanent"] += 1
                logger.info(f"{rn}: {status}")
            elif status.startswith("skipped-transient"):
                stats["skipped-transient"] += 1
                logger.warning(f"{rn}: {status}")
            if i % 50 == 0:
                print(f"  progress: {i}/{len(backups)}  stats={stats}")
        except _AuthAbort as e:
            print(f"\nAUTH FAILURE — aborting whole run: {e}", file=sys.stderr)
            print(f"  Refresh OAuth credentials (delete token_beeper.json / re-run auth) "
                  f"and retry.", file=sys.stderr)
            auth_aborted = True
            break
        except Exception as e:
            # update_contact threw after get_contact succeeded — classify too
            kind = _classify_api_error(e)
            if kind == "auth":
                print(f"\nAUTH FAILURE on update ({rn}) — aborting: {e}", file=sys.stderr)
                auth_aborted = True
                break
            stats["update-errors"] += 1
            logger.warning(f"{rn}: update failed ({kind}): {e}")

    print()
    print(f"Done. Stats: {stats}")
    if auth_aborted:
        print("Run aborted on auth failure — stats above reflect partial progress only.")
        return 2
    if stats["skipped-transient"] > 0:
        print(
            f"⚠ {stats['skipped-transient']} transient failures — re-run to retry."
        )
    if args.dry_run and stats["would-change"] > 0:
        print(f"To apply, re-run without --dry-run (or add --yes to skip the prompt).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

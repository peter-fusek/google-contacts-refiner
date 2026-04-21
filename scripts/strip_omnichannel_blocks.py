#!/usr/bin/env python3
"""
Strip ── Omnichannel (auto · …) ── blocks from all Google Contacts.

Forward-only rollback that removes only the Omnichannel marker block from
each contact's biography. Leaves all other content (── CRM Notes, ── Last
Interaction, ── FollowUp Prompt, user free text) untouched.

Use when:
  - Sprint 3.34 S1 biography write-back produced bad output that needs to
    go away quickly, but no `biographies_YYYY-MM-DD.json` backup is
    appropriate (e.g. the backup predates the bad write by weeks).
  - You want to disable the feature AND clean existing artifacts in one
    pass, rather than waiting for the next weekly run with the env var
    flipped.

--dry-run is REQUIRED on the first invocation. Re-run without it to apply.

Unlike `restore_biographies.py`, this tool does NOT need a backup file —
it just inspects each contact's current biography and removes the block.

Usage
-----
    # Always start with dry-run
    python scripts/strip_omnichannel_blocks.py --dry-run

    # Apply after inspecting dry-run output
    python scripts/strip_omnichannel_blocks.py

    # Skip interactive prompt (for cron / unattended)
    python scripts/strip_omnichannel_blocks.py --yes
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import PeopleAPIClient
from auth import authenticate
from harvester.crm_omnichannel import OMNICHANNEL_MARKER, strip_block

logger = logging.getLogger("strip_omnichannel_blocks")


class _AuthAbort(RuntimeError):
    """Abort the whole run on 401/403 rather than silently per-contact-failing."""


def _classify_api_error(exc: Exception) -> str:
    """Bucket API errors: auth / permanent / transient (see restore_biographies.py)."""
    text = str(exc).lower()
    if "401" in text or "unauthenticated" in text:
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
_last_api_call: list[float] = [0.0]


def _throttle() -> None:
    """Hold to ≤55 QPM — Google People's write cap is 60 QPM and server-side
    backoff when we exceed it surfaces as transient errors that conflate
    with real failures. Mirrors `scripts/restore_biographies.py`."""
    import time as _time
    elapsed = _time.monotonic() - _last_api_call[0]
    if elapsed < _PEOPLE_API_MIN_INTERVAL_SECONDS:
        _time.sleep(_PEOPLE_API_MIN_INTERVAL_SECONDS - elapsed)
    _last_api_call[0] = _time.monotonic()


def strip_one(
    client: PeopleAPIClient, rn: str, *, dry_run: bool,
) -> str:
    """Process one contact. Returns status string for aggregation."""
    _throttle()
    try:
        person = client.get_contact(rn, person_fields="biographies,metadata")
    except Exception as e:
        kind = _classify_api_error(e)
        if kind == "auth":
            raise _AuthAbort(f"auth failure on {rn}: {e}") from e
        return f"fetch-error-{kind}"

    bios = person.get("biographies", [])
    current = bios[0].get("value", "") if bios else ""

    if OMNICHANNEL_MARKER not in current:
        return "no-block"

    new_bio = strip_block(current)

    if new_bio.strip() == current.strip():
        # strip_block left it unchanged — e.g. malformed block ignored.
        return "no-change"

    if dry_run:
        return f"would-strip (len {len(current)} → {len(new_bio)})"

    _throttle()
    etag = person.get("etag")
    body = {"biographies": [{"value": new_bio, "contentType": "TEXT_PLAIN"}]}
    client.update_contact(rn, etag, body, update_fields="biographies")
    return "stripped"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without calling the API")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation prompt")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after processing N contacts (testing aid)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.dry_run and not args.yes:
        print()
        print("⚠️  About to STRIP Omnichannel blocks from EVERY contact's biography.")
        print("    This is a write operation against Google People API.")
        print("    The rest of each biography (other marker blocks + user text)")
        print("    will be preserved verbatim.")
        response = input("    Continue? (type 'yes' to proceed): ")
        if response.strip().lower() != "yes":
            print("Aborted by user.")
            return 1

    creds = authenticate()
    client = PeopleAPIClient(creds)

    # Iterate all contacts. get_all_contacts yields pages; we only need
    # resourceName here — the per-contact update re-fetches for freshest etag.
    print("Fetching contacts…")
    all_contacts = client.get_all_contacts(person_fields="names,metadata")
    print(f"  {len(all_contacts)} contacts")

    stats = {
        "stripped": 0, "no-block": 0, "no-change": 0, "would-strip": 0,
        "fetch-permanent": 0, "fetch-transient": 0, "update-errors": 0,
    }
    processed = 0
    auth_aborted = False

    for contact in all_contacts:
        if args.limit and processed >= args.limit:
            print(f"  Reached --limit={args.limit}, stopping")
            break
        rn = contact.get("resourceName")
        if not rn:
            continue
        try:
            status = strip_one(client, rn, dry_run=args.dry_run)
            if status == "stripped":
                stats["stripped"] += 1
            elif status == "no-block":
                stats["no-block"] += 1
            elif status == "no-change":
                stats["no-change"] += 1
            elif status.startswith("would-strip"):
                stats["would-strip"] += 1
            elif status == "fetch-error-permanent":
                stats["fetch-permanent"] += 1
                logger.info(f"{rn}: {status}")
            elif status == "fetch-error-transient":
                stats["fetch-transient"] += 1
                logger.warning(f"{rn}: {status}")
        except _AuthAbort as e:
            print(f"\nAUTH FAILURE — aborting: {e}", file=sys.stderr)
            auth_aborted = True
            break
        except Exception as e:
            kind = _classify_api_error(e)
            if kind == "auth":
                print(f"\nAUTH FAILURE on update ({rn}) — aborting: {e}", file=sys.stderr)
                auth_aborted = True
                break
            stats["update-errors"] += 1
            logger.warning(f"{rn}: {kind}: {e}")

        processed += 1
        if processed % 100 == 0:
            print(f"  progress: {processed}/{len(all_contacts)}  stats={stats}")

    print()
    print(f"Done. Stats: {stats}")
    if auth_aborted:
        print("Run aborted on auth failure — stats above reflect partial progress only.")
        return 2
    if stats["fetch-transient"] > 0:
        print(f"⚠ {stats['fetch-transient']} transient fetch failures — re-run to retry.")
    if args.dry_run and stats["would-strip"] > 0:
        print(f"To apply, re-run without --dry-run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

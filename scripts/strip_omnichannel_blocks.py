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


def strip_one(
    client: PeopleAPIClient, rn: str, *, dry_run: bool,
) -> str:
    """Process one contact. Returns status string for aggregation."""
    try:
        person = client.get_contact(rn, person_fields="biographies,metadata")
    except Exception as e:
        return f"fetch-error:{e}"

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

    stats = {"stripped": 0, "no-block": 0, "no-change": 0,
             "would-strip": 0, "errors": 0}
    processed = 0

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
            elif status.startswith("fetch-error"):
                stats["errors"] += 1
                logger.warning(f"{rn}: {status}")
        except Exception as e:
            logger.warning(f"{rn}: {e}")
            stats["errors"] += 1

        processed += 1
        if processed % 100 == 0:
            print(f"  progress: {processed}/{len(all_contacts)}  stats={stats}")

    print()
    print(f"Done. Stats: {stats}")
    if args.dry_run and stats["would-strip"] > 0:
        print(f"To apply, re-run without --dry-run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

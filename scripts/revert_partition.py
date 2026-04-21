#!/usr/bin/env python3
"""
Revert a GCS interaction partition (or any data blob) to a prior soft-deleted
generation.

The `contacts-refiner-data` bucket has `soft_delete_policy` with a 7-day
retention, so every overwrite leaves the previous object recoverable via
`gcloud storage ls --soft-deleted`. This tool wraps list + restore so a
bad harvest run (corrupt records, schema drift) can be rolled back in one
command without shell-surgery on generation numbers.

Modes
-----
  list:    show live + soft-deleted generations of one blob (no side effects)
  restore: copy a specific soft-deleted generation over the live blob

Dry-run is the default for `restore`; pass --apply to actually overwrite.

Usage
-----
    # Inspect recent versions of the April interactions partition
    uv run python scripts/revert_partition.py list \\
        data/interactions/2026-04.jsonl

    # Dry-run a rollback (default)
    uv run python scripts/revert_partition.py restore \\
        data/interactions/2026-04.jsonl 1776770296776327

    # Apply the rollback
    uv run python scripts/revert_partition.py restore \\
        data/interactions/2026-04.jsonl 1776770296776327 --apply
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

GCS_BUCKET = os.getenv("GCS_BUCKET", "contacts-refiner-data")

logger = logging.getLogger("revert_partition")

SOFT_DELETE_RETENTION_DAYS = 7


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _list_versions(blob_path: str) -> None:
    """Print live + soft-deleted generations for a single blob."""
    uri = f"gs://{GCS_BUCKET}/{blob_path}"

    print(f"# Live version\n")
    live = _run(["gcloud", "storage", "ls", "--long", uri])
    if live.returncode != 0:
        print(f"  (no live object — it may have been deleted)")
    else:
        print(live.stdout.strip() or "  (empty)")

    print(f"\n# Soft-deleted generations (kept {SOFT_DELETE_RETENTION_DAYS} days)\n")
    soft = _run(["gcloud", "storage", "ls", "--soft-deleted", "--long", uri])
    if soft.returncode != 0 or not soft.stdout.strip():
        print("  (none — nothing to restore)")
        return
    print(soft.stdout.strip())
    print(
        "\nTo roll back to one of these, copy its generation number (the "
        "digits after `#`) and run:\n"
        f"  revert_partition.py restore {blob_path} <generation> --apply"
    )


def _restore(blob_path: str, generation: str, apply: bool) -> int:
    """Copy a soft-deleted generation over the live blob."""
    src = f"gs://{GCS_BUCKET}/{blob_path}#{generation}"
    dst = f"gs://{GCS_BUCKET}/{blob_path}"

    # Verify the generation exists as a soft-deleted version before doing
    # anything destructive. `gcloud storage ls --soft-deleted` returns zero
    # even when nothing matches, so we have to inspect stdout.
    probe = _run(["gcloud", "storage", "ls", "--soft-deleted", "--long", src])
    if probe.returncode != 0 or not probe.stdout.strip():
        print(
            f"error: generation {generation} not found in soft-deleted "
            f"versions of {blob_path}.\nRun:\n"
            f"  revert_partition.py list {blob_path}\n"
            f"to see available generations."
        )
        return 2

    print(f"source:      {src}")
    print(f"destination: {dst}")
    print(f"mode:        {'APPLY' if apply else 'dry-run'}\n")

    if not apply:
        print(
            "dry-run only — not copying. Re-run with --apply to overwrite "
            "the live blob with this generation."
        )
        return 0

    # `gcloud storage cp` with a generation-qualified source reads the
    # soft-deleted version and writes a new live generation on the dest.
    cp = _run(["gcloud", "storage", "cp", src, dst])
    if cp.returncode != 0:
        print("error: copy failed")
        print(cp.stderr)
        return 1

    print("✅ restored")
    print(cp.stdout.strip() or cp.stderr.strip())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show generations of a blob")
    p_list.add_argument("blob", help="path within bucket, e.g. data/interactions/2026-04.jsonl")

    p_restore = sub.add_parser("restore", help="roll a blob back to a generation")
    p_restore.add_argument("blob", help="path within bucket")
    p_restore.add_argument("generation", help="numeric generation from `list`")
    p_restore.add_argument(
        "--apply",
        action="store_true",
        help="actually overwrite the live blob (default: dry-run)",
    )

    args = parser.parse_args()

    if args.cmd == "list":
        _list_versions(args.blob)
        return 0
    if args.cmd == "restore":
        return _restore(args.blob, args.generation, args.apply)
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())

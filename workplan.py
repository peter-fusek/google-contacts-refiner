"""
Workplan generator and tracker.
Creates a structured JSON workplan from analysis results.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from config import DATA_DIR, BATCH_SIZE, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM
from analyzer import summarize_analysis


def generate_workplan(
    analysis_results: list[dict],
    duplicates: list[dict] = None,
    labels_analysis: dict = None,
) -> Path:
    """
    Generate a workplan JSON file from analysis results.

    Args:
        analysis_results: Output from analyze_all_contacts().
        duplicates: Output from find_duplicates().
        labels_analysis: Output from analyze_labels().

    Returns:
        Path to the workplan file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workplan_path = DATA_DIR / f"workplan_{timestamp}.json"

    # Compute batches
    # Sort by: contacts with most high-confidence changes first
    sorted_results = sorted(
        analysis_results,
        key=lambda r: (-r["stats"]["high"], -r["stats"]["total"]),
    )

    batches = []
    for i in range(0, len(sorted_results), BATCH_SIZE):
        batch_contacts = sorted_results[i:i + BATCH_SIZE]
        batch_stats = {
            "high": sum(c["stats"]["high"] for c in batch_contacts),
            "medium": sum(c["stats"]["medium"] for c in batch_contacts),
            "low": sum(c["stats"]["low"] for c in batch_contacts),
            "total_changes": sum(c["stats"]["total"] for c in batch_contacts),
            "contacts": len(batch_contacts),
        }
        batches.append({
            "batch_num": len(batches) + 1,
            "contacts": batch_contacts,
            "stats": batch_stats,
            "status": "pending",
        })

    # Build summary
    summary = summarize_analysis(analysis_results)

    workplan = {
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "version": "1.0",
        },
        "summary": {
            **summary,
            "total_batches": len(batches),
            "batch_size": BATCH_SIZE,
        },
        "batches": batches,
        "duplicates": duplicates or [],
        "labels": labels_analysis or {},
    }

    with open(workplan_path, "w", encoding="utf-8") as f:
        json.dump(workplan, f, ensure_ascii=False, indent=2)

    return workplan_path


def load_workplan(path: Path) -> dict:
    """Load a workplan from file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_latest_workplan() -> Path | None:
    """Find the most recent workplan file."""
    plans = sorted(DATA_DIR.glob("workplan_*.json"), reverse=True)
    return plans[0] if plans else None


def format_workplan_summary(workplan: dict) -> str:
    """Format workplan summary for display."""
    s = workplan["summary"]
    lines = [
        "═══════════════════════════════════════════",
        "          WORKPLAN — SUMMARY",
        "═══════════════════════════════════════════",
        "",
        f"  Contacts with findings:  {s['total_contacts_with_changes']}",
        f"  Total changes:           {s['total_changes']}",
        f"  Batches:                 {s['total_batches']}  ({s['batch_size']} contacts each)",
        "",
        "  Changes by confidence:",
        f"    🟢 HIGH (90%+):    {s['by_confidence']['high']}",
        f"    🟡 MEDIUM (60-90%): {s['by_confidence']['medium']}",
        f"    🔴 LOW (<60%):     {s['by_confidence']['low']}",
        "",
        "  Changes by type:",
    ]

    for field_type, count in sorted(s["by_field_type"].items(), key=lambda x: -x[1]):
        if count > 0:
            label_map = {
                "names": "Names",
                "phones": "Phones",
                "emails": "Emails",
                "addresses": "Addresses",
                "organizations": "Organizations",
                "enrichment_notes": "From notes",
                "enrichment_email": "From email",
                "other": "Other",
            }
            label = label_map.get(field_type, field_type)
            lines.append(f"    {label:20s} {count}")

    # Info items
    info = s.get("info_items", {})
    if info.get("duplicates") or info.get("invalid"):
        lines.append("")
        lines.append("  Info findings:")
        if info.get("duplicates"):
            lines.append(f"    Duplicate values within contacts: {info['duplicates']}")
        if info.get("invalid"):
            lines.append(f"    Invalid values: {info['invalid']}")

    # Duplicates
    dupes = workplan.get("duplicates", [])
    if dupes:
        lines.append("")
        lines.append(f"  🔍 Potential duplicates: {len(dupes)} groups")

    lines.append("")
    lines.append("═══════════════════════════════════════════")

    return "\n".join(lines)

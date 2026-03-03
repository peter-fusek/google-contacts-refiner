"""
Workplan generator and tracker.
Creates a structured JSON workplan from analysis results.
"""
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
        "          PLÁN PRÁCE — SÚHRN",
        "═══════════════════════════════════════════",
        "",
        f"  Kontakty s nálezmi:  {s['total_contacts_with_changes']}",
        f"  Celkom zmien:        {s['total_changes']}",
        f"  Počet batchov:       {s['total_batches']}  (po {s['batch_size']} kontaktov)",
        "",
        "  Zmeny podľa istoty:",
        f"    🟢 HIGH (90%+):    {s['by_confidence']['high']}",
        f"    🟡 MEDIUM (60-90%): {s['by_confidence']['medium']}",
        f"    🔴 LOW (<60%):     {s['by_confidence']['low']}",
        "",
        "  Zmeny podľa typu:",
    ]

    for field_type, count in sorted(s["by_field_type"].items(), key=lambda x: -x[1]):
        if count > 0:
            label_map = {
                "names": "Mená",
                "phones": "Telefóny",
                "emails": "Emaily",
                "addresses": "Adresy",
                "organizations": "Organizácie",
                "enrichment_notes": "Z poznámok",
                "enrichment_email": "Z emailu",
                "other": "Ostatné",
            }
            label = label_map.get(field_type, field_type)
            lines.append(f"    {label:20s} {count}")

    # Info items
    info = s.get("info_items", {})
    if info.get("duplicates") or info.get("invalid"):
        lines.append("")
        lines.append("  Informačné nálezy:")
        if info.get("duplicates"):
            lines.append(f"    Duplicitné hodnoty v kontaktoch: {info['duplicates']}")
        if info.get("invalid"):
            lines.append(f"    Nevalidné hodnoty: {info['invalid']}")

    # Duplicates
    dupes = workplan.get("duplicates", [])
    if dupes:
        lines.append("")
        lines.append(f"  🔍 Potenciálne duplikáty: {len(dupes)} skupín")

    lines.append("")
    lines.append("═══════════════════════════════════════════")

    return "\n".join(lines)

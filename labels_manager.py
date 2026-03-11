"""
Labels/Contact Groups analysis and management.
Analyzes existing labels, their members, and suggests label assignments.
"""
from typing import Optional
from collections import defaultdict

from utils import get_display_name, get_resource_name


def analyze_labels(
    groups: list[dict],
    group_members: dict[str, list[str]],
    contacts: list[dict],
) -> dict:
    """
    Analyze labels and their members.

    Returns:
        {
            'labels': [
                {
                    'resourceName': str,
                    'name': str,
                    'member_count': int,
                    'members': [{'resourceName': str, 'displayName': str}],
                    'org_distribution': {'Firma A': 3, 'Firma B': 1},
                },
                ...
            ],
            'unlabeled_contacts': int,
            'suggestions': [...],
        }
    """
    # Build contact lookup
    contact_lookup = {get_resource_name(c): c for c in contacts}

    # Build reverse index: contact_rn → [label_rns]
    contact_labels = defaultdict(list)
    for group_rn, member_rns in group_members.items():
        for member_rn in member_rns:
            contact_labels[member_rn].append(group_rn)

    # Analyze each label
    label_data = []
    for group in groups:
        if group.get("groupType") != "USER_CONTACT_GROUP":
            continue

        rn = group.get("resourceName", "")
        name = group.get("name", "")
        members_rns = group_members.get(rn, [])

        members_info = []
        org_dist = defaultdict(int)

        for member_rn in members_rns:
            contact = contact_lookup.get(member_rn)
            if contact:
                display = get_display_name(contact)
                members_info.append({
                    "resourceName": member_rn,
                    "displayName": display,
                })
                # Track org distribution
                orgs = contact.get("organizations", [])
                if orgs:
                    org_name = orgs[0].get("name", "")
                    if org_name:
                        org_dist[org_name] += 1

        label_data.append({
            "resourceName": rn,
            "name": name,
            "member_count": len(members_rns),
            "members": members_info,
            "org_distribution": dict(org_dist),
        })

    # Count unlabeled contacts
    all_labeled = set()
    for members in group_members.values():
        all_labeled.update(members)

    unlabeled = [
        c for c in contacts
        if get_resource_name(c) not in all_labeled
    ]

    # Generate suggestions
    suggestions = _suggest_label_assignments(label_data, contacts, contact_labels, contact_lookup)

    return {
        "labels": sorted(label_data, key=lambda x: -x["member_count"]),
        "unlabeled_contacts": len(unlabeled),
        "suggestions": suggestions,
    }


def _suggest_label_assignments(
    label_data: list[dict],
    contacts: list[dict],
    contact_labels: dict[str, list[str]],
    contact_lookup: dict[str, dict],
) -> list[dict]:
    """
    Suggest label assignments based on organization matching.

    If a label has a dominant org (>50% of members from same org),
    suggest adding other contacts from that org to the label.
    """
    suggestions = []

    # Find labels with dominant org
    for label in label_data:
        org_dist = label["org_distribution"]
        total_members = label["member_count"]
        if total_members < 2 or not org_dist:
            continue

        for org_name, count in org_dist.items():
            if count / total_members >= 0.5 and count >= 2:
                # This label has a dominant org
                # Find contacts from this org NOT in this label
                existing_members = {m["resourceName"] for m in label["members"]}

                for person in contacts:
                    rn = get_resource_name(person)
                    if rn in existing_members:
                        continue

                    person_orgs = person.get("organizations", [])
                    for org in person_orgs:
                        if org.get("name", "") == org_name:
                            suggestions.append({
                                "type": "add_to_label",
                                "label_name": label["name"],
                                "label_rn": label["resourceName"],
                                "contact_rn": rn,
                                "contact_name": get_display_name(person),
                                "reason": f"same organization ({org_name})",
                                "confidence": 0.60,
                            })
                            break

    return suggestions


def format_labels_report(analysis: dict) -> str:
    """Format label analysis for display."""
    lines = [
        "═══ LABELS ANALYSIS ═══",
        "",
    ]

    labels = analysis["labels"]
    if not labels:
        lines.append("No user labels/groups found.")
    else:
        lines.append(f"Total {len(labels)} labels:")
        lines.append("")
        for label in labels:
            name = label["name"]
            count = label["member_count"]
            lines.append(f"  📁 {name} ({count} contacts)")
            if label["org_distribution"]:
                top_orgs = sorted(label["org_distribution"].items(), key=lambda x: -x[1])[:3]
                org_str = ", ".join(f"{org}: {cnt}" for org, cnt in top_orgs)
                lines.append(f"     Organizations: {org_str}")

    lines.append("")
    lines.append(f"Contacts without label: {analysis['unlabeled_contacts']}")

    suggestions = analysis["suggestions"]
    if suggestions:
        lines.append("")
        lines.append(f"💡 {len(suggestions)} suggestions for label assignment:")
        for s in suggestions[:20]:  # Show max 20
            lines.append(f"  → {s['contact_name']} → label \"{s['label_name']}\" ({s['reason']})")

    return "\n".join(lines)

"""
FollowUp Scoring — rank contacts for reconnection using interaction history + LinkedIn signals.

Combines Gmail/Calendar interaction data (from InteractionScanner) with LinkedIn signals
to produce a ranked list of the best contacts to follow up with each week.

Key differences from LTNS:
- LinkedIn job_change signals bypass the months_gap minimum (recent contacts surface)
- Additive scoring: interaction_score + linkedin_bonus + completeness_bonus
- Contact completeness (email, phone, org, LinkedIn URL) contributes to score
- Lower interaction threshold (1 vs 2) since LinkedIn signals compensate
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    DATA_DIR,
    FOLLOWUP_COMPLETENESS_WEIGHT,
    FOLLOWUP_LINKEDIN_WEIGHTS,
    FOLLOWUP_MIN_INTERACTIONS,
    FOLLOWUP_MIN_MONTHS,
    FOLLOWUP_SCORES_FILE,
    FOLLOWUP_TOP_N,
)

logger = logging.getLogger("contacts-refiner.followup")


def load_linkedin_signals(path: Optional[Path] = None) -> dict[str, dict]:
    """Load LinkedIn signals from local JSON file, keyed by resourceName."""
    if path is None:
        path = DATA_DIR / "linkedin_signals.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("signals", {})
    except FileNotFoundError:
        logger.info("FollowUp: No linkedin_signals.json found — scoring without LinkedIn data")
        return {}
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"FollowUp: Failed to parse linkedin_signals.json: {e}")
        return {}


def _classify_url(url: str) -> str:
    """Classify URL type — mirrors interaction_scanner._classify_url."""
    url_lower = url.lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "facebook.com" in url_lower or "fb.com" in url_lower:
        return "facebook"
    if "twitter.com" in url_lower or "x.com" in url_lower:
        return "twitter"
    if "instagram.com" in url_lower:
        return "instagram"
    if "github.com" in url_lower:
        return "github"
    return "website"


@dataclass
class FollowUpScore:
    resource_name: str
    name: str
    # Interaction signals
    last_date: Optional[str]
    interaction_count: int
    months_gap: float
    # LinkedIn signal
    linkedin_signal: Optional[str]
    linkedin_signal_text: Optional[str]
    linkedin_headline: Optional[str]
    linkedin_current_role: Optional[str]
    linkedin_url: Optional[str]
    linkedin_scanned_at: Optional[str]
    # Contact completeness (0-4)
    completeness: int
    has_email: bool
    has_phone: bool
    has_org: bool
    has_linkedin_url: bool
    # Score components
    score_interaction: float
    score_linkedin: float
    score_completeness: float
    score_total: float
    # Contact metadata
    org: str
    title: str
    emails: list[str]
    urls: list[dict] = field(default_factory=list)
    # Set after scoring
    rank: int = 0
    followup_prompt: Optional[str] = None


def _compute_completeness(
    contact: dict,
    linkedin_signals: dict[str, dict],
) -> tuple[int, bool, bool, bool, bool]:
    """Compute contact completeness score (0-4)."""
    has_email = bool(contact.get("emailAddresses"))
    has_phone = bool(contact.get("phoneNumbers"))
    has_org = bool(contact.get("organizations"))
    has_linkedin_url = any(
        "linkedin.com" in u.get("value", "").lower()
        for u in contact.get("urls", [])
    )
    # Also count if we have a LinkedIn signal for this contact
    if not has_linkedin_url:
        rn = contact.get("resourceName", "")
        has_linkedin_url = rn in linkedin_signals

    score = sum([has_email, has_phone, has_org, has_linkedin_url])
    return score, has_email, has_phone, has_org, has_linkedin_url


def _get_last_activity(
    rn: str,
    emails: set[str],
    interactions: dict[str, dict],
) -> tuple[Optional[str], int]:
    """Get last interaction date and count for a contact across all emails.

    Returns (last_date, interaction_count) where interaction_count counts
    distinct signal types (1 for email exists, 1 for meeting exists).
    """
    latest_date = None
    has_email = False
    has_meeting = False

    for email in emails:
        data = interactions.get(email, {})
        if isinstance(data, str):
            data = {"last_email": {"date": data, "subject": "", "snippet": ""}}

        le = data.get("last_email", {})
        if le and le.get("date"):
            has_email = True
            if not latest_date or le["date"] > latest_date:
                latest_date = le["date"]

        lm = data.get("last_meeting", {})
        if lm and lm.get("date"):
            has_meeting = True
            if not latest_date or lm["date"] > latest_date:
                latest_date = lm["date"]

    interaction_count = int(has_email) + int(has_meeting)
    return latest_date, interaction_count


def score_contacts(
    contacts: list[dict],
    interactions: dict[str, dict],
    contact_emails: dict[str, set[str]],
    linkedin_signals: dict[str, dict],
    top_n: int = FOLLOWUP_TOP_N,
    min_interactions: int = FOLLOWUP_MIN_INTERACTIONS,
    min_months: float = FOLLOWUP_MIN_MONTHS,
) -> list[FollowUpScore]:
    """Score all contacts and return top_n sorted by score_total descending.

    LinkedIn job_change signals bypass the min_months and min_interactions filters.
    """
    today = datetime.now(timezone.utc)
    contacts_by_rn = {c.get("resourceName", ""): c for c in contacts}
    candidates: list[FollowUpScore] = []

    for rn, emails in contact_emails.items():
        last_date, interaction_count = _get_last_activity(rn, emails, interactions)
        li_signal = linkedin_signals.get(rn, {})
        li_type = li_signal.get("signal_type")
        is_job_change = li_type == "job_change"

        # Calculate months gap
        if last_date:
            try:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                months_gap = (today - last_dt).days / 30.0
            except ValueError:
                months_gap = 0.0
        else:
            # No interaction history — only surface if job_change
            months_gap = 0.0
            if not is_job_change:
                continue

        # Filter: min_months and min_interactions (bypassed for job_change)
        if not is_job_change:
            if last_date and months_gap < min_months:
                continue
            if interaction_count < min_interactions:
                continue

        # Extract contact info
        contact = contacts_by_rn.get(rn, {})
        names = contact.get("names", [{}])
        display_name = names[0].get("displayName", "") if names else ""
        if not display_name:
            continue  # Skip contacts without names

        orgs = contact.get("organizations", [])
        org = orgs[0].get("name", "") if orgs else ""
        title = orgs[0].get("title", "") if orgs else ""

        urls = []
        for url_entry in contact.get("urls", []):
            url_val = url_entry.get("value", "")
            if url_val:
                urls.append({"url": url_val, "type": _classify_url(url_val)})

        # Completeness
        completeness, has_email, has_phone, has_org, has_linkedin_url = _compute_completeness(
            contact, linkedin_signals,
        )

        # Score components (additive)
        score_interaction = interaction_count * months_gap
        score_linkedin = FOLLOWUP_LINKEDIN_WEIGHTS.get(li_type, 0.0) if li_type else 0.0
        score_completeness = completeness * FOLLOWUP_COMPLETENESS_WEIGHT
        score_total = score_interaction + score_linkedin + score_completeness

        # LinkedIn metadata
        li_url = li_signal.get("linkedin_url")
        if not li_url:
            li_urls = [u for u in urls if u["type"] == "linkedin"]
            li_url = li_urls[0]["url"] if li_urls else None

        candidates.append(FollowUpScore(
            resource_name=rn,
            name=display_name,
            last_date=last_date,
            interaction_count=interaction_count,
            months_gap=round(months_gap, 1),
            linkedin_signal=li_type,
            linkedin_signal_text=li_signal.get("signal_text"),
            linkedin_headline=li_signal.get("headline"),
            linkedin_current_role=li_signal.get("current_role"),
            linkedin_url=li_url,
            linkedin_scanned_at=li_signal.get("scanned_at"),
            completeness=completeness,
            has_email=has_email,
            has_phone=has_phone,
            has_org=has_org,
            has_linkedin_url=has_linkedin_url,
            score_interaction=round(score_interaction, 1),
            score_linkedin=score_linkedin,
            score_completeness=score_completeness,
            score_total=round(score_total, 1),
            org=org,
            title=title,
            emails=list(emails),
            urls=urls,
        ))

    # Sort by score descending, take top N
    candidates.sort(key=lambda c: c.score_total, reverse=True)
    result = candidates[:top_n]

    # Assign ranks
    for i, c in enumerate(result, 1):
        c.rank = i

    logger.info(
        f"FollowUp: {len(candidates)} candidates total, "
        f"top {len(result)} selected"
    )
    return result


def build_followup_scores_json(scored_list: list[FollowUpScore]) -> dict:
    """Serialize scored list into dashboard-optimized JSON structure."""
    scores = {}
    for s in scored_list:
        scores[s.resource_name] = {
            "resourceName": s.resource_name,
            "name": s.name,
            "score_total": s.score_total,
            "rank": s.rank,
            "score_breakdown": {
                "interaction": s.score_interaction,
                "linkedin": s.score_linkedin,
                "completeness": s.score_completeness,
            },
            "interaction": {
                "last_date": s.last_date,
                "months_gap": s.months_gap,
                "count": s.interaction_count,
            },
            "linkedin": {
                "signal_type": s.linkedin_signal,
                "signal_text": s.linkedin_signal_text,
                "headline": s.linkedin_headline,
                "current_role": s.linkedin_current_role,
                "scanned_at": s.linkedin_scanned_at,
                "url": s.linkedin_url,
            } if s.linkedin_signal else None,
            "contact": {
                "org": s.org,
                "title": s.title,
                "has_email": s.has_email,
                "has_phone": s.has_phone,
                "has_org": s.has_org,
                "has_linkedin_url": s.has_linkedin_url,
                "completeness": s.completeness,
                "emails": s.emails,
                "urls": s.urls,
            },
            "followup_prompt": s.followup_prompt,
        }

    # Compute stats
    li_types = [s.linkedin_signal for s in scored_list if s.linkedin_signal]
    stats = {
        "job_change": sum(1 for t in li_types if t == "job_change"),
        "active": sum(1 for t in li_types if t == "active"),
        "profile_only": sum(1 for t in li_types if t == "profile"),
        "no_linkedin": sum(1 for s in scored_list if not s.linkedin_signal),
        "avg_completeness": round(
            sum(s.completeness for s in scored_list) / len(scored_list), 1
        ) if scored_list else 0,
    }

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "count": len(scored_list),
        "scores": scores,
        "stats": stats,
    }


def upload_followup_scores_to_gcs():
    """Upload followup_scores.json to GCS so the dashboard stays fresh."""
    from config import ENVIRONMENT
    if ENVIRONMENT == "cloud":
        return  # GCS FUSE handles sync in cloud mode

    try:
        import os
        from google.cloud import storage
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/dashboard-reader-key.json")
        if os.path.exists(creds_path):
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", creds_path)
        client = storage.Client()
        bucket = client.bucket("contacts-refiner-data")
        blob = bucket.blob("data/followup_scores.json")
        blob.upload_from_filename(str(FOLLOWUP_SCORES_FILE))
        logger.info("FollowUp: Scores uploaded to GCS")
    except Exception as e:
        logger.warning(f"FollowUp: GCS upload failed (non-fatal): {e}")

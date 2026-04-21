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
    FOLLOWUP_BEEPER_AWAITING_MY_REPLY,
    FOLLOWUP_BEEPER_BUSINESS_HOURS,
    FOLLOWUP_BEEPER_BUSINESS_HOURS_RATIO,
    FOLLOWUP_BEEPER_BUSINESS_KEYWORDS,
    FOLLOWUP_BEEPER_INBOUND_HEAVY,
    FOLLOWUP_BEEPER_INBOUND_HEAVY_DELTA,
    FOLLOWUP_BEEPER_KPI_FILE,
    FOLLOWUP_BEEPER_LONG_SILENCE_DAYS,
    FOLLOWUP_BEEPER_LONG_SILENCE_PENALTY,
    FOLLOWUP_BEEPER_MAX,
    FOLLOWUP_BEEPER_MIN,
    FOLLOWUP_BEEPER_MULTICHANNEL,
    FOLLOWUP_BEEPER_STALE_SENT_MIN_COUNT,
    FOLLOWUP_BEEPER_STALE_SENT_PENALTY,
    FOLLOWUP_COMPLETENESS_WEIGHT,
    FOLLOWUP_EXEC_TITLE_BONUS,
    FOLLOWUP_EXEC_TITLE_KEYWORDS,
    FOLLOWUP_LINKEDIN_WEIGHTS,
    FOLLOWUP_MAX_AGE_MONTHS,
    FOLLOWUP_MAX_MONTHS_CONTRIBUTION,
    FOLLOWUP_MIN_INTERACTIONS,
    FOLLOWUP_MIN_JOB_CHANGE_HEADLINE_LEN,
    FOLLOWUP_MIN_MONTHS,
    FOLLOWUP_OWN_COMPANY_DOMAINS,
    FOLLOWUP_OWN_COMPANY_ORG_KEYWORDS,
    FOLLOWUP_PERSONAL_EMAIL_DOMAINS,
    FOLLOWUP_PERSONAL_PENALTY,
    FOLLOWUP_SCORES_FILE,
    FOLLOWUP_TOP_N,
)
from harvester.scoring_signals import (
    BeeperWeights,
    ContactKPI,
    compute_beeper_bonus,
    load_kpis_from_json,
)
from interaction_scanner import _classify_url

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


def load_contact_kpis(path: Optional[Path] = None) -> dict[str, ContactKPI]:
    """Load ContactKPI rollups from local JSON file, keyed by resourceName.

    Returns empty dict if file missing or schema_version mismatches —
    scoring falls back gracefully to pre-Beeper behaviour with
    score_beeper=0 for every contact. Harvester hasn't run yet → no
    regression; harvester ran but crashed mid-write → we ignore the
    broken file rather than scoring on half-baked data.
    """
    if path is None:
        path = FOLLOWUP_BEEPER_KPI_FILE
    try:
        kpis = load_kpis_from_json(path)
        if kpis:
            logger.info(f"FollowUp: loaded {len(kpis)} ContactKPI records from {path}")
        return kpis
    except Exception as e:
        logger.warning(f"FollowUp: failed to load contact_kpis.json: {e}")
        return {}


_BEEPER_WEIGHTS = BeeperWeights(
    awaiting_my_reply=FOLLOWUP_BEEPER_AWAITING_MY_REPLY,
    multichannel=FOLLOWUP_BEEPER_MULTICHANNEL,
    business_keywords=FOLLOWUP_BEEPER_BUSINESS_KEYWORDS,
    business_hours=FOLLOWUP_BEEPER_BUSINESS_HOURS,
    inbound_heavy=FOLLOWUP_BEEPER_INBOUND_HEAVY,
    stale_sent_penalty=FOLLOWUP_BEEPER_STALE_SENT_PENALTY,
    long_silence_penalty=FOLLOWUP_BEEPER_LONG_SILENCE_PENALTY,
    cap_max=FOLLOWUP_BEEPER_MAX,
    cap_min=FOLLOWUP_BEEPER_MIN,
    business_hours_ratio_threshold=FOLLOWUP_BEEPER_BUSINESS_HOURS_RATIO,
    inbound_heavy_delta=FOLLOWUP_BEEPER_INBOUND_HEAVY_DELTA,
    stale_sent_min_count=FOLLOWUP_BEEPER_STALE_SENT_MIN_COUNT,
    long_silence_days=FOLLOWUP_BEEPER_LONG_SILENCE_DAYS,
)


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
    # Business relevance
    is_exec: bool
    is_likely_personal: bool
    # Score components
    score_interaction: float
    score_linkedin: float
    score_completeness: float
    score_exec: float
    personal_multiplier: float
    score_beeper: float                        # ContactKPI-driven bonus (#150), capped [FOLLOWUP_BEEPER_MIN, FOLLOWUP_BEEPER_MAX]
    score_total: float
    # Contact metadata
    org: str
    title: str
    emails: list[str]
    urls: list[dict] = field(default_factory=list)
    # Set after scoring
    rank: int = 0
    followup_prompt: Optional[str] = None
    # Beeper metadata (defaults = "no Beeper signal") — placed at end to preserve
    # the dataclass non-default-then-default ordering.
    beeper_channel_primary: Optional[str] = None
    beeper_awaiting_reply_side: Optional[str] = None
    beeper_messages_30d_in: int = 0
    beeper_messages_30d_out: int = 0
    beeper_channels_30d: int = 0


def _is_exec_title(title: str, headline: str, current_role: str) -> bool:
    """Title or LinkedIn role contains C-level/founder/director keyword."""
    hay = " ".join(filter(None, [title, headline, current_role])).lower()
    return any(kw in hay for kw in FOLLOWUP_EXEC_TITLE_KEYWORDS)


def _is_likely_personal(
    has_org: bool, has_linkedin_url: bool, title: str, emails: set[str],
) -> bool:
    """Contact looks personal: no company, no title, no LinkedIn, personal-only email domain."""
    if has_org or has_linkedin_url or title:
        return False
    if not emails:
        return True
    domains = {e.split("@")[-1].lower() for e in emails if "@" in e}
    return bool(domains) and all(d in FOLLOWUP_PERSONAL_EMAIL_DOMAINS for d in domains)


def _is_own_company(org: str, emails: set[str]) -> bool:
    """Contact is Peter's own company — exclude from lead digest."""
    org_lower = (org or "").lower()
    if any(kw in org_lower for kw in FOLLOWUP_OWN_COMPANY_ORG_KEYWORDS):
        return True
    domains = {e.split("@")[-1].lower() for e in emails if "@" in e}
    return any(d in FOLLOWUP_OWN_COMPANY_DOMAINS for d in domains)


def _is_valid_job_change(li_signal: dict) -> bool:
    """Reject junk job_change signals (empty or too-short headlines like 'Oh yeah')."""
    if li_signal.get("signal_type") != "job_change":
        return False
    headline = (li_signal.get("headline") or "").strip()
    return len(headline) >= FOLLOWUP_MIN_JOB_CHANGE_HEADLINE_LEN


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
    contact_kpis: Optional[dict[str, ContactKPI]] = None,
    top_n: int = FOLLOWUP_TOP_N,
    min_interactions: int = FOLLOWUP_MIN_INTERACTIONS,
    min_months: float = FOLLOWUP_MIN_MONTHS,
) -> list[FollowUpScore]:
    """Score all contacts and return top_n sorted by score_total descending.

    LinkedIn job_change signals bypass the min_months and min_interactions filters.
    If contact_kpis is provided, each contact's multi-channel Beeper activity
    contributes an additive bonus via compute_beeper_bonus() (#150).
    """
    contact_kpis = contact_kpis or {}
    today = datetime.now(timezone.utc)
    contacts_by_rn = {c.get("resourceName", ""): c for c in contacts}
    candidates: list[FollowUpScore] = []

    for rn, emails in contact_emails.items():
        last_date, interaction_count = _get_last_activity(rn, emails, interactions)
        li_signal = linkedin_signals.get(rn, {})
        li_type = li_signal.get("signal_type")
        is_job_change = _is_valid_job_change(li_signal)
        if li_type == "job_change" and not is_job_change:
            li_type = "profile"  # downgrade junk job_change to profile-only

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

        # Filter: drop very-old-only contacts (5+ years silent) unless LinkedIn job_change
        if not is_job_change and last_date and months_gap > FOLLOWUP_MAX_AGE_MONTHS:
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

        # Filter: exclude Peter's own company — they're colleagues, not leads
        if _is_own_company(org, emails):
            continue

        urls = []
        for url_entry in contact.get("urls", []):
            url_val = url_entry.get("value", "")
            if url_val:
                urls.append({"url": url_val, "type": _classify_url(url_val)})

        # Completeness
        completeness, has_email, has_phone, has_org, has_linkedin_url = _compute_completeness(
            contact, linkedin_signals,
        )

        # Business relevance flags
        li_headline = li_signal.get("headline") or ""
        li_current_role = li_signal.get("current_role") or ""
        is_exec = _is_exec_title(title, li_headline, li_current_role)
        is_likely_personal = _is_likely_personal(has_org, has_linkedin_url, title, emails)

        # Score components
        # Cap gap contribution: 7-year silence is not 3.5× more actionable than 2 years
        capped_gap = min(months_gap, FOLLOWUP_MAX_MONTHS_CONTRIBUTION)
        score_interaction = interaction_count * capped_gap
        score_linkedin = FOLLOWUP_LINKEDIN_WEIGHTS.get(li_type, 0.0) if li_type else 0.0
        score_completeness = completeness * FOLLOWUP_COMPLETENESS_WEIGHT
        score_exec = FOLLOWUP_EXEC_TITLE_BONUS if is_exec else 0.0
        personal_multiplier = FOLLOWUP_PERSONAL_PENALTY if is_likely_personal else 1.0

        # Beeper bonus — 0 if no KPI data (graceful fallback when harvester
        # hasn't run yet or contact has no cross-channel activity)
        kpi = contact_kpis.get(rn)
        if kpi:
            score_beeper = compute_beeper_bonus(kpi, _BEEPER_WEIGHTS, as_of=today)
            w30 = kpi.windows.get("30d")
            beeper_channel_primary = kpi.channel_primary
            beeper_awaiting_side = kpi.last_awaiting_reply_side
            beeper_msgs_in = w30.messages_in if w30 else 0
            beeper_msgs_out = w30.messages_out if w30 else 0
            beeper_channels = len(w30.channels) if w30 else 0
        else:
            score_beeper = 0.0
            beeper_channel_primary = None
            beeper_awaiting_side = None
            beeper_msgs_in = 0
            beeper_msgs_out = 0
            beeper_channels = 0

        base_score = (
            score_interaction + score_linkedin + score_completeness
            + score_exec + score_beeper
        )
        score_total = base_score * personal_multiplier

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
            is_exec=is_exec,
            is_likely_personal=is_likely_personal,
            score_interaction=round(score_interaction, 1),
            score_linkedin=score_linkedin,
            score_completeness=score_completeness,
            score_exec=score_exec,
            personal_multiplier=personal_multiplier,
            score_beeper=round(score_beeper, 1),
            score_total=round(score_total, 1),
            org=org,
            title=title,
            emails=list(emails),
            urls=urls,
            beeper_channel_primary=beeper_channel_primary,
            beeper_awaiting_reply_side=beeper_awaiting_side,
            beeper_messages_30d_in=beeper_msgs_in,
            beeper_messages_30d_out=beeper_msgs_out,
            beeper_channels_30d=beeper_channels,
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
                "exec_bonus": s.score_exec,
                "beeper": s.score_beeper,
                "personal_multiplier": s.personal_multiplier,
            },
            "flags": {
                "is_exec": s.is_exec,
                "is_likely_personal": s.is_likely_personal,
            },
            "beeper": {
                "channel_primary": s.beeper_channel_primary,
                "awaiting_reply_side": s.beeper_awaiting_reply_side,
                "messages_30d_in": s.beeper_messages_30d_in,
                "messages_30d_out": s.beeper_messages_30d_out,
                "channels_30d": s.beeper_channels_30d,
            } if s.score_beeper != 0 else None,
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
        "no_activity": sum(1 for t in li_types if t == "no_activity"),
        "no_linkedin": sum(1 for s in scored_list if not s.linkedin_signal),
        "avg_completeness": round(
            sum(s.completeness for s in scored_list) / len(scored_list), 1
        ) if scored_list else 0,
        "beeper_enriched": sum(1 for s in scored_list if s.score_beeper != 0),
        "avg_beeper_bonus": round(
            sum(s.score_beeper for s in scored_list) / len(scored_list), 1
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
    from utils import upload_file_to_gcs
    upload_file_to_gcs(FOLLOWUP_SCORES_FILE, "data/followup_scores.json", "FollowUp")

"""Pure decision rules for the loyalty agent.

Everything here is a function of its arguments: no Firestore, no BigQuery, no
model calls, no clock beyond what the caller passes in. That is the point of
the module. These are the rules that decide whether real margin is spent, and
they should be readable and testable without standing up a single client.

What is deliberately *not* here: the churn probability. That number is
BigQuery's alone. Nothing in this file computes, adjusts, boosts or
second-guesses it -- an earlier version of the agent did, with a hand-weighted
five-factor heuristic and a +0.25 constant for complaints, and both were
reimplementing signals the model already trains on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loyalty_agent.config import config

# Historical segment name aliases. The seeder still emits the metal tiers for
# a share of customers, so these are live, not legacy decoration.
SEGMENT_ALIASES = {
    "PLATINUM": "RETAIL_PRO",
    "GOLD": "RETAIL_PRO",
    "SILVER": "STANDARD_LOYALTY",
    "BRONZE": "STANDARD_LOYALTY",
}

# Nested maps on a /customers document whose contents the rules below expect
# to find at the top level.
NESTED_PROFILE_SECTIONS = (
    "engagement",
    "supportMetrics",
    "customerFeedback",
    "transactionalMetrics",
    "accountState",
)

# A rating at or below this is treated as a complaint, matching the threshold
# the mobile client uses to reveal its complaint-reason dropdown.
COMPLAINT_RATING_THRESHOLD = 2

# Which of the four escalation paths a session took. Recorded on the session
# and the trace so that a skip is always attributable to a specific branch,
# including the branches that produce no sentence.
GATE_JUDGED = "JUDGED"
GATE_NO_COMPLAINT = "NO_COMPLAINT"
GATE_TIER_NOT_CANDIDATE = "TIER_NOT_CANDIDATE"
GATE_JUDGE_UNAVAILABLE = "JUDGE_UNAVAILABLE"

# The sentences for the branches the judge never reaches. NO_COMPLAINT has
# none on purpose: a customer who has not complained needs no narration, and
# a sentence written on every uneventful login is one nobody reads on the
# login that matters.
GATE_NOTES = {
    GATE_TIER_NOT_CANDIDATE: (
        "{tier} is outside the escalation allow-list, so no judgement was "
        "asked for."
    ),
    GATE_JUDGE_UNAVAILABLE: (
        "The escalation judge could not be reached, so the model's own tier "
        "stands."
    ),
}


def gate_note(gate: Optional[str], tier: Optional[str] = None) -> Optional[str]:
    """The one sentence that explains ``gate``, or None where none is owed.

    Only the branches the judge never reached are written here. Where it did
    reach a verdict the sentence is the judge's own, and a deterministic
    paraphrase sitting next to it would be a second voice claiming to be the
    decision.
    """
    template = GATE_NOTES.get(gate or "")
    if template is None:
        return None
    return template.format(tier=tier or "This tier")


def evaluate_churn_tier(prob: float) -> str:
    """Classify a churn probability into the configured risk tiers."""
    if prob >= config.churn_critical_threshold:
        return "CRITICAL"
    if prob >= config.churn_trigger_threshold:
        return "HIGH"
    if prob >= config.churn_moderate_threshold:
        return "MODERATE"
    return "LOW"


def flatten_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    """Lift the nested sections of a /customers document to the top level."""
    flat: Dict[str, Any] = {}
    for section in NESTED_PROFILE_SECTIONS:
        nested = data.get(section)
        if isinstance(nested, dict):
            flat.update(nested)
    # Top-level keys win: they are the explicit value where both exist.
    flat.update({k: v for k, v in data.items() if k not in NESTED_PROFILE_SECTIONS})
    return flat


def discount_ceiling_for_segment(segment: Optional[str]) -> int:
    """Return the configured discount ceiling for a customer segment."""
    key = str(segment or "").upper()
    key = SEGMENT_ALIASES.get(key, key)
    ceilings = config.discount_ceilings
    return int(ceilings.get(key, ceilings.get("DEFAULT", 15)))


def ceiling_for(
    segment: Optional[str],
    step_down_percent: int = 0,
    gross_margin: Optional[float] = None,
) -> int:
    """The highest discount this customer may be given, before any request."""
    allowed = discount_ceiling_for_segment(segment)
    if step_down_percent:
        allowed = max(
            int(config.min_followup_discount_percent),
            allowed - int(step_down_percent),
        )
    if gross_margin is not None:
        headroom = float(gross_margin) - float(config.margin_floor_percent)
        allowed = min(allowed, int(max(0.0, headroom)))
    return allowed


def apply_discount_guardrails(
    requested_percent: Any,
    segment: Optional[str],
    gross_margin_percent: Optional[float] = None,
    step_down_percent: int = 0,
) -> int:
    """Clamp a discount to the tier ceiling and the margin floor.

    ``step_down_percent`` lowers the ceiling for a follow-up offer. It is
    applied to the ceiling rather than to the request on purpose: Gemini can
    go on asking for whatever it thinks the customer is worth, and the
    guardrail remains the single place a discount is actually decided.
    """
    try:
        requested = int(requested_percent)
    except (TypeError, ValueError):
        requested = 0

    return max(0, min(requested, ceiling_for(segment, step_down_percent,
                                             gross_margin_percent)))


def gross_margin_percent(profile: Dict[str, Any]) -> Optional[float]:
    """Read the customer's gross margin as a percentage, if recorded."""
    percent = profile.get("grossMarginPercent")
    if percent is not None:
        try:
            return float(percent)
        except (TypeError, ValueError):
            return None

    fraction = profile.get("profitMargin")
    if fraction is not None:
        try:
            return float(fraction) * 100.0
        except (TypeError, ValueError):
            return None
    return None


def resolve_customer_context(
    customer_id: str,
    profile: Dict[str, Any],
    churn: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve the handful of customer attributes the offer path needs.

    Firestore is the first source and the churn row the fallback, because the
    profile is written per order and the churn row is a nightly snapshot.
    """
    segment = str(
        profile.get("customerSegment")
        or profile.get("loyaltyTier")
        or churn.get("customerSegment")
        or "STANDARD_LOYALTY"
    ).upper()

    spend = profile.get("totalSpend90d", profile.get("historicalSpend90d"))
    if spend is None:
        spend = churn.get("totalSpend90d")

    sentiment = profile.get("sentimentScore")
    if sentiment is None:
        sentiment = churn.get("sentimentScore")

    return {
        "customerId": customer_id,
        "customerSegment": segment,
        "primaryComplaintReason": profile.get("primaryComplaintReason"),
        "totalSpend90d": float(spend) if spend is not None else 0.0,
        "sentimentScore": float(sentiment) if sentiment is not None else 0.5,
        "lifetimeSpend": profile.get("lifetimeSpend"),
        "ordersCountLast12m": profile.get("ordersCountLast12m"),
        "feedbackRating": profile.get("rating", profile.get("feedbackRating")),
        "grossMarginPercent": gross_margin_percent(profile),
    }


def latest_complaint(
    order: Optional[Dict[str, Any]],
    scored_at: Optional[datetime],
) -> Optional[Dict[str, Any]]:
    """The complaint on ``order``, with its position relative to the score.

    Returns None only when there is nothing to weigh: no order, or an order
    the customer did not complain about.

    Freshness is *measured* here and *decided* elsewhere. An earlier version
    refused to return anything the churn model could already have seen, on the
    grounds that reacting to it twice counts one grievance twice -- which is
    true, and is the argument the +0.25 acute-friction boost this replaced got
    wrong. But applying it here meant the agent dropped the case silently:
    nothing in the session document distinguished "no complaint" from "a
    complaint the model had already priced", and a skip nobody can explain is
    not a decision anybody can audit.

    So the comparison survives as a fact in the brief instead of a filter in
    front of it. ``gapHours`` is signed -- positive when the complaint landed
    after the score -- and ``alreadyScored`` states the conclusion, or is None
    when a missing timestamp means there is no conclusion to state. The judge
    is told all three and has to argue the point in words a human can check.
    """
    if not order:
        return None

    feedback = order.get("customerFeedback") or {}
    try:
        rating = int(feedback.get("rating"))
    except (TypeError, ValueError):
        return None
    if rating > COMPLAINT_RATING_THRESHOLD:
        return None

    submitted_at = parse_timestamp(feedback.get("feedbackTimestamp"))

    gap_hours: Optional[float] = None
    already_scored: Optional[bool] = None
    if submitted_at is not None and scored_at is not None:
        gap_hours = (submitted_at - scored_at).total_seconds() / 3600.0
        already_scored = gap_hours <= 0

    return {
        "orderId": order.get("orderId"),
        "rating": rating,
        "reason": feedback.get("primaryComplaintReason"),
        "comment": feedback.get("feedbackText"),
        "submittedAt": submitted_at,
        "scoredAt": scored_at,
        "gapHours": gap_hours,
        "alreadyScored": already_scored,
    }


def classify_recent_offers(
    recent: List[Dict[str, Any]], now: datetime
) -> Dict[str, Any]:
    """Sort a customer's recent offers into the three cases that matter.

    An ACTIVE offer blocks a new one outright: the customer already has
    something unspent on the table and issuing a second would let them stack
    the two.

    A REDEEMED offer does not block anything. The cooldown exists to stop us
    discounting at a customer who is ignoring the discounts, and one who spent
    an offer and is still at risk is the opposite of that case. Redemptions are
    counted instead, and the count both steps the next ceiling down and
    eventually ends the sequence.

    Anything else -- an offer that expired unspent -- keeps the original
    cooldown behaviour, which is the guardrail actually earning its keep.
    """
    redeemed = [offer for offer in recent if offer.get("status") == "REDEEMED"]
    counted = {
        "redeemedCount": len(redeemed),
        "lastRedeemedOffer": redeemed[0] if redeemed else None,
    }

    for offer in recent:
        if offer.get("status") == "ACTIVE":
            return {
                **counted,
                "hasActiveOffer": True,
                "activeOffer": offer,
                "inCooldown": False,
                "cooldownUntil": offer.get("cooldownUntil"),
            }

    for offer in recent:
        # Skipping redeemed offers here is load-bearing: their cooldownUntil is
        # still in the future, and honouring it silently suppressed every
        # follow-up.
        if offer.get("status") == "REDEEMED":
            continue
        cooldown_str = offer.get("cooldownUntil") or offer.get("validUntil")
        if not cooldown_str:
            continue
        cooldown_dt = parse_timestamp(cooldown_str)
        if cooldown_dt is not None and cooldown_dt > now:
            return {
                **counted,
                "hasActiveOffer": False,
                "activeOffer": None,
                "inCooldown": True,
                "cooldownUntil": cooldown_str,
            }

    return {**no_recent_offers(), **counted}


def no_recent_offers() -> Dict[str, Any]:
    """The classification for a customer with nothing on file."""
    return {
        "hasActiveOffer": False,
        "activeOffer": None,
        "inCooldown": False,
        "cooldownUntil": None,
        "redeemedCount": 0,
        "lastRedeemedOffer": None,
    }


def cooldown_cutoff(now: datetime, cooldown_days: int) -> datetime:
    """The oldest offer worth considering for cooldown."""
    return now - timedelta(days=cooldown_days)


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string, or pass a datetime through. None on failure.

    Always returns an aware datetime. Everything the demo writes is UTC, but
    not everything writes the suffix that says so, and one naive value is
    enough to make a subtraction against a BigQuery timestamp raise
    TypeError -- inside the escalation path, where the cost of an exception is
    a session that never reaches a terminal state.
    """
    if isinstance(value, datetime):
        parsed = value
    elif not value:
        return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

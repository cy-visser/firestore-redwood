"""Customer roster and order-history generation for the Redwood Retail dataset."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from retail_catalog import COMPLAINT_REASONS, FEEDBACK_SENTIMENT_RANGES

# Feature/label split matching BigQuery feature view.
HISTORY_MONTHS = 18
LABEL_WINDOW_DAYS = 90

# Spend aggregation window for totalSpend90d.
SPEND_WINDOW_DAYS = 90

# Acute grievances matching ACUTE_FRICTION_TYPES in loyalty_agent/agent.py.
ACUTE_FRICTION_EVENTS = (
    "REFUND_REQUESTED",
    "LATE_DELIVERY",
    "DEFECTIVE_COMPONENT",
    "ESCALATION",
    "BILLING_DISPUTE",
    "DAMAGED_SHIPMENT",
)

# Non-acute complaints.
MINOR_COMPLAINT_REASONS = tuple(
    reason for reason in COMPLAINT_REASONS if reason not in ACUTE_FRICTION_EVENTS
)


@dataclass(frozen=True)
class Archetype:
    """A behavioural template describing how a cohort of customers acts.

    ``label_window_purchase_probability`` is the only field that influences the
    churn label, and it does so indirectly: it decides whether the customer
    happens to place an order after the cutoff. Every other field shapes
    features only.
    """

    name: str
    weight: float
    # Mean and jitter (in days) between consecutive orders.
    cadence_days: int
    cadence_jitter: int
    # Multiplier applied to order value, relative to the catalog baseline.
    spend_multiplier: float
    # Probability the customer places at least one order after the cutoff.
    label_window_purchase_probability: float
    # Engagement signal ranges (customer-level, not per order).
    login_frequency_range: tuple
    engagement_score_range: tuple
    session_minutes_range: tuple
    cart_abandonment_range: tuple
    # Support and satisfaction signal ranges.
    support_tickets_range: tuple
    complaints_range: tuple
    return_rate_range: tuple
    feedback_rating_weights: Dict[int, float]
    # Share of the cohort carrying a live grievance from ACUTE_FRICTION_EVENTS.
    # Friction is a behavioural trait like the rest of this template, so it is
    # declared per archetype rather than rolled uniformly across the roster.
    acute_friction_probability: float
    loyalty_tiers: List[str]
    # If set, orders taper in value across the feature window, modelling a
    # customer whose relationship is visibly deteriorating before they leave.
    declining: bool = False


ARCHETYPES: List[Archetype] = [
    Archetype(
        name="loyal_active",
        weight=0.28,
        cadence_days=28,
        cadence_jitter=8,
        spend_multiplier=1.9,
        # A small minority still leave; without this the label would be
        # perfectly predictable from the archetype's feature signature.
        label_window_purchase_probability=0.96,
        login_frequency_range=(16, 30),
        engagement_score_range=(0.72, 0.97),
        session_minutes_range=(9.0, 18.0),
        cart_abandonment_range=(0, 2),
        support_tickets_range=(0, 2),
        complaints_range=(0, 1),
        return_rate_range=(0.0, 4.0),
        feedback_rating_weights={5: 0.62, 4: 0.28, 3: 0.08, 2: 0.015, 1: 0.005},
        acute_friction_probability=0.01,
        loyalty_tiers=["ENTERPRISE_VIP", "PLATINUM", "GOLD"],
    ),
    Archetype(
        name="steady",
        weight=0.30,
        cadence_days=52,
        cadence_jitter=16,
        spend_multiplier=1.2,
        label_window_purchase_probability=0.88,
        login_frequency_range=(8, 18),
        engagement_score_range=(0.48, 0.78),
        session_minutes_range=(6.0, 12.0),
        cart_abandonment_range=(0, 3),
        support_tickets_range=(0, 3),
        complaints_range=(0, 1),
        return_rate_range=(0.0, 8.0),
        feedback_rating_weights={5: 0.40, 4: 0.36, 3: 0.17, 2: 0.05, 1: 0.02},
        acute_friction_probability=0.03,
        loyalty_tiers=["GOLD", "SILVER"],
    ),
    Archetype(
        name="occasional",
        weight=0.20,
        cadence_days=95,
        cadence_jitter=30,
        spend_multiplier=0.85,
        # Genuinely uncertain cohort: this is where the model earns its keep.
        label_window_purchase_probability=0.62,
        login_frequency_range=(3, 10),
        engagement_score_range=(0.28, 0.58),
        session_minutes_range=(3.0, 8.0),
        cart_abandonment_range=(1, 5),
        support_tickets_range=(0, 4),
        complaints_range=(0, 2),
        return_rate_range=(2.0, 12.0),
        feedback_rating_weights={5: 0.24, 4: 0.30, 3: 0.28, 2: 0.13, 1: 0.05},
        acute_friction_probability=0.10,
        loyalty_tiers=["SILVER", "BRONZE"],
    ),
    Archetype(
        name="lapsing",
        weight=0.13,
        cadence_days=70,
        cadence_jitter=25,
        spend_multiplier=0.7,
        label_window_purchase_probability=0.20,
        login_frequency_range=(1, 6),
        engagement_score_range=(0.10, 0.38),
        session_minutes_range=(1.5, 5.0),
        cart_abandonment_range=(3, 8),
        support_tickets_range=(2, 7),
        complaints_range=(1, 4),
        return_rate_range=(8.0, 22.0),
        feedback_rating_weights={5: 0.06, 4: 0.12, 3: 0.24, 2: 0.34, 1: 0.24},
        acute_friction_probability=0.40,
        loyalty_tiers=["SILVER", "BRONZE", "NONE"],
        declining=True,
    ),
    Archetype(
        name="dormant",
        weight=0.09,
        cadence_days=130,
        cadence_jitter=45,
        spend_multiplier=0.55,
        # A handful reactivate, which is exactly the population a retention
        # programme is trying to find.
        label_window_purchase_probability=0.12,
        login_frequency_range=(0, 3),
        engagement_score_range=(0.03, 0.22),
        session_minutes_range=(0.5, 3.0),
        cart_abandonment_range=(2, 9),
        support_tickets_range=(1, 5),
        complaints_range=(0, 3),
        return_rate_range=(5.0, 25.0),
        feedback_rating_weights={5: 0.10, 4: 0.14, 3: 0.26, 2: 0.30, 1: 0.20},
        # Lower than lapsing despite the worse ratings: a dormant customer has
        # largely stopped transacting, so there is less left to go wrong.
        acute_friction_probability=0.22,
        loyalty_tiers=["BRONZE", "NONE"],
        declining=True,
    ),
]

ARCHETYPES_BY_NAME = {a.name: a for a in ARCHETYPES}


# ------------------------------------------------------------------------------
# Demo personas
#
# These two customers back the scripted demo and are pinned to the IAM service
# accounts provisioned in terraform/iam.tf (google_service_account.demo_principals).
# Their archetypes are forced rather than sampled, and their label-window
# behaviour is asserted rather than rolled, so the demo is reproducible:
#   demo1 -> purchases recently  -> not churned -> LOW risk  -> no offer
#   demo2 -> silent since cutoff -> churned     -> HIGH risk -> offer
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class DemoPersona:
    principal_id: str          # IAM service account id, e.g. "demo1-user"
    customer_id: str
    display_name: str
    customer_segment: str
    loyalty_tier: str
    archetype: str
    # Forced outcome; bypasses label_window_purchase_probability entirely.
    purchases_in_label_window: bool
    city: str
    country_code: str
    baseline_orders: int
    account_age_days: int
    days_since_last_purchase: int
    login_frequency_monthly: int
    avg_session_duration_minutes: float
    app_engagement_score: float
    cart_abandonment_count: int
    abandoned_cart_value_90d: float
    support_tickets_count: int
    open_support_tickets_count: int
    complaints_count: int
    return_rate_percent: float
    # Satisfaction and grievance, pinned for the same reason the label is: the
    # demo asserts that demo1 draws no escalation and demo2 does, and a sampled
    # friction event would make that true only on some runs.
    feedback_rating: int
    sentiment_score: float
    has_active_complaint: bool
    primary_complaint_reason: Optional[str]
    recent_friction_event: Optional[str]


DEMO_PERSONAS: List[DemoPersona] = [
    DemoPersona(
        principal_id="demo1-user",
        customer_id="cust_demo1",
        display_name="Meridian Industrial Supply",
        customer_segment="ENTERPRISE_VIP",
        loyalty_tier="ENTERPRISE_VIP",
        archetype="loyal_active",
        purchases_in_label_window=True,
        city="Amsterdam",
        country_code="NL",
        baseline_orders=14,
        account_age_days=720,
        days_since_last_purchase=21,
        login_frequency_monthly=24,
        avg_session_duration_minutes=14.5,
        app_engagement_score=0.92,
        cart_abandonment_count=1,
        abandoned_cart_value_90d=420.0,
        support_tickets_count=1,
        open_support_tickets_count=0,
        complaints_count=0,
        return_rate_percent=1.5,
        feedback_rating=5,
        sentiment_score=0.92,
        has_active_complaint=False,
        primary_complaint_reason=None,
        recent_friction_event=None,
    ),
    DemoPersona(
        principal_id="demo2-user",
        customer_id="cust_demo2",
        display_name="Bavaria Components GmbH",
        customer_segment="STANDARD_LOYALTY",
        loyalty_tier="SILVER",
        archetype="lapsing",
        purchases_in_label_window=False,
        city="Munich",
        country_code="DE",
        baseline_orders=7,
        account_age_days=480,
        days_since_last_purchase=95,
        login_frequency_monthly=9,
        avg_session_duration_minutes=7.5,
        app_engagement_score=0.52,
        cart_abandonment_count=3,
        abandoned_cart_value_90d=840.0,
        support_tickets_count=4,
        open_support_tickets_count=1,
        complaints_count=1,
        return_rate_percent=12.0,
        feedback_rating=1,
        sentiment_score=-0.72,
        has_active_complaint=True,
        # Both fields are set, and to different events, because the agent reads
        # them as two separate signals: the standing complaint on the account is
        # the repeated late delivery, the thing that happened last is the refund
        # it led to. Either one alone would fire the acute path; together they
        # match the support tickets this persona already carries.
        primary_complaint_reason="LATE_DELIVERY",
        recent_friction_event="REFUND_REQUESTED",
    ),
]

DEMO_PERSONAS_BY_PRINCIPAL = {p.principal_id: p for p in DEMO_PERSONAS}
DEMO_CUSTOMER_IDS = {p.customer_id for p in DEMO_PERSONAS}


@dataclass
class Customer:
    """A seeded customer with a stable identity and fixed behavioural profile."""

    customer_id: str
    customer_name: str
    customer_email: str
    customer_segment: str
    loyalty_tier: str
    is_loyalty_member: int
    account_age_days: int
    archetype: Archetype
    # Customer-level engagement and support signals, held constant across the
    # customer's orders so aggregation in BigQuery is stable.
    login_frequency_monthly: int
    avg_session_duration_minutes: float
    app_engagement_score: float
    app_sessions_last_30d: int
    cart_abandonment_count: int
    abandoned_cart_value_90d: float
    support_tickets_count: int
    open_support_tickets_count: int
    complaints_count: int
    return_frequency: int
    return_rate_percent: float
    city: Optional[str] = None
    country_code: Optional[str] = None
    # Resolved when the history is generated.
    purchases_in_label_window: bool = False
    iam_principal: Optional[str] = None
    is_demo_persona: bool = False
    # Satisfaction and grievance, filled in by assign_friction_profile. The
    # defaults describe a contented customer, so a roster built without that
    # call still produces a coherent document rather than nulls.
    feedback_rating: int = 4
    sentiment_score: float = 0.5
    has_active_complaint: bool = False
    primary_complaint_reason: Optional[str] = None
    recent_friction_event: Optional[str] = None
    order_dates: List[datetime] = field(default_factory=list)


def _stable_seed(*parts: Any) -> int:
    """Derive a deterministic integer seed from arbitrary values.

    Using a hash of the identity rather than a global counter means a given
    customer generates the same history regardless of how many workers run or
    what order they run in.
    """
    joined = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def reference_now(now: Optional[datetime] = None) -> datetime:
    """Return the reference 'now', quantized to midnight UTC.

    Order dates are anchored relative to this value. Quantizing to whole days
    means two runs on the same day produce byte-identical histories, which is
    what makes reseeding reproducible; without it the sub-second drift between
    calls would shift every timestamp.
    """
    base = now or datetime.now(timezone.utc)
    return base.replace(hour=0, minute=0, second=0, microsecond=0)


def _weighted_rating(rng: random.Random, weights: Dict[int, float]) -> int:
    ratings = list(weights.keys())
    return rng.choices(ratings, weights=[weights[r] for r in ratings], k=1)[0]


def build_customer(
    index: int,
    rng: random.Random,
    *,
    persona: Optional[DemoPersona] = None,
    project_id: Optional[str] = None,
) -> Customer:
    """Construct a single customer profile.

    When ``persona`` is supplied the archetype, identity, engagement/support
    metrics and label-window outcome are pinned rather than sampled.
    """
    if persona is not None:
        archetype = ARCHETYPES_BY_NAME[persona.archetype]
        customer_id = persona.customer_id
        customer_name = persona.display_name
        customer_email = f"{persona.principal_id}@redwood-demo.example"
        customer_segment = persona.customer_segment
        loyalty_tier = persona.loyalty_tier
        account_age_days = persona.account_age_days
        login_frequency_monthly = persona.login_frequency_monthly
        avg_session_duration_minutes = persona.avg_session_duration_minutes
        app_engagement_score = persona.app_engagement_score
        cart_abandonment_count = persona.cart_abandonment_count
        abandoned_cart_value_90d = persona.abandoned_cart_value_90d
        support_tickets_count = persona.support_tickets_count
        open_support_tickets_count = persona.open_support_tickets_count
        complaints_count = persona.complaints_count
        return_rate_percent = persona.return_rate_percent
    else:
        archetype = rng.choices(
            ARCHETYPES, weights=[a.weight for a in ARCHETYPES], k=1
        )[0]
        customer_id = f"cust_retail_{index:06d}"
        customer_name = f"Enterprise Customer #{index:06d}"
        customer_email = f"client.{index:06d}@enterprise-logistics.eu"
        loyalty_tier = rng.choice(archetype.loyalty_tiers)
        customer_segment = {
            "ENTERPRISE_VIP": "ENTERPRISE_VIP",
            "PLATINUM": "RETAIL_PRO",
            "GOLD": "RETAIL_PRO",
            "SILVER": "STANDARD_LOYALTY",
            "BRONZE": "STANDARD_LOYALTY",
            "NONE": "CASUAL_SHOPPER",
        }.get(loyalty_tier, "CASUAL_SHOPPER")

        account_age_days = rng.randint(120, 1900)
        lo, hi = archetype.login_frequency_range
        login_frequency_monthly = rng.randint(lo, hi)

        lo, hi = archetype.engagement_score_range
        app_engagement_score = round(rng.uniform(lo, hi), 2)

        lo, hi = archetype.session_minutes_range
        avg_session_duration_minutes = round(rng.uniform(lo, hi), 1)

        lo, hi = archetype.cart_abandonment_range
        cart_abandonment_count = rng.randint(lo, hi)
        abandoned_cart_value_90d = round(cart_abandonment_count * rng.uniform(120.0, 680.0), 2)

        lo, hi = archetype.support_tickets_range
        support_tickets_count = rng.randint(lo, hi)
        open_support_tickets_count = min(support_tickets_count, rng.randint(0, 2))

        lo, hi = archetype.complaints_range
        complaints_count = rng.randint(lo, hi)

        lo, hi = archetype.return_rate_range
        return_rate_percent = round(rng.uniform(lo, hi), 1)

    iam_principal = None
    if persona is not None and project_id:
        iam_principal = f"{persona.principal_id}@{project_id}.iam.gserviceaccount.com"

    return Customer(
        customer_id=customer_id,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_segment=customer_segment,
        loyalty_tier=loyalty_tier,
        is_loyalty_member=0 if loyalty_tier == "NONE" else 1,
        account_age_days=account_age_days,
        archetype=archetype,
        login_frequency_monthly=login_frequency_monthly,
        avg_session_duration_minutes=avg_session_duration_minutes,
        app_engagement_score=app_engagement_score,
        app_sessions_last_30d=max(0, int(login_frequency_monthly * rng.uniform(0.8, 1.6))),
        cart_abandonment_count=cart_abandonment_count,
        abandoned_cart_value_90d=abandoned_cart_value_90d,
        support_tickets_count=support_tickets_count,
        open_support_tickets_count=open_support_tickets_count,
        complaints_count=complaints_count,
        return_frequency=int(return_rate_percent // 4),
        return_rate_percent=return_rate_percent,
        city=persona.city if persona else None,
        country_code=persona.country_code if persona else None,
        iam_principal=iam_principal,
        is_demo_persona=persona is not None,
    )


def _rating_sentiment(rating: int, rng: random.Random) -> float:
    """Map a feedback rating onto the catalog's sentiment band.

    Duplicated from order_factory._rating_to_sentiment rather than shared:
    order_factory imports this module, so the helper cannot move there without
    creating an import cycle. The bands themselves come from retail_catalog, so
    an order and its customer cannot end up describing different moods.
    """
    if rating >= 4:
        lo, hi = FEEDBACK_SENTIMENT_RANGES["POSITIVE"]
    elif rating == 3:
        lo, hi = FEEDBACK_SENTIMENT_RANGES["NEUTRAL"]
    else:
        lo, hi = FEEDBACK_SENTIMENT_RANGES["NEGATIVE"]
    return round(rng.uniform(lo, hi), 3)


def assign_friction_profile(
    customer: Customer,
    rng: random.Random,
    *,
    persona: Optional[DemoPersona] = None,
) -> Customer:
    """Attach the satisfaction and grievance signals the loyalty agent reads.

    ``rng`` must be a stream of its own, seeded from the customer id. Drawing
    these from the roster RNG would shift every subsequent order date, which
    would move the demo personas' order history and with it the LOW/CRITICAL
    outcome the demo depends on.
    """
    if persona is not None:
        customer.feedback_rating = persona.feedback_rating
        customer.sentiment_score = persona.sentiment_score
        customer.has_active_complaint = persona.has_active_complaint
        customer.primary_complaint_reason = persona.primary_complaint_reason
        customer.recent_friction_event = persona.recent_friction_event
        return customer

    rating = _weighted_rating(rng, customer.archetype.feedback_rating_weights)

    if rng.random() < customer.archetype.acute_friction_probability:
        event = rng.choice(ACUTE_FRICTION_EVENTS)
        customer.recent_friction_event = event
        customer.primary_complaint_reason = event
        customer.has_active_complaint = True
        # The rest of the document has to agree with the event: a customer
        # escalating a damaged shipment cannot also be rating the service four
        # stars, and the agent would then read a contradiction.
        rating = min(rating, 2)
    elif rating <= 2 and customer.complaints_count > 0:
        # Unhappy, but over something routine. Drawing from the minor reasons
        # keeps this population out of the agent's acute path.
        customer.primary_complaint_reason = rng.choice(MINOR_COMPLAINT_REASONS)
        customer.has_active_complaint = True

    customer.feedback_rating = rating
    customer.sentiment_score = _rating_sentiment(rating, rng)
    return customer


def generate_order_dates(
    customer: Customer,
    rng: random.Random,
    now: datetime,
    *,
    persona: Optional[DemoPersona] = None,
) -> List[datetime]:
    """Produce the customer's order timestamps across the full history window.

    Dates are walked forward from the start of the history window at the
    archetype's cadence. Whether the customer places any order after the cutoff
    is decided once, up front, and then enforced, so the resulting label is
    unambiguous.
    """
    archetype = customer.archetype
    history_start = now - timedelta(days=HISTORY_MONTHS * 30)
    cutoff = now - timedelta(days=LABEL_WINDOW_DAYS)

    if persona is not None:
        purchases_after_cutoff = persona.purchases_in_label_window
    else:
        purchases_after_cutoff = rng.random() < archetype.label_window_purchase_probability
    customer.purchases_in_label_window = purchases_after_cutoff

    dates: List[datetime] = []
    cursor = history_start + timedelta(days=rng.randint(0, archetype.cadence_days))

    while cursor < cutoff:
        dates.append(cursor)
        step = archetype.cadence_days + rng.randint(
            -archetype.cadence_jitter, archetype.cadence_jitter
        )
        # Declining customers slow down as they approach the cutoff, which is
        # what makes recency and frequency genuinely informative features.
        if archetype.declining:
            progress = (cursor - history_start) / max(cutoff - history_start, timedelta(days=1))
            step = int(step * (1.0 + 1.4 * progress))
        cursor += timedelta(days=max(step, 5))

    # Guarantee a minimum viable feature window; a customer with no pre-cutoff
    # orders cannot be scored and would be dropped by the view.
    if len(dates) < 2:
        dates = [
            cutoff - timedelta(days=rng.randint(200, 420)),
            cutoff - timedelta(days=rng.randint(30, 190)),
        ]

    if persona is not None and not purchases_after_cutoff and persona.baseline_orders > 1:
        dates = [
            now - timedelta(days=max(persona.days_since_last_purchase, 450 - i * 60))
            for i in range(persona.baseline_orders)
        ]
    elif persona is not None and persona.baseline_orders:
        # Trim to roughly the persona's intended order count, keeping the most
        # recent orders so recency stays representative.
        dates = dates[-persona.baseline_orders:]

    if purchases_after_cutoff:
        n_recent = rng.randint(1, 3) if archetype.cadence_days < 60 else 1
        for _ in range(n_recent):
            dates.append(cutoff + timedelta(days=rng.randint(1, LABEL_WINDOW_DAYS - 1)))

    customer.order_dates = sorted(dates)
    return customer.order_dates


def build_customer_roster(
    count: int,
    *,
    seed: int = 20260921,
    project_id: Optional[str] = None,
    include_demo_personas: bool = True,
    now: Optional[datetime] = None,
) -> List[Customer]:
    """Build a reproducible roster of customers with generated order timelines.

    The demo personas are always emitted first so they exist even for very
    small ``count`` values.
    """
    now = reference_now(now)
    customers: List[Customer] = []

    # Friction is drawn from its own RNG stream, keyed "friction", for the same
    # reason orders are keyed "orders" in generate_retail_dataset.py: adding
    # draws to an existing stream would renumber everything after them, and
    # here that would have moved every order date in the roster.
    if include_demo_personas:
        for persona in DEMO_PERSONAS:
            rng = random.Random(_stable_seed(seed, persona.customer_id))
            customer = build_customer(0, rng, persona=persona, project_id=project_id)
            generate_order_dates(customer, rng, now, persona=persona)
            assign_friction_profile(
                customer,
                random.Random(_stable_seed(seed, persona.customer_id, "friction")),
                persona=persona,
            )
            customers.append(customer)

    for index in range(1, max(count, 0) + 1):
        rng = random.Random(_stable_seed(seed, index))
        customer = build_customer(index, rng, project_id=project_id)
        generate_order_dates(customer, rng, now)
        assign_friction_profile(
            customer,
            random.Random(_stable_seed(seed, customer.customer_id, "friction")),
        )
        customers.append(customer)

    return customers


def roster_summary(customers: List[Customer]) -> Dict[str, Any]:
    """Summarise a roster for logging and for post-seed sanity assertions."""
    total_orders = sum(len(c.order_dates) for c in customers)
    churned = sum(1 for c in customers if not c.purchases_in_label_window)
    acute_friction = sum(
        1 for c in customers
        if (c.recent_friction_event or c.primary_complaint_reason)
        in ACUTE_FRICTION_EVENTS
    )
    by_archetype: Dict[str, int] = {}
    for c in customers:
        by_archetype[c.archetype.name] = by_archetype.get(c.archetype.name, 0) + 1

    return {
        "customers": len(customers),
        "orders": total_orders,
        "churned": churned,
        "retained": len(customers) - churned,
        "churn_rate": round(churned / max(len(customers), 1), 3),
        "by_archetype": by_archetype,
        # Reported because the agent's escalation path is only reachable for
        # this population; a reseed that produces zero here has broken it.
        "acute_friction": acute_friction,
        "acute_friction_rate": round(acute_friction / max(len(customers), 1), 3),
    }

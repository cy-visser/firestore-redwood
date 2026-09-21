"""
Autonomous loyalty offer agent for Redwood Retail.

A single agent handles one customer login session end to end: resolve the
session, score churn, read the friction signals, apply the cooldown and risk
gates, synthesise the offer copy and persist the voucher.

This replaces a six-agent A2A mesh. The mesh modelled each of those steps as an
independent agent with its own card, discovery entry and task envelope, and
then ran all of them inside one process anyway, so the protocol was pure
overhead: a churn lookup travelled through a JSON task request to reach a
function call in the same interpreter. The steps below are the same steps, in
the same order, as plain methods.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.cloud import bigquery
from google.cloud.firestore_v1.base_query import FieldFilter

from loyalty_agent.config import config
from loyalty_agent.schemas import LoyaltyOffer

logger = logging.getLogger("loyalty_agent.agent")

SESSIONS_COLLECTION = "customer_sessions"
CUSTOMERS_COLLECTION = "customers"
OFFERS_COLLECTION = "loyalty_offers"

# Complaint categories that justify raising a customer's churn score above the
# model's baseline: they describe something that went wrong recently, which the
# batch prediction was scored too early to have seen.
ACUTE_FRICTION_TYPES = {
    "REFUND_REQUESTED",
    "LATE_DELIVERY",
    "DEFECTIVE_COMPONENT",
    "ESCALATION",
    "BILLING_DISPUTE",
    "DAMAGED_SHIPMENT",
}

# Segment names that predate the config ceilings and still appear on seeded
# customer documents. They resolve to the equivalent configured tier rather
# than to a second, independently maintained ceiling table.
SEGMENT_ALIASES = {
    "PLATINUM": "RETAIL_PRO",
    "GOLD": "RETAIL_PRO",
    "SILVER": "STANDARD_LOYALTY",
    "BRONZE": "STANDARD_LOYALTY",
}

# Nested maps on a /customers document whose contents the scoring code expects
# to find at the top level.
NESTED_PROFILE_SECTIONS = (
    "engagement",
    "supportMetrics",
    "customerFeedback",
    "transactionalMetrics",
    "accountState",
)

# Upper bound on the offers read when evaluating a cooldown. A customer cannot
# legitimately have many offers inside one cooldown window, so a larger result
# means something is wrong and reading more of it would not change the verdict.
COOLDOWN_SCAN_LIMIT = 10


def evaluate_churn_tier(prob: float) -> str:
    """Classifies churn probability into calibrated risk tiers."""
    if prob >= 0.75:
        return "CRITICAL"
    elif prob >= 0.50:
        return "HIGH"
    elif prob >= 0.25:
        return "MODERATE"
    return "LOW"


def evaluate_5pillar_heuristic(customer_data: Dict[str, Any]) -> float:
    """
    Cold-start / service fallback 5-pillar heuristic formulation (SDD Section 3.2 C):
    P_heuristic = (0.30 * S_rating) + (0.25 * S_sentiment) + (0.20 * S_complaint) + (0.15 * S_cart) + (0.10 * S_tickets)
    """
    account_age = customer_data.get("accountAgeDays", 365)

    # 1. Feedback Rating (w1 = 0.30): S_rating = (5 - rating) / 4
    raw_rating = customer_data.get("feedbackRating", customer_data.get("rating", 4))
    s_rating = max(0.0, min(1.0, (5.0 - float(raw_rating)) / 4.0))

    # 2. Sentiment Score (w2 = 0.25): S_sentiment = (1.0 - sentiment) / 2.0 (sentiment in [-1, 1])
    raw_sentiment = float(customer_data.get("sentimentScore", 0.5))
    s_sentiment = max(0.0, min(1.0, (1.0 - raw_sentiment) / 2.0))

    # 3. Complaint Severity (w3 = 0.20)
    complaints = int(customer_data.get("complaintsCount", 0))
    reason = str(customer_data.get("primaryComplaintReason") or "").upper()
    severity_map = {
        "DEFECTIVE_COMPONENT": 1.00,
        "BILLING_DISPUTE": 1.00,
        "DAMAGED_FREIGHT": 0.85,
        "RMA_DELAY": 0.85,
        "REFUND_REQUESTED": 0.85,
        "LATE_DELIVERY": 0.70,
        "POOR_SUPPORT_RESPONSE": 0.60,
        "ESCALATION": 0.60
    }
    s_complaint = severity_map.get(reason, min(1.0, complaints * 0.50))

    # 4. Cart Abandonment (w4 = 0.15): S_cart = min(1.0, cart_abandonment / 3)
    cart_count = int(customer_data.get("cartAbandonmentCount", 0))
    s_cart = min(1.0, cart_count / 3.0)

    # 5. Support Tickets & Returns (w5 = 0.10): S_tickets = min(1.0, (tickets + returns) / 2)
    tickets = int(customer_data.get("supportTicketsCount", 0))
    returns = int(customer_data.get("returnFrequency", 0))
    s_tickets = min(1.0, (tickets + returns) / 2.0)

    p_heuristic = (
        0.30 * s_rating +
        0.25 * s_sentiment +
        0.20 * s_complaint +
        0.15 * s_cart +
        0.10 * s_tickets
    )

    if account_age < 30 and complaints == 0 and s_complaint == 0:
        return round(min(p_heuristic, 0.15), 4)

    days_since_purchase = int(customer_data.get("daysSinceLastPurchase", 0))
    if days_since_purchase > 60 and complaints >= 2:
        return 0.75
    elif days_since_purchase > 60 or complaints >= 2 or raw_sentiment < 0.25:
        p_heuristic = max(p_heuristic, 0.75)
    elif days_since_purchase > 30 or complaints == 1 or raw_sentiment < 0.45:
        p_heuristic = max(p_heuristic, 0.55)

    return round(max(0.0, min(1.0, p_heuristic)), 4)


def flatten_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    """Lift the nested sections of a /customers document to the top level.

    The seeder groups engagement and support figures into maps, while the
    scoring code reads flat keys. Without this every such field looked absent
    and silently scored as its default.
    """
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


def apply_discount_guardrails(
    requested_percent: Any,
    segment: Optional[str],
    gross_margin_percent: Optional[float] = None,
) -> int:
    """Clamp a discount to the tier ceiling and the margin floor.

    A discount comes straight off gross margin, so where the customer's margin
    is known the largest defensible giveaway is whatever is left once
    margin_floor_percent has been reserved. Where it is not known the tier
    ceiling is the only bound available.
    """
    try:
        requested = int(requested_percent)
    except (TypeError, ValueError):
        requested = 0

    allowed = discount_ceiling_for_segment(segment)
    if gross_margin_percent is not None:
        headroom = float(gross_margin_percent) - float(config.margin_floor_percent)
        allowed = min(allowed, int(max(0.0, headroom)))

    return max(0, min(requested, allowed))


def _gross_margin_percent(profile: Dict[str, Any]) -> Optional[float]:
    """Read the customer's gross margin as a percentage, if it is recorded.

    Orders carry profitMargin as a fraction; a profile may carry either that or
    an explicit percentage.
    """
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


class LoyaltyAgent:
    """Evaluates a customer login session and issues a retention offer.

    All Google Cloud clients are injected so the flow can be exercised offline
    against fakes; see selftest.py.
    """

    def __init__(
        self,
        firestore_client: Any = None,
        bigquery_client: Any = None,
        genai_client: Any = None,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        table_id: Optional[str] = None,
        model_name: Optional[str] = None,
        cooldown_days: Optional[int] = None,
        churn_threshold: Optional[float] = None,
        acute_friction_boost: Optional[float] = None,
        offer_validity_days: Optional[int] = None,
        audit_ttl_days: Optional[int] = None,
    ):
        self.fs = firestore_client
        self.bq = bigquery_client
        self.genai = genai_client

        self.project_id = project_id or config.project_id
        self.dataset_id = dataset_id or config.bigquery_dataset
        self.table_id = table_id or config.churn_predictions_table
        self.model_name = model_name or config.reasoning_model

        self.cooldown_days = cooldown_days if cooldown_days is not None else config.cooldown_days
        self.churn_threshold = churn_threshold if churn_threshold is not None else config.churn_trigger_threshold
        self.acute_friction_boost = (
            acute_friction_boost if acute_friction_boost is not None else config.acute_friction_boost
        )
        self.offer_validity_days = (
            offer_validity_days if offer_validity_days is not None else config.offer_validity_days
        )
        self.audit_ttl_days = audit_ttl_days if audit_ttl_days is not None else config.offer_audit_ttl_days

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def process_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Run the retention evaluation for one session.

        Returns the offer document when one is issued or already active, and
        None when the session is skipped. Every exit path leaves the session in
        a terminal state so the mobile client stops waiting.
        """
        if not self.fs:
            logger.error("Firestore client is not configured on LoyaltyAgent.")
            return None

        sess_ref = self.fs.collection(SESSIONS_COLLECTION).document(session_id)
        sess_snap = sess_ref.get()
        if not sess_snap.exists:
            logger.warning("Session %s not found in Firestore.", session_id)
            return None

        sess_data = sess_snap.to_dict() or {}
        customer_id = sess_data.get("customerId")
        if not customer_id:
            logger.warning("Session %s has no customerId.", session_id)
            return None

        now = datetime.now(timezone.utc)
        profile = self._load_profile(customer_id)

        churn = self._lookup_churn(customer_id, profile, sess_data.get("customerData"))
        friction = self._analyse_friction(customer_id, profile, churn)

        cooldown = self._check_cooldown(customer_id, now)

        if cooldown.get("hasActiveOffer"):
            active_offer = cooldown["activeOffer"]
            sess_ref.update({
                "agentProcessingStatus": "PROCESSED",
                "status": "PROCESSED",
                "offerId": active_offer.get("offerId"),
                "activeOfferId": active_offer.get("offerId"),
                "skipReason": "ACTIVE_OFFER_ALREADY_EXISTS",
                "processedAt": now.isoformat()
            })
            logger.info("Session %s: Active offer already exists (%s).", session_id, active_offer.get("offerId"))
            return active_offer

        if cooldown.get("inCooldown"):
            sess_ref.update({
                "agentProcessingStatus": "SKIPPED",
                "status": "PROCESSED",
                "offerId": None,
                "activeOfferId": None,
                "skipReason": "COOLDOWN_ACTIVE",
                "processedAt": now.isoformat()
            })
            logger.info("Session %s: Customer %s is in active cooldown window.", session_id, customer_id)
            return None

        baseline_prob = float(churn["churnProbability"])
        if friction["hasAcuteFriction"]:
            # The batch model scores overnight, so a complaint raised since then
            # is invisible to it. The boost is what makes the agent react to
            # today's grievance rather than yesterday's snapshot.
            churn_prob = round(min(1.0, baseline_prob + self.acute_friction_boost), 4)
            churn_tier = evaluate_churn_tier(churn_prob)
            eval_source = "EVENT_AUGMENTED_HYBRID"
        else:
            churn_prob = baseline_prob
            churn_tier = churn["churnTier"]
            eval_source = churn["evaluationSource"]

        if churn_prob < self.churn_threshold:
            sess_ref.update({
                "agentProcessingStatus": "SKIPPED",
                "status": "PROCESSED",
                "offerId": None,
                "activeOfferId": None,
                "skipReason": "LOW_CHURN_RISK",
                "processedAt": now.isoformat()
            })
            logger.info(
                "Session %s: Churn probability %.2f below threshold %.2f. Skipped.",
                session_id, churn_prob, self.churn_threshold
            )
            return None

        offer_payload = self.synthesize_offer(
            customer_id=customer_id,
            churn_probability=churn_prob,
            churn_tier=churn_tier,
            complaint=friction["primaryComplaintReason"],
            segment=friction["customerSegment"],
            gross_margin_percent=friction["grossMarginPercent"],
        )
        offer_payload["churnProbability"] = churn_prob
        offer_payload["churnRiskTier"] = churn_tier
        offer_payload["baselineChurnRisk"] = baseline_prob
        offer_payload["evaluationSource"] = eval_source
        offer_payload["historicalSpend90d"] = friction["totalSpend90d"]
        offer_payload["sentimentScore"] = friction["sentimentScore"]

        offer = self.persist_offer(customer_id, session_id, offer_payload, now)
        logger.info("Session %s: Offer issued and persisted (%s).", session_id, offer.get("offerId"))
        return offer

    # ------------------------------------------------------------------
    # Step 1: customer profile
    # ------------------------------------------------------------------

    def _load_profile(self, customer_id: str) -> Dict[str, Any]:
        """Read /customers/{id}, flattened, or an empty dict if absent."""
        if not self.fs:
            return {}
        try:
            snap = self.fs.collection(CUSTOMERS_COLLECTION).document(customer_id).get()
        except Exception as exc:
            logger.warning("Customer profile read failed for %s: %s", customer_id, exc)
            return {}
        if not snap.exists:
            return {}
        return flatten_profile(snap.to_dict() or {})

    # ------------------------------------------------------------------
    # Step 2: churn lookup
    # ------------------------------------------------------------------

    def _lookup_churn(
        self,
        customer_id: str,
        profile: Dict[str, Any],
        session_customer_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Score churn from the BigQuery model output, else from the heuristic.

        There is deliberately no Firestore cache tier in front of this. A
        cached baselineChurnRisk on the customer document would shadow the
        model output, and the seeder omits the field for exactly that reason,
        so reading it here would only ever serve a stale number written by
        something other than the model.
        """
        row = self._query_churn_row(customer_id)
        if row is not None:
            prob = _row_value(row, "churn_probability")
            if prob is not None:
                prob = float(prob)
                tier = _row_value(row, "churn_risk_tier")
                return {
                    "customerId": customer_id,
                    "churnProbability": prob,
                    "churnTier": str(tier).upper() if tier else evaluate_churn_tier(prob),
                    "evaluationSource": "BIGQUERY_BATCH",
                    "totalSpend90d": _row_value(row, "total_spend_90d"),
                    "sentimentScore": _row_value(row, "sentiment_score"),
                    "customerSegment": _row_value(row, "customer_segment"),
                }

        customer_data = dict(profile)
        if session_customer_data:
            customer_data.update(session_customer_data)
        heuristic_prob = evaluate_5pillar_heuristic(customer_data)
        return {
            "customerId": customer_id,
            "churnProbability": heuristic_prob,
            "churnTier": evaluate_churn_tier(heuristic_prob),
            "evaluationSource": "HEURISTIC_FALLBACK",
            "totalSpend90d": None,
            "sentimentScore": None,
            "customerSegment": None,
        }

    def _query_churn_row(self, customer_id: str) -> Optional[Any]:
        """Fetch the customer's row from the churn predictions table.

        The customer id is bound as a query parameter rather than interpolated
        into the SQL text, the table is fully qualified so the result does not
        depend on the client's default project, and the row count is bounded:
        the table is a MERGE target keyed on customer_id today, but an append
        of a second scoring run would otherwise turn this into a full scan.
        """
        if not self.bq:
            return None

        sql = (
            "SELECT customer_id, churn_probability, churn_risk_tier, "
            "customer_segment, total_spend_90d, sentiment_score, "
            "calculation_timestamp\n"
            f"FROM `{self.project_id}.{self.dataset_id}.{self.table_id}`\n"
            "WHERE customer_id = @customer_id\n"
            "ORDER BY calculation_timestamp DESC\n"
            "LIMIT 1"
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("customer_id", "STRING", customer_id)
            ]
        )

        try:
            rows = list(self.bq.query(sql, job_config=job_config).result())
        except Exception as exc:
            logger.warning(
                "BigQuery churn lookup failed for %s (%s). Falling back to 5-pillar heuristic.",
                customer_id, exc
            )
            return None

        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Step 3: friction signals
    # ------------------------------------------------------------------

    def _analyse_friction(
        self,
        customer_id: str,
        profile: Dict[str, Any],
        churn: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Derive segment, spend, sentiment and acute grievance from the profile.

        Where the profile is silent, the churn model's own feature values stand
        in: they were computed from the same customer's order history.
        """
        segment = str(
            profile.get("customerSegment")
            or profile.get("loyaltyTier")
            or churn.get("customerSegment")
            or "STANDARD_LOYALTY"
        ).upper()

        complaint = (
            profile.get("primaryComplaintReason")
            or profile.get("recentFrictionEvent")
            or profile.get("recent_friction_event")
        )
        recent_friction = profile.get("recentFrictionEvent") or profile.get("recent_friction_event")

        spend = profile.get("totalSpend90d", profile.get("historicalSpend90d"))
        if spend is None:
            spend = churn.get("totalSpend90d")
        spend = float(spend) if spend is not None else 0.0

        sentiment = profile.get("sentimentScore")
        if sentiment is None:
            sentiment = churn.get("sentimentScore")
        sentiment = float(sentiment) if sentiment is not None else 0.5

        has_acute = bool(
            (complaint and str(complaint).upper() in ACUTE_FRICTION_TYPES) or
            (recent_friction and str(recent_friction).upper() in ACUTE_FRICTION_TYPES)
        )

        return {
            "customerId": customer_id,
            "customerName": profile.get("customerName") or profile.get("name"),
            "customerEmail": profile.get("customerEmail") or profile.get("email"),
            "customerSegment": segment,
            "primaryComplaintReason": complaint,
            "recentFrictionEvent": recent_friction,
            "totalSpend90d": spend,
            "sentimentScore": sentiment,
            "accountAgeDays": profile.get("accountAgeDays", 365),
            "discountCapPercent": discount_ceiling_for_segment(segment),
            "grossMarginPercent": _gross_margin_percent(profile),
            "hasAcuteFriction": has_acute,
        }

    # ------------------------------------------------------------------
    # Step 4: cooldown
    # ------------------------------------------------------------------

    def _check_cooldown(self, customer_id: str, now: datetime) -> Dict[str, Any]:
        """Report whether the customer already holds, or recently held, an offer.

        Only offers created inside the cooldown window are read. The previous
        implementation streamed every offer the customer had ever received to
        answer a question about the last seven days, and an offer left in
        status ACTIVE from months ago suppressed the customer permanently.
        """
        ineligible = {
            "hasActiveOffer": False,
            "activeOffer": None,
            "inCooldown": False,
            "cooldownUntil": None,
        }
        if not self.fs:
            return ineligible

        cutoff = now - timedelta(days=self.cooldown_days)
        try:
            # createdAt is stored as an ISO-8601 UTC string, so a string range
            # filter orders chronologically as long as every writer uses the
            # same format, which persist_offer below does.
            query = (
                self.fs.collection(OFFERS_COLLECTION)
                .where(filter=FieldFilter("customerId", "==", customer_id))
                .where(filter=FieldFilter("createdAt", ">=", cutoff.isoformat()))
                .order_by("createdAt", direction="DESCENDING")
                .limit(COOLDOWN_SCAN_LIMIT)
            )
            recent = [doc.to_dict() or {} for doc in query.stream()]
        except Exception as exc:
            # Failing open would spam the customer; failing closed would only
            # delay an offer until the next login.
            logger.warning("Cooldown lookup failed for %s (%s). Treating as in cooldown.", customer_id, exc)
            return {**ineligible, "inCooldown": True}

        for offer in recent:
            if offer.get("status") == "ACTIVE":
                return {
                    "hasActiveOffer": True,
                    "activeOffer": offer,
                    "inCooldown": False,
                    "cooldownUntil": offer.get("cooldownUntil"),
                }

        for offer in recent:
            cooldown_str = offer.get("cooldownUntil") or offer.get("validUntil")
            if not cooldown_str:
                continue
            try:
                cooldown_dt = datetime.fromisoformat(cooldown_str)
            except (ValueError, TypeError):
                continue
            if cooldown_dt > now:
                return {
                    "hasActiveOffer": False,
                    "activeOffer": None,
                    "inCooldown": True,
                    "cooldownUntil": cooldown_str,
                }

        return ineligible

    # ------------------------------------------------------------------
    # Step 5: offer synthesis
    # ------------------------------------------------------------------

    def generate_deterministic_offer(
        self,
        customer_id: str,
        churn_tier: str,
        complaint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rule engine used when Vertex AI is unreachable or quota exhausted."""
        is_critical = (churn_tier == "CRITICAL")
        discount = 25 if is_critical else 15
        perks = ["FREE_EXPRESS_SHIPPING"] if is_critical else ["FREE_SHIPPING"]
        apology = "We apologize for any past shipping inconveniences." if complaint else None
        suffix = customer_id[-4:] if len(customer_id) >= 4 else customer_id

        return {
            "title": "Special Customer Loyalty Incentive",
            "description": "We appreciate your ongoing business and want to offer you an exclusive discount.",
            "discountPercent": discount,
            "promoCode": f"RETENTION-DET-{churn_tier}-{suffix}",
            "freeExpressShipping": is_critical,
            "perks": perks,
            "personalizedApology": apology,
            "generationSource": "DETERMINISTIC_RULES",
        }

    def synthesize_offer(
        self,
        customer_id: str,
        churn_probability: float,
        churn_tier: str,
        complaint: Optional[str] = None,
        segment: Optional[str] = None,
        gross_margin_percent: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Compose the offer copy, preferring Gemini and falling back to rules.

        generationSource records which path actually ran. It used to always say
        DETERMINISTIC_RULES because the Gemini call was written against an API
        that does not exist and threw on every invocation.
        """
        ceiling = discount_ceiling_for_segment(segment)
        payload = self._generate_with_gemini(
            customer_id, churn_probability, churn_tier, complaint, ceiling
        )
        if payload is None:
            payload = self.generate_deterministic_offer(customer_id, churn_tier, complaint)

        discount = apply_discount_guardrails(
            payload.get("discountPercent", payload.get("discountPercentage", 15)),
            segment,
            gross_margin_percent,
        )
        payload["discountPercent"] = discount
        payload["discountPercentage"] = discount
        return payload

    def _generate_with_gemini(
        self,
        customer_id: str,
        churn_probability: float,
        churn_tier: str,
        complaint: Optional[str],
        discount_ceiling: int,
    ) -> Optional[Dict[str, Any]]:
        """Ask Gemini for the offer copy, or None if it cannot be obtained."""
        if not self.genai:
            return None

        prompt = (
            "You write retention offers for a retailer. Reply with a single JSON "
            "object and nothing else, with keys: title (string), description "
            "(string, at most two sentences, addressed to the customer), "
            "promoCode (string, uppercase, no spaces), discountPercent "
            "(integer), freeExpressShipping (boolean), perks (array of strings), "
            "personalizedApology (string or null; only when a complaint is given).\n"
            f"Customer: {customer_id}\n"
            f"Churn probability: {churn_probability}\n"
            f"Churn tier: {churn_tier}\n"
            f"Open complaint: {complaint or 'none'}\n"
            f"Maximum discount percent: {discount_ceiling}"
        )

        try:
            response = self.genai.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            payload = json.loads(response.text)
            if not isinstance(payload, dict):
                raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
        except Exception as exc:
            logger.warning(
                "Gemini generation failed for %s (%s). Falling back to deterministic rules.",
                customer_id, exc
            )
            return None

        payload["generationSource"] = "GEMINI_AI"
        return payload

    # ------------------------------------------------------------------
    # Step 6: persistence
    # ------------------------------------------------------------------

    def persist_offer(
        self,
        customer_id: str,
        session_id: str,
        offer_payload: Dict[str, Any],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Validate the offer, write it, and move the session to PROCESSED.

        The field names below are the mobile client's contract; LoyaltyOffer
        validation is what stops a malformed payload reaching it.
        """
        now = now or datetime.now(timezone.utc)
        valid_until = now + timedelta(days=self.offer_validity_days)
        cooldown_until = now + timedelta(days=self.cooldown_days)
        ttl_expiry = now + timedelta(days=self.audit_ttl_days)

        offer_id = offer_payload.get("offerId") or f"off_{session_id}_retention"

        title = offer_payload.get("title") or offer_payload.get("headline", "Special Customer Loyalty Incentive")
        desc = offer_payload.get("description") or offer_payload.get("messageBody", "Exclusive discount on your next order.")
        discount = offer_payload.get("discountPercent") or offer_payload.get("discountPercentage", 15)
        promo = offer_payload.get("promoCode") or offer_payload.get("voucherCode", f"RETENTION-{customer_id[-4:]}")
        perks: List[str] = offer_payload.get("perks", [])
        free_shipping = offer_payload.get("freeExpressShipping", "FREE_EXPRESS_SHIPPING" in perks)
        churn_prob = offer_payload.get("churnProbability") or offer_payload.get("churnScore", 0.75)
        churn_tier = offer_payload.get("churnRiskTier") or offer_payload.get("churnTier", "HIGH")
        source = offer_payload.get("generationSource", "DETERMINISTIC_RULES")
        apology = offer_payload.get("personalizedApology")
        baseline = float(offer_payload.get("baselineChurnRisk", churn_prob))
        eval_source = offer_payload.get("evaluationSource", "HEURISTIC_FALLBACK")

        offer_doc = {
            "offerId": offer_id,
            "customerId": customer_id,
            "sessionId": session_id,
            "churnProbability": float(churn_prob),
            "churnScore": float(churn_prob),
            "churnRiskTier": churn_tier,
            "churnTier": churn_tier,
            "title": title,
            "headline": title,
            "description": desc,
            "messageBody": desc,
            "promoCode": promo,
            "voucherCode": promo,
            "discountPercent": int(discount),
            "discountPercentage": int(discount),
            "freeExpressShipping": bool(free_shipping),
            "perks": perks,
            "personalizedApology": apology,
            "generationSource": source,
            "baselineChurnRisk": baseline,
            "evaluationSource": eval_source,
            "status": "ACTIVE",
            "createdAt": now.isoformat(),
            "validUntil": valid_until.isoformat(),
            "expiresAt": valid_until.isoformat(),
            "cooldownUntil": cooldown_until.isoformat(),
            "claimedAt": None,
            "ttlExpiryAt": ttl_expiry.isoformat(),
            "metadata": {
                "mlModelVersion": "redwood_churn_v1",
                "retentionAction": f"DISPATCH_{churn_tier}_OFFER",
                "historicalSpend90d": offer_payload.get("historicalSpend90d", 0.0),
                "supportSentimentScore": offer_payload.get("sentimentScore", 0.5),
                "baselineChurnRisk": baseline,
                "evaluationSource": eval_source,
            }
        }

        validated_payload = LoyaltyOffer.model_validate(offer_doc).model_dump()

        if self.fs:
            self.fs.collection(OFFERS_COLLECTION).document(offer_id).set(validated_payload)
            try:
                self.fs.collection(SESSIONS_COLLECTION).document(session_id).set({
                    "agentProcessingStatus": "PROCESSED",
                    "status": "PROCESSED",
                    "offerId": offer_id,
                    "activeOfferId": offer_id,
                    "processedAt": now.isoformat(),
                    "skipReason": None
                }, merge=True)
            except Exception as exc:
                logger.warning("Could not update session %s in Firestore: %s", session_id, exc)

        return validated_payload

    # ------------------------------------------------------------------
    # Failure path
    # ------------------------------------------------------------------

    def mark_session_error(self, session_id: str, message: str) -> None:
        """Record a processing failure on the session."""
        if not self.fs:
            return
        try:
            self.fs.collection(SESSIONS_COLLECTION).document(session_id).update({
                "agentProcessingStatus": "ERROR",
                "errorMessage": message,
                "processedAt": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            logger.warning("Could not mark session %s as ERROR: %s", session_id, exc)


def _row_value(row: Any, key: str) -> Any:
    """Read a column from a BigQuery Row, which supports both access styles."""
    value = getattr(row, key, None)
    if value is None and hasattr(row, "get"):
        try:
            value = row.get(key)
        except Exception:
            value = None
    return value

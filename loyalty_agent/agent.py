"""
Autonomous Loyalty Offer Agent powered by Google Agent Development Kit (ADK).
Uses Gemini 3.8 Flash as the reasoning engine with multi-tool delegation and deterministic fallback.
"""

import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from google.api_core.exceptions import ResourceExhausted, GoogleAPICallError

try:
    from google.adk import Agent
    ADK_AVAILABLE = True
except ImportError:
    Agent = None
    ADK_AVAILABLE = False

from loyalty_agent.config import config
from loyalty_agent.schemas import CustomerSession, LoyaltyOffer, CustomerProfile, ChurnAssessment
from loyalty_agent.tools import (
    check_active_offers_cooldown,
    query_customer_churn_risk,
    get_customer_friction_and_profile,
    issue_loyalty_offer,
    evaluate_churn_tier
)


class AutonomousLoyaltyAgent:
    """
    Autonomous Retention Agent for Redwood Retail.
    Coordinates Firestore real-time streaming, BigQuery ML inference, and Gemini 3.8 Flash.
    """

    def __init__(
        self,
        firestore_client: Any,
        bigquery_client: Any,
        gemini_model: Any = None,
        model_name: str = config.reasoning_model,
        cooldown_days: int = config.cooldown_days,
        churn_threshold: float = config.churn_trigger_threshold
    ):
        self.fs = firestore_client
        self.bq = bigquery_client
        self.gemini = gemini_model
        self.model_name = model_name
        self.cooldown_days = cooldown_days
        self.churn_threshold = churn_threshold

        # Initialize ADK Agent definition if ADK is installed
        if ADK_AVAILABLE:
            self.adk_agent = Agent(
                name="loyalty_retention_agent",
                model=self.model_name,
                instruction=(
                    "You are an autonomous customer retention agent for Redwood Retail. "
                    "When a customer logs in, evaluate churn risk and friction points. "
                    "If churn risk is elevated (>= 0.50), synthesize a high-converting, "
                    "personalized loyalty voucher respecting discount ceilings and cooldown policies."
                ),
                tools=[
                    self._tool_cooldown,
                    self._tool_churn,
                    self._tool_friction,
                    self._tool_issue
                ]
            )
        else:
            self.adk_agent = None

    # ADK Tool Adapters
    def _tool_cooldown(self, customer_id: str) -> Dict[str, Any]:
        """ADK Tool: Inspects cooldown and existing active offers in Firestore."""
        return check_active_offers_cooldown(customer_id, self.fs, self.cooldown_days)

    def _tool_churn(self, customer_id: str) -> Dict[str, Any]:
        """ADK Tool: Queries BigQuery ML churn probability with cold-start fallback."""
        return query_customer_churn_risk(customer_id, self.bq, self.fs, config.bigquery_dataset)

    def _tool_friction(self, customer_id: str) -> Dict[str, Any]:
        """ADK Tool: Retrieves customer friction history and discount cap."""
        return get_customer_friction_and_profile(customer_id, self.fs)

    def _tool_issue(self, customer_id: str, session_id: str, offer_payload: Dict[str, Any]) -> Dict[str, Any]:
        """ADK Tool: Transactionally issues loyalty offer and marks session PROCESSED."""
        return issue_loyalty_offer(customer_id, session_id, offer_payload, self.fs, self.cooldown_days)

    def generate_deterministic_offer(
        self,
        customer_id: str,
        churn_tier: str,
        complaint: Optional[str] = None
    ) -> Dict[str, Any]:
        """Deterministic rule engine fallback when Vertex AI API is unavailable or rate limited."""
        is_critical = (churn_tier == "CRITICAL")
        discount = 25 if is_critical else 15
        shipping = is_critical
        perks = ["FREE_EXPRESS_SHIPPING"] if is_critical else ["FREE_SHIPPING"]
        apology = "We apologize for any past shipping inconveniences." if complaint else None
        suffix = customer_id[-4:] if len(customer_id) >= 4 else customer_id

        return {
            "title": "Special Customer Loyalty Incentive",
            "headline": "Special Customer Loyalty Incentive",
            "description": "We appreciate your ongoing business and want to offer you an exclusive discount.",
            "messageBody": "We appreciate your ongoing business and want to offer you an exclusive discount.",
            "discountPercent": discount,
            "discountPercentage": discount,
            "promoCode": f"RETENTION-DET-{churn_tier}-{suffix}",
            "voucherCode": f"RETENTION-DET-{churn_tier}-{suffix}",
            "freeExpressShipping": shipping,
            "perks": perks,
            "personalizedApology": apology,
            "generationSource": "DETERMINISTIC_RULES"
        }

    def process_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Executes end-to-end retention workflow for a single login session.

        Steps:
        1. Fetch session document from Firestore.
        2. Check active offer & cooldown guard.
        3. Query customer churn risk via BigQuery ML / heuristic.
        4. Query customer profile & friction point.
        5. Synthesize offer copy via Gemini 3.8 Flash (or deterministic fallback).
        6. Atomically persist offer and update session in Firestore.
        """
        sess_ref = self.fs.collection("customer_sessions").document(session_id)
        sess_snap = sess_ref.get()
        if not sess_snap.exists:
            return None

        sess_data = sess_snap.to_dict()
        try:
            session_model = CustomerSession.model_validate(sess_data)
            customer_id = session_model.customerId
        except Exception:
            customer_id = sess_data["customerId"]
        now = datetime.now(timezone.utc)

        # 1. Cooldown & Active Offer Guard
        cooldown_res = self._tool_cooldown(customer_id)
        if cooldown_res["has_active_offer"]:
            active_offer = cooldown_res["active_offer"]
            sess_ref.update({
                "agentProcessingStatus": "PROCESSED",
                "status": "PROCESSED",
                "offerId": active_offer["offerId"],
                "activeOfferId": active_offer["offerId"],
                "skipReason": "ACTIVE_OFFER_ALREADY_EXISTS",
                "processedAt": now.isoformat()
            })
            return active_offer

        if cooldown_res["in_cooldown"]:
            sess_ref.update({
                "agentProcessingStatus": "SKIPPED",
                "status": "PROCESSED",
                "offerId": None,
                "activeOfferId": None,
                "skipReason": "COOLDOWN_ACTIVE",
                "processedAt": now.isoformat()
            })
            return None

        # 2. Retrieve Baseline Churn Risk & Acute Friction Profile
        churn_res = self._tool_churn(customer_id)
        baseline_prob = churn_res["churn_probability"]
        baseline_tier = churn_res["churn_tier"]
        eval_source = churn_res["evaluation_source"]

        profile = self._tool_friction(customer_id)
        complaint = profile.get("primary_complaint_reason")
        recent_friction = profile.get("recent_friction_event")
        discount_cap = profile.get("discount_cap_percent", 15)

        # 3. Event-Augmented Hybrid Risk Synthesis (SDD Section 1.2)
        # Synthesizes daily batch baseline propensity with live operational friction
        acute_friction_types = {
            "REFUND_REQUESTED",
            "LATE_DELIVERY",
            "DEFECTIVE_COMPONENT",
            "ESCALATION",
            "BILLING_DISPUTE",
            "DAMAGED_SHIPMENT"
        }
        has_acute_friction = bool(
            (complaint and complaint.upper() in acute_friction_types) or
            (recent_friction and recent_friction.upper() in acute_friction_types)
        )

        if has_acute_friction:
            churn_prob = round(min(1.0, baseline_prob + config.acute_friction_boost), 4)
            churn_tier = evaluate_churn_tier(churn_prob)
            eval_source = "EVENT_AUGMENTED_HYBRID"
        else:
            churn_prob = baseline_prob
            churn_tier = baseline_tier

        # Actionability gate: evaluated against synthesized risk
        if churn_prob < self.churn_threshold:
            sess_ref.update({
                "agentProcessingStatus": "SKIPPED",
                "status": "PROCESSED",
                "offerId": None,
                "activeOfferId": None,
                "skipReason": "LOW_CHURN_RISK",
                "processedAt": now.isoformat()
            })
            return None

        # 4. Generate Offer (Gemini 3.8 Flash with Deterministic Fallback)
        offer_payload: Dict[str, Any] = {}
        if self.gemini:
            try:
                prompt = (
                    f"Customer: {customer_id}, Churn Risk: {churn_prob}, "
                    f"Tier: {churn_tier}, Complaint: {complaint}, Max Discount: {discount_cap}%, "
                    f"Source: {eval_source}"
                )
                response = self.gemini.generate_content(prompt)
                offer_payload = json.loads(response.text)
                offer_payload["generationSource"] = "GEMINI_AI"
            except (ResourceExhausted, GoogleAPICallError, Exception):
                offer_payload = self.generate_deterministic_offer(customer_id, churn_tier, complaint)
        else:
            offer_payload = self.generate_deterministic_offer(customer_id, churn_tier, complaint)

        # Enforce discount cap guardrail (SDD: Critical churn ceiling up to 25%)
        effective_cap = 25 if churn_tier == "CRITICAL" else discount_cap
        raw_discount = offer_payload.get("discountPercent", offer_payload.get("discountPercentage", 15))
        clamped_discount = min(raw_discount, effective_cap)
        offer_payload["discountPercent"] = clamped_discount
        offer_payload["discountPercentage"] = clamped_discount
        offer_payload["churnProbability"] = churn_prob
        offer_payload["churnRiskTier"] = churn_tier
        offer_payload["baselineChurnRisk"] = baseline_prob
        offer_payload["evaluationSource"] = eval_source
        offer_payload["historicalSpend90d"] = profile.get("total_spend_90d", 500.0)
        offer_payload["sentimentScore"] = profile.get("sentiment_score", 0.5)

        # 5. Issue Offer & Update Session
        return self._tool_issue(customer_id, session_id, offer_payload)

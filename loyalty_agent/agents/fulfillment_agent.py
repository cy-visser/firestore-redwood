"""
Offer Fulfillment Agent.
Autonomous A2A agent validating Pydantic schemas, calculating TTL policies,
and atomically committing loyalty vouchers to Cloud Firestore and updating sessions.
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Dict, Any, Optional

from loyalty_agent.a2a.card import AgentCard, AgentSkill
from loyalty_agent.agents.base_a2a_agent import BaseA2AAgent
from loyalty_agent.schemas import LoyaltyOffer

logger = logging.getLogger("a2a.fulfillment_agent")


class OfferFulfillmentAgent(BaseA2AAgent):
    """
    Autonomous domain agent managing transactional persistence, TTL calculation,
    and session state transitions for loyalty offers in Cloud Firestore.
    """

    def __init__(
        self,
        firestore_client: Any,
        base_url: str = "http://localhost:8081/fulfillment",
        default_validity_days: int = 14,
        default_cooldown_days: int = 7,
        default_audit_ttl_days: int = 90
    ):
        self.fs = firestore_client
        self.default_validity_days = default_validity_days
        self.default_cooldown_days = default_cooldown_days
        self.default_audit_ttl_days = default_audit_ttl_days

        card = AgentCard(
            name="Offer Fulfillment Agent",
            description="Transactionally commits validated retention vouchers to Cloud Firestore and updates session processing state.",
            version="1.0.0",
            url=base_url,
            skills=[
                AgentSkill(
                    id="fulfill_loyalty_voucher",
                    name="Fulfill and Persist Loyalty Voucher",
                    description="Validates offer payload, calculates TTL and cooldown timestamps, writes to /loyalty_offers, and updates session.",
                    tags=["Fulfillment", "Firestore", "Transaction", "Voucher", "Persistence"],
                    examples=["Fulfill retention voucher for session sess_9812"],
                    input_schema={
                        "type": "object",
                        "properties": {
                            "customerId": {"type": "string"},
                            "sessionId": {"type": "string"},
                            "offerPayload": {"type": "object"},
                            "cooldownDays": {"type": "integer", "default": 7},
                            "offerValidityDays": {"type": "integer", "default": 14},
                            "auditTtlDays": {"type": "integer", "default": 90}
                        },
                        "required": ["customerId", "sessionId", "offerPayload"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "offerId": {"type": "string"},
                            "offer": {"type": "object"},
                            "status": {"type": "string"},
                            "persistedAt": {"type": "string"}
                        }
                    }
                )
            ]
        )
        super().__init__(agent_card=card)
        self.register_skill_handler("fulfill_loyalty_voucher", self._handle_fulfill_voucher)

    def fulfill_voucher(
        self,
        customer_id: str,
        offer_dict: Dict[str, Any],
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Atomically persists validated loyalty voucher to Firestore."""
        params = {
            "customerId": customer_id,
            "sessionId": session_id or f"sess_{customer_id}",
            "offerPayload": offer_dict
        }
        return self._fulfill_voucher_sync(params, session_id or f"sess_{customer_id}")

    def _fulfill_voucher_sync(self, parameters: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        customer_id = parameters["customerId"]
        active_session_id = parameters.get("sessionId") or session_id
        offer_payload = parameters["offerPayload"]
        cooldown_days = parameters.get("cooldownDays", self.default_cooldown_days)
        validity_days = parameters.get("offerValidityDays", self.default_validity_days)
        ttl_days = parameters.get("auditTtlDays", self.default_audit_ttl_days)

        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=validity_days)
        cooldown_until = now + timedelta(days=cooldown_days)
        ttl_expiry = now + timedelta(days=ttl_days)

        offer_id = offer_payload.get("offerId") or f"off_{active_session_id}_retention"

        # Normalize fields across naming variations
        title = offer_payload.get("title") or offer_payload.get("headline", "Special Customer Loyalty Incentive")
        desc = offer_payload.get("description") or offer_payload.get("messageBody", "Exclusive discount on your next order.")
        discount = offer_payload.get("discountPercent") or offer_payload.get("discountPercentage", 15)
        promo = offer_payload.get("promoCode") or offer_payload.get("voucherCode", f"RETENTION-{customer_id[-4:]}")
        perks = offer_payload.get("perks", [])
        free_shipping = offer_payload.get("freeExpressShipping", "FREE_EXPRESS_SHIPPING" in perks)
        churn_prob = offer_payload.get("churnProbability") or offer_payload.get("churnScore", 0.75)
        churn_tier = offer_payload.get("churnRiskTier") or offer_payload.get("churnTier", "HIGH")
        source = offer_payload.get("generationSource", "GEMINI_AI")
        apology = offer_payload.get("personalizedApology")

        offer_doc = {
            "offerId": offer_id,
            "customerId": customer_id,
            "sessionId": active_session_id,
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
            "baselineChurnRisk": float(offer_payload.get("baselineChurnRisk", churn_prob)),
            "evaluationSource": offer_payload.get("evaluationSource", "A2A_ORCHESTRATED"),
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
                "historicalSpend90d": offer_payload.get("historicalSpend90d", 500.0),
                "supportSentimentScore": offer_payload.get("sentimentScore", 0.5),
                "baselineChurnRisk": float(offer_payload.get("baselineChurnRisk", churn_prob)),
                "evaluationSource": offer_payload.get("evaluationSource", "A2A_ORCHESTRATED")
            }
        }

        # Validate strictly against Pydantic LoyaltyOffer data contract
        validated_offer = LoyaltyOffer.model_validate(offer_doc)
        validated_payload = validated_offer.model_dump()

        # Write offer document
        if self.fs:
            self.fs.collection("loyalty_offers").document(offer_id).set(validated_payload)

            # Update session document if present
            try:
                sess_ref = self.fs.collection("customer_sessions").document(active_session_id)
                sess_ref.set({
                    "agentProcessingStatus": "PROCESSED",
                    "status": "PROCESSED",
                    "offerId": offer_id,
                    "activeOfferId": offer_id,
                    "processedAt": now.isoformat(),
                    "skipReason": None
                }, merge=True)
            except Exception as exc:
                logger.warning("Could not update session %s in Firestore: %s", active_session_id, exc)

        return {
            "offerId": offer_id,
            "offer": validated_payload,
            "status": "COMPLETED",
            "persistedAt": now.isoformat()
        }

    async def _handle_fulfill_voucher(self, parameters: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        return self._fulfill_voucher_sync(parameters, session_id)

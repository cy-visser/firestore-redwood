"""
Offer Synthesis Agent.
Autonomous A2A agent generating personalized retention copy via Vertex AI Gemini 3.8 Flash with deterministic fallback.
"""

import json
import logging
from typing import Dict, Any, Optional
from google.api_core.exceptions import ResourceExhausted, GoogleAPICallError

from loyalty_agent.a2a.card import AgentCard, AgentSkill
from loyalty_agent.agents.base_a2a_agent import BaseA2AAgent
from loyalty_agent.config import config

logger = logging.getLogger("a2a.synthesis_agent")


class OfferSynthesisAgent(BaseA2AAgent):
    """
    Autonomous domain agent synthesizing personalized retention offers, marketing copy,
    and apology messaging tailored to customer churn risk and acute operational grievances.
    """

    def __init__(
        self,
        gemini_model: Any = None,
        base_url: str = "http://localhost:8081/synthesis",
        model_name: str = config.reasoning_model
    ):
        self.gemini = gemini_model
        self.model_name = model_name

        card = AgentCard(
            name="Offer Synthesis Agent",
            description="Synthesizes personalized customer loyalty vouchers using Vertex AI Gemini 3.8 Flash with deterministic rule fallback.",
            version="1.0.0",
            url=base_url,
            skills=[
                AgentSkill(
                    id="synthesize_retention_offer",
                    name="Synthesize Personalized Retention Offer",
                    description="Composes compelling discount copy, promo codes, and grievance apologies tailored to customer churn context.",
                    tags=["Generative AI", "Gemini", "Personalization", "Retention", "Copywriting"],
                    examples=["Synthesize retention voucher for high-churn customer with late delivery"],
                    input_schema={
                        "type": "object",
                        "properties": {
                            "customerId": {"type": "string"},
                            "churnProbability": {"type": "number"},
                            "churnTier": {"type": "string"},
                            "primaryComplaint": {"type": "string"},
                            "discountCapPercent": {"type": "integer"},
                            "evaluationSource": {"type": "string"}
                        },
                        "required": ["customerId", "churnProbability", "churnTier"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "promoCode": {"type": "string"},
                            "discountPercent": {"type": "integer"},
                            "freeExpressShipping": {"type": "boolean"},
                            "perks": {"type": "array", "items": {"type": "string"}},
                            "personalizedApology": {"type": "string"},
                            "generationSource": {"type": "string"}
                        }
                    }
                )
            ]
        )
        super().__init__(agent_card=card)
        self.register_skill_handler("synthesize_retention_offer", self._handle_synthesize_offer)

    def generate_deterministic_offer(
        self,
        customer_id: str,
        churn_tier: str,
        complaint: Optional[str] = None
    ) -> Dict[str, Any]:
        """Deterministic rule engine fallback when Vertex AI is unreachable or quota exhausted."""
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

    async def _handle_synthesize_offer(self, parameters: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        customer_id = parameters["customerId"]
        churn_prob = parameters["churnProbability"]
        churn_tier = parameters["churnTier"]
        complaint = parameters.get("primaryComplaint")
        discount_cap = parameters.get("discountCapPercent", 15)
        eval_source = parameters.get("evaluationSource", "A2A_ORCHESTRATED")

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
            except (ResourceExhausted, GoogleAPICallError, Exception) as exc:
                logger.warning("Gemini AI generation failed (%s). Falling back to deterministic rules.", exc)
                offer_payload = self.generate_deterministic_offer(customer_id, churn_tier, complaint)
        else:
            offer_payload = self.generate_deterministic_offer(customer_id, churn_tier, complaint)

        # Enforce discount cap guardrail
        effective_cap = 25 if churn_tier == "CRITICAL" else discount_cap
        raw_discount = offer_payload.get("discountPercent", offer_payload.get("discountPercentage", 15))
        clamped_discount = min(int(raw_discount), effective_cap)

        offer_payload["discountPercent"] = clamped_discount
        offer_payload["discountPercentage"] = clamped_discount

        return offer_payload

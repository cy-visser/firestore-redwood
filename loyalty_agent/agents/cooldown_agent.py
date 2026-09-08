"""
Cooldown & Policy Agent.
Autonomous A2A agent enforcing 7-day cooldowns, active offer deduplication, and frequency capping.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional

from loyalty_agent.a2a.card import AgentCard, AgentSkill
from loyalty_agent.agents.base_a2a_agent import BaseA2AAgent


class CooldownPolicyAgent(BaseA2AAgent):
    """
    Autonomous domain agent managing customer eligibility policies and offer cooldowns.
    """

    def __init__(
        self,
        firestore_client: Any,
        base_url: str = "http://localhost:8081/cooldown",
        default_cooldown_days: int = 7
    ):
        self.fs = firestore_client
        self.default_cooldown_days = default_cooldown_days

        card = AgentCard(
            name="Cooldown & Policy Agent",
            description="Enforces 7-day offer cooldowns, active offer deduplication, and anti-spam frequency capping.",
            version="1.0.0",
            url=base_url,
            skills=[
                AgentSkill(
                    id="check_cooldown_eligibility",
                    name="Check Cooldown & Offer Eligibility",
                    description="Inspects Firestore for active offers and evaluates 7-day cooldown compliance.",
                    tags=["Policy", "Anti-Spam", "Cooldown", "Eligibility"],
                    examples=["Check eligibility for customer cust_8471"],
                    input_schema={
                        "type": "object",
                        "properties": {
                            "customerId": {"type": "string"},
                            "cooldownDays": {"type": "integer", "default": 7}
                        },
                        "required": ["customerId"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "isEligible": {"type": "boolean"},
                            "hasActiveOffer": {"type": "boolean"},
                            "activeOffer": {"type": "object"},
                            "inCooldown": {"type": "boolean"},
                            "cooldownUntil": {"type": "string"},
                            "reason": {"type": "string"}
                        }
                    }
                )
            ]
        )
        super().__init__(agent_card=card)
        self.register_skill_handler("check_cooldown_eligibility", self._handle_check_eligibility)

    def check_eligibility(self, customer_id: str, cooldown_days: Optional[int] = None) -> Dict[str, Any]:
        """Evaluates 7-day cooldown compliance and active offer presence in Firestore."""
        cd_days = cooldown_days if cooldown_days is not None else self.default_cooldown_days
        now = datetime.now(timezone.utc)
        offers_ref = self.fs.collection("loyalty_offers")
        query = offers_ref.where("customerId", "==", customer_id)

        for doc in query.stream():
            offer = doc.to_dict()
            status = offer.get("status", "")

            # If customer already has an ACTIVE offer, surface it
            if status == "ACTIVE":
                return {
                    "isEligible": False,
                    "hasActiveOffer": True,
                    "activeOffer": offer,
                    "inCooldown": False,
                    "cooldownUntil": None,
                    "reason": "ACTIVE_OFFER_ALREADY_EXISTS"
                }

            # Check cooldown timestamp
            cooldown_str = offer.get("cooldownUntil") or offer.get("validUntil")
            if cooldown_str:
                try:
                    cooldown_dt = datetime.fromisoformat(cooldown_str)
                    if cooldown_dt > now:
                        return {
                            "isEligible": False,
                            "hasActiveOffer": False,
                            "activeOffer": None,
                            "inCooldown": True,
                            "cooldownUntil": cooldown_str,
                            "reason": "COOLDOWN_ACTIVE"
                        }
                except (ValueError, TypeError):
                    pass

        return {
            "isEligible": True,
            "hasActiveOffer": False,
            "activeOffer": None,
            "inCooldown": False,
            "cooldownUntil": None,
            "reason": "ELIGIBLE_FOR_EVALUATION"
        }

    async def _handle_check_eligibility(self, parameters: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        customer_id = parameters["customerId"]
        cooldown_days = parameters.get("cooldownDays", self.default_cooldown_days)
        return self.check_eligibility(customer_id, cooldown_days=cooldown_days)


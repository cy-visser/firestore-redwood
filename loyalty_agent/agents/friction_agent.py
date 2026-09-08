"""
Customer Friction & Profile Agent.
Autonomous A2A agent investigating customer relationship context, spend, and acute operational friction.
"""

from typing import Dict, Any, Optional

from loyalty_agent.a2a.card import AgentCard, AgentSkill
from loyalty_agent.agents.base_a2a_agent import BaseA2AAgent

ACUTE_FRICTION_TYPES = {
    "REFUND_REQUESTED",
    "LATE_DELIVERY",
    "DEFECTIVE_COMPONENT",
    "ESCALATION",
    "BILLING_DISPUTE",
    "DAMAGED_SHIPMENT"
}

DISCOUNT_CAP_MAP = {
    "ENTERPRISE_VIP": 25,
    "RETAIL_PRO": 20,
    "PLATINUM": 20,
    "GOLD": 20,
    "STANDARD_LOYALTY": 15,
    "SILVER": 15,
    "BRONZE": 15,
    "CASUAL": 12
}


class CustomerFrictionAgent(BaseA2AAgent):
    """
    Autonomous domain agent analyzing customer tier, operational friction, and financial guardrails.
    """

    def __init__(
        self,
        firestore_client: Any,
        base_url: str = "http://localhost:8081/friction"
    ):
        self.fs = firestore_client

        card = AgentCard(
            name="Customer Friction Agent",
            description="Analyzes customer relationship context, loyalty tier, spend history, and operational friction events.",
            version="1.0.0",
            url=base_url,
            skills=[
                AgentSkill(
                    id="analyze_friction_and_profile",
                    name="Analyze Friction and Customer Profile",
                    description="Retrieves customer profile, sentiment score, 90-day spend, and detects acute operational grievances.",
                    tags=["Customer", "Friction", "Complaints", "Sentiment", "LTV"],
                    examples=["Analyze profile and friction for cust_98412"],
                    input_schema={
                        "type": "object",
                        "properties": {
                            "customerId": {"type": "string"}
                        },
                        "required": ["customerId"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "customerId": {"type": "string"},
                            "customerName": {"type": "string"},
                            "customerSegment": {"type": "string"},
                            "primaryComplaintReason": {"type": "string"},
                            "primaryFriction": {"type": "string"},
                            "recentFrictionEvent": {"type": "string"},
                            "totalSpend90d": {"type": "number"},
                            "sentimentScore": {"type": "number"},
                            "accountAgeDays": {"type": "integer"},
                            "discountCapPercent": {"type": "integer"},
                            "hasAcuteFriction": {"type": "boolean"}
                        }
                    }
                )
            ]
        )
        super().__init__(agent_card=card)
        self.register_skill_handler("analyze_friction_and_profile", self._handle_analyze_friction)

    def analyze_friction(self, customer_id: str, device_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Analyzes customer relationship context, sentiment score, and acute operational friction."""
        return self._analyze_friction_sync({"customerId": customer_id, "deviceInfo": device_info})

    def _analyze_friction_sync(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        customer_id = parameters["customerId"]
        doc_ref = self.fs.collection("customers").document(customer_id)
        doc_snap = doc_ref.get()

        if not doc_snap.exists:
            return {
                "customerId": customer_id,
                "customerName": None,
                "customerEmail": None,
                "customerSegment": "STANDARD_LOYALTY",
                "primaryComplaintReason": None,
                "primaryFriction": None,
                "recentFrictionEvent": None,
                "totalSpend90d": 0.0,
                "sentimentScore": 0.5,
                "accountAgeDays": 365,
                "discountCapPercent": 15,
                "hasAcuteFriction": False
            }

        data = doc_snap.to_dict()
        segment = str(data.get("customerSegment") or data.get("loyaltyTier") or "STANDARD_LOYALTY").upper()
        complaint = data.get("primaryComplaintReason") or data.get("recentFrictionEvent") or data.get("recent_friction_event")
        recent_friction = data.get("recentFrictionEvent") or data.get("recent_friction_event")
        spend = float(data.get("totalSpend90d", data.get("historicalSpend90d", 500.0)))
        sentiment = float(data.get("sentimentScore", 0.5))

        cap = DISCOUNT_CAP_MAP.get(segment, 15)

        has_acute = bool(
            (complaint and str(complaint).upper() in ACUTE_FRICTION_TYPES) or
            (recent_friction and str(recent_friction).upper() in ACUTE_FRICTION_TYPES)
        )

        return {
            "customerId": customer_id,
            "customerName": data.get("name"),
            "customerEmail": data.get("email"),
            "customerSegment": segment,
            "primaryComplaintReason": complaint,
            "primaryFriction": complaint,
            "recentFrictionEvent": recent_friction,
            "totalSpend90d": spend,
            "sentimentScore": sentiment,
            "accountAgeDays": data.get("accountAgeDays", 365),
            "discountCapPercent": cap,
            "hasAcuteFriction": has_acute
        }

    async def _handle_analyze_friction(self, parameters: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        return self._analyze_friction_sync(parameters)

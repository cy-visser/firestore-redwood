"""
Redwood Retail Autonomous Loyalty Offer Agent.
"""

from loyalty_agent.agent import LoyaltyAgent
from loyalty_agent.config import AgentConfig, ConfigurationError, config
from loyalty_agent.policy import (
    apply_discount_guardrails,
    discount_ceiling_for_segment,
    evaluate_churn_tier,
    latest_complaint,
)

__all__ = [
    "config",
    "AgentConfig",
    "ConfigurationError",
    "LoyaltyAgent",
    "evaluate_churn_tier",
    "discount_ceiling_for_segment",
    "apply_discount_guardrails",
    "latest_complaint",
]

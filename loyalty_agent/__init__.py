"""
Redwood Retail Autonomous Loyalty Multi-Agent System on Google Cloud Agent Runtime.
Built on the official A2A (Agent2Agent) Protocol Specification.
"""

from loyalty_agent.config import config, AgentConfig
from loyalty_agent.listener import SessionEventListener
from loyalty_agent.agents import (
    BaseA2AAgent,
    RetentionOrchestratorAgent,
    CooldownPolicyAgent,
    CustomerFrictionAgent,
    ChurnIntelligenceAgent,
    OfferSynthesisAgent,
    OfferFulfillmentAgent,
)

__all__ = [
    "config",
    "AgentConfig",
    "SessionEventListener",
    "BaseA2AAgent",
    "RetentionOrchestratorAgent",
    "CooldownPolicyAgent",
    "CustomerFrictionAgent",
    "ChurnIntelligenceAgent",
    "OfferSynthesisAgent",
    "OfferFulfillmentAgent",
]

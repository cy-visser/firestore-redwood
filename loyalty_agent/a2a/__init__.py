"""
A2A (Agent2Agent) Protocol Package.
Standard AgentCard manifests, dynamic discovery client, and task execution engine.
"""

from loyalty_agent.a2a.card import (
    AgentCard,
    AgentSkill,
    AgentCapabilities,
    AgentInterface,
)
from loyalty_agent.a2a.task import (
    TaskRequest,
    TaskResponse,
    TaskState,
)
from loyalty_agent.a2a.discovery import (
    A2ADiscoveryClient,
    AgentDiscoveryError,
    DISCOVERY_PATHS,
)
from loyalty_agent.a2a.client import (
    A2AClient,
    A2AExecutionError,
)

__all__ = [
    "AgentCard",
    "AgentSkill",
    "AgentCapabilities",
    "AgentInterface",
    "TaskRequest",
    "TaskResponse",
    "TaskState",
    "A2ADiscoveryClient",
    "AgentDiscoveryError",
    "DISCOVERY_PATHS",
    "A2AClient",
    "A2AExecutionError",
]

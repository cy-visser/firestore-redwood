"""
A2A (Agent2Agent) Protocol - Agent Card Specification.
Strictly conforms to the open A2A specification (https://a2a-protocol.org/latest/).
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class AgentCapabilities(BaseModel):
    """Declaration of capabilities supported by the agent."""
    streaming: bool = False
    state_transition_history: bool = True
    push_notifications: bool = False


class AgentInterface(BaseModel):
    """Interface configuration specifying transport and endpoint URL."""
    transport: str = "HTTP+JSON"  # HTTP+JSON, JSONRPC, GRPC
    url: str


class AgentSkill(BaseModel):
    """
    Declaration of a discrete capability or skill offered by an agent.
    Provides semantic metadata and JSON schemas for inputs and outputs.
    """
    id: str = Field(..., description="Unique machine-readable identifier for the skill.")
    name: str = Field(..., description="Human-readable title of the skill.")
    description: str = Field(..., description="Detailed description of what the skill performs.")
    tags: List[str] = Field(default_factory=list, description="Categorization tags for skill discovery.")
    examples: List[str] = Field(default_factory=list, description="Natural language prompt or query examples.")
    input_modes: List[str] = Field(default_factory=lambda: ["application/json"])
    output_modes: List[str] = Field(default_factory=lambda: ["application/json"])
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for task input parameters.")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for task output results.")


class AgentCard(BaseModel):
    """
    The AgentCard is a self-describing manifest for an agent in the A2A Protocol.
    It provides essential metadata including identity, capabilities, skills, and communication endpoints.
    Hosted canonically at `/.well-known/agent-card.json`.
    """
    name: str = Field(..., description="Human-readable name of the agent.")
    description: str = Field(..., description="Functional summary of the agent's purpose.")
    version: str = Field(default="1.0.0", description="Semantic version of the agent.")
    url: str = Field(..., description="Base URL of the agent service.")
    preferred_transport: str = Field(default="HTTP+JSON", description="Preferred transport mechanism.")
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    additional_interfaces: List[AgentInterface] = Field(default_factory=list)
    default_input_modes: List[str] = Field(default_factory=lambda: ["application/json"])
    default_output_modes: List[str] = Field(default_factory=lambda: ["application/json"])
    documentation_url: Optional[str] = None
    icon_url: Optional[str] = None
    skills: List[AgentSkill] = Field(default_factory=list, description="List of skills published by this agent.")

    def get_skill(self, skill_id: str) -> Optional[AgentSkill]:
        """Retrieves a skill declaration by ID."""
        for skill in self.skills:
            if skill.id == skill_id:
                return skill
        return None

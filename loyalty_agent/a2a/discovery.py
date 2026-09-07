"""
A2A (Agent2Agent) Protocol - Discovery Client.
Discovers and inspects remote and local A2A agents via canonical `/.well-known/agent-card.json` endpoints.
"""

import logging
from typing import Dict, Optional, List, Tuple
import httpx
from pydantic import ValidationError

from loyalty_agent.a2a.card import AgentCard, AgentSkill

logger = logging.getLogger("a2a.discovery")

DISCOVERY_PATHS: Tuple[str, ...] = (
    "/.well-known/agent-card.json",
    "/.well-known/agent.json",
)


class AgentDiscoveryError(Exception):
    """Raised when an agent card cannot be resolved from the target endpoint."""
    pass


class A2ADiscoveryClient:
    """
    Standard discovery client resolving `AgentCard` manifests across distributed agent endpoints.
    Maintains a dynamic registry cache of discovered agents and indexed skills.
    """

    def __init__(self, timeout_seconds: float = 5.0):
        self.timeout = timeout_seconds
        self._cache: Dict[str, AgentCard] = {}
        self._skill_index: Dict[str, str] = {}  # skill_id -> agent_url
        self._local_registry: Dict[str, AgentCard] = {}  # In-process mock/local cards

    def register_local_card(self, url: str, card: AgentCard) -> None:
        """Registers an in-process AgentCard for hermetic testing and local execution."""
        clean_url = url.rstrip("/")
        self._local_registry[clean_url] = card
        self._cache[clean_url] = card
        for skill in card.skills:
            self._skill_index[skill.id] = clean_url

    async def discover(self, base_url: str, force_refresh: bool = False) -> AgentCard:
        """
        Discovers an agent by querying its canonical `/.well-known/agent-card.json` endpoint.

        Args:
            base_url: Base endpoint URL of the agent service.
            force_refresh: Whether to bypass local cache.

        Returns:
            Validated `AgentCard` manifest.
        """
        clean_url = base_url.rstrip("/")

        if not force_refresh and clean_url in self._cache:
            return self._cache[clean_url]

        # Check in-memory local registry
        if clean_url in self._local_registry:
            card = self._local_registry[clean_url]
            self._cache[clean_url] = card
            return card

        # Perform remote HTTP discovery across standard well-known paths
        last_error = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for path in DISCOVERY_PATHS:
                endpoint = f"{clean_url}{path}"
                try:
                    response = await client.get(endpoint, headers={"Accept": "application/json"})
                    if response.status_code == 200:
                        data = response.json()
                        card = AgentCard.model_validate(data)
                        self._cache[clean_url] = card
                        for skill in card.skills:
                            self._skill_index[skill.id] = clean_url
                        logger.info("Successfully discovered A2A Agent '%s' (%s) at %s", card.name, card.version, endpoint)
                        return card
                    else:
                        last_error = f"HTTP {response.status_code} at {endpoint}"
                except (httpx.RequestError, ValidationError, Exception) as exc:
                    last_error = f"{type(exc).__name__}: {str(exc)} at {endpoint}"
                    continue

        raise AgentDiscoveryError(f"Failed to discover A2A AgentCard at {base_url}. Last attempt: {last_error}")

    def find_agent_for_skill(self, skill_id: str) -> Optional[str]:
        """Returns the base URL of the agent that publishes the specified skill ID."""
        return self._skill_index.get(skill_id)

    def get_cached_card(self, base_url: str) -> Optional[AgentCard]:
        """Returns cached AgentCard if available."""
        return self._cache.get(base_url.rstrip("/"))

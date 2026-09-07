"""
Base A2A Agent Class.
Provides standard AgentCard hosting, skill registration, and task dispatching.
"""

from abc import ABC, abstractmethod
import logging
from typing import Dict, Any, Callable, Awaitable, Optional

from loyalty_agent.a2a.card import AgentCard, AgentSkill
from loyalty_agent.a2a.task import TaskRequest, TaskResponse, TaskState

logger = logging.getLogger("a2a.agent")


class BaseA2AAgent(ABC):
    """
    Abstract base class for all A2A agents deployed on Google Cloud Agent Runtime.
    Exposes discovery card at `/.well-known/agent-card.json` and executes skills via `POST /a2a/v1/tasks`.
    """

    def __init__(self, agent_card: AgentCard):
        self.agent_card = agent_card
        self._skill_handlers: Dict[str, Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]] = {}

    def register_skill_handler(
        self,
        skill_id: str,
        handler: Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]
    ) -> None:
        """Registers an asynchronous handler function for a declared skill ID."""
        self._skill_handlers[skill_id] = handler

    def get_agent_card(self) -> AgentCard:
        """Returns the self-describing AgentCard manifest for discovery."""
        return self.agent_card

    async def handle_task(self, request: TaskRequest) -> TaskResponse:
        """
        Executes a task request against registered skill handlers.

        Args:
            request: The incoming TaskRequest.

        Returns:
            TaskResponse containing output data or error details.
        """
        skill = self.agent_card.get_skill(request.skill_id)
        if not skill:
            return TaskResponse(
                task_id=request.task_id,
                skill_id=request.skill_id,
                status=TaskState.FAILED,
                error_message=f"Skill '{request.skill_id}' not found in AgentCard for {self.agent_card.name}."
            )

        handler = self._skill_handlers.get(request.skill_id)
        if not handler:
            return TaskResponse(
                task_id=request.task_id,
                skill_id=request.skill_id,
                status=TaskState.FAILED,
                error_message=f"No execution handler registered for skill '{request.skill_id}'."
            )

        try:
            output = await handler(request.parameters, request.session_id)
            return TaskResponse(
                task_id=request.task_id,
                skill_id=request.skill_id,
                status=TaskState.COMPLETED,
                output=output,
                execution_metadata={"agent_name": self.agent_card.name, "version": self.agent_card.version}
            )
        except Exception as exc:
            logger.exception("Error executing skill '%s' on %s: %s", request.skill_id, self.agent_card.name, exc)
            return TaskResponse(
                task_id=request.task_id,
                skill_id=request.skill_id,
                status=TaskState.FAILED,
                error_message=str(exc),
                execution_metadata={"agent_name": self.agent_card.name, "version": self.agent_card.version}
            )

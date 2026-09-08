"""
A2A (Agent2Agent) Protocol - Execution Client.
Dispatches standard A2A task requests to discovered agents over HTTP or in-process routers.
"""

import time
import logging
from typing import Dict, Any, Optional, Callable, Awaitable
import httpx

from loyalty_agent.a2a.card import AgentCard
from loyalty_agent.a2a.task import TaskRequest, TaskResponse, TaskState
from loyalty_agent.a2a.discovery import A2ADiscoveryClient

logger = logging.getLogger("a2a.client")


class A2AExecutionError(Exception):
    """Raised when an A2A task invocation fails or returns FAILED status."""
    pass


class A2AClient:
    """
    Standard client for invoking skills on A2A agents discovered via their AgentCard.
    Supports both remote HTTP endpoints and direct in-memory invocation.
    """

    def __init__(
        self,
        discovery_client: Optional[A2ADiscoveryClient] = None,
        timeout_seconds: float = 10.0
    ):
        self.discovery = discovery_client or A2ADiscoveryClient()
        self.timeout = timeout_seconds
        # In-process execution router: agent_url -> handler_callable(TaskRequest) -> TaskResponse
        self._local_handlers: Dict[str, Callable[[TaskRequest], Awaitable[TaskResponse]]] = {}

    def register_local_handler(
        self,
        agent_url: str,
        handler: Callable[[TaskRequest], Awaitable[TaskResponse]]
    ) -> None:
        """Registers a direct in-process handler for an agent URL (for hermetic unit tests & collocated runtimes)."""
        clean_url = agent_url.rstrip("/")
        self._local_handlers[clean_url] = handler

    async def execute_task(
        self,
        agent_url: str,
        skill_id: str,
        session_id: str,
        parameters: Dict[str, Any]
    ) -> TaskResponse:
        """
        Executes a task on the target A2A agent.

        Args:
            agent_url: Base endpoint URL of the agent service.
            skill_id: The ID of the skill to execute.
            session_id: The active customer session correlation ID.
            parameters: Input parameters conforming to the skill's inputSchema.

        Returns:
            TaskResponse containing task output and status.
        """
        clean_url = agent_url.rstrip("/")
        request = TaskRequest(
            skill_id=skill_id,
            session_id=session_id,
            parameters=parameters
        )

        # 1. Check for in-process local handler
        if clean_url in self._local_handlers:
            start_t = time.perf_counter()
            response = await self._local_handlers[clean_url](request)
            latency = (time.perf_counter() - start_t) * 1000
            response.execution_metadata["latency_ms"] = round(latency, 2)
            if response.status == TaskState.FAILED:
                raise A2AExecutionError(f"A2A Task {request.task_id} failed on {clean_url}: {response.error_message}")
            return response

        # 2. Verify agent card via discovery
        card = await self.discovery.discover(clean_url)
        skill = card.get_skill(skill_id)
        if not skill:
            raise A2AExecutionError(f"Agent at {clean_url} does not declare skill '{skill_id}' in its AgentCard.")

        # 3. Check for Vertex AI Reasoning Engine execution
        is_re = (
            clean_url.startswith("projects/") or
            clean_url.startswith("vertexai://") or
            clean_url.isdigit() or
            (not clean_url.startswith("http://") and not clean_url.startswith("https://"))
        )
        if is_re:
            from loyalty_agent.config import config
            resource_name = clean_url.replace("vertexai://", "")
            if not resource_name.startswith("projects/"):
                resource_name = f"projects/{config.project_id}/locations/{config.region}/reasoningEngines/{resource_name}"
            start_t = time.perf_counter()
            import asyncio
            import vertexai
            from vertexai.preview import reasoning_engines
            vertexai.init(project=config.project_id, location=config.region)
            loop = asyncio.get_running_loop()

            def _call_engine():
                vertexai.init(project=config.project_id, location=config.region)
                engine = reasoning_engines.ReasoningEngine(resource_name)
                return engine.handle_task(task_request_data=request.model_dump())

            try:
                raw_resp = await loop.run_in_executor(None, _call_engine)
                latency = (time.perf_counter() - start_t) * 1000
                response = TaskResponse.model_validate(raw_resp)
                response.execution_metadata["latency_ms"] = round(latency, 2)
                response.execution_metadata["transport"] = "vertex_ai_reasoning_engine"
                if response.status == TaskState.FAILED:
                    raise A2AExecutionError(f"A2A Task failed on {resource_name}: {response.error_message}")
                return response
            except Exception as exc:
                if isinstance(exc, A2AExecutionError):
                    raise
                raise A2AExecutionError(f"Error invoking Reasoning Engine at {resource_name}: {exc}") from exc

        # 4. Dispatch remote HTTP request
        task_endpoint = f"{clean_url}/a2a/v1/tasks"
        start_t = time.perf_counter()

        async with httpx.AsyncClient(timeout=self.timeout) as http_client:
            try:
                http_resp = await http_client.post(
                    task_endpoint,
                    json=request.model_dump(),
                    headers={"Content-Type": "application/json", "Accept": "application/json"}
                )
                latency = (time.perf_counter() - start_t) * 1000

                if http_resp.status_code != 200:
                    raise A2AExecutionError(
                        f"HTTP {http_resp.status_code} calling {task_endpoint}: {http_resp.text}"
                    )

                response_data = http_resp.json()
                response = TaskResponse.model_validate(response_data)
                response.execution_metadata["latency_ms"] = round(latency, 2)

                if response.status == TaskState.FAILED:
                    raise A2AExecutionError(f"A2A Task failed on {clean_url}: {response.error_message}")

                return response
            except (httpx.RequestError, Exception) as exc:
                if isinstance(exc, A2AExecutionError):
                    raise
                raise A2AExecutionError(f"Network error invoking A2A agent at {task_endpoint}: {str(exc)}") from exc

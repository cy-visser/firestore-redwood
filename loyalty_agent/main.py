"""
Entrypoint CLI runner for Redwood Retail Multi-Agent Retention System.
Deployed on Google Cloud Agent Runtime with standard A2A discovery endpoints.
"""

import sys
import os
import time
import json
import signal
import asyncio
import logging
import threading
from typing import Dict, Any, Optional
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from loyalty_agent.config import config
from loyalty_agent.a2a.card import AgentCard
from loyalty_agent.a2a.task import TaskRequest, TaskResponse, TaskState
from loyalty_agent.agents import (
    RetentionOrchestratorAgent,
    CooldownPolicyAgent,
    CustomerFrictionAgent,
    ChurnIntelligenceAgent,
    OfferSynthesisAgent,
    OfferFulfillmentAgent,
    BaseA2AAgent
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("loyalty_agent.main")


class MultiAgentA2ARuntimeHandler(BaseHTTPRequestHandler):
    """
    HTTP Request Handler hosting canonical A2A discovery endpoints
    (/.well-known/agent-card.json) and standard task execution endpoints (/a2a/v1/tasks).
    """

    agent_registry: Dict[str, BaseA2AAgent] = {}
    orchestrator: Optional[RetentionOrchestratorAgent] = None

    @classmethod
    def set_agents(cls, orchestrator: RetentionOrchestratorAgent, domain_agents: Dict[str, BaseA2AAgent]):
        cls.orchestrator = orchestrator
        cls.agent_registry = domain_agents
        cls.agent_registry["orchestrator"] = orchestrator

    def _send_json(self, status_code: int, data: Any):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url_path = self.path.split("?")[0]

        # 1. Health and Liveness Probes
        if url_path in ("/", "/healthz", "/health"):
            agent_roster = {
                prefix: {
                    "name": agent.get_agent_card().name,
                    "version": agent.get_agent_card().version,
                    "skills": [s.id for s in agent.get_agent_card().skills]
                }
                for prefix, agent in self.agent_registry.items()
            }
            self._send_json(200, {
                "status": "HEALTHY",
                "platform": "Google Cloud Agent Runtime",
                "protocol": "A2A (Agent2Agent) v1.0",
                "agents": agent_roster
            })
            return

        # 2. Canonical Agent Discovery Endpoints (/.well-known/agent-card.json)
        # Root agent card defaults to Retention Orchestrator
        if url_path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            if self.orchestrator:
                self._send_json(200, self.orchestrator.get_agent_card().model_dump())
            else:
                self._send_json(503, {"error": "Orchestrator not initialized"})
            return

        # Scoped agent card discovery endpoints: /{agent_name}/.well-known/agent-card.json
        for prefix, agent in self.agent_registry.items():
            if url_path in (f"/{prefix}/.well-known/agent-card.json", f"/{prefix}/.well-known/agent.json"):
                self._send_json(200, agent.get_agent_card().model_dump())
                return

        self._send_json(404, {"error": f"Endpoint {url_path} not found"})

    def do_POST(self):
        url_path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            body_json = json.loads(raw_body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON payload in request body"})
            return

        # 1. Standard A2A Task Execution Endpoints
        target_agent: Optional[BaseA2AAgent] = None
        if url_path in ("/a2a/v1/tasks", "/orchestrator/a2a/v1/tasks"):
            target_agent = self.orchestrator
        else:
            for prefix, agent in self.agent_registry.items():
                if url_path == f"/{prefix}/a2a/v1/tasks":
                    target_agent = agent
                    break

        if target_agent:
            try:
                task_request = TaskRequest.model_validate(body_json)
                task_response = asyncio.run(target_agent.handle_task(task_request))
                status_code = 200 if task_response.status != TaskState.FAILED else 500
                self._send_json(status_code, task_response.model_dump())
            except Exception as exc:
                logger.exception("Error executing task on %s: %s", target_agent.get_agent_card().name, exc)
                self._send_json(500, {
                    "status": "FAILED",
                    "error": str(exc),
                    "agent": target_agent.get_agent_card().name
                })
            return

        # 2. Session Trigger / Evaluation Helper: /trigger_session
        if url_path == "/trigger_session" and self.orchestrator:
            session_id = body_json.get("sessionId")
            if not session_id:
                self._send_json(400, {"error": "Missing sessionId in request body"})
                return
            offer = self.orchestrator.process_session(session_id)
            self._send_json(200, {
                "sessionId": session_id,
                "action": "OFFER_ISSUED" if offer else "NO_OFFER_ISSUED",
                "offer": offer
            })
            return

        self._send_json(404, {"error": f"Endpoint {url_path} not found"})

    def log_message(self, format, *args):
        # Suppress routine health check log spam
        if "health" not in args[0]:
            logger.info("%s - %s", self.address_string(), format % args)


def start_multi_agent_server(
    orchestrator: RetentionOrchestratorAgent,
    domain_agents: Dict[str, BaseA2AAgent],
    port: int = 8080
) -> ThreadingHTTPServer:
    """Starts background ThreadingHTTPServer for Agent Runtime discovery and execution."""
    MultiAgentA2ARuntimeHandler.set_agents(orchestrator, domain_agents)
    server = ThreadingHTTPServer(("0.0.0.0", port), MultiAgentA2ARuntimeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("📡 A2A Multi-Agent Runtime server listening on 0.0.0.0:%d", port)
    logger.info("   Discovery:   http://0.0.0.0:%d/.well-known/agent-card.json", port)
    logger.info("   Tasks:       http://0.0.0.0:%d/a2a/v1/tasks", port)
    return server


class RetentionAgentEngine:
    """
    Google Cloud Agent Runtime / Vertex AI Agent Engine.
    Coordinates the Redwood Retail multi-agent retention platform
    via standard A2A protocol.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        firestore_database: Optional[str] = None,
        bigquery_dataset: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        cooldown_days: Optional[int] = None,
    ):
        self.project_id = project_id or config.project_id
        self.region = region or config.region
        self.firestore_database = firestore_database or config.firestore_database
        self.bigquery_dataset = bigquery_dataset or config.bigquery_dataset
        self.reasoning_model = reasoning_model or config.reasoning_model
        self.cooldown_days = cooldown_days if cooldown_days is not None else config.cooldown_days

        self.fs_client = None
        self.bq_client = None
        self.genai_client = None
        self.orchestrator: Optional[RetentionOrchestratorAgent] = None
        self.domain_agents: Dict[str, BaseA2AAgent] = {}

    def set_up(self):
        """Lifecycle hook invoked by Agent Runtime upon deployment/initialization."""
        from google.cloud import firestore
        from google.cloud import bigquery
        from google import genai

        logger.info("Initializing RetentionAgentEngine on Google Cloud Agent Runtime...")
        self.fs_client = firestore.Client(project=self.project_id, database=self.firestore_database)
        self.bq_client = bigquery.Client(project=self.project_id, location=self.region)

        try:
            self.genai_client = genai.Client()
        except Exception as e:
            logger.warning("Vertex AI Client init warning (will use deterministic fallback): %s", e)
            self.genai_client = None

        cooldown_agent = CooldownPolicyAgent(
            firestore_client=self.fs_client,
            default_cooldown_days=self.cooldown_days
        )
        friction_agent = CustomerFrictionAgent(
            firestore_client=self.fs_client
        )
        churn_agent = ChurnIntelligenceAgent(
            bigquery_client=self.bq_client,
            firestore_client=self.fs_client
        )
        synthesis_agent = OfferSynthesisAgent(
            gemini_model=self.genai_client
        )
        fulfillment_agent = OfferFulfillmentAgent(
            firestore_client=self.fs_client,
            default_cooldown_days=self.cooldown_days
        )

        self.domain_agents = {
            "cooldown": cooldown_agent,
            "friction": friction_agent,
            "churn": churn_agent,
            "synthesis": synthesis_agent,
            "fulfillment": fulfillment_agent
        }

        self.orchestrator = RetentionOrchestratorAgent(
            firestore_client=self.fs_client,
            bigquery_client=self.bq_client,
            gemini_model=self.genai_client,
            auto_register_local_domain_agents=False
        )
        for agent in self.domain_agents.values():
            self.orchestrator.register_domain_agent(agent)

        logger.info("✅ RetentionAgentEngine initialized with 5 domain agents.")

    def query(self, session_id: str, **kwargs) -> Dict[str, Any]:
        """
        Primary query entrypoint called statelessly by Google Cloud Agent Runtime.
        Evaluates session, coordinates agents via A2A, and issues loyalty offer.
        """
        if self.orchestrator is None:
            self.set_up()

        offer = self.orchestrator.process_session(session_id)
        return {
            "sessionId": session_id,
            "action": "OFFER_ISSUED" if offer else "NO_OFFER_ISSUED",
            "offer": offer
        }

    async def handle_task(self, task_request_data: Dict[str, Any]) -> Dict[str, Any]:
        """A2A task execution handler invoked by Agent Runtime."""
        if self.orchestrator is None:
            self.set_up()

        task_request = TaskRequest.model_validate(task_request_data)
        task_response = await self.orchestrator.handle_task(task_request)
        return task_response.model_dump()

    def get_agent_card(self) -> Dict[str, Any]:
        """Returns canonical A2A AgentCard manifest for Agent Registry."""
        if self.orchestrator is None:
            self.set_up()
        return self.orchestrator.get_agent_card().model_dump()


def deploy_to_agent_runtime(
    display_name: str = "redwood-retention-orchestrator",
    requirements: Optional[list] = None,
    staging_bucket: Optional[str] = None
):
    """
    Deploys the RetentionAgentEngine to Google Cloud Agent Runtime / Vertex AI.
    """
    import vertexai
    from vertexai.preview import reasoning_engines

    staging_bucket = staging_bucket or f"gs://{config.project_id}-{config.region}-agent-runtime"
    vertexai.init(
        project=config.project_id,
        location=config.region,
        staging_bucket=staging_bucket
    )

    engine = RetentionAgentEngine()
    reqs = requirements or [
        "google-cloud-firestore>=2.14.0",
        "google-cloud-bigquery>=3.14.0",
        "google-genai>=1.0.0",
        "pydantic>=2.0.0",
        "httpx>=0.25.0"
    ]

    remote_agent = reasoning_engines.ReasoningEngine.create(
        reasoning_engine=engine,
        requirements=reqs,
        display_name=display_name,
        description="Redwood Retail Multi-Agent Retention Platform on Agent Runtime",
    )
    logger.info("🎉 Deployed to Google Cloud Agent Runtime: %s", remote_agent.resource_name)
    return remote_agent


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Redwood Retail Retention Agent Engine on Agent Runtime")
    parser.add_argument("--session-id", help="Execute retention evaluation for a customer session and print result")
    parser.add_argument("--deploy", action="store_true", help="Deploy RetentionAgentEngine to Google Cloud Agent Runtime")
    parser.add_argument("--serve", action="store_true", help="Start local A2A test server for development/testing")
    parser.add_argument("--port", type=int, default=8080, help="Port for local A2A test server")
    args = parser.parse_args()

    engine = RetentionAgentEngine()
    engine.set_up()

    if args.deploy:
        deploy_to_agent_runtime()
    elif args.session_id:
        result = engine.query(args.session_id)
        print(json.dumps(result, indent=2))
    elif args.serve:
        server = start_multi_agent_server(engine.orchestrator, engine.domain_agents, port=args.port)
        logger.info("Local Agent Runtime test server running on port %d. Press Ctrl+C to stop.", args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server stopped.")
    else:
        card = engine.get_agent_card()
        print(json.dumps(card, indent=2))


if __name__ == "__main__":
    main()

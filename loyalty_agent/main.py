"""
Entrypoint CLI runner for Redwood Retail Multi-Agent Retention System.
Deployed on Google Cloud Agent Runtime and Cloud Run with standard A2A discovery endpoints.
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
from loyalty_agent.listener import SessionEventListener

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


def build_clients():
    """Initializes Google Cloud clients for Firestore, BigQuery, and Vertex AI."""
    from google.cloud import firestore
    from google.cloud import bigquery
    from google import genai

    logger.info("Connecting to Firestore database '%s' in '%s'...", config.firestore_database, config.region)
    fs_client = firestore.Client(project=config.project_id, database=config.firestore_database)

    logger.info("Connecting to BigQuery dataset '%s'...", config.bigquery_dataset)
    bq_client = bigquery.Client(project=config.project_id, location=config.region)

    logger.info("Initializing Gemini Client with model '%s'...", config.reasoning_model)
    try:
        genai_client = genai.Client()
    except Exception as e:
        logger.warning("Vertex AI Client init warning (will use deterministic fallback): %s", e)
        genai_client = None

    return fs_client, bq_client, genai_client


def run_daemon():
    """Runs the multi-agent loyalty system as a persistent background daemon on Agent Runtime."""
    logger.info("🌲 Redwood Retail Multi-Agent Retention Platform Starting...")
    logger.info("Target GCP Project: %s", config.project_id)
    logger.info("Reasoning Model:    %s", config.reasoning_model)

    port = int(os.getenv("PORT", "8080"))
    base_host = os.getenv("AGENT_RUNTIME_HOST", f"http://localhost:{port}")

    fs_client, bq_client, genai_client = build_clients()

    # 1. Instantiate Domain Agents with scoped URLs
    cooldown_agent = CooldownPolicyAgent(
        firestore_client=fs_client,
        base_url=f"{base_host}/cooldown",
        default_cooldown_days=config.cooldown_days
    )
    friction_agent = CustomerFrictionAgent(
        firestore_client=fs_client,
        base_url=f"{base_host}/friction"
    )
    churn_agent = ChurnIntelligenceAgent(
        bigquery_client=bq_client,
        firestore_client=fs_client,
        base_url=f"{base_host}/churn"
    )
    synthesis_agent = OfferSynthesisAgent(
        gemini_model=genai_client,
        base_url=f"{base_host}/synthesis"
    )
    fulfillment_agent = OfferFulfillmentAgent(
        firestore_client=fs_client,
        base_url=f"{base_host}/fulfillment",
        default_cooldown_days=config.cooldown_days
    )

    domain_agents = {
        "cooldown": cooldown_agent,
        "friction": friction_agent,
        "churn": churn_agent,
        "synthesis": synthesis_agent,
        "fulfillment": fulfillment_agent
    }

    # 2. Instantiate Retention Orchestrator Agent and Register Domain Agents
    orchestrator = RetentionOrchestratorAgent(
        firestore_client=fs_client,
        bigquery_client=bq_client,
        gemini_model=genai_client,
        base_url=base_host,
        cooldown_agent_url=f"{base_host}/cooldown",
        churn_agent_url=f"{base_host}/churn",
        friction_agent_url=f"{base_host}/friction",
        synthesis_agent_url=f"{base_host}/synthesis",
        fulfillment_agent_url=f"{base_host}/fulfillment",
        auto_register_local_domain_agents=False
    )
    for agent in domain_agents.values():
        orchestrator.register_domain_agent(agent)

    # 3. Start A2A Multi-Agent Server
    runtime_server = start_multi_agent_server(orchestrator, domain_agents, port=port)

    # 4. Attach Real-Time Firestore Session Listener
    listener = SessionEventListener(
        firestore_client=fs_client,
        session_processor=orchestrator.process_session
    )

    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received. Exiting gracefully...")
        listener.stop()
        runtime_server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    listener.start()
    logger.info("⚡ Multi-Agent A2A Platform is live on Agent Runtime and listening for customer sessions.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_handler(None, None)


if __name__ == "__main__":
    if "--daemon" in sys.argv or len(sys.argv) == 1:
        run_daemon()

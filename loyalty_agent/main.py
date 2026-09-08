"""
Entrypoint CLI runner for Redwood Retail Multi-Agent Retention System.
Deployed on Google Cloud Agent Runtime as discrete, standalone A2A micro-agents.
Each container runs exactly one autonomous agent based on AGENT_ROLE.
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


class StandaloneAgentEngine:
    """
    Independent Agent Runtime Reasoning Engine serving a single A2A agent role.
    Exposes canonical A2A discovery (get_agent_card), task execution (handle_task),
    and direct external domain query methods (query).
    """

    def __init__(
        self,
        role: Optional[str] = None,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        firestore_database: Optional[str] = None,
        bigquery_dataset: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        cooldown_days: Optional[int] = None,
    ):
        self.role = (role or os.getenv("AGENT_ROLE") or "orchestrator").lower().strip()
        self.project_id = project_id or config.project_id
        self.region = region or config.region
        self.firestore_database = firestore_database or config.firestore_database
        self.bigquery_dataset = bigquery_dataset or config.bigquery_dataset
        self.reasoning_model = reasoning_model or config.reasoning_model
        self.cooldown_days = cooldown_days if cooldown_days is not None else config.cooldown_days

        self.agent: Optional[BaseA2AAgent] = None
        self.fs_client = None
        self.bq_client = None
        self.genai_client = None

        # Expose orchestrator and domain_agents attributes for test backwards compatibility
        self.orchestrator: Optional[RetentionOrchestratorAgent] = None
        self.domain_agents: Dict[str, BaseA2AAgent] = {}

    def set_up(self):
        """Lifecycle hook invoked by Agent Runtime to initialize the standalone agent."""
        if self.agent is not None:
            return
        if self.orchestrator is not None:
            self.agent = self.orchestrator
            return

        from google.cloud import firestore
        from google.cloud import bigquery
        from google import genai
        import vertexai

        logger.info("Initializing Standalone Agent [%s] on Google Cloud Agent Runtime...", self.role.upper())
        try:
            vertexai.init(project=self.project_id, location=self.region)
        except Exception as e:
            logger.warning("Vertex AI init warning: %s", e)

        # Initialize only the specific clients needed by this agent role
        if self.fs_client is None and self.role in ("cooldown", "friction", "churn", "fulfillment", "orchestrator"):
            self.fs_client = firestore.Client(project=self.project_id, database=self.firestore_database)

        if self.bq_client is None and self.role in ("churn", "orchestrator"):
            self.bq_client = bigquery.Client(project=self.project_id, location=self.region)

        if self.genai_client is None and self.role in ("synthesis", "orchestrator"):
            try:
                self.genai_client = genai.Client()
            except Exception as e:
                logger.warning("Vertex AI GenAI Client init warning (will use deterministic fallback): %s", e)
                self.genai_client = None

        if self.role == "cooldown":
            self.agent = CooldownPolicyAgent(
                firestore_client=self.fs_client,
                default_cooldown_days=self.cooldown_days
            )
        elif self.role == "churn":
            self.agent = ChurnIntelligenceAgent(
                bigquery_client=self.bq_client,
                firestore_client=self.fs_client
            )
        elif self.role == "friction":
            self.agent = CustomerFrictionAgent(
                firestore_client=self.fs_client
            )
        elif self.role == "synthesis":
            self.agent = OfferSynthesisAgent(
                gemini_model=self.genai_client
            )
        elif self.role == "fulfillment":
            self.agent = OfferFulfillmentAgent(
                firestore_client=self.fs_client,
                default_cooldown_days=self.cooldown_days
            )
        elif self.role == "orchestrator":
            self.agent = RetentionOrchestratorAgent(
                firestore_client=self.fs_client,
                bigquery_client=self.bq_client,
                gemini_model=self.genai_client,
                auto_register_local_domain_agents=False
            )
            self.orchestrator = self.agent
        else:
            raise ValueError(
                f"Unknown AGENT_ROLE: '{self.role}'. "
                f"Supported roles: cooldown, churn, friction, synthesis, fulfillment, orchestrator"
            )

        logger.info(
            "✅ Standalone Agent [%s] successfully initialized: %s (%s)",
            self.role.upper(),
            self.agent.get_agent_card().name,
            self.agent.get_agent_card().version
        )

    def get_agent_card(self) -> Dict[str, Any]:
        """Returns the canonical A2A AgentCard manifest for this standalone agent."""
        if self.agent is None:
            self.set_up()
        return self.agent.get_agent_card().model_dump()

    async def handle_task(self, *args, **kwargs) -> Dict[str, Any]:
        """A2A task execution handler invoked by Agent Runtime or external callers."""
        if self.agent is None:
            self.set_up()
        data = {}
        if args and isinstance(args[0], dict):
            data.update(args[0])
        elif args and isinstance(args[0], str):
            data["taskId"] = args[0]
        data.update(kwargs)
        if "task_request_data" in data and isinstance(data["task_request_data"], dict):
            data = data["task_request_data"]
        task_request = TaskRequest.model_validate(data)
        task_response = await self.agent.handle_task(task_request)
        return task_response.model_dump()

    def query(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Direct domain query entrypoint callable by external clients or Agent Runtime.
        Provides specialized domain execution per agent role.
        """
        if self.agent is None:
            self.set_up()

        input_data = dict(kwargs)
        if args and isinstance(args[0], dict):
            input_data.update(args[0])
        elif args and isinstance(args[0], str):
            if self.role == "orchestrator":
                input_data.setdefault("session_id", args[0])
            else:
                input_data.setdefault("customer_id", args[0])

        if self.role == "cooldown":
            cid = input_data.get("customer_id") or input_data.get("customerId", "")
            cdays = input_data.get("cooldown_days") or input_data.get("cooldownDays")
            return self.agent.check_eligibility(str(cid), cooldown_days=cdays)

        elif self.role == "churn":
            cid = input_data.get("customer_id") or input_data.get("customerId", "")
            hist = input_data.get("historical_data") or input_data.get("historicalData")
            return self.agent.evaluate_churn(str(cid), historical_data=hist)

        elif self.role == "friction":
            cid = input_data.get("customer_id") or input_data.get("customerId", "")
            dev = input_data.get("device_info") or input_data.get("deviceInfo")
            return self.agent.analyze_friction(str(cid), device_info=dev)

        elif self.role == "synthesis":
            cid = input_data.get("customer_id") or input_data.get("customerId", "")
            tier = input_data.get("churn_tier") or input_data.get("churnTier", "HIGH")
            fric = input_data.get("primary_friction") or input_data.get("primaryFriction", "EXPERIENCE")
            hist = input_data.get("historical_data") or input_data.get("historicalData", {})
            return self.agent.synthesize_offer(str(cid), churn_tier=tier, primary_friction=fric, historical_data=hist)

        elif self.role == "fulfillment":
            cid = input_data.get("customer_id") or input_data.get("customerId", "")
            offer = input_data.get("offer", {})
            sid = input_data.get("session_id") or input_data.get("sessionId")
            return self.agent.fulfill_voucher(str(cid), offer_dict=offer, session_id=sid)

        elif self.role == "orchestrator":
            sid = input_data.get("session_id") or input_data.get("sessionId", "")
            cid = input_data.get("customer_id") or input_data.get("customerId", "")
            if not sid and cid:
                import time
                from datetime import datetime, timezone
                sid = f"sess_auto_{cid}_{int(time.time())}"
                if self.fs_client:
                    self.fs_client.collection("customer_sessions").document(sid).set({
                        "sessionId": sid,
                        "customerId": cid,
                        "loginTimestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "PENDING",
                        "agentProcessingStatus": "PENDING"
                    })
            offer = self.agent.process_session(str(sid))
            return {
                "sessionId": str(sid),
                "action": "OFFER_ISSUED" if offer else "NO_OFFER_ISSUED",
                "offer": offer
            }
        else:
            return {"error": f"Unhandled role: {self.role}"}


# Backward compatibility alias
RetentionAgentEngine = StandaloneAgentEngine


class StandaloneAgentRuntimeHandler(BaseHTTPRequestHandler):
    """
    HTTP Request Handler serving a single standalone A2A agent on Agent Runtime.
    Exposes canonical discovery (/.well-known/agent-card.json), task execution (/a2a/v1/tasks),
    and Agent Runtime BYOC dispatch endpoints (/api/reasoning_engine).
    """

    engine: Optional[StandaloneAgentEngine] = None
    agent: Optional[BaseA2AAgent] = None
    agent_registry: Dict[str, BaseA2AAgent] = {}

    @classmethod
    def set_engine(cls, engine: StandaloneAgentEngine):
        cls.engine = engine
        cls.agent = engine.agent
        if engine.agent:
            cls.agent_registry = {engine.role: engine.agent}

    @classmethod
    def set_agents(
        cls,
        orchestrator: RetentionOrchestratorAgent,
        domain_agents: Dict[str, BaseA2AAgent],
        engine: Optional[Any] = None
    ):
        """Helper for test mock servers that configure multiple agents."""
        cls.engine = engine
        cls.agent = orchestrator
        cls.agent_registry = dict(domain_agents)
        cls.agent_registry["orchestrator"] = orchestrator

    def _send_json(self, status_code: int, data: Any):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url_path = self.path.split("?")[0]

        # 1. Root & Health probes
        if url_path == "/":
            role_name = self.engine.role if self.engine else "standalone"
            self._send_json(200, {"status": "ok", "role": role_name})
            return

        if url_path in ("/healthz", "/health"):
            role_name = self.engine.role if self.engine else "standalone"
            agent_name = self.engine.get_agent_card()["name"] if self.engine else "Unknown"
            health_data = {
                "status": "HEALTHY",
                "platform": "Google Cloud Agent Runtime",
                "protocol": "A2A (Agent2Agent) v1.0",
                "role": role_name,
                "agent": agent_name
            }
            if self.agent_registry:
                health_data["agents"] = {
                    prefix: {
                        "name": ag.get_agent_card().name,
                        "version": ag.get_agent_card().version,
                        "skills": [s.id for s in ag.get_agent_card().skills]
                    }
                    for prefix, ag in self.agent_registry.items()
                }
            self._send_json(200, health_data)
            return

        # 2. Canonical Agent Discovery (/.well-known/agent-card.json)
        if url_path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            if self.engine:
                self._send_json(200, self.engine.get_agent_card())
            elif self.agent:
                self._send_json(200, self.agent.get_agent_card().model_dump())
            else:
                self._send_json(503, {"error": "Agent not initialized"})
            return

        # Scoped agent card discovery fallback: /{prefix}/.well-known/agent-card.json
        for prefix, ag in self.agent_registry.items():
            if url_path in (f"/{prefix}/.well-known/agent-card.json", f"/{prefix}/.well-known/agent.json"):
                self._send_json(200, ag.get_agent_card().model_dump())
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

        # 0. Native Vertex AI Agent Engine BYOC Contract: /api/reasoning_engine
        if url_path in ("/api/reasoning_engine", "/reasoning_engine"):
            class_method = body_json.get("class_method") or "query"
            payload = body_json.get("input")
            if payload is None:
                payload = {k: v for k, v in body_json.items() if k != "class_method"}

            target_obj = self.engine or self.agent
            logger.info("⚡ [Agent Runtime] Invoking class_method='%s', payload=%s", class_method, payload)

            try:
                if class_method == "set_up":
                    if hasattr(target_obj, "set_up"):
                        target_obj.set_up()
                    self._send_json(200, {"output": "ok"})
                elif class_method == "get_agent_card":
                    card = target_obj.get_agent_card()
                    card_dict = card.model_dump() if hasattr(card, "model_dump") else card
                    self._send_json(200, {"output": card_dict})
                elif class_method == "query":
                    if isinstance(payload, dict):
                        result = target_obj.query(**payload)
                    elif payload is not None:
                        result = target_obj.query(payload)
                    else:
                        result = target_obj.query()
                    self._send_json(200, {"output": result})
                elif class_method == "handle_task":
                    task_dict = payload.get("task_request_data") if (isinstance(payload, dict) and "task_request_data" in payload) else payload
                    if not isinstance(task_dict, dict):
                        task_dict = {"parameters": task_dict}
                    task_req = TaskRequest.model_validate(task_dict)
                    import inspect
                    if hasattr(target_obj, "handle_task"):
                        res = target_obj.handle_task(task_req.model_dump())
                        if inspect.iscoroutine(res):
                            task_res = asyncio.run(res)
                        else:
                            task_res = res
                    else:
                        task_res = asyncio.run(target_obj.handle_task(task_req))
                    out = task_res.model_dump() if hasattr(task_res, "model_dump") else task_res
                    self._send_json(200, {"output": out})
                elif hasattr(target_obj, class_method):
                    method = getattr(target_obj, class_method)
                    if isinstance(payload, dict):
                        res = method(**payload)
                    elif payload is not None:
                        res = method(payload)
                    else:
                        res = method()
                    self._send_json(200, {"output": res})
                else:
                    self._send_json(400, {"error": f"Unknown class_method: {class_method}"})
            except Exception as exc:
                logger.exception("Error executing Agent Runtime class_method %s: %s", class_method, exc)
                self._send_json(500, {"error": str(exc), "output": None})
            return

        # 0.1 Streaming Reasoning Engine Contract
        if url_path in ("/api/stream_reasoning_engine", "/stream_reasoning_engine"):
            payload = body_json.get("input")
            if payload is None:
                payload = {k: v for k, v in body_json.items() if k != "class_method"}
            target_obj = self.engine or self.agent
            if isinstance(payload, dict):
                result = target_obj.query(**payload)
            elif payload is not None:
                result = target_obj.query(payload)
            else:
                result = target_obj.query()
            chunk = (json.dumps({"output": result}, default=str) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return

        # 1. Session Trigger Endpoint (for Orchestrator test invocations)
        if url_path == "/trigger_session":
            target_orch = self.engine.agent if self.engine and isinstance(self.engine.agent, RetentionOrchestratorAgent) else self.agent
            if isinstance(target_orch, RetentionOrchestratorAgent):
                session_id = body_json.get("sessionId")
                if not session_id:
                    self._send_json(400, {"error": "Missing sessionId in request body"})
                    return
                offer = target_orch.process_session(session_id)
                self._send_json(200, {
                    "sessionId": session_id,
                    "action": "OFFER_ISSUED" if offer else "NO_OFFER_ISSUED",
                    "offer": offer
                })
                return

        # 2. Standard A2A Task Execution Endpoints (/a2a/v1/tasks)
        target_agent = None
        if url_path in ("/a2a/v1/tasks", "/orchestrator/a2a/v1/tasks"):
            target_agent = self.engine.agent if self.engine else self.agent
        else:
            for prefix, ag in self.agent_registry.items():
                if url_path == f"/{prefix}/a2a/v1/tasks":
                    target_agent = ag
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

        self._send_json(404, {"error": f"Endpoint {url_path} not found"})

    def log_message(self, format, *args):
        if "health" not in args[0]:
            logger.info("%s - %s", self.address_string(), format % args)


# Backward compatibility alias
MultiAgentA2ARuntimeHandler = StandaloneAgentRuntimeHandler


def start_standalone_agent_server(
    engine: StandaloneAgentEngine,
    port: int = 8080,
    run_in_thread: bool = True
) -> ThreadingHTTPServer:
    """Starts ThreadingHTTPServer hosting a single standalone A2A agent."""
    StandaloneAgentRuntimeHandler.set_engine(engine)
    server = ThreadingHTTPServer(("0.0.0.0", port), StandaloneAgentRuntimeHandler)
    if run_in_thread:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    logger.info("📡 Standalone A2A Agent [%s] listening on 0.0.0.0:%d", engine.role.upper(), port)
    logger.info("   Discovery:   http://0.0.0.0:%d/.well-known/agent-card.json", port)
    logger.info("   Tasks:       http://0.0.0.0:%d/a2a/v1/tasks", port)
    logger.info("   Reasoning:   http://0.0.0.0:%d/api/reasoning_engine", port)
    return server


def start_multi_agent_server(
    orchestrator: RetentionOrchestratorAgent,
    domain_agents: Dict[str, BaseA2AAgent],
    engine: Optional[Any] = None,
    port: int = 8080,
    run_in_thread: bool = True
) -> ThreadingHTTPServer:
    """Helper for test mock servers hosting multi-agent routing."""
    StandaloneAgentRuntimeHandler.set_agents(orchestrator, domain_agents, engine=engine)
    server = ThreadingHTTPServer(("0.0.0.0", port), StandaloneAgentRuntimeHandler)
    if run_in_thread:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    return server


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Redwood Retail Standalone A2A Agent on Agent Runtime")
    parser.add_argument(
        "--role",
        default=os.getenv("AGENT_ROLE", "orchestrator"),
        choices=["orchestrator", "cooldown", "churn", "friction", "synthesis", "fulfillment"],
        help="Agent role to run as an independent micro-agent"
    )
    parser.add_argument("--session-id", help="Execute retention evaluation for a customer session (orchestrator role)")
    parser.add_argument("--customer-id", help="Execute domain query for a customer ID")
    parser.add_argument("--serve", action="store_true", help="Start standalone A2A server for Agent Runtime")
    parser.add_argument("--port", type=int, default=8080, help="Port for standalone server")
    args = parser.parse_args()

    engine = StandaloneAgentEngine(role=args.role)
    engine.set_up()

    if args.session_id:
        result = engine.query(session_id=args.session_id)
        print(json.dumps(result, indent=2, default=str))
    elif args.customer_id:
        result = engine.query(customer_id=args.customer_id)
        print(json.dumps(result, indent=2, default=str))
    elif args.serve:
        server = start_standalone_agent_server(
            engine=engine,
            port=args.port,
            run_in_thread=False
        )
        logger.info("Standalone Agent [%s] server running on port %d. Press Ctrl+C to stop.", args.role.upper(), args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server stopped.")
    else:
        card = engine.get_agent_card()
        print(json.dumps(card, indent=2, default=str))


if __name__ == "__main__":
    main()

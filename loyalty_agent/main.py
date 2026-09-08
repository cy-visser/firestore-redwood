"""
Standalone Agent Runtime Reasoning Engine for Redwood Retail Multi-Agent Retention System.
Deployed directly on Google Cloud Agent Runtime (Vertex AI Reasoning Engine) via native source packaging.
Each instance runs an autonomous, specialized agent according to its role (cooldown, churn, friction, synthesis, fulfillment, orchestrator).
"""

import sys
import os
import json
import asyncio
import logging
from typing import Dict, Any, Optional

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


class TaskResultDict(dict):
    """Dictionary that can also be awaited in async contexts."""
    def __await__(self):
        async def _coro():
            return self
        return _coro().__await__()


class StandaloneAgentEngine:
    """
    Independent Agent Runtime Reasoning Engine serving a single A2A agent role.
    Exposes canonical A2A discovery (get_agent_card), task execution (handle_task),
    and direct external domain query methods (query).
    """

    def register_operations(self) -> Dict[str, Any]:
        """Registers methods for Vertex AI Reasoning Engine execution and schema generation."""
        return {
            "": ["query", "get_agent_card", "handle_task"]
        }

    def __init__(
        self,
        role: Optional[str] = None,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        firestore_database: Optional[str] = None,
        bigquery_dataset: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        cooldown_days: Optional[int] = None,
        cooldown_agent_url: Optional[str] = None,
        churn_agent_url: Optional[str] = None,
        friction_agent_url: Optional[str] = None,
        synthesis_agent_url: Optional[str] = None,
        fulfillment_agent_url: Optional[str] = None,
    ):
        self.role = (role or os.getenv("AGENT_ROLE") or "orchestrator").lower().strip()
        self.project_id = project_id or config.project_id
        self.region = region or config.region
        self.firestore_database = firestore_database or config.firestore_database
        self.bigquery_dataset = bigquery_dataset or config.bigquery_dataset
        self.reasoning_model = reasoning_model or config.reasoning_model
        self.cooldown_days = cooldown_days if cooldown_days is not None else config.cooldown_days

        self.cooldown_agent_url = cooldown_agent_url
        self.churn_agent_url = churn_agent_url
        self.friction_agent_url = friction_agent_url
        self.synthesis_agent_url = synthesis_agent_url
        self.fulfillment_agent_url = fulfillment_agent_url

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
                cooldown_agent_url=self.cooldown_agent_url,
                churn_agent_url=self.churn_agent_url,
                friction_agent_url=self.friction_agent_url,
                synthesis_agent_url=self.synthesis_agent_url,
                fulfillment_agent_url=self.fulfillment_agent_url,
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

    def handle_task(self, *args, **kwargs) -> Dict[str, Any]:
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

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                task_response = pool.submit(asyncio.run, self.agent.handle_task(task_request)).result()
        else:
            task_response = asyncio.run(self.agent.handle_task(task_request))

        return TaskResultDict(task_response.model_dump())

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
    args = parser.parse_args()

    engine = StandaloneAgentEngine(role=args.role)
    engine.set_up()

    if args.session_id:
        result = engine.query(session_id=args.session_id)
        print(json.dumps(result, indent=2, default=str))
    elif args.customer_id:
        result = engine.query(customer_id=args.customer_id)
        print(json.dumps(result, indent=2, default=str))
    else:
        card = engine.get_agent_card()
        print(json.dumps(card, indent=2, default=str))


if __name__ == "__main__":
    main()

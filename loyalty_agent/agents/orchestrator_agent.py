"""
Retention Orchestrator Agent.
Coordinates multi-agent retention evaluation, concurrent telemetry discovery,
risk gating, offer synthesis, and transactional fulfillment using the A2A protocol.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from loyalty_agent.a2a.card import AgentCard, AgentSkill
from loyalty_agent.a2a.client import A2AClient
from loyalty_agent.a2a.discovery import A2ADiscoveryClient
from loyalty_agent.agents.base_a2a_agent import BaseA2AAgent
from loyalty_agent.agents.cooldown_agent import CooldownPolicyAgent
from loyalty_agent.agents.friction_agent import CustomerFrictionAgent
from loyalty_agent.agents.churn_agent import ChurnIntelligenceAgent, evaluate_churn_tier
from loyalty_agent.agents.synthesis_agent import OfferSynthesisAgent
from loyalty_agent.agents.fulfillment_agent import OfferFulfillmentAgent
from loyalty_agent.config import config

logger = logging.getLogger("a2a.orchestrator_agent")


class RetentionOrchestratorAgent(BaseA2AAgent):
    """
    Retention Orchestrator Agent for Google Cloud Agent Runtime.
    Discovers domain agents via canonical A2A manifests (/.well-known/agent-card.json)
    and executes parallel retention evaluation workflows via standard A2A tasks.
    """

    def __init__(
        self,
        firestore_client: Any,
        bigquery_client: Any = None,
        gemini_model: Any = None,
        discovery_client: Optional[A2ADiscoveryClient] = None,
        a2a_client: Optional[A2AClient] = None,
        base_url: str = "http://localhost:8081/orchestrator",
        cooldown_agent_url: str = "http://localhost:8081/cooldown",
        churn_agent_url: str = "http://localhost:8081/churn",
        friction_agent_url: str = "http://localhost:8081/friction",
        synthesis_agent_url: str = "http://localhost:8081/synthesis",
        fulfillment_agent_url: str = "http://localhost:8081/fulfillment",
        cooldown_days: int = config.cooldown_days,
        churn_threshold: float = config.churn_trigger_threshold,
        acute_friction_boost: float = config.acute_friction_boost,
        auto_register_local_domain_agents: bool = True
    ):
        self.fs = firestore_client
        self.bq = bigquery_client
        self.gemini = gemini_model

        self.discovery = discovery_client or A2ADiscoveryClient()
        self.a2a_client = a2a_client or A2AClient(discovery_client=self.discovery)

        self.cooldown_agent_url = cooldown_agent_url
        self.churn_agent_url = churn_agent_url
        self.friction_agent_url = friction_agent_url
        self.synthesis_agent_url = synthesis_agent_url
        self.fulfillment_agent_url = fulfillment_agent_url

        self.cooldown_days = cooldown_days
        self.churn_threshold = churn_threshold
        self.acute_friction_boost = acute_friction_boost

        card = AgentCard(
            name="Retention Orchestrator Agent",
            description="Coordinates multi-agent retention workflows via dynamic A2A discovery and parallel task delegation on Agent Runtime.",
            version="1.0.0",
            url=base_url,
            skills=[
                AgentSkill(
                    id="orchestrate_retention_flow",
                    name="Orchestrate Customer Retention Flow",
                    description="Executes end-to-end A2A evaluation: checks cooldown, queries churn and friction in parallel, synthesizes voucher copy, and fulfills offer.",
                    tags=["Orchestration", "A2A", "Retention", "Multi-Agent"],
                    examples=["Orchestrate retention evaluation for session sess_9812"],
                    input_schema={
                        "type": "object",
                        "properties": {
                            "sessionId": {"type": "string"},
                            "customerId": {"type": "string"}
                        },
                        "required": ["sessionId"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "sessionId": {"type": "string"},
                            "decision": {"type": "string"},
                            "offer": {"type": "object"}
                        }
                    }
                )
            ]
        )
        super().__init__(agent_card=card)
        self.register_skill_handler("orchestrate_retention_flow", self._handle_orchestrate_flow)

        if auto_register_local_domain_agents:
            self._register_default_domain_agents()

    def _register_default_domain_agents(self) -> None:
        """Initializes and registers standard local domain agents for hermetic execution."""
        cooldown_agent = CooldownPolicyAgent(
            firestore_client=self.fs,
            base_url=self.cooldown_agent_url,
            default_cooldown_days=self.cooldown_days
        )
        friction_agent = CustomerFrictionAgent(
            firestore_client=self.fs,
            base_url=self.friction_agent_url
        )
        churn_agent = ChurnIntelligenceAgent(
            bigquery_client=self.bq,
            firestore_client=self.fs,
            base_url=self.churn_agent_url
        )
        synthesis_agent = OfferSynthesisAgent(
            gemini_model=self.gemini,
            base_url=self.synthesis_agent_url
        )
        fulfillment_agent = OfferFulfillmentAgent(
            firestore_client=self.fs,
            base_url=self.fulfillment_agent_url,
            default_cooldown_days=self.cooldown_days
        )

        for agent in [cooldown_agent, friction_agent, churn_agent, synthesis_agent, fulfillment_agent]:
            self.register_domain_agent(agent)

    def register_domain_agent(self, agent: BaseA2AAgent) -> None:
        """
        Registers an in-process domain agent with both discovery and execution clients.
        Automatically maps well-known skills to agent endpoint URLs.
        """
        card = agent.get_agent_card()
        url = card.url
        self.discovery.register_local_card(url, card)
        self.a2a_client.register_local_handler(url, agent.handle_task)

        # Update skill routing URLs
        if card.get_skill("check_cooldown_eligibility"):
            self.cooldown_agent_url = url
        if card.get_skill("evaluate_churn_propensity"):
            self.churn_agent_url = url
        if card.get_skill("analyze_friction_and_profile"):
            self.friction_agent_url = url
        if card.get_skill("synthesize_retention_offer"):
            self.synthesis_agent_url = url
        if card.get_skill("fulfill_loyalty_voucher"):
            self.fulfillment_agent_url = url

        logger.info("Registered domain agent '%s' (%s) at %s", card.name, card.version, url)

    async def _handle_orchestrate_flow(self, parameters: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        active_session_id = parameters.get("sessionId") or session_id
        offer = await self.process_session_async(active_session_id)
        return {
            "sessionId": active_session_id,
            "decision": "OFFER_ISSUED" if offer else "NO_OFFER_ISSUED",
            "offer": offer
        }

    async def process_session_async(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Asynchronously executes the end-to-end multi-agent retention flow via A2A protocol.
        """
        if not self.fs:
            logger.error("Firestore client is not configured on RetentionOrchestratorAgent.")
            return None

        sess_ref = self.fs.collection("customer_sessions").document(session_id)
        sess_snap = sess_ref.get()
        if not sess_snap.exists:
            logger.warning("Session %s not found in Firestore.", session_id)
            return None

        sess_data = sess_snap.to_dict() or {}
        customer_id = sess_data.get("customerId")
        if not customer_id:
            logger.warning("Session %s has no customerId.", session_id)
            return None

        now = datetime.now(timezone.utc)

        # -------------------------------------------------------------
        # Step 1: Cooldown Policy Evaluation via A2A
        # -------------------------------------------------------------
        cooldown_url = self.discovery.find_agent_for_skill("check_cooldown_eligibility") or self.cooldown_agent_url
        try:
            cooldown_resp = await self.a2a_client.execute_task(
                agent_url=cooldown_url,
                skill_id="check_cooldown_eligibility",
                session_id=session_id,
                parameters={"customerId": customer_id, "cooldownDays": self.cooldown_days}
            )
            cooldown_out = cooldown_resp.output
        except Exception as exc:
            logger.exception("Cooldown check failed via A2A: %s", exc)
            cooldown_out = {"isEligible": True, "hasActiveOffer": False, "inCooldown": False}

        if cooldown_out.get("hasActiveOffer"):
            active_offer = cooldown_out["activeOffer"]
            sess_ref.update({
                "agentProcessingStatus": "PROCESSED",
                "status": "PROCESSED",
                "offerId": active_offer.get("offerId"),
                "activeOfferId": active_offer.get("offerId"),
                "skipReason": "ACTIVE_OFFER_ALREADY_EXISTS",
                "processedAt": now.isoformat()
            })
            logger.info("Session %s: Active offer already exists (%s).", session_id, active_offer.get("offerId"))
            return active_offer

        if cooldown_out.get("inCooldown"):
            sess_ref.update({
                "agentProcessingStatus": "SKIPPED",
                "status": "PROCESSED",
                "offerId": None,
                "activeOfferId": None,
                "skipReason": "COOLDOWN_ACTIVE",
                "processedAt": now.isoformat()
            })
            logger.info("Session %s: Customer %s is in active cooldown window.", session_id, customer_id)
            return None

        # -------------------------------------------------------------
        # Step 2: Parallel Telemetry Fan-Out (Churn + Friction) via asyncio.gather()
        # -------------------------------------------------------------
        churn_url = self.discovery.find_agent_for_skill("evaluate_churn_propensity") or self.churn_agent_url
        friction_url = self.discovery.find_agent_for_skill("analyze_friction_and_profile") or self.friction_agent_url

        churn_task = self.a2a_client.execute_task(
            agent_url=churn_url,
            skill_id="evaluate_churn_propensity",
            session_id=session_id,
            parameters={"customerId": customer_id, "customerData": sess_data.get("customerData", {})}
        )
        friction_task = self.a2a_client.execute_task(
            agent_url=friction_url,
            skill_id="analyze_friction_and_profile",
            session_id=session_id,
            parameters={"customerId": customer_id}
        )

        churn_resp, friction_resp = await asyncio.gather(churn_task, friction_task)
        churn_out = churn_resp.output
        friction_out = friction_resp.output

        # -------------------------------------------------------------
        # Step 3: Event-Augmented Hybrid Risk Synthesis
        # -------------------------------------------------------------
        baseline_prob = float(churn_out.get("churnProbability", 0.0))
        baseline_tier = churn_out.get("churnTier", "LOW")
        churn_eval_source = churn_out.get("evaluationSource", "A2A_CHURN")

        complaint = friction_out.get("primaryComplaintReason")
        discount_cap = friction_out.get("discountCapPercent", 15)
        has_acute_friction = friction_out.get("hasAcuteFriction", False)

        if has_acute_friction:
            churn_prob = round(min(1.0, baseline_prob + self.acute_friction_boost), 4)
            churn_tier = evaluate_churn_tier(churn_prob)
            eval_source = "EVENT_AUGMENTED_HYBRID"
        else:
            churn_prob = baseline_prob
            churn_tier = baseline_tier
            eval_source = churn_eval_source

        # -------------------------------------------------------------
        # Step 4: Actionability Gate (< churn_threshold => SKIPPED)
        # -------------------------------------------------------------
        if churn_prob < self.churn_threshold:
            sess_ref.update({
                "agentProcessingStatus": "SKIPPED",
                "status": "PROCESSED",
                "offerId": None,
                "activeOfferId": None,
                "skipReason": "LOW_CHURN_RISK",
                "processedAt": now.isoformat()
            })
            logger.info("Session %s: Churn probability %.2f below threshold %.2f. Skipped.", session_id, churn_prob, self.churn_threshold)
            return None

        # -------------------------------------------------------------
        # Step 5: Offer Copy Synthesis via A2A
        # -------------------------------------------------------------
        synthesis_url = self.discovery.find_agent_for_skill("synthesize_retention_offer") or self.synthesis_agent_url
        synth_resp = await self.a2a_client.execute_task(
            agent_url=synthesis_url,
            skill_id="synthesize_retention_offer",
            session_id=session_id,
            parameters={
                "customerId": customer_id,
                "churnProbability": churn_prob,
                "churnTier": churn_tier,
                "primaryComplaint": complaint,
                "discountCapPercent": discount_cap,
                "evaluationSource": eval_source
            }
        )
        offer_payload = synth_resp.output
        offer_payload["churnProbability"] = churn_prob
        offer_payload["churnRiskTier"] = churn_tier
        offer_payload["baselineChurnRisk"] = baseline_prob
        offer_payload["evaluationSource"] = eval_source
        offer_payload["historicalSpend90d"] = friction_out.get("totalSpend90d", 500.0)
        offer_payload["sentimentScore"] = friction_out.get("sentimentScore", 0.5)

        # -------------------------------------------------------------
        # Step 6: Transactional Voucher Fulfillment via A2A
        # -------------------------------------------------------------
        fulfillment_url = self.discovery.find_agent_for_skill("fulfill_loyalty_voucher") or self.fulfillment_agent_url
        fulfill_resp = await self.a2a_client.execute_task(
            agent_url=fulfillment_url,
            skill_id="fulfill_loyalty_voucher",
            session_id=session_id,
            parameters={
                "customerId": customer_id,
                "sessionId": session_id,
                "offerPayload": offer_payload,
                "cooldownDays": self.cooldown_days
            }
        )
        persisted_offer = fulfill_resp.output.get("offer")
        logger.info("Session %s: Offer successfully fulfilled and persisted (%s).", session_id, persisted_offer.get("offerId"))
        return persisted_offer

    def process_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Synchronous wrapper to safely invoke process_session_async across thread pools or direct callers.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.process_session_async(session_id))

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, self.process_session_async(session_id))
            return future.result()

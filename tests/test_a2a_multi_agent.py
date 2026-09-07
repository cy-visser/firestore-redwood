"""
Test Suite for Redwood Retail A2A (Agent2Agent) Multi-Agent Architecture.
Validates standard discovery endpoints (/.well-known/agent-card.json),
A2A task exchanges, domain agent skill executions, and multi-agent orchestration.
"""

import time
import json
import asyncio
from datetime import datetime, timezone, timedelta
import pytest
import httpx

from loyalty_agent.a2a.card import AgentCard, AgentSkill, AgentCapabilities
from loyalty_agent.a2a.task import TaskRequest, TaskResponse, TaskState
from loyalty_agent.a2a.discovery import A2ADiscoveryClient, AgentDiscoveryError
from loyalty_agent.a2a.client import A2AClient, A2AExecutionError
from loyalty_agent.agents import (
    BaseA2AAgent,
    RetentionOrchestratorAgent,
    CooldownPolicyAgent,
    CustomerFrictionAgent,
    ChurnIntelligenceAgent,
    OfferSynthesisAgent,
    OfferFulfillmentAgent,
    evaluate_churn_tier,
    evaluate_5pillar_heuristic
)
from loyalty_agent.main import start_multi_agent_server


# ------------------------------------------------------------------------------
# 1. A2A Protocol Core & Discovery Tests
# ------------------------------------------------------------------------------

def test_agent_card_schema():
    """Verifies standard AgentCard serialization and skill querying."""
    skill = AgentSkill(
        id="test_skill",
        name="Test Skill",
        description="A test skill",
        input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"result": {"type": "string"}}}
    )
    card = AgentCard(
        name="Test Agent",
        description="Testing AgentCard manifest",
        version="1.0.0",
        url="http://localhost:8081/test",
        skills=[skill]
    )

    assert card.get_skill("test_skill") is not None
    assert card.get_skill("non_existent") is None

    dumped = card.model_dump()
    assert dumped["name"] == "Test Agent"
    assert dumped["skills"][0]["id"] == "test_skill"

    reloaded = AgentCard.model_validate(dumped)
    assert reloaded.url == "http://localhost:8081/test"


@pytest.mark.anyio
async def test_a2a_discovery_client_local_registry():
    """Verifies A2ADiscoveryClient can index and find agents by skill."""
    discovery = A2ADiscoveryClient()
    card = AgentCard(
        name="Churn Agent",
        description="Predicts churn",
        version="1.0.0",
        url="http://localhost:8081/churn",
        skills=[AgentSkill(id="evaluate_churn_propensity", name="Eval Churn", description="")]
    )

    discovery.register_local_card(card.url, card)

    resolved = await discovery.discover("http://localhost:8081/churn")
    assert resolved.name == "Churn Agent"
    assert discovery.find_agent_for_skill("evaluate_churn_propensity") == "http://localhost:8081/churn"
    assert discovery.find_agent_for_skill("unknown_skill") is None


@pytest.mark.anyio
async def test_a2a_client_in_process_execution():
    """Verifies in-process A2A task invocation and response parsing."""
    a2a_client = A2AClient()

    async def mock_handler(req: TaskRequest) -> TaskResponse:
        return TaskResponse(
            task_id=req.task_id,
            skill_id=req.skill_id,
            status=TaskState.COMPLETED,
            output={"echo": req.parameters.get("msg")}
        )

    a2a_client.register_local_handler("http://test-agent", mock_handler)

    resp = await a2a_client.execute_task(
        agent_url="http://test-agent",
        skill_id="echo_skill",
        session_id="sess_123",
        parameters={"msg": "hello A2A"}
    )
    assert resp.status == TaskState.COMPLETED
    assert resp.output["echo"] == "hello A2A"
    assert "latency_ms" in resp.execution_metadata


# ------------------------------------------------------------------------------
# 2. Autonomous Domain Agent Tests
# ------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cooldown_agent_eligibility(mock_firestore):
    """Tests CooldownPolicyAgent for eligible and cooldown states."""
    agent = CooldownPolicyAgent(firestore_client=mock_firestore)
    customer_id = "cust_cool_01"

    # Initially eligible
    req1 = TaskRequest(
        skill_id="check_cooldown_eligibility",
        session_id="sess_01",
        parameters={"customerId": customer_id}
    )
    resp1 = await agent.handle_task(req1)
    assert resp1.status == TaskState.COMPLETED
    assert resp1.output["isEligible"] is True
    assert resp1.output["hasActiveOffer"] is False

    # Seed an active offer
    now = datetime.now(timezone.utc)
    mock_firestore.collection("loyalty_offers").document("off_01").set({
        "offerId": "off_01",
        "customerId": customer_id,
        "status": "ACTIVE",
        "cooldownUntil": (now + timedelta(days=7)).isoformat()
    })

    resp2 = await agent.handle_task(req1)
    assert resp2.status == TaskState.COMPLETED
    assert resp2.output["isEligible"] is False
    assert resp2.output["hasActiveOffer"] is True
    assert resp2.output["activeOffer"]["offerId"] == "off_01"


@pytest.mark.anyio
async def test_friction_agent_acute_detection(mock_firestore):
    """Tests CustomerFrictionAgent detection of acute complaints."""
    agent = CustomerFrictionAgent(firestore_client=mock_firestore)
    customer_id = "cust_fric_01"

    mock_firestore.collection("customers").document(customer_id).set({
        "customerId": customer_id,
        "loyaltyTier": "ENTERPRISE_VIP",
        "primaryComplaintReason": "REFUND_REQUESTED",
        "totalSpend90d": 3500.0,
        "sentimentScore": -0.6
    })

    req = TaskRequest(
        skill_id="analyze_friction_and_profile",
        session_id="sess_01",
        parameters={"customerId": customer_id}
    )
    resp = await agent.handle_task(req)
    assert resp.status == TaskState.COMPLETED
    out = resp.output
    assert out["customerSegment"] == "ENTERPRISE_VIP"
    assert out["discountCapPercent"] == 25
    assert out["hasAcuteFriction"] is True
    assert out["totalSpend90d"] == 3500.0


@pytest.mark.anyio
async def test_churn_agent_cache_and_bqml(mock_firestore, mock_bigquery):
    """Tests ChurnIntelligenceAgent fast-path cache and BigQuery fallback."""
    agent = ChurnIntelligenceAgent(bigquery_client=mock_bigquery, firestore_client=mock_firestore)

    # 1. Fast-path cache
    mock_firestore.collection("customers").document("cust_cache").set({
        "customerId": "cust_cache",
        "baselineChurnRisk": 0.81
    })
    resp1 = await agent.handle_task(TaskRequest(
        skill_id="evaluate_churn_propensity",
        session_id="s1",
        parameters={"customerId": "cust_cache"}
    ))
    assert resp1.output["churnProbability"] == 0.81
    assert resp1.output["evaluationSource"] == "FIRESTORE_CACHE"
    assert resp1.output["churnTier"] == "CRITICAL"

    # 2. BigQuery Batch
    mock_bigquery.set_predictions("cust_bq", 0.65)
    resp2 = await agent.handle_task(TaskRequest(
        skill_id="evaluate_churn_propensity",
        session_id="s2",
        parameters={"customerId": "cust_bq"}
    ))
    assert resp2.output["churnProbability"] == 0.65
    assert resp2.output["evaluationSource"] == "BIGQUERY_BATCH"
    assert resp2.output["churnTier"] == "HIGH"


@pytest.mark.anyio
async def test_synthesis_agent_deterministic_fallback():
    """Tests OfferSynthesisAgent generating structured deterministic fallback copy."""
    agent = OfferSynthesisAgent(gemini_model=None)
    req = TaskRequest(
        skill_id="synthesize_retention_offer",
        session_id="s1",
        parameters={
            "customerId": "cust_fallback_9999",
            "churnProbability": 0.85,
            "churnTier": "CRITICAL",
            "primaryComplaint": "LATE_DELIVERY",
            "discountCapPercent": 25
        }
    )
    resp = await agent.handle_task(req)
    assert resp.status == TaskState.COMPLETED
    out = resp.output
    assert out["generationSource"] == "DETERMINISTIC_RULES"
    assert out["discountPercent"] == 25
    assert out["freeExpressShipping"] is True
    assert "RETENTION-DET-CRITICAL" in out["promoCode"]


@pytest.mark.anyio
async def test_fulfillment_agent_transactional_write(mock_firestore):
    """Tests OfferFulfillmentAgent writing offer and updating session state."""
    agent = OfferFulfillmentAgent(firestore_client=mock_firestore)
    session_id = "sess_fulfill_01"
    customer_id = "cust_fulfill_01"

    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "agentProcessingStatus": "PENDING"
    })

    req = TaskRequest(
        skill_id="fulfill_loyalty_voucher",
        session_id=session_id,
        parameters={
            "customerId": customer_id,
            "sessionId": session_id,
            "offerPayload": {
                "title": "Welcome Back Special",
                "description": "20% off your next purchase",
                "discountPercent": 20,
                "promoCode": "VOUCHER-20",
                "churnProbability": 0.70,
                "churnRiskTier": "HIGH"
            }
        }
    )
    resp = await agent.handle_task(req)
    assert resp.status == TaskState.COMPLETED
    assert resp.output["status"] == "COMPLETED"

    # Verify Firestore persistence
    offer_id = resp.output["offerId"]
    stored_offer = mock_firestore.collection("loyalty_offers").document(offer_id).get().to_dict()
    assert stored_offer["offerId"] == offer_id
    assert stored_offer["discountPercent"] == 20
    assert stored_offer["status"] == "ACTIVE"

    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "PROCESSED"
    assert sess["offerId"] == offer_id


# ------------------------------------------------------------------------------
# 3. Retention Orchestrator Agent End-to-End Tests
# ------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_orchestrator_parallel_fanout_flow(mock_firestore, mock_bigquery, mock_gemini):
    """Tests full orchestrator pipeline with concurrent telemetry gathering and voucher issuance."""
    customer_id = "cust_orch_01"
    session_id = "sess_orch_01"

    mock_bigquery.set_predictions(customer_id, 0.76)
    mock_firestore.collection("customers").document(customer_id).set({
        "customerId": customer_id,
        "primaryComplaintReason": "LATE_DELIVERY"
    })
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    orchestrator = RetentionOrchestratorAgent(
        firestore_client=mock_firestore,
        bigquery_client=mock_bigquery,
        gemini_model=mock_gemini
    )

    offer = await orchestrator.process_session_async(session_id)
    assert offer is not None
    assert offer["churnRiskTier"] == "CRITICAL"
    assert offer["discountPercent"] == 25

    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "PROCESSED"
    assert sess["offerId"] == offer["offerId"]


# ------------------------------------------------------------------------------
# 4. HTTP Agent Runtime & Discovery Endpoint Tests
# ------------------------------------------------------------------------------

def test_runtime_http_discovery_and_tasks(mock_firestore, mock_bigquery, mock_gemini):
    """Tests HTTP server hosting /.well-known/agent-card.json and /a2a/v1/tasks."""
    import socket
    # Allocate free port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()

    orchestrator = RetentionOrchestratorAgent(
        firestore_client=mock_firestore,
        bigquery_client=mock_bigquery,
        gemini_model=mock_gemini
    )

    cooldown = CooldownPolicyAgent(mock_firestore)
    churn = ChurnIntelligenceAgent(mock_bigquery, mock_firestore)
    friction = CustomerFrictionAgent(mock_firestore)
    synthesis = OfferSynthesisAgent(mock_gemini)
    fulfillment = OfferFulfillmentAgent(mock_firestore)

    domain_agents = {
        "cooldown": cooldown,
        "churn": churn,
        "friction": friction,
        "synthesis": synthesis,
        "fulfillment": fulfillment
    }

    server = start_multi_agent_server(orchestrator, domain_agents, port=port)
    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. Healthcheck
        resp = httpx.get(f"{base_url}/healthz")
        assert resp.status_code == 200
        health_data = resp.json()
        assert health_data["status"] == "HEALTHY"
        assert "churn" in health_data["agents"]

        # 2. Canonical Root Discovery Endpoint (Orchestrator)
        resp = httpx.get(f"{base_url}/.well-known/agent-card.json")
        assert resp.status_code == 200
        root_card = resp.json()
        assert root_card["name"] == "Retention Orchestrator Agent"
        assert any(s["id"] == "orchestrate_retention_flow" for s in root_card["skills"])

        # 3. Domain Scoped Discovery Endpoint (Churn Agent)
        resp = httpx.get(f"{base_url}/churn/.well-known/agent-card.json")
        assert resp.status_code == 200
        churn_card = resp.json()
        assert churn_card["name"] == "Churn Intelligence Agent"
        assert any(s["id"] == "evaluate_churn_propensity" for s in churn_card["skills"])

        # 4. A2A Task Execution via HTTP (Churn Prediction Task)
        mock_bigquery.set_predictions("cust_http_test", 0.85)
        task_payload = {
            "skillId": "evaluate_churn_propensity",
            "sessionId": "sess_http_01",
            "parameters": {"customerId": "cust_http_test"}
        }
        resp = httpx.post(f"{base_url}/churn/a2a/v1/tasks", json=task_payload)
        assert resp.status_code == 200
        task_resp = resp.json()
        assert task_resp["status"] == "COMPLETED"
        assert task_resp["output"]["churnProbability"] == 0.85
        assert task_resp["output"]["churnTier"] == "CRITICAL"

        # 5. Root A2A Task Execution via HTTP (Orchestrator Flow)
        mock_firestore.collection("customers").document("cust_http_test").set({
            "customerId": "cust_http_test",
            "primaryComplaintReason": "LATE_DELIVERY"
        })
        mock_firestore.collection("customer_sessions").document("sess_http_01").set({
            "sessionId": "sess_http_01",
            "customerId": "cust_http_test",
            "status": "ACTIVE",
            "agentProcessingStatus": "PENDING"
        })
        orch_task_payload = {
            "skillId": "orchestrate_retention_flow",
            "sessionId": "sess_http_01",
            "parameters": {"sessionId": "sess_http_01"}
        }
        resp = httpx.post(f"{base_url}/a2a/v1/tasks", json=orch_task_payload)
        assert resp.status_code == 200
        orch_resp = resp.json()
        assert orch_resp["status"] == "COMPLETED"
        assert orch_resp["output"]["decision"] == "OFFER_ISSUED"
        assert orch_resp["output"]["offer"] is not None

        # 6. Session Trigger Endpoint via HTTP
        session_id_2 = "sess_http_02"
        mock_firestore.collection("customer_sessions").document(session_id_2).set({
            "sessionId": session_id_2,
            "customerId": "cust_http_test",
            "status": "ACTIVE",
            "agentProcessingStatus": "PENDING"
        })
        resp = httpx.post(f"{base_url}/trigger_session", json={"sessionId": session_id_2})
        assert resp.status_code == 200
        trig_resp = resp.json()
        assert trig_resp["sessionId"] == session_id_2
        # Since an active offer was created in step 5, step 6 should surface active offer
        assert trig_resp["offer"] is not None

    finally:
        server.shutdown()


@pytest.mark.anyio
async def test_retention_agent_engine_native(mock_firestore, mock_bigquery, mock_gemini):
    """Verifies that RetentionAgentEngine adheres to Google Cloud Agent Runtime interface."""
    from loyalty_agent.main import RetentionAgentEngine

    engine = RetentionAgentEngine()
    engine.fs_client = mock_firestore
    engine.bq_client = mock_bigquery
    engine.genai_client = mock_gemini

    cooldown = CooldownPolicyAgent(mock_firestore)
    churn = ChurnIntelligenceAgent(mock_bigquery, mock_firestore)
    friction = CustomerFrictionAgent(mock_firestore)
    synthesis = OfferSynthesisAgent(mock_gemini)
    fulfillment = OfferFulfillmentAgent(mock_firestore)

    engine.domain_agents = {
        "cooldown": cooldown,
        "churn": churn,
        "friction": friction,
        "synthesis": synthesis,
        "fulfillment": fulfillment
    }

    engine.orchestrator = RetentionOrchestratorAgent(
        firestore_client=mock_firestore,
        bigquery_client=mock_bigquery,
        gemini_model=mock_gemini,
        auto_register_local_domain_agents=False
    )
    for agent in engine.domain_agents.values():
        engine.orchestrator.register_domain_agent(agent)

    # 1. Test get_agent_card()
    card = engine.get_agent_card()
    assert card["name"] == "Retention Orchestrator Agent"
    assert len(card["skills"]) >= 1

    # 2. Test query()
    mock_bigquery.set_predictions("cust_eng_test", 0.88)
    mock_firestore.collection("customers").document("cust_eng_test").set({
        "customerId": "cust_eng_test",
        "primaryComplaintReason": "LATE_DELIVERY"
    })
    mock_firestore.collection("customer_sessions").document("sess_eng_01").set({
        "sessionId": "sess_eng_01",
        "customerId": "cust_eng_test",
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    query_res = engine.query("sess_eng_01")
    assert query_res["sessionId"] == "sess_eng_01"
    assert query_res["action"] == "OFFER_ISSUED"
    assert query_res["offer"]["discountPercent"] == 25

    # 3. Test handle_task()
    task_res = await engine.handle_task({
        "skillId": "orchestrate_retention_flow",
        "sessionId": "sess_eng_01",
        "parameters": {"sessionId": "sess_eng_01"}
    })
    assert task_res["status"] == "COMPLETED"

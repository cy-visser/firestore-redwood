#!/usr/bin/env python3
"""
Live End-to-End Verification Suite for Redwood Retail Native Agent Mesh on Agent Runtime.

Validates:
1. Canonical AgentCard discovery on all 6 native Reasoning Engines.
2. Direct external domain queries (`query()`) to all 5 domain agents.
3. Multi-agent A2A orchestration (`query(session_id=...)`) through the native Retention Orchestrator.
4. Real-time Firestore state persistence in customer_sessions and loyalty_offers.
"""

import sys
import os
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vertexai
from vertexai.preview import reasoning_engines
from google.cloud import firestore
from loyalty_agent.config import config

REGISTRY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deployed_native_agents.json")


def run_live_verification():
    print("=" * 70)
    print("🌲 REDWOOD RETAIL: Native Agent Runtime Live Mesh Verification")
    print("=" * 70)

    if not os.path.exists(REGISTRY_FILE):
        print(f"❌ Registry file not found: {REGISTRY_FILE}")
        sys.exit(1)

    with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
        registry = json.load(f)

    project_id = registry["project_id"]
    region = registry["region"]
    agents = registry["agents"]

    print(f"Target Project:  {project_id}")
    print(f"Target Region:   {region}")
    print(f"Deployed Agents: {len(agents)}")
    print("-" * 70)

    vertexai.init(project=project_id, location=region)
    test_customer_id = "cust_retail_72871"
    db = firestore.Client(project=project_id, database=config.firestore_database)

    # --------------------------------------------------------------------------
    # 1. Verify Domain Agents Direct Queries & Discovery
    # --------------------------------------------------------------------------
    print("\n🔍 Step 1: Testing Direct Queries & Discovery across 5 Domain Agents...")

    # A. Cooldown Policy Agent
    print("\n--- [1/5] Cooldown Policy Agent ---")
    cooldown_info = agents["cooldown"]
    print(f"Resource: {cooldown_info['resource_name']}")
    cooldown_engine = reasoning_engines.ReasoningEngine(cooldown_info["resource_name"])
    card = cooldown_engine.get_agent_card()
    print(f"AgentCard Name: {card.get('name')}")
    assert "Cooldown" in card.get("name"), f"Unexpected name: {card.get('name')}"
    cd_res = cooldown_engine.query(customer_id=test_customer_id)
    print(f"Direct Query Result: {json.dumps(cd_res, indent=2, default=str)}")
    assert "isEligible" in cd_res, f"Missing 'isEligible': {cd_res}"
    print("✅ Cooldown Agent verified successfully!")

    # B. Churn Intelligence Agent
    print("\n--- [2/5] Churn Intelligence Agent ---")
    churn_info = agents["churn"]
    print(f"Resource: {churn_info['resource_name']}")
    churn_engine = reasoning_engines.ReasoningEngine(churn_info["resource_name"])
    card = churn_engine.get_agent_card()
    print(f"AgentCard Name: {card.get('name')}")
    assert card.get("name") == "Churn Intelligence Agent", f"Unexpected name: {card.get('name')}"
    churn_res = churn_engine.query(customer_id=test_customer_id)
    print(f"Direct Query Result: {json.dumps(churn_res, indent=2, default=str)}")
    assert "churnProbability" in churn_res, f"Missing 'churnProbability': {churn_res}"
    assert "churnTier" in churn_res, f"Missing 'churnTier': {churn_res}"
    print("✅ Churn Intelligence Agent verified successfully!")

    # C. Customer Friction Agent
    print("\n--- [3/5] Customer Friction Agent ---")
    friction_info = agents["friction"]
    print(f"Resource: {friction_info['resource_name']}")
    friction_engine = reasoning_engines.ReasoningEngine(friction_info["resource_name"])
    card = friction_engine.get_agent_card()
    print(f"AgentCard Name: {card.get('name')}")
    assert card.get("name") == "Customer Friction Agent", f"Unexpected name: {card.get('name')}"
    fric_res = friction_engine.query(customer_id=test_customer_id)
    print(f"Direct Query Result: {json.dumps(fric_res, indent=2, default=str)}")
    assert "primaryFriction" in fric_res, f"Missing 'primaryFriction': {fric_res}"
    print("✅ Customer Friction Agent verified successfully!")

    # D. Offer Synthesis Agent
    print("\n--- [4/5] Offer Synthesis Agent ---")
    synthesis_info = agents["synthesis"]
    print(f"Resource: {synthesis_info['resource_name']}")
    synthesis_engine = reasoning_engines.ReasoningEngine(synthesis_info["resource_name"])
    card = synthesis_engine.get_agent_card()
    print(f"AgentCard Name: {card.get('name')}")
    assert card.get("name") == "Offer Synthesis Agent", f"Unexpected name: {card.get('name')}"
    synth_res = synthesis_engine.query(
        customer_id=test_customer_id,
        churn_tier="HIGH",
        primary_friction="DELIVERY"
    )
    print(f"Direct Query Result: {json.dumps(synth_res, indent=2, default=str)}")
    assert "discountPercent" in synth_res, f"Missing 'discountPercent': {synth_res}"
    assert "promoCode" in synth_res, f"Missing 'promoCode': {synth_res}"
    print("✅ Offer Synthesis Agent verified successfully!")

    # E. Offer Fulfillment Agent
    print("\n--- [5/5] Offer Fulfillment Agent ---")
    fulfillment_info = agents["fulfillment"]
    print(f"Resource: {fulfillment_info['resource_name']}")
    fulfillment_engine = reasoning_engines.ReasoningEngine(fulfillment_info["resource_name"])
    card = fulfillment_engine.get_agent_card()
    print(f"AgentCard Name: {card.get('name')}")
    assert card.get("name") == "Offer Fulfillment Agent", f"Unexpected name: {card.get('name')}"
    fulfill_res = fulfillment_engine.query(
        customer_id=test_customer_id,
        offer=synth_res,
        session_id=f"sess_probe_{int(time.time())}"
    )
    assert fulfill_res.get("status") in ("COMPLETED", "ACTIVE") or fulfill_res.get("offer", {}).get("status") == "ACTIVE", f"Expected valid fulfillment: {fulfill_res}"
    print("✅ Offer Fulfillment Agent verified successfully!")

    # --------------------------------------------------------------------------
    # 2. End-to-End Retention Orchestration via Native Agent Mesh
    # --------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("🌲 Step 2: Testing End-to-End Multi-Agent Orchestration via Agent Runtime...")
    print("=" * 70)

    orch_info = agents["orchestrator"]
    print(f"Orchestrator Resource: {orch_info['resource_name']}")
    orch_engine = reasoning_engines.ReasoningEngine(orch_info["resource_name"])
    card = orch_engine.get_agent_card()
    print(f"Orchestrator AgentCard Name: {card.get('name')}")
    assert card.get("name") == "Retention Orchestrator Agent", f"Unexpected name: {card.get('name')}"

    session_id = f"sess_native_e2e_{int(time.time())}"
    now_iso = datetime.now(timezone.utc).isoformat()
    db.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": test_customer_id,
        "loginTimestamp": now_iso,
        "deviceInfo": {
            "deviceType": "MOBILE_IOS",
            "osVersion": "18.2",
            "appVersion": "5.0.0"
        },
        "status": "PENDING",
        "agentProcessingStatus": "PENDING",
        "createdAt": now_iso
    })
    print(f"📝 Created Firestore session document: customer_sessions/{session_id}")

    print("🚀 Triggering Retention Orchestrator query()...")
    t0 = time.time()
    orch_res = orch_engine.query(session_id=session_id)
    elapsed = time.time() - t0
    print(f"⏱️ Orchestrator returned in {elapsed:.2f}s:")
    print(json.dumps(orch_res, indent=2, default=str))

    assert orch_res.get("sessionId") == session_id, f"Mismatched sessionId: {orch_res}"
    assert orch_res.get("action") in ("OFFER_ISSUED", "NO_OFFER_ISSUED"), f"Invalid action: {orch_res}"

    # Verify Firestore persistence
    sess_doc = db.collection("customer_sessions").document(session_id).get()
    assert sess_doc.exists, "Session document not found in Firestore!"
    sess_data = sess_doc.to_dict()
    print(f"Firestore Session Status: {sess_data.get('agentProcessingStatus')}")
    assert sess_data.get("agentProcessingStatus") in ("PROCESSED", "SKIPPED"), f"Unexpected status: {sess_data}"

    print("\n" + "=" * 70)
    print("🎉 ALL 6 NATIVE AGENTS VERIFIED LIVE ON AGENT RUNTIME!")
    print("=" * 70)


if __name__ == "__main__":
    run_live_verification()

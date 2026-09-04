"""
End-to-End Test Suite for Loyalty Offer Agent (Issue #4).
Executes all positive, negative, edge, and performance test cases.
Aligned with Redwood Retail Architecture Design (docs/firestore_enterprise_churn_agent_design.md).
"""

import time
import concurrent.futures
from datetime import datetime, timezone, timedelta
from google.api_core.exceptions import ServiceUnavailable, ResourceExhausted
import pytest


from loyalty_agent.agent import AutonomousLoyaltyAgent as LoyaltyOfferAgent



# ------------------------------------------------------------------------------
# Test Cases Implementation
# ------------------------------------------------------------------------------

def test_tc01_high_churn_trigger(mock_firestore, mock_bigquery, mock_gemini):
    """TC-01: High churn customer (0.65) generates standard retention offer."""
    customer_id = "cust_retail_10001"
    mock_bigquery.set_predictions(customer_id, 0.65, tier="SILVER")
    mock_firestore.collection("customers").document(customer_id).set({"customerId": customer_id})

    session_id = "sess_tc01_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer is not None
    assert offer["status"] == "ACTIVE"
    assert offer["churnRiskTier"] == "HIGH"
    assert offer["discountPercent"] == 15

    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "PROCESSED"
    assert sess["offerId"] == offer["offerId"]


def test_tc02_healthy_customer(mock_firestore, mock_bigquery, mock_gemini):
    """TC-02: Healthy customer (0.12) does not receive loyalty offer."""
    customer_id = "cust_retail_10002"
    mock_bigquery.set_predictions(customer_id, 0.12, tier="PLATINUM")
    mock_firestore.collection("customers").document(customer_id).set({"customerId": customer_id})

    session_id = "sess_tc02_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer is None
    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "SKIPPED"
    assert sess["offerId"] is None
    assert sess["skipReason"] == "LOW_CHURN_RISK"


def test_tc03_critical_churn_with_complaint(mock_firestore, mock_bigquery, mock_gemini):
    """TC-03: Critical churn (0.88) with LATE_DELIVERY receives 25% + apology."""
    customer_id = "cust_retail_10003"
    mock_bigquery.set_predictions(customer_id, 0.88, tier="GOLD")
    mock_firestore.collection("customers").document(customer_id).set({
        "customerId": customer_id,
        "primaryComplaintReason": "LATE_DELIVERY"
    })

    session_id = "sess_tc03_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer is not None
    assert offer["churnRiskTier"] == "CRITICAL"
    assert offer["discountPercent"] == 25
    assert offer["freeExpressShipping"] is True or "FREE_EXPRESS_SHIPPING" in offer["perks"]
    assert offer["personalizedApology"] is not None
    assert "delay" in offer["personalizedApology"].lower()


def test_tc04_new_customer_cold_start(mock_firestore, mock_bigquery, mock_gemini):
    """TC-04: New customer not in BQML handled gracefully without crash."""
    customer_id = "cust_retail_new_999"
    mock_firestore.collection("customers").document(customer_id).set({
        "customerId": customer_id,
        "accountAgeDays": 2,
        "ordersCountLast12m": 0
    })

    session_id = "sess_tc04_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer is None
    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "SKIPPED"
    assert sess["offerId"] is None
    assert sess["skipReason"] == "LOW_CHURN_RISK"


def test_tc05_rapid_login_spurt_idempotency(mock_firestore, mock_bigquery, mock_gemini):
    """TC-05: 3 rapid logins within seconds result in exactly 1 offer."""
    customer_id = "cust_retail_10005"
    mock_bigquery.set_predictions(customer_id, 0.70)
    mock_firestore.collection("customers").document(customer_id).set({"customerId": customer_id})

    session_ids = ["sess_tc05_1", "sess_tc05_2", "sess_tc05_3"]
    for sid in session_ids:
        mock_firestore.collection("customer_sessions").document(sid).set({
            "sessionId": sid,
            "customerId": customer_id,
            "status": "ACTIVE",
            "agentProcessingStatus": "PENDING"
        })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    results = [agent.process_session(sid) for sid in session_ids]

    offers = list(mock_firestore.collection("loyalty_offers").where("customerId", "==", customer_id).stream())
    assert len(offers) == 1
    created_offer_id = offers[0].to_dict()["offerId"]

    for sid in session_ids:
        sess = mock_firestore.collection("customer_sessions").document(sid).get().to_dict()
        assert sess["agentProcessingStatus"] == "PROCESSED"
        assert sess["offerId"] == created_offer_id


def test_tc06_active_offer_cooldown(mock_firestore, mock_bigquery, mock_gemini):
    """TC-06: Existing active offer within cooldown surfaces existing offer."""
    customer_id = "cust_retail_10006"
    mock_bigquery.set_predictions(customer_id, 0.72)
    mock_firestore.collection("customers").document(customer_id).set({"customerId": customer_id})

    now = datetime.now(timezone.utc)
    existing_offer_id = "off_sess_existing_retention"
    mock_firestore.collection("loyalty_offers").document(existing_offer_id).set({
        "offerId": existing_offer_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "cooldownUntil": (now + timedelta(days=6)).isoformat()
    })

    session_id = "sess_tc06_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer["offerId"] == existing_offer_id
    total_offers = list(mock_firestore.collection("loyalty_offers").where("customerId", "==", customer_id).stream())
    assert len(total_offers) == 1


def test_tc07_bigquery_service_failure_fallback(mock_firestore, mock_bigquery, mock_gemini):
    """TC-07: BigQuery 503 triggers heuristic fallback based on Firestore metrics."""
    customer_id = "cust_retail_10007"
    mock_bigquery.fail_with_exception = ServiceUnavailable("BigQuery service unavailable")
    mock_firestore.collection("customers").document(customer_id).set({
        "customerId": customer_id,
        "daysSinceLastPurchase": 95,
        "complaintsCount": 3
    })

    session_id = "sess_tc07_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer is not None
    assert offer["churnRiskTier"] == "CRITICAL"
    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "PROCESSED"


def test_tc08_vertex_gemini_quota_fallback(mock_firestore, mock_bigquery, mock_gemini):
    """TC-08: Vertex AI 429 triggers deterministic rule generator."""
    customer_id = "cust_retail_10008"
    mock_bigquery.set_predictions(customer_id, 0.82)
    mock_gemini.fail_with_exception = ResourceExhausted("Gemini API quota exceeded")
    mock_firestore.collection("customers").document(customer_id).set({"customerId": customer_id})

    session_id = "sess_tc08_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer is not None
    assert offer["generationSource"] == "DETERMINISTIC_RULES"
    assert offer["discountPercent"] == 25
    assert offer["promoCode"].startswith("RETENTION-DET-CRITICAL")


def test_tc09_realtime_sync_simulation_sla(mock_firestore, mock_bigquery, mock_gemini):
    """TC-09: Mobile client receives offer snapshot within < 500ms."""
    customer_id = "cust_retail_10009"
    mock_bigquery.set_predictions(customer_id, 0.68)
    mock_firestore.collection("customers").document(customer_id).set({"customerId": customer_id})

    session_id = "sess_tc09_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    received_events = []
    
    def on_snapshot_callback(snapshots, changes, read_time):
        for snap in snapshots:
            received_events.append((time.perf_counter(), snap.to_dict()))

    # Register client real-time listener
    unsubscribe = mock_firestore.collection("loyalty_offers").on_snapshot(on_snapshot_callback)

    start_time = time.perf_counter()
    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    agent.process_session(session_id)

    assert len(received_events) > 0
    event_time, event_data = received_events[-1]
    elapsed = event_time - start_time

    assert event_data["customerId"] == customer_id
    assert elapsed < 0.500, f"SLA violated: Sync took {elapsed*1000:.2f}ms (>500ms)"
    unsubscribe()


def test_tc10_offer_redemption_lifecycle(mock_firestore, mock_bigquery, mock_gemini):
    """TC-10: Redeemed offer respected; subsequent login handles cooldown cleanly."""
    customer_id = "cust_retail_10010"
    mock_bigquery.set_predictions(customer_id, 0.77)
    mock_firestore.collection("customers").document(customer_id).set({"customerId": customer_id})

    now = datetime.now(timezone.utc)
    offer_id = "off_sess_tc10_redeemed"
    # Seed already redeemed offer with cooldown
    mock_firestore.collection("loyalty_offers").document(offer_id).set({
        "offerId": offer_id,
        "customerId": customer_id,
        "status": "REDEEMED",
        "claimedAt": now.isoformat(),
        "cooldownUntil": (now + timedelta(days=5)).isoformat()
    })

    session_id = "sess_tc10_002"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer is None
    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "SKIPPED"
    assert sess["offerId"] is None
    assert sess["skipReason"] == "COOLDOWN_ACTIVE"


def test_tc11_concurrency_multi_customer_isolation(mock_firestore, mock_bigquery, mock_gemini):
    """TC-11: 10 concurrent customer logins processed in parallel without cross-talk."""
    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    customers = [f"cust_retail_conc_{i:02d}" for i in range(10)]

    for idx, cid in enumerate(customers):
        churn = 0.80 if idx % 2 == 0 else 0.15
        mock_bigquery.set_predictions(cid, churn)
        mock_firestore.collection("customers").document(cid).set({"customerId": cid})
        mock_firestore.collection("customer_sessions").document(f"sess_conc_{idx}").set({
            "sessionId": f"sess_conc_{idx}",
            "customerId": cid,
            "status": "ACTIVE",
            "agentProcessingStatus": "PENDING"
        })

    def run_worker(idx):
        return agent.process_session(f"sess_conc_{idx}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_worker, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Verify all 10 sessions were processed
    for idx, cid in enumerate(customers):
        sess = mock_firestore.collection("customer_sessions").document(f"sess_conc_{idx}").get().to_dict()
        if idx % 2 == 0:
            assert sess["agentProcessingStatus"] == "PROCESSED"
            assert sess["offerId"] is not None
            offer = mock_firestore.collection("loyalty_offers").document(sess["offerId"]).get().to_dict()
            assert offer["customerId"] == cid
        else:
            assert sess["agentProcessingStatus"] == "SKIPPED"
            assert sess["offerId"] is None
            assert sess["skipReason"] == "LOW_CHURN_RISK"


def test_tc12_event_augmented_hybrid_escalation(mock_firestore, mock_bigquery, mock_gemini):
    """
    TC-12: Event-Augmented Hybrid Model (SDD Section 1.2 Consensus):
    Customer has moderate baseline churn risk (0.40, normally SKIPPED as < 0.50),
    BUT today experienced an acute friction event (REFUND_REQUESTED).
    Agent synthesizes hybrid risk: 0.40 + 0.25 = 0.65 (HIGH), escalating into
    an actionable retention offer at login!
    """
    customer_id = "cust_retail_10012_hybrid"
    # Baseline batch prediction is 0.40 (normally non-actionable)
    mock_bigquery.set_predictions(customer_id, 0.40, tier="SILVER")
    # Customer profile has acute friction event from today's session
    mock_firestore.collection("customers").document(customer_id).set({
        "customerId": customer_id,
        "customerSegment": "STANDARD_LOYALTY",
        "recentFrictionEvent": "REFUND_REQUESTED",
        "primaryComplaintReason": "REFUND_REQUESTED"
    })

    session_id = "sess_tc12_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    # Offer MUST be created because acute friction boosted risk to 0.65 (HIGH)
    assert offer is not None
    assert offer["status"] == "ACTIVE"
    assert offer["churnProbability"] == 0.65
    assert offer["churnRiskTier"] == "HIGH"
    assert offer["baselineChurnRisk"] == 0.40
    assert offer["evaluationSource"] == "EVENT_AUGMENTED_HYBRID"
    assert offer["discountPercent"] == 15

    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "PROCESSED"
    assert sess["offerId"] == offer["offerId"]


def test_tc13_cached_firestore_churn_risk_fastpath(mock_firestore, mock_bigquery, mock_gemini):
    """
    TC-13: Fast-Path Point Lookup (<15ms):
    Baseline churn risk is cached in Firestore customer profile.
    Agent retrieves cached score directly without querying BigQuery,
    achieving sub-15ms OLTP response time.
    """
    customer_id = "cust_retail_10013_cached"
    # Note: mock_bigquery is NOT seeded, so if it queried BigQuery, it would return empty/None
    mock_firestore.collection("customers").document(customer_id).set({
        "customerId": customer_id,
        "customerSegment": "RETAIL_PRO",
        "baselineChurnRisk": 0.72
    })

    session_id = "sess_tc13_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    agent = LoyaltyOfferAgent(mock_firestore, mock_bigquery, mock_gemini)
    offer = agent.process_session(session_id)

    assert offer is not None
    assert offer["churnProbability"] == 0.72
    assert offer["churnRiskTier"] == "HIGH"
    assert offer["evaluationSource"] == "FIRESTORE_CACHE"
    assert offer["discountPercent"] == 15

    sess = mock_firestore.collection("customer_sessions").document(session_id).get().to_dict()
    assert sess["agentProcessingStatus"] == "PROCESSED"
    assert sess["offerId"] == offer["offerId"]


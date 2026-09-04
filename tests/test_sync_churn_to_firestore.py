"""
Test Suite for BigQuery to Firestore Reverse-ETL Churn Sync Pipeline.
Verifies data mapping, batched ingestion, merge=True field preservation,
dry-run preview, and end-to-end integration with the Loyalty Agent's fast-path cache.
"""

import pytest
from datetime import datetime, timezone

from sync_churn_to_firestore import BigQueryToFirestoreChurnSync
try:
    from loyalty_agent.agent import AutonomousLoyaltyAgent
    HAS_LOYALTY_AGENT = True
except (ImportError, ModuleNotFoundError):
    AutonomousLoyaltyAgent = None
    HAS_LOYALTY_AGENT = False


def test_sync_mapping_fields():
    """Verify BQ row mapping transforms snake_case to camelCase and sets baselineChurnRisk."""
    raw_bq_row = {
        "customer_id": "cust_retail_77777",
        "customer_name": "Test Enterprise",
        "customer_email": "test@enterprise.com",
        "customer_segment": "ENTERPRISE_VIP",
        "loyalty_tier": "GOLD",
        "churn_probability": 0.6821,
        "churn_risk_tier": "HIGH",
        "total_spend_90d": 4500.50,
        "days_since_last_purchase": 52,
        "cart_abandonment_count": 2,
        "support_tickets_count": 3,
        "sentiment_score": 0.25,
        "automated_retention_action": "⚠️ HIGH RISK: Dispatch Free Express Shipping Voucher",
        "calculation_timestamp": datetime(2026, 9, 3, 14, 0, 0, tzinfo=timezone.utc),
    }

    doc = BigQueryToFirestoreChurnSync.map_to_firestore_document(raw_bq_row)

    assert doc["customerId"] == "cust_retail_77777"
    assert doc["baselineChurnRisk"] == 0.6821
    assert doc["churnProbability"] == 0.6821
    assert doc["churnRiskTier"] == "HIGH"
    assert doc["customerSegment"] == "ENTERPRISE_VIP"
    assert doc["loyaltyTier"] == "GOLD"
    assert doc["totalSpend90d"] == 4500.50
    assert doc["daysSinceLastPurchase"] == 52
    assert doc["cartAbandonmentCount"] == 2
    assert doc["supportTicketsCount"] == 3
    assert doc["sentimentScore"] == 0.25
    assert "2026-09-03" in doc["churnEvaluatedAt"]
    assert "lastSyncedAt" in doc


def test_sync_all_customers_to_firestore(mock_firestore, mock_bigquery):
    """Verify batch sync materializes all BQ predictions into Firestore."""
    for i in range(1, 6):
        cid = f"cust_sync_{i:03d}"
        mock_bigquery.set_predictions(cid, churn_prob=0.10 * i, tier="SILVER")

    pipeline = BigQueryToFirestoreChurnSync(
        project_id="test-project",
        database_id="redwood",
        dataset_id="redwood_retail",
        table_id="customer_churn_risk",
        collection_name="customers",
        batch_size=2,  # 5 records -> 3 batches (2, 2, 1)
        max_workers=2,
        bigquery_client=mock_bigquery,
        firestore_client=mock_firestore
    )

    summary = pipeline.sync(dry_run=False)

    assert summary.total_fetched == 5
    assert summary.total_synced == 5
    assert summary.total_failed == 0
    assert summary.total_batches == 3

    # Check Firestore documents
    for i in range(1, 6):
        cid = f"cust_sync_{i:03d}"
        snap = mock_firestore.collection("customers").document(cid).get()
        assert snap.exists
        data = snap.to_dict()
        assert data["customerId"] == cid
        assert data["baselineChurnRisk"] == pytest.approx(0.10 * i, 0.01)


def test_sync_single_customer_point_lookup(mock_firestore, mock_bigquery):
    """Verify targeted single customer point sync."""
    target_cid = "cust_target_999"
    mock_bigquery.set_predictions(target_cid, churn_prob=0.82, tier="PLATINUM")
    mock_bigquery.set_predictions("cust_other_001", churn_prob=0.20, tier="BRONZE")

    pipeline = BigQueryToFirestoreChurnSync(
        bigquery_client=mock_bigquery,
        firestore_client=mock_firestore
    )

    summary = pipeline.sync(customer_id=target_cid, dry_run=False)

    assert summary.total_fetched == 1
    assert summary.total_synced == 1

    target_doc = mock_firestore.collection("customers").document(target_cid).get()
    assert target_doc.exists
    assert target_doc.to_dict()["baselineChurnRisk"] == 0.82

    # Verify other customer was NOT synced
    other_doc = mock_firestore.collection("customers").document("cust_other_001").get()
    assert not other_doc.exists


def test_sync_dry_run_mode(mock_firestore, mock_bigquery):
    """Verify dry run reads BQ but performs zero Firestore writes."""
    cid = "cust_dry_run_01"
    mock_bigquery.set_predictions(cid, churn_prob=0.77)

    pipeline = BigQueryToFirestoreChurnSync(
        bigquery_client=mock_bigquery,
        firestore_client=mock_firestore
    )

    summary = pipeline.sync(dry_run=True)

    assert summary.dry_run is True
    assert summary.total_fetched == 1
    assert summary.total_synced == 1

    # Verify Firestore document was NOT created
    doc = mock_firestore.collection("customers").document(cid).get()
    assert not doc.exists


def test_sync_preserves_existing_live_fields(mock_firestore, mock_bigquery):
    """Verify merge=True preserves live operational fields (friction, active offers)."""
    cid = "cust_preserve_01"
    # Pre-seed live operational state in Firestore
    mock_firestore.collection("customers").document(cid).set({
        "customerId": cid,
        "recentFrictionEvent": "LATE_DELIVERY",
        "primaryComplaintReason": "LATE_DELIVERY",
        "liveCartItems": ["PUMP-HYD-500", "VALVE-CTL-01"],
        "assignedAccountManager": "Sarah Chen"
    })

    # In BQ, churn predictions table has updated batch ML score
    mock_bigquery.set_predictions(cid, churn_prob=0.62, tier="GOLD")

    pipeline = BigQueryToFirestoreChurnSync(
        bigquery_client=mock_bigquery,
        firestore_client=mock_firestore
    )

    summary = pipeline.sync(customer_id=cid)
    assert summary.total_synced == 1

    # Check that both existing live fields and synced BQML fields co-exist
    updated_snap = mock_firestore.collection("customers").document(cid).get()
    data = updated_snap.to_dict()

    # Preserved live fields
    assert data["recentFrictionEvent"] == "LATE_DELIVERY"
    assert data["primaryComplaintReason"] == "LATE_DELIVERY"
    assert "PUMP-HYD-500" in data["liveCartItems"]
    assert data["assignedAccountManager"] == "Sarah Chen"

    # Newly synced BigQuery fields
    assert data["baselineChurnRisk"] == 0.62
    assert data["churnRiskTier"] == "HIGH"
    assert "lastSyncedAt" in data


@pytest.mark.skipif(not HAS_LOYALTY_AGENT, reason="loyalty_agent not yet merged into branch")
def test_sync_loyalty_agent_fastpath_integration(mock_firestore, mock_bigquery, mock_gemini):
    """
    End-to-end integration test:
    1. Reverse-ETL sync runs and materializes BQML baseline risk into Firestore.
    2. Customer logs in -> Loyalty Agent evaluates session.
    3. Verify Loyalty Agent hits fast-path FIRESTORE_CACHE without calling BigQuery.
    """
    cid = "cust_fastpath_integration"
    mock_bigquery.set_predictions(cid, churn_prob=0.78, tier="ENTERPRISE_VIP")

    # Step 1: Run Reverse-ETL sync
    pipeline = BigQueryToFirestoreChurnSync(
        bigquery_client=mock_bigquery,
        firestore_client=mock_firestore
    )
    pipeline.sync(customer_id=cid)

    # Verify Firestore has the cached risk
    cust_snap = mock_firestore.collection("customers").document(cid).get()
    assert cust_snap.exists
    assert cust_snap.to_dict()["baselineChurnRisk"] == 0.78

    # Step 2: Customer logs in
    session_id = "sess_fastpath_001"
    mock_firestore.collection("customer_sessions").document(session_id).set({
        "sessionId": session_id,
        "customerId": cid,
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING"
    })

    # Break BigQuery client intentionally to prove agent runs purely from Firestore cache!
    broken_bq_client = None

    agent = AutonomousLoyaltyAgent(
        firestore_client=mock_firestore,
        bigquery_client=broken_bq_client,
        gemini_model=mock_gemini
    )

    offer = agent.process_session(session_id)

    assert offer is not None
    assert offer["status"] == "ACTIVE"
    assert offer["churnRiskTier"] == "CRITICAL"
    assert offer["baselineChurnRisk"] == 0.78
    assert offer["discountPercent"] == 25  # VIP critical discount ceiling

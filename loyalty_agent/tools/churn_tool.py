"""
Tool 2: BigQuery ML Churn Inference & Cold-Start Heuristic Tool.
Evaluates customer churn probability (P_churn) via BQML with 5-pillar cold-start fallback.
"""

from typing import Dict, Any, Optional
from google.api_core.exceptions import GoogleAPICallError, ServiceUnavailable


from loyalty_agent.config import config


def evaluate_churn_tier(prob: float) -> str:
    """Classifies churn probability into calibrated risk tiers."""
    if prob >= 0.75:
        return "CRITICAL"
    elif prob >= 0.50:
        return "HIGH"
    elif prob >= 0.25:
        return "MODERATE"
    return "LOW"


def evaluate_heuristic_churn(customer_data: Dict[str, Any]) -> float:
    """
    Cold-start / service fallback 5-pillar heuristic evaluator (SDD Section 3.2 C):
    P_heuristic = (0.30 * S_rating) + (0.25 * S_sentiment) + (0.20 * S_complaint) + (0.15 * S_cart) + (0.10 * S_tickets)
    """
    account_age = customer_data.get("accountAgeDays", 365)

    # 1. Feedback Rating (w1 = 0.30): S_rating = (5 - rating) / 4
    raw_rating = customer_data.get("feedbackRating", customer_data.get("rating", 4))
    s_rating = max(0.0, min(1.0, (5.0 - float(raw_rating)) / 4.0))

    # 2. Sentiment Score (w2 = 0.25): S_sentiment = (1.0 - sentiment) / 2.0 (sentiment in [-1, 1])
    raw_sentiment = float(customer_data.get("sentimentScore", 0.5))
    s_sentiment = max(0.0, min(1.0, (1.0 - raw_sentiment) / 2.0))

    # 3. Complaint Severity (w3 = 0.20)
    complaints = int(customer_data.get("complaintsCount", 0))
    reason = str(customer_data.get("primaryComplaintReason") or "").upper()
    severity_map = {
        "DEFECTIVE_COMPONENT": 1.00,
        "BILLING_DISPUTE": 1.00,
        "DAMAGED_FREIGHT": 0.85,
        "RMA_DELAY": 0.85,
        "REFUND_REQUESTED": 0.85,
        "LATE_DELIVERY": 0.70,
        "POOR_SUPPORT_RESPONSE": 0.60,
        "ESCALATION": 0.60
    }
    s_complaint = severity_map.get(reason, min(1.0, complaints * 0.50))

    # 4. Cart Abandonment (w4 = 0.15): S_cart = min(1.0, cart_abandonment / 3)
    cart_count = int(customer_data.get("cartAbandonmentCount", 0))
    s_cart = min(1.0, cart_count / 3.0)

    # 5. Support Tickets & Returns (w5 = 0.10): S_tickets = min(1.0, (tickets + returns) / 2)
    tickets = int(customer_data.get("supportTicketsCount", 0))
    returns = int(customer_data.get("returnFrequency", 0))
    s_tickets = min(1.0, (tickets + returns) / 2.0)

    # Compute weighted base score
    p_heuristic = (
        0.30 * s_rating +
        0.25 * s_sentiment +
        0.20 * s_complaint +
        0.15 * s_cart +
        0.10 * s_tickets
    )

    # For new account cold-start (<30d) without severe complaints, keep baseline low
    if account_age < 30 and complaints == 0 and s_complaint == 0:
        return round(min(p_heuristic, 0.15), 4)

    # Inactivity recency amplifier (days_since_last_purchase > 60d or severe dissatisfaction)
    days_since_purchase = int(customer_data.get("daysSinceLastPurchase", 0))
    if days_since_purchase > 60 and complaints >= 2:
        return 0.75
    elif days_since_purchase > 60 or complaints >= 2 or raw_sentiment < 0.25:
        p_heuristic = max(p_heuristic, 0.75)
    elif days_since_purchase > 30 or complaints == 1 or raw_sentiment < 0.45:
        p_heuristic = max(p_heuristic, 0.55)

    return round(max(0.0, min(1.0, p_heuristic)), 4)


def query_customer_churn_risk(
    customer_id: str,
    bigquery_client: Any,
    firestore_client: Any,
    dataset_id: str = config.bigquery_dataset,
    table_id: str = config.churn_predictions_table
) -> Dict[str, Any]:
    """
    Queries customer churn risk with Event-Augmented Hybrid Architecture (SDD Section 1.2):
    1. Fast-path lookup: inspects Firestore /customers/{customerId} for cached baseline churn risk (<15ms).
    2. Batch analytical lookup: queries BigQuery customer_churn_risk table.
    3. Heuristic fallback: 5-pillar heuristic evaluator if BQ is unreachable or cold-start account.

    Returns:
        Dict containing:
            churn_probability (float): Probability of churn between 0.0 and 1.0.
            churn_tier (str): LOW, MODERATE, HIGH, or CRITICAL.
            evaluation_source (str): FIRESTORE_CACHE, BIGQUERY_BATCH, or HEURISTIC_FALLBACK.
            is_actionable (bool): True if probability >= 0.50.
    """
    churn_prob: Optional[float] = None
    source = "BIGQUERY_BATCH"
    cust_data: Optional[Dict[str, Any]] = None

    # Step 1: Check Firestore cache first for sub-15ms OLTP response
    if firestore_client is not None:
        try:
            cust_doc = firestore_client.collection("customers").document(customer_id).get()
            if cust_doc.exists:
                cust_data = cust_doc.to_dict()
                cached_prob = cust_data.get("baselineChurnRisk")
                if cached_prob is not None:
                    churn_prob = float(cached_prob)
                    source = "FIRESTORE_CACHE"
        except Exception:
            pass

    # Step 2: Query BigQuery materialized table if not found in Firestore cache
    if churn_prob is None and bigquery_client is not None:
        try:
            sql = f"SELECT * FROM `{dataset_id}.{table_id}` WHERE customer_id = '{customer_id}'"
            results = list(bigquery_client.query(sql).result())
            if results:
                row = results[0]
                churn_prob = getattr(row, "churn_probability", None)
                if churn_prob is None and hasattr(row, "get"):
                    churn_prob = row.get("churn_probability")
                if churn_prob is not None:
                    source = "BIGQUERY_BATCH"
        except (ServiceUnavailable, GoogleAPICallError, Exception):
            source = "HEURISTIC_FALLBACK"

    # Step 3: Heuristic fallback for cold-start or failed BQML
    if churn_prob is None:
        source = "HEURISTIC_FALLBACK"
        if cust_data is None and firestore_client is not None:
            try:
                cust_doc = firestore_client.collection("customers").document(customer_id).get()
                cust_data = cust_doc.to_dict() if cust_doc.exists else {}
            except Exception:
                cust_data = {}
        churn_prob = evaluate_heuristic_churn(cust_data or {})

    tier = evaluate_churn_tier(churn_prob)
    return {
        "customer_id": customer_id,
        "churn_probability": round(churn_prob, 4),
        "churn_tier": tier,
        "evaluation_source": source,
        "is_actionable": churn_prob >= config.churn_trigger_threshold
    }

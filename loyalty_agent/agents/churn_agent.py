"""
Churn Intelligence Agent.
Autonomous A2A agent evaluating churn propensity via BigQuery ML, Firestore cache, and 5-pillar heuristic.
"""

from typing import Dict, Any, Optional
import logging
from google.api_core.exceptions import GoogleAPICallError, ServiceUnavailable

from loyalty_agent.a2a.card import AgentCard, AgentSkill
from loyalty_agent.agents.base_a2a_agent import BaseA2AAgent
from loyalty_agent.config import config

logger = logging.getLogger("a2a.churn_agent")


def evaluate_churn_tier(prob: float) -> str:
    """Classifies churn probability into calibrated risk tiers."""
    if prob >= 0.75:
        return "CRITICAL"
    elif prob >= 0.50:
        return "HIGH"
    elif prob >= 0.25:
        return "MODERATE"
    return "LOW"


def evaluate_5pillar_heuristic(customer_data: Dict[str, Any]) -> float:
    """
    Cold-start / service fallback 5-pillar heuristic formulation (SDD Section 3.2 C):
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

    p_heuristic = (
        0.30 * s_rating +
        0.25 * s_sentiment +
        0.20 * s_complaint +
        0.15 * s_cart +
        0.10 * s_tickets
    )

    if account_age < 30 and complaints == 0 and s_complaint == 0:
        return round(min(p_heuristic, 0.15), 4)

    days_since_purchase = int(customer_data.get("daysSinceLastPurchase", 0))
    if days_since_purchase > 60 and complaints >= 2:
        return 0.75
    elif days_since_purchase > 60 or complaints >= 2 or raw_sentiment < 0.25:
        p_heuristic = max(p_heuristic, 0.75)
    elif days_since_purchase > 30 or complaints == 1 or raw_sentiment < 0.45:
        p_heuristic = max(p_heuristic, 0.55)

    return round(max(0.0, min(1.0, p_heuristic)), 4)


class ChurnIntelligenceAgent(BaseA2AAgent):
    """
    Autonomous domain agent predicting customer churn propensity using BigQuery ML,
    Firestore fast-path cache, and calibrated 5-pillar heuristic cold-start models.
    """

    def __init__(
        self,
        bigquery_client: Any,
        firestore_client: Any,
        base_url: str = "http://localhost:8081/churn",
        dataset_id: str = config.bigquery_dataset,
        table_id: str = config.churn_predictions_table
    ):
        self.bq = bigquery_client
        self.fs = firestore_client
        self.dataset_id = dataset_id
        self.table_id = table_id

        card = AgentCard(
            name="Churn Intelligence Agent",
            description="Predicts customer churn propensity via BigQuery ML models, fast-path Firestore cache, and 5-pillar heuristic cold-start evaluation.",
            version="1.0.0",
            url=base_url,
            skills=[
                AgentSkill(
                    id="evaluate_churn_propensity",
                    name="Evaluate Customer Churn Propensity",
                    description="Computes calibrated churn probability and risk tier from cache, BQML, or 5-pillar heuristic.",
                    tags=["Machine Learning", "BigQuery ML", "Churn", "Predictive Analytics"],
                    examples=["Evaluate churn for cust_8471"],
                    input_schema={
                        "type": "object",
                        "properties": {
                            "customerId": {"type": "string"},
                            "customerData": {"type": "object"}
                        },
                        "required": ["customerId"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "customerId": {"type": "string"},
                            "churnProbability": {"type": "number"},
                            "churnTier": {"type": "string", "enum": ["LOW", "MODERATE", "HIGH", "CRITICAL"]},
                            "evaluationSource": {"type": "string"}
                        }
                    }
                )
            ]
        )
        super().__init__(agent_card=card)
        self.register_skill_handler("evaluate_churn_propensity", self._handle_evaluate_churn)

    async def _handle_evaluate_churn(self, parameters: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        customer_id = parameters["customerId"]
        customer_data = parameters.get("customerData", {})

        # 1. Fast-Path Lookup: Check Firestore customer profile for precomputed baseline (<15ms)
        if self.fs:
            try:
                cust_doc = self.fs.collection("customers").document(customer_id).get()
                if cust_doc.exists:
                    doc_data = cust_doc.to_dict()
                    cached_churn = doc_data.get("baselineChurnRisk")
                    if cached_churn is not None:
                        prob = float(cached_churn)
                        return {
                            "customerId": customer_id,
                            "churnProbability": prob,
                            "churnTier": evaluate_churn_tier(prob),
                            "evaluationSource": "FIRESTORE_CACHE"
                        }
                    if not customer_data:
                        customer_data = doc_data
            except Exception as exc:
                logger.warning("Firestore fast-path cache read error for %s: %s", customer_id, exc)

        # 2. Analytical Lookup: Query BigQuery customer_churn_risk table
        if self.bq:
            try:
                sql = f"SELECT * FROM `{self.dataset_id}.{self.table_id}` WHERE customer_id = '{customer_id}'"
                query_job = self.bq.query(sql)
                results = list(query_job.result())

                if results:
                    row = results[0]
                    prob = getattr(row, "churn_probability", None)
                    if prob is None and hasattr(row, "get"):
                        prob = row.get("churn_probability")
                    if prob is not None:
                        prob = float(prob)
                        tier = getattr(row, "churn_risk_tier", None)
                        if tier is None and hasattr(row, "get"):
                            tier = row.get("churn_risk_tier")
                        tier_str = str(tier).upper() if tier else evaluate_churn_tier(prob)
                        return {
                            "customerId": customer_id,
                            "churnProbability": prob,
                            "churnTier": tier_str,
                            "evaluationSource": "BIGQUERY_BATCH"
                        }
            except (GoogleAPICallError, ServiceUnavailable, Exception) as bq_err:
                logger.warning("BigQuery lookup failed for %s (%s). Falling back to 5-pillar heuristic.", customer_id, bq_err)

        # 3. 5-Pillar Heuristic Fallback
        if not customer_data and self.fs:
            try:
                cust_doc = self.fs.collection("customers").document(customer_id).get()
                if cust_doc.exists:
                    customer_data = cust_doc.to_dict()
            except Exception:
                pass

        heuristic_prob = evaluate_5pillar_heuristic(customer_data or {})
        return {
            "customerId": customer_id,
            "churnProbability": heuristic_prob,
            "churnTier": evaluate_churn_tier(heuristic_prob),
            "evaluationSource": "HEURISTIC_FALLBACK"
        }

# Software Design Document (SDD)
# Autonomous Loyalty Offer Agent Based on Churn Risk (Google ADK Architecture)
**Project:** Redwood Retail (Google Cloud Architecture)  
**Issue:** [cy-visser/firestore-redwood #4](https://github.com/cy-visser/firestore-redwood/issues/4)  
**Branch:** `feature/loyalty-offer-agent`  
**Author:** Ganesh Raja (Practice Customer Engineer)  
**Framework:** Google Agent Development Kit (`google-adk`) & Gemini Enterprise Platform  
**Target Platform:** Google Cloud (Firestore Enterprise Native, BigQuery ML, Vertex AI Agent Engine, Cloud Dataflow)  
**Target Database:** `projects/${GCP_PROJECT_ID}/databases/redwood` (`europe-west4`)  
**Status:** UPDATED WITH ADK MULTI-TOOL ARCHITECTURE  

---

## 1. Executive Summary & Problem Framing

### 1.1 Context & Objective
In enterprise industrial and electronics e-commerce, customer churn represents severe revenue loss. When high-value customers experience operational friction (e.g., late shipments, defective parts, or billing disputes), their likelihood of defecting to competing suppliers rises exponentially.

**GitHub Issue #4 Requirements:**
1. The retention agent must run **independently** as an autonomous background service.
2. When a customer initiates a **new login session**, the agent evaluates churn risk in real time.
3. If churn risk is **high** ($\ge 0.50$), the agent autonomously synthesizes a personalized loyalty offer.
4. The loyalty offer is written to **Firestore Enterprise Native** and pushed immediately to the **mobile client** via real-time listeners.

### 1.2 Core Architectural Paradigm: Google Agent Development Kit (ADK)
To transition from rigid procedural scripts to true AI agency, the system utilizes the **Google Agent Development Kit (`google-adk`)**. The ADK agent operates as an autonomous reasoning orchestrator powered by Gemini, equipped with a dedicated **Toolset** containing specialized tools for querying BigQuery ML, inspecting customer friction in Firestore, verifying retention cooldowns, and executing transactional writes.

### 1.3 Service Level Agreements (SLAs) & Technical Targets
* **Detection Latency:** $< 50\text{ms}$ from session document creation to agent receipt via Firestore `on_snapshot` gRPC watch streams.
* **End-to-End Retention SLA:** $< 800\text{ms}$ from customer authentication to the appearance of an active retention voucher banner on the mobile device.
* **Availability & Durability:** 99.99% multi-zone regional availability in `europe-west4`, backed by 7-day Point-in-Time Recovery (PITR).
* **Zero Duplication Guarantee:** Exactly-once offer issuance per session and a strict 7-day retention cooldown per customer profile enforced through Firestore ACID transactions.

---

## 2. High-Fidelity System Architecture

The end-to-end topology decouples the client interaction, operational database, analytical ML warehouse, and generative AI retention engine.

![Autonomous Retention AI Agent with Google Agent Development Kit (ADK)](/usr/local/google/home/ganeshraja/projects/firestore-redwood/docs/images/adk_loyalty_agent_architecture.jpg)

### Core Architectural Flow:
1. **Customer Login**: Mobile Client authenticates and creates a session document in Firestore `/customer_sessions` with `agentProcessingStatus = 'PENDING'`.
2. **Reactive Interception**: The Autonomous Agent daemon, connected via persistent gRPC bidirectional watch stream (`on_snapshot`), detects the pending session in $< 50\text{ms}$.
3. **ADK Agent Invocation**: The session event is passed to the ADK `Runner`, which instantiates the `loyalty_retention_agent`.
4. **Dynamic Multi-Tool Execution Loop**:
   * **Tool 1 (`check_active_offers_cooldown`)**: Verifies if the customer already received an active offer in the last 7 days. If active, reuses the existing offer and marks session `SKIPPED_COOLDOWN`.
   * **Tool 2 (`retrieve_customer_churn_risk`)**: Retrieves pre-calculated churn baseline risk from the daily BigQuery `customer_churn_risk` table (synced to Firestore for sub-15ms reads). Eliminates expensive dynamic `ML.PREDICT` on the login critical path, with 5-pillar heuristic fallback for cold start.
   * **Tool 3 (`get_customer_friction_and_profile`)**: Pulls customer order history, loyalty tier, and acute real-time friction events (`LATE_DELIVERY`, `DEFECTIVE_COMPONENT`, `REFUND_REQUESTED`, `BILLING_DISPUTE`).
   * **LLM Synthesis**: Gemini 3.8 Flash reasons over the baseline risk combined with the acute friction event, drafts an empathetic apology, and formulates a tailored discount code adhering to customer tier ceilings.
   * **Tool 4 (`issue_loyalty_offer`)**: Atomically writes the finalized voucher to `/loyalty_offers`, updates `/customer_sessions` to `PROCESSED`, and records cooldown timestamps on `/customers/{customerId}`.
5. **Instant Mobile Push**: Firestore Enterprise Native engine streams the new offer document to the mobile client's active listener in $< 100\text{ms}$, rendering the personalized retention banner instantly.

---

## 3. ADK Multi-Tool Specification

The ADK Agent is equipped with four modular, single-responsibility tools.

### 3.1 Tool 1: `check_active_offers_cooldown_tool`
* **Purpose**: Enforces anti-spam governance and checks whether a retention offer was minted within the 7-day cooldown period.
* **Input Signature**:
  ```python
  def check_active_offers_cooldown(customer_id: str) -> dict:
  ```
* **Output Schema**:
  ```json
  {
    "cooldown_active": true,
    "reused_offer_id": "off_sess_01J6K8M9_retention",
    "last_offer_timestamp": "2026-09-01T12:00:00Z",
    "days_remaining_in_cooldown": 5
  }
  ```
* **Implementation Details**: Reads `/customers/{customerId}` to inspect `lastRetentionOfferAt`. If $< 7\text{ days}$, retrieves the active offer from `/loyalty_offers` and instructs the agent to suppress new creation.

### 3.2 Tool 2: `query_customer_churn_risk_tool`
* **Purpose**: Retrieves baseline churn risk from cached Firestore customer profile (<15ms) or materialized BigQuery `customer_churn_risk` table, with 5-pillar cold-start heuristic fallback.
* **Input Signature**:
  ```python
  def query_customer_churn_risk(customer_id: str) -> dict:
  ```
* **SQL Query Executed (when not in Firestore cache)**:
  ```sql
  SELECT
    customer_id,
    predicted_is_churned,
    churn_probability,
    churn_risk_tier,
    total_spend_90d,
    days_since_last_purchase,
    cart_abandonment_count,
    support_tickets_count,
    sentiment_score
  FROM
    `@project_id.@dataset_id.customer_churn_risk`
  WHERE
    customer_id = @target_customer_id;
  ```
* **Output Schema**:
  ```json
  {
    "customer_id": "cust_retail_04821",
    "churn_probability": 0.824,
    "churn_risk_tier": "CRITICAL",
    "predicted_is_churned": 1,
    "evaluation_engine": "BIGQUERY_ML",
    "key_risk_drivers": {
      "days_since_last_purchase": 72,
      "cart_abandonment_count": 4,
      "sentiment_score": -0.42
    }
  }
  ```

### 3.3 Tool 3: `get_customer_friction_and_profile_tool`
* **Purpose**: Enriches agent reasoning context with granular transaction metrics, loyalty tiers, and customer service grievances.
* **Input Signature**:
  ```python
  def get_customer_friction_and_profile(customer_id: str) -> dict:
  ```
* **Output Schema**:
  ```json
  {
    "customer_id": "cust_retail_04821",
    "customer_name": "Enterprise Logistics B.V.",
    "customer_segment": "ENTERPRISE_VIP",
    "loyalty_tier": "PLATINUM",
    "account_age_days": 420,
    "total_spend_90d": 14250.00,
    "primary_friction_point": "LATE_DELIVERY",
    "complaint_history": [
      {
        "reason": "LATE_DELIVERY",
        "feedback_text": "Critical delay on Edge Gateway routers without proactive notice.",
        "rating": 1,
        "sentiment_score": -0.85,
        "timestamp": "2026-08-28T09:15:00Z"
      }
    ],
    "authorized_discount_ceiling": 25
  }
  ```

### 3.4 Tool 4: `issue_loyalty_offer_tool`
* **Purpose**: Atomically registers the synthesized retention package in Firestore and transitions the session state.
* **Input Signature**:
  ```python
  def issue_loyalty_offer(
      customer_id: str,
      session_id: str,
      title: str,
      description: str,
      promo_code: str,
      discount_percent: int,
      free_express_shipping: bool,
      personalized_apology: str,
      churn_probability: float,
      churn_risk_tier: str
  ) -> dict:
  ```
* **ACID Transaction Commit**:
  1. Writes `/loyalty_offers/{offerId}` with `status = 'ACTIVE'`.
  2. Updates `/customer_sessions/{sessionId}` with `agentProcessingStatus = 'PROCESSED'` and references `offerId`.
  3. Updates `/customers/{customerId}` with `lastRetentionOfferAt = SERVER_TIMESTAMP` and `lastActiveOfferId = offerId`.
* **Output Schema**:
  ```json
  {
    "status": "SUCCESS",
    "offer_id": "off_sess_01J6K8M9_retention",
    "promo_code": "STAYVIP25-04821",
    "discount_percent": 25,
    "free_express_shipping": true,
    "committed_at": "2026-09-03T04:50:00Z"
  }
  ```

---

## 4. Google ADK Agent Definition & Orchestration

The autonomous retention agent is implemented using `google.adk.Agent`:

```python
"""
Redwood Retail: Autonomous Retention Agent
File: loyalty_agent/agent.py
"""

from google.adk import Agent
from loyalty_agent.tools import (
    check_active_offers_cooldown,
    query_customer_churn_risk,
    get_customer_friction_and_profile,
    issue_loyalty_offer,
)

RETAIN_AGENT_INSTRUCTION = """
You are the Redwood Retail Autonomous Retention Specialist.
Your mission is to prevent customer defection by evaluating churn risk and issuing targeted, personalized retention offers.

When a customer session is presented:
1. STEP 1: Always call `check_active_offers_cooldown` with the customer_id.
   - If `cooldown_active` is True, do NOT create a new offer. Conclude evaluation immediately.
2. STEP 2: If cooldown is clear, call `query_customer_churn_risk` to evaluate the customer in BigQuery ML.
   - If `churn_probability` is below 0.50 (LOW or MODERATE risk), do NOT issue a discount offer. Conclude evaluation.
3. STEP 3: If `churn_probability` is >= 0.50 (HIGH or CRITICAL risk):
   - Call `get_customer_friction_and_profile` to retrieve their order history, complaints, and discount ceiling.
   - Identify the primary friction point (e.g. LATE_DELIVERY, DEFECTIVE_COMPONENT, BILLING_DISPUTE).
4. STEP 4: Reason and formulate a personalized retention offer:
   - Write an empathetic `personalized_apology` directly addressing their friction point.
   - Set `discount_percent` strictly within `authorized_discount_ceiling` (VIP: <=25%, Pro: <=20%, Standard: <=15%).
   - If friction is logistics-related, include `free_express_shipping = True`.
   - Generate a clean uppercase promo code: e.g. `RW-{TIER}-{DISCOUNT}-{REASON}-{ID}`.
5. STEP 5: Call `issue_loyalty_offer` with all offer parameters to persist the voucher to Firestore.
"""

def create_retention_agent() -> Agent:
    """Instantiates the ADK Autonomous Agent equipped with the 4-tool suite."""
    return Agent(
        name="loyalty_retention_agent",
        model="gemini-3.8-flash",
        instruction=RETAIN_AGENT_INSTRUCTION,
        tools=[
            check_active_offers_cooldown,
            query_customer_churn_risk,
            get_customer_friction_and_profile,
            issue_loyalty_offer,
        ],
    )
```

---

## 5. Firestore Enterprise Native Data Architecture

### 5.1 Collection Hierarchy: Root Collection Pattern
To optimize for both the autonomous agent ingestion and real-time mobile push, decoupled root collections are deployed:

* `/customer_sessions/{sessionId}`: Operational login events. Document key: `sess_{timestamp_millis}_{entropy}`.
* `/loyalty_offers/{offerId}`: Customer vouchers. Document key: `off_{sessionId}_retention`.
* `/customers/{customerId}`: Master customer profile, loyalty status, and cooldown timestamps.

### 5.2 Composite Index Strategy
Defined in `terraform/firestore.tf` to support mobile queries and the agent pending queue:

```hcl
# 1. Mobile Client Real-Time Active Offers Listener
resource "google_firestore_index" "loyalty_offers_active_mobile_idx" {
  project    = var.project_id
  database   = var.firestore_database_id
  collection = "loyalty_offers"

  fields {
    field_path = "customerId"
    order      = "ASCENDING"
  }
  fields {
    field_path = "status"
    order      = "ASCENDING"
  }
  fields {
    field_path = "createdAt"
    order      = "DESCENDING"
  }
}

# 2. Autonomous Retention Agent Pending Queue
resource "google_firestore_index" "customer_sessions_pending_agent_idx" {
  project    = var.project_id
  database   = var.firestore_database_id
  collection = "customer_sessions"

  fields {
    field_path = "agentProcessingStatus"
    order      = "ASCENDING"
  }
  fields {
    field_path = "loginTimestamp"
    order      = "ASCENDING"
  }
}
```

### 5.3 Native Time-To-Live (TTL) Policies
Automated zero-cost document lifecycle management:
* **Sessions Purge:** `expireAt` field on `customer_sessions` set to $T_{\text{login}} + 30\text{ days}$.
* **Audit Purge:** `ttlExpiryAt` field on `loyalty_offers` set to $T_{\text{validUntil}} + 90\text{ days}$.

---

## 6. Deterministic Fallbacks & Policy Guardrails

### 6.1 Circuit Breaker & Fallback Matrix
If Vertex AI is temporarily unreachable, rate-limited (HTTP 429), or times out ($> 2.5\text{s}$), the agent executes the **Deterministic Rule Fallback Matrix**:
* `(CRITICAL, LATE_DELIVERY)`: 25% discount, Free Next-Day Air Freight, apology for factory delay.
* `(CRITICAL, DEFECTIVE_COMPONENT)`: 25% discount, Immediate Advance Replacement dispatch.
* `(CRITICAL, BILLING_DISPUTE)`: 25% discount, Dedicated Finance AM escalation code.
* `(HIGH, LATE_DELIVERY)`: 15% discount, Priority courier upgrade.
* `(HIGH, CART_ABANDONMENT)`: 10% discount, 7-day inventory reservation.

### 6.2 Financial & Margin Safeguards
* **Discount Ceilings:** Enterprise VIP: 25% max; Retail Pro: 20% max; Standard: 15% max; Casual: 12% max.
* **Margin Safeguard:** Verifies gross margin remains $\ge 10\%$ after discount application.

---

## 7. End-to-End Test Specification & Verification Matrix

The test harness in [`tests/conftest.py`](file:///usr/local/google/home/ganeshraja/projects/firestore-redwood/tests/conftest.py) provides 100% in-memory isolation with high-fidelity doubles for Firestore, BigQuery ML, and Gemini tool calling.

### Test Catalog:
* **TC-01 (Happy Path - High Churn):** BQML Churn = 0.65 $\rightarrow$ Agent calls tools 1, 2, 3, 4 $\rightarrow$ Generates 15% offer + free shipping; session marked `PROCESSED`.
* **TC-02 (Negative Path - Healthy):** BQML Churn = 0.12 $\rightarrow$ Agent terminates after tool 2 $\rightarrow$ Zero offers created; session marked `SKIPPED` (`LOW_CHURN_RISK`).
* **TC-03 (Critical Churn with Complaint):** BQML Churn = 0.88, Complaint = `LATE_DELIVERY` $\rightarrow$ Generates 25% offer with customized apology.
* **TC-04 (Cold Start / New Customer):** Account not in BQML $\rightarrow$ Heuristic evaluator grades account; zero crashes.
* **TC-05 (Idempotency / Rapid Burst):** 3 simultaneous logins within 10s $\rightarrow$ Exactly 1 offer created; all sessions link to identical offer.
* **TC-06 (Cooldown Enforcement):** Active offer exists within 7 days $\rightarrow$ Tool 1 returns `cooldown_active = True` $\rightarrow$ Reuses existing offer, suppresses duplicate creation.
* **TC-07 (BigQuery Failure Fallback):** BQML throws HTTP 503 $\rightarrow$ Heuristic fallback generates safe offer; session processed.
* **TC-08 (Vertex AI Quota Fallback):** Gemini throws HTTP 429 $\rightarrow$ Deterministic rule engine mints valid offer without failure.
* **TC-09 (Mobile Sync SLA):** Measures client snapshot callback latency upon agent write $\rightarrow$ Verified $< 5\text{ms}$ (SLA $< 500\text{ms}$).
* **TC-10 (Offer Redemption Lifecycle):** Offer updated to `REDEEMED` $\rightarrow$ Subsequent login enforces cooldown without regenerating.
* **TC-11 (Multi-Customer Isolation):** 10 concurrent customers evaluated concurrently $\rightarrow$ Zero state leakage across tenants.

### Test Execution Verification:
```
$ ./deploy.sh --run-tests
============================= test session starts ==============================
collected 11 items
tests/test_loyalty_agent.py::test_tc01_high_churn_trigger PASSED         [  9%]
tests/test_loyalty_agent.py::test_tc02_healthy_customer PASSED           [ 18%]
tests/test_loyalty_agent.py::test_tc03_critical_churn_with_complaint PASSED [ 27%]
tests/test_loyalty_agent.py::test_tc04_new_customer_cold_start PASSED    [ 36%]
tests/test_loyalty_agent.py::test_tc05_rapid_login_spurt_idempotency PASSED [ 45%]
tests/test_loyalty_agent.py::test_tc06_active_offer_cooldown PASSED      [ 54%]
tests/test_loyalty_agent.py::test_tc07_bigquery_service_failure_fallback PASSED [ 63%]
tests/test_loyalty_agent.py::test_tc08_vertex_gemini_quota_fallback PASSED [ 72%]
tests/test_loyalty_agent.py::test_tc09_realtime_sync_simulation_sla PASSED [ 81%]
tests/test_loyalty_agent.py::test_tc10_offer_redemption_lifecycle PASSED [ 90%]
tests/test_loyalty_agent.py::test_tc11_concurrency_multi_customer_isolation PASSED [100%]
============================== 11 passed in 0.14s ==============================
🎉 All Loyalty Offer Agent tests passed successfully!
```

---

## 8. Multi-Agent Implementation Roadmap

| Workstream | Subagent Role | Primary Deliverables | Target Files |
| :--- | :--- | :--- | :--- |
| **WS-1: Infrastructure & Indexes** | `database-ce` | Terraform composite indexes, TTL policies, and Firestore security rules | `terraform/firestore.tf`, `firestore.rules` |
| **WS-2: ADK Agent & Tools** | `agent-ml-architect` | ADK Agent definition, 4 specialized tools, BQML inference, and rule fallback | `loyalty_agent/agent.py`, `loyalty_agent/tools.py`, `loyalty_agent/runner.py` |
| **WS-3: Verification & CLI Packaging** | `qa-test-architect` | Test suite maintenance, local emulator integration, and `deploy.sh` runner | `tests/`, `deploy.sh`, `README.md` |

---
*Signed off by Practice Customer Engineering.*

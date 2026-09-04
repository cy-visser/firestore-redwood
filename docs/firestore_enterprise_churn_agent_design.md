# Google Cloud Firestore Enterprise Native Architecture Design
## Issue #4: Autonomous Agent for Churn Risk-Based Loyalty Offer Generation
**Project:** Redwood Retail  
**Database:** `projects/${GCP_PROJECT_ID}/databases/redwood`  
**Region:** `europe-west4` (Eemshaven, Netherlands)  
**Author:** Senior Google Cloud Database Customer Engineer  

---

## 1. Executive Summary & Workload Characteristics

Redwood Retail deploys Google Cloud Firestore Enterprise in Native Mode (`europe-west4`) as its operational database of record, paired with real-time Change Data Capture (CDC) via Cloud Dataflow into BigQuery. Issue #4 introduces an Autonomous Customer Retention Agent that identifies churn risk at customer login and immediately provisions targeted loyalty offers.

### Workload Profile & Performance Budgets
* **Transaction Pattern:** Read-heavy on mobile/web clients (active offers and active sessions), write-heavy during burst login hours, and atomic multi-document writes during agent offer generation.
* **Latency SLA:** 
  * Mobile active offer snapshot response: `< 15ms` (cached), `< 40ms` (network fetch).
  * Real-time session event detection (login to agent trigger): `< 250ms` (zero polling lag via gRPC watch streams).
  * End-to-end offer creation lifecycle: `< 800ms` from login timestamp to active offer availability.
* **Availability & Durability:** Firestore Enterprise regional multi-zone SLA (99.99%), Point-in-Time Recovery (PITR) enabled with 7-day continuous backup retention.
* **Consistency Model:** Strong consistency across primary keys and secondary indexes on read/write within the region.

---

## 2. Data Modeling & Collection Hierarchy Architecture

Designing the collection hierarchy requires balancing low-latency mobile client snapshot listeners, backend cross-customer agent consumption, index maintenance overhead, and security rule enforcement.

### Structural Comparison Matrix

| Dimension | Option A: Subcollections (`customers/{customerId}/...`) | Option B: Root Collections (`customer_sessions`, `loyalty_offers`) |
| :--- | :--- | :--- |
| **Mobile Client Snapshot Listeners** | **Optimal:** Listeners attach directly to `customers/{uid}/offers`. Scoping is strictly local to the user path. Zero chance of cross-tenant leakage. | **High Performance:** Listeners query `loyalty_offers.where('customerId', '==', uid)`. Requires composite index on `(customerId, status, createdAt)`. |
| **Autonomous Agent Cross-Customer Queries** | **Complex:** Requires Collection Group queries (`collectionGroup('sessions')`). Incurs collection group index storage, global query overhead, and naming collision risks. | **Optimal:** Direct collection query `customer_sessions.where('agentProcessingStatus', '==', 'PENDING')`. High throughput, simple cursor pagination. |
| **Dataflow CDC & BigQuery Integration** | **Complex:** Document paths vary dynamically; CDC transforms must parse parent path hierarchies to extract customer context. | **Optimal:** Flat root collections streamline BigQuery CDC tables (`orders_cdc`, `sessions_cdc`, `offers_cdc`) with uniform schema extraction. |
| **Security Rules (`firestore.rules`)** | **Path-Based:** `request.auth.uid == customerId` evaluated instantly with zero document field inspection. | **Resource-Based:** Evaluates `request.auth.uid == resource.data.customerId`. Client queries must include matching `where` filter. |
| **Batch Expiration & Maintenance** | Requires Collection Group queries and distributed cross-partition scans to find expired vouchers across all customers. | Straightforward collection-level scan on `status == 'ACTIVE' AND validUntil < NOW()` or native Firestore TTL. |

### Architectural Recommendation: Hybrid Decoupled Model

1. **Customer Sessions Collection:** Root collection `customer_sessions` (Document ID: `sess_{UUIDv7}`)
   * *Rationale:* Sessions are ephemeral event records ingested by identity/auth microservices and primarily consumed by global backend systems (the Autonomous Agent and security anomaly detection). A root collection avoids Collection Group query overhead, provides uniform keyspace distribution, and simplifies native TTL deletion.
2. **Loyalty Offers Collection:** Root collection `loyalty_offers` (Document ID: `off_{sessionId}_retention`)
   * *Rationale:* Offers represent financial liabilities (discount codes, free shipping vouchers) requiring cross-customer analytics, batch redemption reconciliation, fraud monitoring, and real-time streaming into BigQuery CDC. Mobile clients access their offers via parameterized indexed queries protected by strict Firestore Security Rules.

---

## 3. Production JSON Schemas & Type Specifications

### 3.1. Customer Session Document
* **Collection:** `customer_sessions`
* **Document ID Pattern:** `sess_{timestamp_millis}_{unique_entropy}` (or UUIDv7)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "CustomerSessionDocument",
  "type": "object",
  "required": [
    "sessionId",
    "customerId",
    "deviceInfo",
    "loginTimestamp",
    "status",
    "agentProcessingStatus",
    "expireAt"
  ],
  "properties": {
    "sessionId": {
      "type": "string",
      "pattern": "^sess_[a-zA-Z0-9_-]+$",
      "description": "Unique session identifier generated at login."
    },
    "customerId": {
      "type": "string",
      "pattern": "^cust_retail_[0-9]{5}$",
      "description": "Canonical customer identifier linking to customer profile."
    },
    "deviceInfo": {
      "type": "object",
      "required": ["deviceType", "platform", "appVersion", "ipAddress"],
      "properties": {
        "deviceType": { "type": "string", "enum": ["MOBILE", "TABLET", "DESKTOP", "WEB"] },
        "platform": { "type": "string", "enum": ["IOS", "ANDROID", "WEB", "MACOS", "WINDOWS"] },
        "osVersion": { "type": "string" },
        "appVersion": { "type": "string" },
        "ipAddress": { "type": "string" },
        "userAgent": { "type": "string" },
        "geoCity": { "type": "string" },
        "geoCountry": { "type": "string" }
      }
    },
    "loginTimestamp": {
      "type": "string",
      "format": "date-time",
      "description": "Firestore Timestamp recording the login event."
    },
    "status": {
      "type": "string",
      "enum": ["ACTIVE", "TERMINATED", "EXPIRED"],
      "description": "Lifecycle state of the user login session."
    },
    "agentProcessingStatus": {
      "type": "string",
      "enum": ["PENDING", "PROCESSING", "PROCESSED", "SKIPPED"],
      "description": "State of Autonomous Churn Evaluation Agent workflow."
    },
    "processedAt": {
      "type": ["string", "null"],
      "format": "date-time",
      "description": "Firestore Timestamp when agent completed churn evaluation."
    },
    "agentWorkerId": {
      "type": ["string", "null"],
      "description": "Worker instance ID claiming the session processing lease."
    },
    "processingLeaseUntil": {
      "type": ["string", "null"],
      "format": "date-time",
      "description": "Lease expiration timestamp to prevent zombie worker lockouts."
    },
    "offerId": {
      "type": ["string", "null"],
      "description": "Associated loyalty offer ID if retention offer was generated."
    },
    "skipReason": {
      "type": ["string", "null"],
      "enum": ["LOW_CHURN_RISK", "COOLDOWN_ACTIVE", "NOT_LOYALTY_MEMBER", "RECENT_PURCHASE"],
      "description": "Detailed reasoning when agentProcessingStatus is SKIPPED."
    },
    "expireAt": {
      "type": "string",
      "format": "date-time",
      "description": "Calculated TTL timestamp (e.g. loginTimestamp + 30 days) for automatic Firestore document purging."
    }
  }
}
```

#### Session Document Example
```json
{
  "sessionId": "sess_01J6K8M9V4X8N2P1Q7R5T9Y3AB",
  "customerId": "cust_retail_04821",
  "deviceInfo": {
    "deviceType": "MOBILE",
    "platform": "IOS",
    "osVersion": "17.5.1",
    "appVersion": "4.12.0",
    "ipAddress": "194.109.22.45",
    "userAgent": "RedwoodRetail/4.12.0 (iPhone; iOS 17.5.1; Scale/3.00)",
    "geoCity": "Amsterdam",
    "geoCountry": "NL"
  },
  "loginTimestamp": "2026-09-03T03:25:00.000Z",
  "status": "ACTIVE",
  "agentProcessingStatus": "PROCESSED",
  "processedAt": "2026-09-03T03:25:00.650Z",
  "agentWorkerId": "retention-agent-worker-europe-west4-a-01",
  "processingLeaseUntil": "2026-09-03T03:25:30.000Z",
  "offerId": "off_sess_01J6K8M9V4X8N2P1Q7R5T9Y3AB_retention",
  "skipReason": null,
  "expireAt": "2026-10-03T03:25:00.000Z"
}
```

---

### 3.2. Loyalty Offer Document
* **Collection:** `loyalty_offers`
* **Document ID Pattern:** `off_{sessionId}_retention` (Guarantees idempotency per session)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "LoyaltyOfferDocument",
  "type": "object",
  "required": [
    "offerId",
    "customerId",
    "sessionId",
    "churnProbability",
    "churnRiskTier",
    "title",
    "description",
    "promoCode",
    "discountPercent",
    "freeExpressShipping",
    "validUntil",
    "status",
    "createdAt",
    "metadata",
    "ttlExpiryAt"
  ],
  "properties": {
    "offerId": {
      "type": "string",
      "pattern": "^off_[a-zA-Z0-9_-]+$",
      "description": "Unique deterministic offer identifier."
    },
    "customerId": {
      "type": "string",
      "pattern": "^cust_retail_[0-9]{5}$",
      "description": "Customer recipient identifier."
    },
    "sessionId": {
      "type": "string",
      "description": "Originating login session that triggered this offer."
    },
    "churnProbability": {
      "type": "number",
      "minimum": 0.0,
      "maximum": 1.0,
      "description": "Predicted churn probability from BigQuery ML logistic regression model."
    },
    "churnRiskTier": {
      "type": "string",
      "enum": ["CRITICAL", "HIGH", "MODERATE", "LOW"],
      "description": "Categorical risk bracket determining retention benefit."
    },
    "title": {
      "type": "string",
      "description": "Customer-facing promotional headline."
    },
    "description": {
      "type": "string",
      "description": "Customer-facing terms and motivational copy."
    },
    "promoCode": {
      "type": "string",
      "description": "Alphanumeric coupon code for checkout redemption."
    },
    "discountPercent": {
      "type": "integer",
      "minimum": 0,
      "maximum": 100,
      "description": "Percentage discount applied to order subtotal."
    },
    "freeExpressShipping": {
      "type": "boolean",
      "description": "Indicates whether order qualifies for complimentary express shipping."
    },
    "validUntil": {
      "type": "string",
      "format": "date-time",
      "description": "Business expiration timestamp of the offer."
    },
    "status": {
      "type": "string",
      "enum": ["ACTIVE", "REDEEMED", "EXPIRED", "CANCELLED"],
      "description": "Current lifecycle status of the voucher."
    },
    "createdAt": {
      "type": "string",
      "format": "date-time",
      "description": "Firestore Timestamp recording offer issuance."
    },
    "claimedAt": {
      "type": ["string", "null"],
      "format": "date-time",
      "description": "Firestore Timestamp when offer was redeemed at checkout."
    },
    "metadata": {
      "type": "object",
      "required": ["mlModelVersion", "retentionAction", "historicalSpend90d"],
      "properties": {
        "mlModelVersion": { "type": "string" },
        "retentionAction": { "type": "string" },
        "historicalSpend90d": { "type": "number" },
        "daysSinceLastPurchase": { "type": "integer" },
        "cartAbandonmentCount": { "type": "integer" },
        "supportSentimentScore": { "type": "number" },
        "cooldownBypass": { "type": "boolean" }
      }
    },
    "ttlExpiryAt": {
      "type": "string",
      "format": "date-time",
      "description": "Physical TTL purge timestamp (validUntil + 90 days) for auditing retention."
    }
  }
}
```

#### Loyalty Offer Document Example
```json
{
  "offerId": "off_sess_01J6K8M9V4X8N2P1Q7R5T9Y3AB_retention",
  "customerId": "cust_retail_04821",
  "sessionId": "sess_01J6K8M9V4X8N2P1Q7R5T9Y3AB",
  "churnProbability": 0.8145,
  "churnRiskTier": "CRITICAL",
  "title": "Exclusive VIP Reward: 25% Off + Express Delivery",
  "description": "We noticed you have not shopped with us lately! Enjoy 25% off your next order plus complimentary priority delivery.",
  "promoCode": "STAYVIP25-04821",
  "discountPercent": 25,
  "freeExpressShipping": true,
  "validUntil": "2026-09-10T03:25:00.000Z",
  "status": "ACTIVE",
  "createdAt": "2026-09-03T03:25:00.650Z",
  "claimedAt": null,
  "metadata": {
    "mlModelVersion": "redwood_retail.customer_churn_model_v1",
    "retentionAction": "CRITICAL_RETENTION_VOUCHER_DISPATCH",
    "historicalSpend90d": 1420.50,
    "daysSinceLastPurchase": 72,
    "cartAbandonmentCount": 4,
    "supportSentimentScore": -0.42,
    "cooldownBypass": false
  },
  "ttlExpiryAt": "2026-12-09T03:25:00.000Z"
}
```

---

## 4. Firestore Index Strategy & Native TTL Policies

### 4.1. Single-Field Index Exemptions (Storage & Write Optimization)
Firestore Enterprise automatically generates single-field ascending and descending indexes for every root scalar field and map field. On high-volume documents, this produces significant write amplification and unnecessary mutation costs.

* **Exemptions for `customer_sessions`:**
  * Exclude `deviceInfo` (entire submap): Prevents indexing `userAgent`, `ipAddress`, and variable hardware strings.
  * Exclude `skipReason`.
* **Exemptions for `loyalty_offers`:**
  * Exclude `description` (long unstructured text).
  * Exclude `metadata` (nested feature maps).

### 4.2. Required Composite Indexes

```
1. Mobile Active Offers Listener:
   Collection: loyalty_offers
   Query: customerId == $uid AND status == 'ACTIVE' ORDER BY createdAt DESC
   Fields:
     - customerId: ASCENDING
     - status: ASCENDING
     - createdAt: DESCENDING
   Scope: COLLECTION

2. Autonomous Retention Agent Work Queue:
   Collection: customer_sessions
   Query: agentProcessingStatus == 'PENDING' ORDER BY loginTimestamp ASC
   Fields:
     - agentProcessingStatus: ASCENDING
     - loginTimestamp: ASCENDING
   Scope: COLLECTION

3. Zombie Lease Recovery Scanner:
   Collection: customer_sessions
   Query: agentProcessingStatus == 'PROCESSING' AND processingLeaseUntil < $now
   Fields:
     - agentProcessingStatus: ASCENDING
     - processingLeaseUntil: ASCENDING
   Scope: COLLECTION

4. Batch Promo Expiration Worker:
   Collection: loyalty_offers
   Query: status == 'ACTIVE' AND validUntil < $now
   Fields:
     - status: ASCENDING
     - validUntil: ASCENDING
   Scope: COLLECTION

5. Promo Code Checkout Lookup:
   Collection: loyalty_offers
   Query: promoCode == $code AND status == 'ACTIVE'
   Fields:
     - promoCode: ASCENDING
     - status: ASCENDING
   Scope: COLLECTION
```

### 4.3. Terraform Infrastructure as Code Configuration

Add the following index declarations to `terraform/firestore.tf`:

```hcl
# Composite Index: Mobile Active Offers Real-Time Query
resource "google_firestore_index" "loyalty_offers_active_user_idx" {
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

# Composite Index: Agent Pending Sessions Listener
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

# Composite Index: Batch Expiration Reaping Worker
resource "google_firestore_index" "loyalty_offers_expiry_sweep_idx" {
  project    = var.project_id
  database   = var.firestore_database_id
  collection = "loyalty_offers"

  fields {
    field_path = "status"
    order      = "ASCENDING"
  }
  fields {
    field_path = "validUntil"
    order      = "ASCENDING"
  }
}
```

### 4.4. Native Time-To-Live (TTL) Policy Configuration
Firestore Enterprise provides native, zero-cost TTL document deletion. Background deletion workers purge expired documents within 72 hours of expiration without consuming document write operations or write IOPS quota.

```bash
# Enable TTL on customer_sessions using the 'expireAt' timestamp field
gcloud firestore fields ttls update expireAt \
  --collection-group=customer_sessions \
  --database=redwood \
  --project=${GCP_PROJECT_ID} \
  --enable-ttl

# Enable TTL on loyalty_offers using the 'ttlExpiryAt' timestamp field
gcloud firestore fields ttls update ttlExpiryAt \
  --collection-group=loyalty_offers \
  --database=redwood \
  --project=${GCP_PROJECT_ID} \
  --enable-ttl
```

---

## 5. Real-Time Autonomous Agent Listener Architecture

The Autonomous Retention Agent monitors incoming customer sessions using Firestore Native **Watch Streams** (`on_snapshot`). Watch streams utilize HTTP/2 persistent gRPC bidirectional streaming, pushing document mutations to the agent in `< 50ms` without repetitive polling queries.

### 5.1. Resilience, Reconnection Backoff & Cursor Resumption
* **Connection Lifecycle:** gRPC channels terminate periodically (load balancer rebalancing, token refresh, transient network blips). The client must intercept stream closures without terminating the process.
* **Cursor-Based Resumption:** When reconnecting, rather than replaying all documents matching `agentProcessingStatus == 'PENDING'`, the agent resumes from the `last_processed_timestamp` using `start_after(last_snapshot)`. This prevents stream churn and burst re-evaluations.
* **Truncated Exponential Backoff with Jitter:** Upon error, reconnection waits:
  `delay = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** attempt)) * random.uniform(0.8, 1.2)`

### 5.2. Production Agent Listener Implementation

```python
"""
Autonomous Churn Retention Agent: Real-Time Firestore Listener
Watches customer_sessions collection for PENDING login events using gRPC streaming.
"""

import os
import sys
import time
import random
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from google.cloud import firestore
from google.cloud.firestore_v1.watch import Watch
from google.api_core.exceptions import GoogleAPICallError, RetryError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RetentionAgentListener")

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "redwood")
WORKER_ID = f"agent-worker-{os.uname().nodename}-{os.getpid()}"

BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 32.0


class SessionRetentionWatcher:
    def __init__(self, db: firestore.Client):
        self.db = db
        self.sessions_col = db.collection("customer_sessions")
        self.last_cursor: Optional[firestore.DocumentSnapshot] = None
        self.is_running = True

    def build_query(self) -> firestore.Query:
        """Constructs the pending session query with cursor resumption."""
        q = (
            self.sessions_col.where("agentProcessingStatus", "==", "PENDING")
            .order_by("loginTimestamp", direction=firestore.Query.ASCENDING)
            .limit(50)
        )
        if self.last_cursor:
            q = q.start_after(self.last_cursor)
        return q

    def on_snapshot(self, col_snapshot, changes, read_time):
        """Callback triggered instantaneously upon Firestore document mutation."""
        for change in changes:
            # Process newly added documents or transitions into PENDING
            if change.type.name in ("ADDED", "MODIFIED"):
                doc = change.document
                data = doc.to_dict()
                if data.get("agentProcessingStatus") == "PENDING":
                    logger.info(f"Detected PENDING session: {doc.id} (Customer: {data.get('customerId')})")
                    self.process_session_safe(doc)
                    self.last_cursor = doc

    def process_session_safe(self, doc: firestore.DocumentSnapshot):
        """Executes transactional acquisition and retention processing."""
        from agent_retention_processor import evaluate_and_generate_offer_transaction
        try:
            evaluate_and_generate_offer_transaction(self.db, doc.id, WORKER_ID)
        except Exception as e:
            logger.error(f"Failed processing session {doc.id}: {str(e)}", exc_info=True)

    def start_resilient_watch(self):
        """Maintains persistent streaming watch with exponential backoff and jitter."""
        attempt = 0
        while self.is_running:
            try:
                query = self.build_query()
                logger.info(f"Opening gRPC Watch stream on 'customer_sessions' (Attempt: {attempt + 1})...")
                
                # Attach real-time snapshot listener
                watch_unsubscribe = query.on_snapshot(self.on_snapshot)
                attempt = 0  # Reset backoff on successful connection
                
                # Keep main thread alive while background gRPC stream processes events
                while self.is_running:
                    time.sleep(1.0)

            except (GoogleAPICallError, RetryError, Exception) as stream_err:
                attempt += 1
                backoff = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                jittered_delay = backoff * random.uniform(0.75, 1.25)
                logger.warning(
                    f"Watch stream interrupted: {stream_err}. Reconnecting in {jittered_delay:.2f}s..."
                )
                time.sleep(jittered_delay)
            finally:
                try:
                    watch_unsubscribe()
                except Exception:
                    pass
```

---

## 6. Concurrency, ACID Transactions & Idempotency Controls

### 6.1. Concurrency Risks & Race Conditions
1. **Worker Contention:** Multiple scaled instances of the Autonomous Agent observe the same `PENDING` session document and attempt simultaneous processing.
2. **Duplicate Offer Generation:** A customer logging in across multiple devices (mobile app and desktop browser simultaneously) generates two concurrent login sessions, triggering duplicate loyalty offers.
3. **Ghost Processing:** An agent marks a session `PROCESSED`, but a network failure drops the offer creation, leaving the customer without their voucher.

### 6.2. Why Firestore Transactions Over Write Batches
* **Write Batches:** Non-atomic reads. Batches execute blind writes without verifying prior document states. If two workers write simultaneously, the last write silently overwrites the first.
* **Firestore Transactions:** Provide **Optimistic Concurrency Control (OCC)** with Strict Serializability. All reads occur prior to writes. If any document read is mutated by another client before the commit phase, Firestore automatically aborts and retries the transaction.

### 6.3. Idempotent Offer Generation Flow
1. **Deterministic Document ID:** The offer ID is derived deterministically from the session ID:  
   `offerId = f"off_{sessionId}_retention"`
2. **Read Phase (Isolation Verification):**
   * Read `customer_sessions/{sessionId}`. Verify `agentProcessingStatus` is strictly `PENDING`. If already claimed or processed, abort immediately.
   * Read `customers/{customerId}` (Customer Account Profile). Inspect `lastRetentionOfferAt`. If an offer was issued within the cooldown period (e.g. 7 days), transition session to `SKIPPED` with `skipReason = 'COOLDOWN_ACTIVE'` and commit.
3. **Write Phase (Atomic Dual-Write):**
   * Create `loyalty_offers/{offerId}` with `status = 'ACTIVE'`.
   * Update `customer_sessions/{sessionId}` with `agentProcessingStatus = 'PROCESSED'`, `offerId = offerId`, and `processedAt = firestore.SERVER_TIMESTAMP`.
   * Update `customers/{customerId}` setting `lastRetentionOfferAt = firestore.SERVER_TIMESTAMP`.

### 6.4. Production Transaction Implementation

```python
"""
ACID Transaction Implementation for Idempotent Retention Offer Issuance.
"""

from datetime import datetime, timezone, timedelta
from google.cloud import firestore
from google.api_core.exceptions import GoogleAPICallError

RETENTION_COOLDOWN_DAYS = 7


def determine_retention_package(churn_prob: float):
    """Maps churn risk tier to retention offer terms."""
    if churn_prob >= 0.75:
        return {
            "tier": "CRITICAL",
            "title": "Exclusive VIP Reward: 25% Off + Express Delivery",
            "description": "We value your relationship! Enjoy 25% off your next order plus free express shipping.",
            "discount": 25,
            "freeShipping": True,
            "promoPrefix": "STAYVIP25"
        }
    elif churn_prob >= 0.50:
        return {
            "tier": "HIGH",
            "title": "Special Comeback Gift: Free Express Shipping",
            "description": "Here is a voucher for complimentary express delivery on your upcoming order.",
            "discount": 0,
            "freeShipping": True,
            "promoPrefix": "FREESHIP"
        }
    elif churn_prob >= 0.25:
        return {
            "tier": "MODERATE",
            "title": "Welcome Back: 10% Off Your Cart",
            "description": "Save 10% on all catalog items this week.",
            "discount": 10,
            "freeShipping": False,
            "promoPrefix": "WELCOME10"
        }
    return None


@firestore.transactional
def execute_retention_transaction(
    transaction: firestore.Transaction,
    db: firestore.Client,
    session_ref: firestore.DocumentReference,
    customer_ref: firestore.DocumentReference,
    worker_id: str
) -> dict:
    """
    Executes atomic read-verify-write transaction to prevent duplicate offers.
    """
    now = datetime.now(timezone.utc)
    
    # -------------------------------------------------------------------------
    # 1. TRANSACTION READ PHASE
    # -------------------------------------------------------------------------
    session_snapshot = session_ref.get(transaction=transaction)
    if not session_snapshot.exists:
        raise ValueError("Session document does not exist.")
    
    session_data = session_snapshot.to_dict()
    if session_data.get("agentProcessingStatus") != "PENDING":
        return {"status": "SKIPPED", "reason": "ALREADY_PROCESSED_OR_LEASED"}
    
    customer_id = session_data["customerId"]
    session_id = session_snapshot.id

    # Check Customer Retention Cooldown
    customer_snapshot = customer_ref.get(transaction=transaction)
    if customer_snapshot.exists:
        customer_data = customer_snapshot.to_dict()
        last_offer_ts = customer_data.get("lastRetentionOfferAt")
        if last_offer_ts:
            last_offer_dt = last_offer_ts if isinstance(last_offer_ts, datetime) else last_offer_ts.to_datetime()
            if (now - last_offer_dt) < timedelta(days=RETENTION_COOLDOWN_DAYS):
                # Mark session as SKIPPED within transaction
                transaction.update(session_ref, {
                    "agentProcessingStatus": "SKIPPED",
                    "skipReason": "COOLDOWN_ACTIVE",
                    "processedAt": firestore.SERVER_TIMESTAMP,
                    "agentWorkerId": worker_id
                })
                return {"status": "SKIPPED", "reason": "COOLDOWN_ACTIVE"}

    # -------------------------------------------------------------------------
    # 2. EVALUATE CHURN INFERENCE (Mocked / Cached BQML Feature Vector)
    # -------------------------------------------------------------------------
    # In production, features are extracted from session_data or Redis cache
    mock_churn_prob = session_data.get("simulatedChurnProbability", 0.7825)
    package = determine_retention_package(mock_churn_prob)
    
    if not package:
        transaction.update(session_ref, {
            "agentProcessingStatus": "SKIPPED",
            "skipReason": "LOW_CHURN_RISK",
            "processedAt": firestore.SERVER_TIMESTAMP,
            "agentWorkerId": worker_id
        })
        return {"status": "SKIPPED", "reason": "LOW_CHURN_RISK"}

    # -------------------------------------------------------------------------
    # 3. TRANSACTION WRITE PHASE
    # -------------------------------------------------------------------------
    deterministic_offer_id = f"off_{session_id}_retention"
    offer_ref = db.collection("loyalty_offers").document(deterministic_offer_id)
    
    valid_until_ts = now + timedelta(days=7)
    ttl_purge_ts = valid_until_ts + timedelta(days=90)
    customer_num = customer_id.split("_")[-1]

    offer_payload = {
      "offerId": deterministic_offer_id,
      "customerId": customer_id,
      "sessionId": session_id,
      "churnProbability": round(mock_churn_prob, 4),
      "churnRiskTier": package["tier"],
      "title": package["title"],
      "description": package["description"],
      "promoCode": f"{package['promoPrefix']}-{customer_num}",
      "discountPercent": package["discount"],
      "freeExpressShipping": package["freeShipping"],
      "validUntil": valid_until_ts,
      "status": "ACTIVE",
      "createdAt": firestore.SERVER_TIMESTAMP,
      "claimedAt": None,
      "metadata": {
          "mlModelVersion": "redwood_retail.customer_churn_model_v1",
          "retentionAction": f"{package['tier']}_RETENTION_OFFER",
          "historicalSpend90d": session_data.get("totalSpend90d", 850.0),
          "cooldownBypass": False
      },
      "ttlExpiryAt": ttl_purge_ts
    }

    # 1. Create Offer Document
    transaction.set(offer_ref, offer_payload)

    # 2. Update Session Document
    transaction.update(session_ref, {
      "agentProcessingStatus": "PROCESSED",
      "processedAt": firestore.SERVER_TIMESTAMP,
      "offerId": deterministic_offer_id,
      "agentWorkerId": worker_id
    })

    # 3. Update Customer Cooldown State
    transaction.set(customer_ref, {
      "lastRetentionOfferAt": firestore.SERVER_TIMESTAMP,
      "lastActiveOfferId": deterministic_offer_id
    }, merge=True)

    return {"status": "CREATED", "offerId": deterministic_offer_id}
```

---

## 7. Mobile Client Security Rules (`firestore.rules`)

To ensure multi-tenant isolation, mobile and web applications must interact with Firestore under least-privilege security rules. Clients are strictly prohibited from writing offers or modifying session processing statuses.

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // Helper functions
    function isAuthenticated() {
      return request.auth != null;
    }

    function isOwner(customerId) {
      return isAuthenticated() && request.auth.uid == customerId;
    }

    // 1. Customer Sessions: Mobile client can create its own session, but cannot alter agent state
    match /customer_sessions/{sessionId} {
      allow read: if isAuthenticated() && resource.data.customerId == request.auth.uid;
      
      // Client may record login session during authentication
      allow create: if isAuthenticated() 
        && request.resource.data.customerId == request.auth.uid
        && request.resource.data.status == 'ACTIVE'
        && request.resource.data.agentProcessingStatus == 'PENDING';

      // Updates strictly forbidden to mobile clients; handled via IAM Service Account
      allow update, delete: if false;
    }

    // 2. Loyalty Offers: Client has read-only access to their own active offers
    match /loyalty_offers/{offerId} {
      allow read: if isAuthenticated() && resource.data.customerId == request.auth.uid;
      
      // Zero client write/update permission. Offers are issued and redeemed by backend services.
      allow write: if false;
    }

    // 3. Customer Profile: Client reads self, updates basic profile fields
    match /customers/{customerId} {
      allow read: if isOwner(customerId);
      allow update: if isOwner(customerId) 
        && !request.resource.data.diff(resource.data).affectedKeys().hasAny(['lastRetentionOfferAt', 'lastActiveOfferId']);
      allow create, delete: if false;
    }
  }
}
```

---

## 8. Operational Risk & Mitigation Matrix

| Potential Risk | Root Cause | Impact | Database CE Mitigation Strategy |
| :--- | :--- | :--- | :--- |
| **Write Hotspotting on Sessions** | Sequential document IDs (e.g. integer or simple timestamp keys) routed to the same Tablet/Spanner split. | Elevated write latency, write throttling (503 Service Unavailable). | Use UUIDv7, reverse timestamps, or high-entropy suffixes (`sess_01J6K8...`) to ensure even hash distribution across internal storage partitions. |
| **Watch Stream Reconnection Storms** | Regional network hiccup causes dozens of agent pods to reconnect simultaneously. | Backend gRPC connection spikes, resource starvation. | Enforce full jitter on exponential backoff and stagger listener initialization across agent worker instances. |
| **BigQuery ML Inference Latency Bottlenecks** | Agent invoking synchronous BigQuery ML queries (`ML.PREDICT`) inside the real-time login loop. | Adds 2–5 seconds of latency to session evaluation, violating the < 800ms SLA. | **Decoupled Scoring:** Batch-predict churn probabilities nightly or hourly into a low-latency cache or customer profile document; agent reads the cached churn score in < 10ms during the live login transaction. |
| **Unbounded Collection Growth** | Ephemeral login sessions and expired promo codes accumulating millions of documents over time. | Higher storage costs, slower administrative backups, degraded query performance. | Enable Firestore Native TTL on `expireAt` (30 days) and `ttlExpiryAt` (90 days post-validity) for automated zero-cost background reaping. |
| **Transaction Contention on Hot Customers** | Rapid successive login events for a single customer account triggering parallel transactions. | Firestore `Aborted` exceptions due to lock contention on the `customers/{customerId}` document. | Use deterministic `offerId` derived from `sessionId` and enforce exponential backoff retries with client-side deduplication. |

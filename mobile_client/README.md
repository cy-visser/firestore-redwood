# Redwood Retail Mobile Client

An interactive mobile retail application client that connects directly to **Google Cloud Firestore Enterprise Native** (database: `redwood`). Customers sign in as one of two demo principals, browse the hardware catalog, manage cart and logistics, submit satisfaction feedback, and place orders that generate the **exact same JSON schema** as the dataset seeder.

Signing in writes a `customer_sessions` document, which triggers the loyalty agent pipeline. Every submitted order is replicated to BigQuery (`redwood_retail.retail_cdc`) within seconds by the Eventarc-driven CDC service, and powers the BigQuery churn model. See [docs/architecture.md](../docs/architecture.md).

The same backend also serves the **Redwood Console**, an operator view of the pipeline for driving demos.

---

## 1. Key Features

- **Login-gated session flow**:
  - The app opens on a login screen. There is no identity switcher any more, because switching identity mid-session would leave an orphaned session document behind.
  - `POST /api/session/login` resolves the principal to a customer id, reuses an in-flight session if one already exists (so a double-tap cannot race two sessions), and otherwise writes `customer_sessions/{id}` with `agentProcessingStatus: PENDING`.
  - Reuse is bounded: only a session started within the last **120 seconds** counts, and the freshest one wins. Without that bound a session the agent had silently abandoned would be reused forever and no login would ever trigger the pipeline again. Tune it with `SESSION_REUSE_MAX_AGE_SECONDS`; a missing or nonsensical value falls back to 120.
  - That write is what Eventarc picks up, so login is the demo's trigger.
- **Live offer delivery over SSE**:
  - The client subscribes to `GET /api/stream/session/{sessionId}` and watches the session document move `PENDING → PROCESSING → PROCESSED`.
  - When the loyalty agent writes `loyalty_offers/{offerId}`, the offer sheet slides up in the app. The customer can claim it, which writes back through `POST /api/offers/{offerId}/claim`.
  - The client reports `clientAckMs` and `offerVisibleMs` back over `POST /api/telemetry/{sessionId}` so the console can show perceived latency.
- **The agent sets the price, and nothing else does**:
  - There is no tier discount. `principals[*].discountRate` is `0.0` for both demo identities; an order is at list price unless the agent decided otherwise.
  - `POST /api/orders/preview` and `/submit` take an `offerId` (or find the customer's unspent offer themselves), load the offer document server-side, and take `discountPercent` and `freeExpressShipping` from it. The browser sends an id, never a price.
  - An `offerId` that fails validation — wrong customer, expired, already attached to an order — is ignored and the order is placed at list price. Checkout never fails because of an offer.
  - Every order carries a `loyaltyOffer` block recording `offerApplied`, `offerId`, `promoCode`, `discountPercent` and `freeExpressShipping`, so the discount on the document can always be traced back to the agent decision that caused it.
- **Two dedicated IAM principals**:
  - **`demo1` (`demo1-user@elevate-cyvisser.iam.gserviceaccount.com`)** → customer `cust_demo1`, Meridian Industrial Supply. Enterprise VIP member, high spend profile, low churn risk.
  - **`demo2` (`demo2-user@elevate-cyvisser.iam.gserviceaccount.com`)** → customer `cust_demo2`. Standard Loyalty member, moderate spend, active complaint risk testing, high churn risk.
- **JSON schema parity with the seeder**:
  - Matches all nested subsections and value types produced by the seeder's `order_factory.build_order`.
  - The only top-level addition is `loyaltyOffer`, which the seeder cannot write because it has no retention agent. `test_schema_parity.py` pins this: seeder keys ∪ `{loyaltyOffer}`, and byte-for-byte parity inside every subsection. No BigQuery column changes, because `cdc_service/schemas.py` carries unmapped fields in `document_data`.
  - Implements the complete **4-Pillars BigQuery ML Feature Matrix**: Demographics, Transactional Metrics, App Engagement, and Customer Support Satisfaction.
  - Sets `"metadata.sourcePlatform": "CUSTOM_MOBILE_APP"` and `"customerFeedback.channel": "MOBILE_APP"`.
- **Redwood Console** (`/console.html`):
  - **Pipeline panel** — per-session timing for each hop, derived from Firestore commit timestamps.
  - **Documents panel** — the live session and offer documents as they are written.
  - **Churn panel** — current BigQuery churn scores, with an optional recalculate button.
  - **Event log** — a running feed of everything the console observed.

---

## 2. Architecture Diagram

```
+-------------------------------------------------------------------------+
|             Mobile Client (Frontend)      |      Redwood Console        |
|   React 19 + Tailwind + Lucide            |   Pipeline / Docs / Churn   |
|   Login -> Catalog -> Cart -> Offer sheet |   Event log                 |
+-------------------------------------------------------------------------+
            |  REST for writes,  Server-Sent Events for reads
            v
+-------------------------------------------------------------------------+
|                       FastAPI Backend Engine                            |
|   - session_engine.py  (login, session lifecycle, offer claim)          |
|   - sse.py             (Firestore on_snapshot -> SSE bridge)            |
|   - console_service.py (operator controls, telemetry, churn)            |
|   - order_engine.py    (schema parity with the dataset seeder)          |
|   - firestore_auth.py  (Google Cloud IAM / ADC client)                  |
+-------------------------------------------------------------------------+
            |
            v
+-------------------------------------------------------------------------+
|               Google Cloud Firestore Enterprise Native                  |
|        Project: elevate-cyvisser | Database: redwood                    |
|        Collections: retail | customer_sessions | loyalty_offers         |
+-------------------------------------------------------------------------+
        |                                          |
        | retail writes                            | customer_sessions writes
        v                                          v
+---------------------------+      +--------------------------------------+
| Eventarc -> CDC service   |      | Eventarc -> agent_bridge (Cloud Run)  |
|         -> BigQuery       |      |          -> Agent Engine LoyaltyAgent |
+---------------------------+      +--------------------------------------+
        |                                          |
        v                                          v
+---------------------------+      +--------------------------------------+
|        BigQuery           |----->| Writes loyalty_offers/{offerId} and   |
| redwood_retail.retail_cdc |churn | marks the session PROCESSED/SKIPPED   |
+---------------------------+      +--------------------------------------+
```

---

## 3. Quick Start

### Deployed (Cloud Run)

`./deploy.sh` builds this directory into the `redwood-app` Cloud Run service and
finishes by opening a proxy to it, so there is nothing to start by hand. The
service is not public — access is `roles/run.invoker` granted to named
principals — so it is reached through the proxy rather than its `run.app` URL:

```bash
python3 scripts/run_proxy.py --url "${APP_URL}" --port 8080
```

| Surface | URL |
| --- | --- |
| Mobile app | `http://localhost:8080` |
| Redwood Console | `http://localhost:8080/console` |
| Interactive API docs (Swagger) | `http://localhost:8080/docs` |

One container serves both: the Vite bundles are built into the image and
FastAPI serves them alongside `/api`. The service is pinned to a single
always-on instance, because the console's SSE fan-out and telemetry live in
process memory and its Firestore listeners fire between requests.

### Local (frontend development)

For hot reload, the launcher runs the same backend on this machine:

```bash
./mobile_client/start_mobile_app.sh
```

| Surface | URL |
| --- | --- |
| Mobile app (Vite dev server, hot reload) | `http://localhost:5174` |
| Redwood Console | `http://localhost:5174/console.html` |
| Production unified SPA & API (FastAPI) | `http://localhost:8087` |
| Interactive API docs (Swagger) | `http://localhost:8087/docs` |

Ports are probed rather than fixed, so the ones printed by the script win over
the ones above. Everything else still runs in the cloud: the launcher reads
`CHURN_FUNCTION_URL` out of Terraform state, so the churn buttons call the same
deployed function the Cloud Run copy does.

The script uses `firestore/redwood/.venv`, the environment `deploy.sh` provisions. Override it with `VENV_DIR=... ./mobile_client/start_mobile_app.sh` if you keep your interpreter elsewhere; it pre-flight-checks that `fastapi` and `uvicorn` import before starting anything.

### Demo controls

The console's **Recalculate Churn** and **Reset Demo** buttons call the churn
Cloud Run function and delete Firestore documents respectively. They are **on by
default**, because a demo that cannot be reset between runs is not much of a
demo. Turn them off for a shared or unattended deployment:

```bash
ENABLE_DEMO_CONTROLS=0 ./mobile_client/start_mobile_app.sh
# or, on the deployed service:
#   terraform apply -var app_enable_demo_controls=false
```

With them off the buttons grey out and the API returns `403`. The churn buttons
additionally grey out if `CHURN_FUNCTION_URL` is unset, with the panel saying
so rather than failing on the press.

**Reset Demo** does two things:

1. **Sweeps** demo `customer_sessions`, `loyalty_offers`, orders this app wrote (`orderId` prefixed `ORD-26-MOB-`), and legacy short-form-id orders. The seeded dataset is left alone — the prefix is what distinguishes app-written orders from seeded ones. This matters because the loyalty agent suppresses a new offer while an `ACTIVE` one is less than seven days old, so back-to-back runs need the slate cleared.
2. **Warms up the agent** by writing a throwaway `demo1` session with `channel: CONSOLE_WARMUP`, waiting for the agent to reach a terminal status, and then deleting the session again. Agent Engine cold-starts, and paying that cost before the audience is watching turns the first real login from tens of seconds into a few. The warm-up is best-effort: it has a 60-second timeout, it always deletes its own session, and the reset still succeeds if the agent never answers. The console's event log says which happened.

---

## 4. HTTP Surface

Beyond the catalog route, the backend exposes:

| Route | Purpose |
| --- | --- |
| `POST /api/orders/preview` | Price a cart without writing. Takes an optional `offerId` |
| `POST /api/orders/submit` | Write the order, then mark the offer spent |
| `GET /api/orders?principalId=…` | This customer's orders, newest first. `principalId` or `customerId` is required; without one the route returns `400` rather than everybody's orders |
| `GET /api/orders/{orderId}` | One order document |
| `POST /api/session/login` | Start or reuse a session for a principal |
| `GET /api/session/{sessionId}` | Fetch one session plus its offers |
| `GET /api/stream/session/{sessionId}` | SSE: session document and its offers, live |
| `POST /api/telemetry/{sessionId}` | Client-measured acknowledgement and offer-visible times |
| `POST /api/offers/{offerId}/claim` | Claim an offer (writes only the fields `firestore.rules` permits) |
| `GET /api/stream/console` | SSE: everything the console observes, fanned out in-process |
| `GET /api/console/churn` | Current BigQuery churn scores |
| `POST /api/console/recalculate-churn` | SSE: streams the churn pipeline's output (demo controls only) |
| `POST /api/console/reset` | Sweep demo documents and warm the agent (demo controls only) |

> **Index note.** `GET /api/orders` sorts on `createdAt` inside a `customerId` filter, which wants a composite index on `retail (customerId ASC, createdAt DESC)`. If it is missing the route falls back to an unordered read and sorts in process, logs the missing index, and reports `orderedByFirestore: false` in the response, so the demo still works while the index is being created.

All SSE streams send a `: keepalive` comment every 15 seconds so intermediaries do not close idle connections.

---

## 5. Testing

Automated tests live under `mobile_client/tests/`:

```bash
# From firestore/redwood. Covers schema parity, the API surface,
# and the session engine (offline, using Firestore fakes).
PYTHONPATH=. .venv/bin/python3 -m pytest mobile_client/tests -q

# Frontend type check and production build (mobile + console bundles)
cd mobile_client/frontend && npm run build
```

`./deploy.sh --run-tests` runs the same Python suite as part of the deployment wrapper.

---

## 6. Demonstration Walkthrough

1. **Open the mobile app**, and the **Redwood Console** at `/console` beside it (`/console.html` under the Vite dev server). Open the console first so it observes the claim boundary directly rather than inferring it.
2. **Press Reset Demo** in the console. This clears the previous run's sessions, offers and app-written orders, and warms the agent so the first login is fast. Wait for the event log to say the agent is warm.
3. **Sign in** as `demo1` or `demo2`. This writes the session document; watch the console's pipeline panel light up as the session goes `PENDING → PROCESSING → PROCESSED`.
4. **Receive the offer**. The loyalty agent writes `loyalty_offers/{offerId}`, the SSE stream delivers it, and the offer sheet slides up in the app. Note the discount percentage it chose — the header pill repeats it. Tap **Claim** to write the claim back.
5. **Add items**: browse the Catalog and add industrial optical sensors, edge gateways, or PLCs to your cart.
6. **Check the cart ledger**. The discount line reads *Agent retention offer (N%)* with the promo code, and `N` is the number from step 4 — not a tier rate. Sign in as the other principal with no offer and the same cart is at list price.
7. **Inspect JSON**: tap **"Inspect Generated JSON Schema"** to view the live document, including the `loyaltyOffer` block naming the offer that set the price.
8. **Place the order**. The document is saved directly into the live Firestore `retail` collection, and the offer is marked spent so it cannot discount a second order.
9. **Track real-time status**: switch to the **Orders** tab. The order you just placed is at the top — the feed is scoped to your customer and sorted newest first — and carries the agent-offer chip.
10. **Test the churn alert**: sign in as `demo2`, select a 1-star or 2-star rating, choose a complaint reason (e.g. `LATE_DELIVERY`), and place the order. Inspect the document to see `hasActiveComplaint: true` and negative sentiment scores logged for BigQuery ML.

> **Timing caveat.** The console derives each hop from Firestore commit timestamps, which all share one clock. The session's `processingStartedAt` field does **not** — the agent stamps it from its own host — so the console only falls back to it when it connected after the claim, and marks those rows approximate. Client-measured numbers come from the browser's clock and are reported on their own, never subtracted from Firestore timestamps.

"""FastAPI Server for Redwood Retail Mobile App Client."""

import os
import sys
import logging
import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# Set up paths
PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from google.cloud.firestore_v1.base_query import FieldFilter

from firestore_auth import get_firestore_native_client
from retail_catalog import (
    CATALOG_ITEMS, SHIPPING_CITIES, WAREHOUSES, CARRIERS,
    COMPLAINT_REASONS, LOYALTY_TIERS
)
from mobile_client.backend.order_engine import (
    create_order_from_cart, DEMO_PRINCIPALS, CATALOG_BY_SKU, MOBILE_ORDER_ID_PREFIX
)
from mobile_client.backend import console_service, session_engine
from mobile_client.backend.sse import Watch, stream_lines, stream_watches

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("redwood-mobile-api")

# Environment. On Cloud Run every one of these is set by Terraform. The
# project falls back to the historical literal only for local development, and
# says so: a deployed container quietly talking to somebody else's project is
# the kind of bug that is found late and explains nothing when it is.
PROJECT_ID = os.getenv("GCP_PROJECT_ID") or "elevate-cyvisser"
if not os.getenv("GCP_PROJECT_ID"):
    logger.warning(
        "GCP_PROJECT_ID is not set; falling back to '%s'. Set it in .env or on "
        "the Cloud Run service.", PROJECT_ID
    )
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "redwood")
COLLECTION_NAME = os.getenv("FIRESTORE_COLLECTION", "retail")

# Lazy-loaded Firestore client
_firestore_client = None
_bigquery_client = None


def get_db():
    global _firestore_client
    if _firestore_client is None:
        try:
            _firestore_client = get_firestore_native_client(PROJECT_ID, DATABASE_ID)
        except Exception as e:
            logger.error(f"Failed to initialize Firestore client: {e}")
            raise
    return _firestore_client


def get_bq():
    """Lazily build the BigQuery client for churn panel lookups."""
    global _bigquery_client
    if _bigquery_client is None:
        from google.cloud import bigquery

        _bigquery_client = bigquery.Client(project=PROJECT_ID)
    return _bigquery_client


app = FastAPI(
    title="Redwood Retail Mobile API",
    description="Backend API connecting the mobile retail app to Firestore Enterprise Native",
    version="2.0.0"
)

# Enable CORS for Vite and mobile access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request Models
class CartItem(BaseModel):
    sku: str
    quantity: int = Field(gt=0, default=1)
    name: Optional[str] = None
    category: Optional[str] = None
    unitPrice: Optional[float] = None
    allocatedWarehouse: Optional[str] = None


class OrderRequest(BaseModel):
    principalId: str = "demo1"
    items: List[CartItem]
    shippingAddress: Optional[Dict[str, str]] = None
    paymentMethod: str = "INVOICE_NET30"
    carrierCode: Optional[str] = None
    serviceLevel: str = "NEXT_DAY_AIR"
    feedbackRating: int = Field(ge=1, le=5, default=5)
    feedbackText: Optional[str] = None
    complaintReason: Optional[str] = None
    offerId: Optional[str] = None
    dryRun: bool = False


def resolve_offer(principal_id: str, offer_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Load the loyalty offer that prices an order, or None for list price."""
    try:
        customer_id = session_engine.resolve_customer_id(principal_id)
        return session_engine.resolve_offer_for_order(
            get_db(), customer_id, offer_id=offer_id
        )
    except Exception as exc:
        logger.warning(
            f"Offer lookup failed for {principal_id} (offerId={offer_id}): {exc}. "
            "Pricing at list."
        )
        return None


def count_prior_mobile_orders(principal_id: str) -> int:
    """How many app orders this principal's customer already has.

    Drives demo2's recovery snapshot in order_engine. Filtered on customerId
    alone (a single-field index Firestore maintains automatically) and matched
    on the document id prefix in process, because the set is one customer's
    orders and a composite index for a demo counter is not worth the deploy.
    Reset Demo deletes every ORD-26-MOB- order, so this restarts at zero.

    A failed lookup counts as zero: the order still goes through, carrying the
    struggling snapshot, which is the conservative reading.
    """
    try:
        customer_id = session_engine.resolve_customer_id(principal_id)
        docs = get_db().collection(COLLECTION_NAME).where(
            filter=FieldFilter("customerId", "==", customer_id)
        ).stream()
        return sum(1 for doc in docs if str(doc.id).startswith(MOBILE_ORDER_ID_PREFIX))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not count prior app orders for {principal_id}: {exc}")
        return 0


@app.get("/api/health")
def health_check():
    """Checks service health and Firestore database connection."""
    firestore_status = "unknown"
    doc_count_sample = 0
    try:
        db = get_db()
        # Verify collection access with a lightweight query
        docs = list(db.collection(COLLECTION_NAME).limit(1).stream())
        firestore_status = "connected"
        doc_count_sample = len(docs)
    except Exception as e:
        firestore_status = f"error: {str(e)[:100]}"

    return {
        "status": "healthy",
        "projectId": PROJECT_ID,
        "databaseId": DATABASE_ID,
        "collection": COLLECTION_NAME,
        "firestoreConnection": firestore_status,
        "sampleDocAvailable": doc_count_sample > 0
    }


@app.get("/api/principals")
def get_principals():
    """Returns metadata and profiles for demo1 and demo2 IAM principals."""
    return {
        "activePrincipals": ["demo1", "demo2"],
        "profiles": DEMO_PRINCIPALS
    }


@app.get("/api/catalog")
def get_catalog():
    """Returns the industrial hardware product catalog grouped by category."""
    categories = sorted(list({item["category"] for item in CATALOG_ITEMS}))
    return {
        "items": CATALOG_ITEMS,
        "categories": categories,
        "warehouses": WAREHOUSES,
        "carriers": CARRIERS,
        "cities": SHIPPING_CITIES,
        "complaintReasons": COMPLAINT_REASONS,
        "loyaltyTiers": LOYALTY_TIERS
    }


@app.post("/api/orders/preview")
def preview_order(req: OrderRequest):
    """
    Simulates order creation and returns the generated JSON document
    without saving to Firestore.
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="Cart cannot be empty")

    offer = resolve_offer(req.principalId, req.offerId)
    cart_dicts = [item.model_dump() for item in req.items]
    order_doc = create_order_from_cart(
        cart_items=cart_dicts,
        principal_id=req.principalId,
        shipping_address=req.shippingAddress,
        payment_method=req.paymentMethod,
        carrier_code=req.carrierCode,
        service_level=req.serviceLevel,
        feedback_rating=req.feedbackRating,
        feedback_text=req.feedbackText,
        complaint_reason=req.complaintReason,
        offer=offer,
        prior_mobile_orders=count_prior_mobile_orders(req.principalId)
    )
    return {
        "order": order_doc,
        # Lifted out of the document so the cart can render the discount line
        # without reaching into the order it is previewing.
        "loyaltyOffer": order_doc["loyaltyOffer"],
        "parityVerified": True,
        "sourcePlatform": "CUSTOM_MOBILE_APP"
    }


@app.post("/api/orders/submit")
def submit_order(req: OrderRequest):
    """
    Generates the retail order document and persists it directly to
    Firestore Native database 'redwood', collection 'retail'.
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="Cart cannot be empty")

    offer = resolve_offer(req.principalId, req.offerId)
    cart_dicts = [item.model_dump() for item in req.items]
    order_doc = create_order_from_cart(
        cart_items=cart_dicts,
        principal_id=req.principalId,
        shipping_address=req.shippingAddress,
        payment_method=req.paymentMethod,
        carrier_code=req.carrierCode,
        service_level=req.serviceLevel,
        feedback_rating=req.feedbackRating,
        feedback_text=req.feedbackText,
        complaint_reason=req.complaintReason,
        offer=offer,
        prior_mobile_orders=count_prior_mobile_orders(req.principalId)
    )
    order_id = order_doc["orderId"]

    if req.dryRun:
        return {
            "status": "dry_run_success",
            "orderId": order_id,
            "order": order_doc,
            "loyaltyOffer": order_doc["loyaltyOffer"],
            "message": "Order simulated successfully without writing to Firestore"
        }

    try:
        db = get_db()
        coll = db.collection(COLLECTION_NAME)
        doc_ref = coll.document(order_id)

        # One clock, around the commit alone. Building the document above is
        # this process's own work and is not what "mobile client -> Firestore"
        # means.
        started = time.monotonic()
        doc_ref.set(order_doc)
        elapsed_ms = (time.monotonic() - started) * 1000.0

        logger.info(
            f"Successfully committed order {order_id} to Firestore "
            f"{COLLECTION_NAME} in {elapsed_ms:.0f}ms"
        )
    except Exception as e:
        logger.error(f"Firestore write failed for order {order_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write order to Firestore: {str(e)}"
        )

    # The console has no Firestore listener on the orders collection -- that
    # collection is CDC-replicated and watching it from the browser would mean
    # a second stream of every order in the system. It gets the order from
    # here instead, which is also the only place the write latency is known.
    try:
        console_service.record_write(
            "order", order_id, elapsed_ms,
            customerId=order_doc.get("customerId"),
        )
        console_service.record_order(order_doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not publish order {order_id} to the console: {exc}")

    # The order exists now, so the claim is reported rather than enforced: an
    # offer that cannot be marked spent is a bookkeeping problem, and failing
    # the request at this point would tell the customer their order did not go
    # through when it did.
    offer_claimed = False
    if offer:
        try:
            session_engine.claim_offer(db, offer["offerId"], order_id=order_id)
            offer_claimed = True
        except Exception as exc:
            logger.warning(
                f"Order {order_id} used offer {offer.get('offerId')} but the "
                f"claim did not stick: {exc}"
            )

    return {
        "status": "success",
        "orderId": order_id,
        "order": order_doc,
        "loyaltyOffer": {**order_doc["loyaltyOffer"], "claimed": offer_claimed},
        "message": f"Order {order_id} committed to Firestore collection '{COLLECTION_NAME}' in database '{DATABASE_ID}'"
    }


@app.get("/api/orders")
def list_orders(
    principal_id: Optional[str] = Query(None, alias="principalId"),
    customer_id_param: Optional[str] = Query(None, alias="customerId"),
    limit: int = Query(30, ge=1, le=100)
):
    """
    Retrieves one customer's recent orders from Firestore collection 'retail',
    newest first.

    The caller has to say whose orders it wants. Unfiltered, this returned
    whatever `limit` documents Firestore happened to hand back out of the whole
    seeded collection, in no order, and the client filtered them afterwards --
    so an order placed a second ago was usually not among them and appeared to
    vanish on refresh. Accepts either the IAM principal short name or the
    customer id, because an order carries the customer id and the frontend
    holds the principal.
    """
    if not principal_id and not customer_id_param:
        raise HTTPException(
            status_code=400,
            detail="principalId or customerId is required",
        )

    customer_id = customer_id_param or DEMO_PRINCIPALS.get(principal_id, {}).get(
        "customerId", principal_id
    )

    try:
        db = get_db()
        coll = db.collection(COLLECTION_NAME)
        filtered = coll.where(filter=FieldFilter("customerId", "==", customer_id))

        # createdAt is the order date, and ordering on it is what puts the
        # order just placed at the top. It needs a (customerId ASC, createdAt
        # DESC) composite index on `retail`; until that index is built
        # Firestore answers FAILED_PRECONDITION, so the same result is
        # assembled in memory instead. The set is one customer's orders, which
        # is small, and a demo that degrades is better than one that 500s.
        try:
            docs = list(
                filtered.order_by("createdAt", direction="DESCENDING")
                .limit(limit)
                .stream()
            )
            ordered_by_firestore = True
        except Exception as exc:
            logger.warning(
                f"Ordered orders query failed ({exc}); falling back to an "
                "unordered read and sorting in the process. Create the "
                "(customerId ASC, createdAt DESC) index on 'retail' to remove this."
            )
            docs = list(filtered.limit(limit).stream())
            ordered_by_firestore = False

        orders = [doc.to_dict() for doc in docs if doc.to_dict()]
        if not ordered_by_firestore:
            orders.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

        return {
            "customerId": customer_id,
            "totalReturned": len(orders),
            "orderedByFirestore": ordered_by_firestore,
            "orders": orders
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list orders from Firestore: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to query Firestore orders: {str(e)}"
        )


@app.get("/api/orders/{order_id}")
def get_order_by_id(order_id: str):
    """Retrieves a single order from Firestore by its orderId."""
    try:
        db = get_db()
        doc = db.collection(COLLECTION_NAME).document(order_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
        return {"order": doc.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch order {order_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error reading order from Firestore: {str(e)}"
        )


# ==============================================================================
# Login sessions
#
# Everything below is declared before the dist/ catch-all at the bottom of this
# file. That route matches "/{full_path:path}", so anything registered after it
# is unreachable.
# ==============================================================================


class LoginRequest(BaseModel):
    principalId: str = "demo1"


class TelemetryRequest(BaseModel):
    """Timings the browser measured against its own clock.

    Both are click-to-something durations recorded end to end in the browser,
    which is the only way to get a number that is not the difference between
    two unrelated clocks.
    """

    clientAckMs: Optional[float] = Field(
        default=None, description="Sign in tapped until the login POST returned."
    )
    offerVisibleMs: Optional[float] = Field(
        default=None, description="Sign in tapped until the outcome rendered."
    )
    outcome: Optional[str] = None


class ClaimRequest(BaseModel):
    orderId: Optional[str] = None


@app.post("/api/session/login")
def login(req: LoginRequest):
    """Write a customer_sessions document and return immediately.

    The response is deliberately not the outcome. The agent has not run yet;
    the client opens the app on the catalog and watches the session over SSE.
    """
    # Validated before a Firestore client is built, so a bad principal is a
    # 400 about the principal rather than whatever failure a connection
    # attempt happens to produce.
    try:
        session_engine.resolve_customer_id(req.principalId)
    except session_engine.UnknownPrincipalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        session, reused = session_engine.start_session(
            get_db(),
            req.principalId,
            on_commit=lambda session_id, ms: console_service.record_write(
                "login", session_id, ms, customerId=session_engine.resolve_customer_id(req.principalId)
            ),
        )
    except Exception as exc:
        logger.error(f"Login failed for {req.principalId}: {exc}")
        raise HTTPException(status_code=500, detail=f"Login failed: {exc}")

    return {
        "sessionId": session.get("sessionId"),
        "customerId": session.get("customerId"),
        "principalId": req.principalId,
        "reused": reused,
        "session": session,
    }


@app.get("/api/session/{session_id}")
def read_session(session_id: str):
    """Read one session, with any offer the agent has written for it."""
    try:
        session = session_engine.get_session(get_db(), session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        offers = session_engine.offers_for_session(get_db(), session_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to read session {session_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to read session: {exc}")

    return {
        "session": session,
        "offer": offers[0] if offers else None,
        "telemetry": console_service.get_telemetry(session_id),
    }


def _sse_response(generator) -> StreamingResponse:
    """Wrap an SSE generator with the headers a long-lived stream needs."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Vite's dev proxy and any reverse proxy in front of this will
            # buffer the response without it, which turns a live stream into
            # one silent block delivered at the end.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/stream/session/{session_id}")
async def stream_session(session_id: str, request: Request):
    """Live view of one session and the offer, if any, written against it."""
    db = get_db()
    watches = [
        Watch("session", db.collection(session_engine.SESSIONS_COLLECTION).document(session_id)),
        Watch("offer", console_service.session_offers_query(db, session_id)),
    ]
    return _sse_response(stream_watches(watches, request.is_disconnected))


@app.post("/api/telemetry/{session_id}")
def record_telemetry(session_id: str, req: TelemetryRequest):
    """Accept the browser's own timings for a session.

    Held in a process-local dict. A Firestore collection would fire another
    Eventarc event, which would mean the console's measurements changed the
    pipeline they measure.
    """
    entry = console_service.record_telemetry(session_id, req.model_dump())
    return {"telemetry": entry}


@app.post("/api/offers/{offer_id}/claim")
def claim_offer(offer_id: str, req: ClaimRequest):
    """Redeem an active offer."""
    try:
        offer = session_engine.claim_offer(get_db(), offer_id, order_id=req.orderId)
    except session_engine.OfferNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except session_engine.OfferNotClaimableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to claim offer {offer_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to claim offer: {exc}")
    return {"offer": offer}


# ==============================================================================
# Redwood Console
# ==============================================================================


@app.get("/api/stream/console")
async def stream_console(request: Request):
    """Live view of the pipeline's collections, plus telemetry and controls."""
    db = get_db()
    watches = [
        Watch("session", console_service.recent_sessions_query(db)),
        Watch("offer", console_service.recent_offers_query(db)),
        Watch("trace", console_service.recent_traces_query(db)),
    ]
    queue = console_service.broadcaster.subscribe()

    # Orders are pushed by the write path rather than watched, so a console
    # that connects after an order was placed would never learn about it.
    # Replay the buffer into this subscriber's own queue before the stream
    # starts; the other subscribers have already seen these.
    for order in reversed(console_service.recent_orders()):
        queue.put_nowait(("order", order))

    async def generator():
        try:
            async for frame in stream_watches(
                watches, request.is_disconnected, extra_queue=queue
            ):
                yield frame
        finally:
            console_service.broadcaster.unsubscribe(queue)

    return _sse_response(generator())


@app.get("/api/console/churn")
def console_churn():
    """Current churn scores for the demo customers, straight from the model."""
    enabled = console_service.demo_controls_enabled()
    try:
        scores = console_service.read_churn_scores(get_bq(), PROJECT_ID)
    except Exception as exc:
        logger.error(f"Churn score read failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Churn score read failed: {exc}")
    return {
        "scores": scores,
        "demoControlsEnabled": enabled,
        # Reported alongside the scores so the panel can distinguish "the
        # button is switched off" from "the button has nowhere to call".
        "churnFunctionConfigured": console_service.churn_function_configured(),
    }


@app.post("/api/console/recalculate-churn")
async def console_recalculate_churn():
    """Rerun the churn pipeline, streaming its log as it goes.

    Gated: this route triggers a BigQuery ML retrain in the churn Cloud Run
    function. Consumed with fetch() rather than EventSource, which cannot
    issue a POST.
    """
    if not console_service.demo_controls_enabled():
        raise HTTPException(
            status_code=403,
            detail="Demo controls are disabled. Set ENABLE_DEMO_CONTROLS=1 to enable them.",
        )

    console_service.broadcaster.publish("control", {"action": "recalculate-churn"})
    return _sse_response(
        stream_lines(console_service.recalculate_churn_lines(PROJECT_ID))
    )


@app.post("/api/console/reset")
async def console_reset():
    """Restore the demo's starting point, streaming the log as it goes.

    Deletes the demo customers' sessions and offers and the orders previous
    demo runs left in `retail`, waits for those deletes to reach BigQuery,
    re-scores churn against them, then puts one throwaway session through the
    pipeline so the first real login of the demo is not the one that pays for
    the Agent Engine cold start.

    Streamed rather than returned in one piece, and for the same reason the
    churn recalculation is: it takes the better part of a minute and a silent
    button for that long is indistinguishable from a broken one. Consumed with
    fetch() rather than EventSource, which cannot issue a POST.
    """
    if not console_service.demo_controls_enabled():
        raise HTTPException(
            status_code=403,
            detail="Demo controls are disabled. Set ENABLE_DEMO_CONTROLS=1 to enable them.",
        )

    console_service.broadcaster.publish("control", {"action": "reset-started"})
    return _sse_response(
        stream_lines(console_service.reset_demo_lines(get_db(), get_bq(), PROJECT_ID))
    )


# Serve compiled frontend SPA if dist/ exists
DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.exists(DIST_DIR):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    
    assets_dir = os.path.join(DIST_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="API route not found")
        target_path = os.path.join(DIST_DIR, full_path)
        if os.path.exists(target_path) and os.path.isfile(target_path):
            return FileResponse(target_path)
        # The console is a second Vite entry point, so it is a real file in
        # the bundle rather than a route of the mobile SPA. "/console" is
        # accepted as well as "/console.html" because nobody types the
        # extension.
        if full_path.strip("/") == "console":
            console_html = os.path.join(DIST_DIR, "console.html")
            if os.path.exists(console_html):
                return FileResponse(console_html)
        return FileResponse(os.path.join(DIST_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn
    # 8085, matching start_mobile_app.sh and the Vite dev proxy. It used to say
    # 8000, which meant running this module directly listened on a port
    # nothing was configured to talk to.
    uvicorn.run(app, host="0.0.0.0", port=8085)


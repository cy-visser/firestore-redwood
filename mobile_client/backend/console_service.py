"""Operator controls behind the Redwood Console."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from google.cloud.firestore_v1.base_query import FieldFilter

from mobile_client.backend import idtoken, session_engine
from mobile_client.backend.order_engine import MOBILE_ORDER_ID_PREFIX

logger = logging.getLogger("redwood-mobile-api.console")

SESSIONS_COLLECTION = "customer_sessions"
OFFERS_COLLECTION = "loyalty_offers"

# Firestore collection for latency traces.
TRACES_COLLECTION = "pipeline_traces"
CHURN_TABLE = os.getenv("BIGQUERY_PREDICTIONS_TABLE", "customer_churn_risk")
BIGQUERY_DATASET = os.getenv("BIGQUERY_DATASET", "redwood_retail")

# The deployed churn function. Terraform sets this on the Cloud Run service;
# start_mobile_app.sh exports it for local development. Empty means the churn
# controls will explain themselves rather than fail obscurely.
CHURN_FUNCTION_URL = os.getenv("CHURN_FUNCTION_URL", "").strip().rstrip("/")

# Mirrors churn_service.pipeline.FULL / RESCORE. Duplicated as literals rather
# than imported: this process does not ship the function's code, and a shared
# constants module for two strings would be its own kind of silly.
CHURN_MODE_FULL = "full"
CHURN_MODE_RESCORE = "rescore"

# Typed CDC mirror table for orders.
ORDERS_TABLE = os.getenv("BIGQUERY_ORDERS_TABLE") or (
    f"{os.getenv('FIRESTORE_COLLECTION', 'retail')}_current"
)

# Seeded demo customers.
DEMO_CUSTOMER_IDS = ("cust_demo1", "cust_demo2")

# How long to wait on the churn function without output before giving up.
RECALCULATE_TIMEOUT_SECONDS = 600

# Timeout and poll intervals for CDC mirror synchronization.
CDC_DRAIN_TIMEOUT_SECONDS = 45.0
CDC_DRAIN_POLL_SECONDS = 3.0

# Principal used for Agent Engine warm-up.
WARM_UP_PRINCIPAL = "demo1"

# Timeout and poll intervals for Agent Engine warm-up.
WARM_UP_TIMEOUT_SECONDS = 60.0
WARM_UP_POLL_SECONDS = 1.0

# Terminal session processing statuses.
TERMINAL_AGENT_STATUSES = ("PROCESSED", "SKIPPED", "ERROR")


def demo_controls_enabled() -> bool:
    """Whether destructive and executing controls are enabled."""
    return os.getenv("ENABLE_DEMO_CONTROLS", "").strip().lower() in {"1", "true", "yes", "on"}


def churn_function_configured() -> bool:
    """Whether this process knows where the churn function lives.

    Reported to the console so an unconfigured deployment says so on the panel
    rather than only in the Event Log after a button press.
    """
    return bool(CHURN_FUNCTION_URL)


# ----------------------------------------------------------------------
# In-process fan-out
# ----------------------------------------------------------------------


class Broadcaster:
    """Fan one event out to every open console stream.

    Deliberately in-process and unbounded in neither direction: there is one
    presenter, the events are small, and persisting them would mean a new
    Firestore collection, which would trigger another Eventarc delivery and
    put the console's own chatter into the pipeline it is measuring.
    """

    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: str, payload: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait((event, payload))


broadcaster = Broadcaster()


# ----------------------------------------------------------------------
# Browser-clock telemetry
# ----------------------------------------------------------------------

# Timings measured by the mobile client against its own clock, keyed by
# session. A process-local dict, not a collection: writing these to Firestore
# would fire another Eventarc event and pollute the very pipeline the console
# is timing.
_TELEMETRY: Dict[str, Dict[str, Any]] = {}


def record_telemetry(session_id: str, measurements: Dict[str, Any]) -> Dict[str, Any]:
    """Store the browser-clock timings for a session and tell the console.

    These never get mixed with Firestore commit timestamps. Four clocks are
    involved in this system -- the browser, the FastAPI process, Firestore and
    the Agent Engine runtime -- and subtracting one from another yields a
    number that looks precise and means nothing.
    """
    entry = _TELEMETRY.setdefault(session_id, {"sessionId": session_id})
    entry.update({k: v for k, v in measurements.items() if v is not None})
    entry["recordedAt"] = datetime.now(timezone.utc).isoformat()
    broadcaster.publish("telemetry", entry)
    return entry


def get_telemetry(session_id: str) -> Dict[str, Any]:
    return _TELEMETRY.get(session_id, {})


def all_telemetry() -> Dict[str, Dict[str, Any]]:
    return dict(_TELEMETRY)


# ----------------------------------------------------------------------
# Client -> Firestore write latency
# ----------------------------------------------------------------------


def record_write(
    kind: str,
    document_id: str,
    duration_ms: float,
    **extra: Any,
) -> Dict[str, Any]:
    """Publish how long one Firestore commit took, as this process measured it.

    ``duration_ms`` must be a monotonic elapsed time around the ``.set()``
    call, not a difference between a request timestamp and a commit timestamp.
    This process and Firestore keep separate clocks, and the console would
    happily render a negative millisecond count if we subtracted one from the
    other.

    Not stored anywhere. The console is watching when the write happens or it
    is not; persisting this would need a collection, and a collection needs a
    reason better than "so a panel can back-fill".
    """
    payload = {
        "kind": kind,
        "documentId": document_id,
        "durationMs": round(duration_ms, 1),
        "at": datetime.now(timezone.utc).isoformat(),
        **{key: value for key, value in extra.items() if value is not None},
    }
    broadcaster.publish("write", payload)
    return payload


# ----------------------------------------------------------------------
# Orders
# ----------------------------------------------------------------------

# The last few orders placed from the mobile client, newest last.
#
# The console does not watch the orders collection. That collection is
# CDC-replicated to BigQuery and holds the whole seeded history, so a browser
# listener on it would stream thousands of documents to show the one the
# presenter just placed. The write path publishes here instead, and this buffer
# exists only so a console opened after the order still shows it.
_RECENT_ORDERS: List[Dict[str, Any]] = []
RECENT_ORDER_LIMIT = 10


def record_order(order: Dict[str, Any]) -> Dict[str, Any]:
    """Remember one order and push it to every open console."""
    _RECENT_ORDERS.append(order)
    del _RECENT_ORDERS[:-RECENT_ORDER_LIMIT]
    broadcaster.publish("order", order)
    return order


def recent_orders() -> List[Dict[str, Any]]:
    """The buffered orders, newest first."""
    return list(reversed(_RECENT_ORDERS))


def clear_recent_orders() -> None:
    """Drop the buffer. Called by the reset, which deletes the orders too."""
    _RECENT_ORDERS.clear()



# ----------------------------------------------------------------------
# Churn scores
# ----------------------------------------------------------------------


def read_churn_scores(bq_client: Any, project_id: str) -> List[Dict[str, Any]]:
    """Read the current churn scores for the demo customers.

    Straight from the model's output table, with the customer ids bound as a
    parameter rather than interpolated, and fully qualified so the result does
    not depend on the client's default project.
    """
    from google.cloud import bigquery

    sql = (
        "SELECT customer_id, churn_probability, churn_risk_tier, "
        "customer_segment, total_spend_90d, sentiment_score, "
        "calculation_timestamp\n"
        f"FROM `{project_id}.{BIGQUERY_DATASET}.{CHURN_TABLE}`\n"
        "WHERE customer_id IN UNNEST(@customer_ids)\n"
        "ORDER BY customer_id"
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter(
                "customer_ids", "STRING", list(DEMO_CUSTOMER_IDS)
            )
        ]
    )
    rows = bq_client.query(sql, job_config=job_config).result()

    scores: List[Dict[str, Any]] = []
    for row in rows:
        timestamp = row.get("calculation_timestamp")
        scores.append({
            "customerId": row.get("customer_id"),
            "churnProbability": row.get("churn_probability"),
            "churnRiskTier": row.get("churn_risk_tier"),
            "customerSegment": row.get("customer_segment"),
            "totalSpend90d": row.get("total_spend_90d"),
            "sentimentScore": row.get("sentiment_score"),
            "calculatedAt": timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp,
        })
    return scores


# ----------------------------------------------------------------------
# Recalculate churn
# ----------------------------------------------------------------------


async def _stream_churn_function(
    mode: str,
    report: bool = True,
) -> AsyncIterator[str]:
    """Call the churn Cloud Run function and yield its output line by line.

    Shared by the recalculate button and the reset's re-score step, which
    differ only in the mode they ask for.

    The function streams ``text/plain``, one log line per chunk, ending in
    ``[exit N]``. That is deliberately the same contract the subprocess had:
    callers here already branch on the exit line rather than on an exception,
    so moving the pipeline into Cloud Run did not change the reset's control
    flow.

    Nothing raises out of here. A transport failure -- the service is not
    deployed, the token was refused, the connection dropped mid-run -- is
    reported as an ``[ERROR]`` line followed by ``[exit 1]``, because a caller
    already mid-stream has no other way to hear about it, and a reset that
    silently skips its re-score is exactly the failure that leaves cust_demo2
    scored as healthy in front of an audience.
    """
    url = CHURN_FUNCTION_URL
    if not url:
        yield (
            "[ERROR] CHURN_FUNCTION_URL is not set, so the churn function cannot "
            "be reached. Deploy the stack with ./deploy.sh, or export the URL "
            "from `terraform output -raw churn_service_url`."
        )
        yield "[exit 1]"
        return

    yield f'$ POST {url} {{"mode": "{mode}", "report": {str(report).lower()}}}'

    import httpx

    try:
        # Blocking credential work, kept off the event loop: both the metadata
        # server and the impersonation path do network IO.
        token = await asyncio.to_thread(idtoken.fetch, url)
    except Exception as exc:  # noqa: BLE001
        yield f"[ERROR] Could not mint an ID token for {url}: {exc}"
        yield "[exit 1]"
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"mode": mode, "report": report}

    # read=RECALCULATE_TIMEOUT_SECONDS keeps the old guarantee in a new
    # mechanism: the subprocess was killed after that long without output, and
    # a stream that stalls for that long is abandoned here. write and pool are
    # short because they only cover the request going out.
    timeout = httpx.Timeout(
        connect=30.0, read=RECALCULATE_TIMEOUT_SECONDS, write=30.0, pool=30.0
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace").strip()
                    yield f"[ERROR] Churn function returned {response.status_code}: {body[:500]}"
                    if response.status_code in (401, 403):
                        yield (
                            "        The caller needs roles/run.invoker on the "
                            "churn service."
                        )
                    yield "[exit 1]"
                    return

                saw_exit = False
                async for line in response.aiter_lines():
                    stripped = line.rstrip()
                    if stripped.startswith("[exit "):
                        saw_exit = True
                    yield stripped

                if not saw_exit:
                    # The stream ended without the function saying how it went,
                    # which means it died rather than finished.
                    yield "[ERROR] Churn function closed the stream without an exit line."
                    yield "[exit 1]"
    except httpx.ReadTimeout:
        yield (
            f"[ERROR] No output from the churn function for "
            f"{RECALCULATE_TIMEOUT_SECONDS}s; giving up on the stream. The "
            f"BigQuery jobs it started may still be running."
        )
        yield "[exit 1]"
    except Exception as exc:  # noqa: BLE001
        yield f"[ERROR] Churn function call failed: {exc}"
        yield "[exit 1]"


async def recalculate_churn_lines(project_id: str) -> AsyncIterator[str]:
    """Run the full churn pipeline, yielding its output line by line.

    Streamed rather than awaited. The pipeline takes over a minute, and a
    silent button for that long is indistinguishable from a broken one; with
    the log on screen the audience watches the model retrain instead of a
    spinner.

    ``report`` is what prints the model evaluation and the two demo persona
    scores at the end, which is the part of the output anybody actually reads.

    ``project_id`` is accepted and ignored: the function reads its project from
    its own environment, and every caller here already has one to hand.
    """
    async for line in _stream_churn_function(CHURN_MODE_FULL, report=True):
        yield line




# ----------------------------------------------------------------------
# Reset
# ----------------------------------------------------------------------


def _delete_matching(db: Any, collection: str, field: str, values: Any) -> int:
    """Delete every document in ``collection`` whose ``field`` is in ``values``."""
    deleted = 0
    for value in values:
        query = db.collection(collection).where(filter=FieldFilter(field, "==", value))
        for snap in query.stream():
            snap.reference.delete()
            deleted += 1
    return deleted


def _delete_by_id_prefix(db: Any, collection: str, field: str, prefix: str) -> int:
    """Delete every document in ``collection`` whose ``field`` starts with ``prefix``.

    A range on one field, which any single-field index answers. Filtering on
    customerId as well would be more obviously targeted but would need a
    composite index, and it would buy nothing: only this app writes ids with
    this prefix, and it only ever writes them for the demo customers.
    """
    deleted = 0
    query = (
        db.collection(collection)
        .where(filter=FieldFilter(field, ">=", prefix))
        # \uf8ff sorts above any character that can appear in an id, so this is
        # the standard Firestore way to close a prefix range.
        .where(filter=FieldFilter(field, "<", prefix + "\uf8ff"))
    )
    for snap in query.stream():
        snap.reference.delete()
        deleted += 1
    return deleted


def warm_up_agent(
    db: Any,
    timeout_seconds: float = WARM_UP_TIMEOUT_SECONDS,
    poll_seconds: float = WARM_UP_POLL_SECONDS,
    sleep: Any = time.sleep,
) -> Dict[str, Any]:
    """Push one throwaway login through the pipeline, then delete it.

    Agent Engine cold starts cost tens of seconds, and without this the login
    that pays for it is the first one the audience watches. The warm-up runs
    demo1 deliberately: demo1 is the low-risk persona, so the agent evaluates
    it and skips, and no offer is created. Running demo2 would burn the very
    offer the demo exists to show.

    The session is deleted afterwards whatever happens, including on timeout,
    because a leftover PENDING session is exactly what the login reuse would
    latch onto.
    """
    started = time.monotonic()
    doc = session_engine.build_session_doc(WARM_UP_PRINCIPAL)
    session_id = doc["sessionId"]
    doc["channel"] = "CONSOLE_WARMUP"

    result: Dict[str, Any] = {
        "attempted": True,
        "succeeded": False,
        "sessionId": session_id,
        "status": None,
        "elapsedSeconds": 0.0,
    }

    try:
        db.collection(SESSIONS_COLLECTION).document(session_id).set(doc)

        while time.monotonic() - started < timeout_seconds:
            sleep(poll_seconds)
            session = session_engine.get_session(db, session_id) or {}
            status = session.get("agentProcessingStatus")
            if status in TERMINAL_AGENT_STATUSES:
                result["succeeded"] = True
                result["status"] = status
                break
            result["status"] = status
        else:
            logger.warning(
                "Agent warm-up gave up after %.0fs; last status %s.",
                timeout_seconds, result["status"],
            )
    except Exception as exc:
        # A warm-up is an optimisation. Failing it must not fail the reset the
        # presenter is standing in front of.
        logger.warning("Agent warm-up failed: %s", exc)
        result["error"] = str(exc)
    finally:
        try:
            db.collection(SESSIONS_COLLECTION).document(session_id).delete()
        except Exception as exc:
            logger.warning("Could not delete warm-up session %s: %s", session_id, exc)
        result["elapsedSeconds"] = round(time.monotonic() - started, 1)

    return result


def _reset_collections(db: Any) -> Dict[str, int]:
    """Delete everything the demo writes, and report how much there was.

    The orders this app wrote are swept, so the seeded dataset is exactly what
    it was. That is not tidiness: a demo order sets cust_demo2's
    daysSinceLastPurchase to 0, and the next churn run would then score the
    high-risk persona as healthy and the agent would have no reason to make an
    offer at all. Deleting them here reaches BigQuery as well, because
    cdc_service replicates deletes.

    Legacy orders are swept too. Orders placed before the identity fix carry
    the principal short name as their customerId, so they no longer match the
    customer the rest of the system knows about and are only demo litter.
    """
    orders_collection = os.getenv("FIRESTORE_COLLECTION", "retail")
    counts = {
        "sessions": _delete_matching(
            db, SESSIONS_COLLECTION, "customerId", DEMO_CUSTOMER_IDS
        ),
        "offers": _delete_matching(
            db, OFFERS_COLLECTION, "customerId", DEMO_CUSTOMER_IDS
        ),
        "traces": _delete_matching(
            db, TRACES_COLLECTION, "customerId", DEMO_CUSTOMER_IDS
        ),
        "mobileOrders": _delete_by_id_prefix(
            db, orders_collection, "orderId", MOBILE_ORDER_ID_PREFIX
        ),
        "legacyOrders": _delete_matching(
            db,
            orders_collection,
            "customerId",
            ("demo1", "demo2"),
        ),
    }
    clear_recent_orders()
    # Process-local and nothing else expires it, so a reset that left it in
    # place would redisplay the previous run's browser timings next to an
    # otherwise empty pipeline.
    _TELEMETRY.clear()
    return counts


def reset_demo(db: Any, warm_up: bool = True) -> Dict[str, Any]:
    """Put the demo back to the state it ships in.

    Without this the demo works exactly once: an issued offer suppresses the
    next one for the length of the cooldown, which is correct behaviour and
    fatal to a second run in front of an audience.

    The orders this app wrote are swept too, so the seeded dataset is exactly
    what it was. That is not tidiness: a demo order sets cust_demo2's
    daysSinceLastPurchase to 0, and the next churn run would then score the
    high-risk persona as healthy and the agent would have no reason to make an
    offer at all. Deleting them here is enough for BigQuery as well, because
    cdc_service replicates deletes.

    Legacy orders are swept too. Orders placed before the identity fix carry
    the principal short name as their customerId, so they no longer match the
    customer the rest of the system knows about and are only demo litter.

    This is the non-streaming form, kept for tests and for any caller that
    just wants the reset done. The console uses ``reset_demo_lines``, which
    additionally re-scores churn -- the deletions above are what make the old
    scores wrong, so a reset that stops here leaves cust_demo2 looking healthy.
    """
    counts = _reset_collections(db)

    warm_up_result = (
        warm_up_agent(db) if warm_up else {"attempted": False, "succeeded": False}
    )

    logger.info("Demo reset: %s warm-up: %s", counts, warm_up_result)
    result = {"deleted": counts, "warmUp": warm_up_result}
    broadcaster.publish("control", {
        "action": "reset",
        "counts": counts,
        "warmUp": warm_up_result,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    return result


def _mobile_orders_remaining(bq_client: Any, project_id: str) -> int:
    """How many of this app's orders BigQuery still has.

    Counted in the typed CDC mirror the feature view reads, not in the raw
    change table.
    """
    sql = (
        f"SELECT COUNT(*) AS remaining\n"
        f"FROM `{project_id}.{BIGQUERY_DATASET}.{ORDERS_TABLE}`\n"
        f"WHERE order_id LIKE @prefix"
    )
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(
                "prefix", "STRING", f"{MOBILE_ORDER_ID_PREFIX}%"
            )
        ]
    )
    for row in bq_client.query(sql, job_config=job_config).result():
        return int(row.get("remaining") or 0)
    return 0


async def reset_demo_lines(
    db: Any,
    bq_client: Any,
    project_id: str,
) -> AsyncIterator[str]:
    """Run the reset, narrating each step.

    Streamed for the same reason the churn recalculation is: this takes the
    better part of a minute and a button that does nothing visible for that
    long looks broken.

    The order of the steps is not arbitrary. The deletions have to land in
    BigQuery before the re-score runs, or the re-score reads a feature view
    that still contains the demo orders and scores cust_demo2 -- the persona
    the whole demo depends on being at risk -- as a customer who bought
    something today. The drain poll in step 3 is what enforces that.
    """
    yield "[1/4] Deleting demo sessions, offers and orders"
    try:
        counts = await asyncio.to_thread(_reset_collections, db)
    except Exception as exc:  # noqa: BLE001
        yield f"[ERROR] Deletion failed: {exc}"
        yield f"[SUMMARY] {json.dumps({'succeeded': False, 'error': str(exc)})}"
        yield "[exit 1]"
        return

    yield (
        f"      {counts['sessions']} session(s), {counts['offers']} offer(s), "
        f"{counts['mobileOrders']} mobile order(s), "
        f"{counts['legacyOrders']} legacy order(s)"
    )

    yield "[2/4] Waiting for Firestore deletes to reach BigQuery"
    drained = False
    deadline = time.monotonic() + CDC_DRAIN_TIMEOUT_SECONDS
    remaining = -1
    while time.monotonic() < deadline:
        try:
            remaining = await asyncio.to_thread(
                _mobile_orders_remaining, bq_client, project_id
            )
        except Exception as exc:  # noqa: BLE001
            yield f"      (could not read {ORDERS_TABLE}: {exc})"
            break
        if remaining == 0:
            drained = True
            break
        yield f"      {remaining} order(s) still in {ORDERS_TABLE}"
        await asyncio.sleep(CDC_DRAIN_POLL_SECONDS)

    if drained:
        yield f"      {ORDERS_TABLE} is clear"
    else:
        # Not fatal, but the presenter needs to know the next number they see
        # may be wrong rather than discovering it during the demo.
        yield (
            f"      [WARN] Still {remaining} order(s) after "
            f"{CDC_DRAIN_TIMEOUT_SECONDS:.0f}s. Churn scores may be stale."
        )

    yield "[3/4] Re-scoring churn against the existing model"
    exit_code = 0
    async for line in _rescore_churn_lines(project_id):
        if line.startswith("[exit "):
            exit_code = int(line[6:-1] or 0)
            continue
        yield f"      {line}" if line else ""

    if exit_code != 0:
        yield f"      [WARN] Re-score exited {exit_code}; scores may be stale."

    yield "[4/4] Warming up the agent"
    warm_up_result = await asyncio.to_thread(warm_up_agent, db)
    if warm_up_result.get("succeeded"):
        yield (
            f"      Agent responded in {warm_up_result['elapsedSeconds']}s "
            f"({warm_up_result.get('status')})"
        )
    else:
        yield (
            f"      [WARN] Agent did not reach a terminal state in "
            f"{warm_up_result.get('elapsedSeconds')}s; the first login of the "
            f"demo will pay the cold start."
        )

    scores = []
    if exit_code == 0:
        try:
            scores = await asyncio.to_thread(read_churn_scores, bq_client, project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read churn scores after reset: %s", exc)

    summary = {
        "succeeded": True,
        "deleted": counts,
        "scores": scores,
        "warmUp": warm_up_result,
        "cdcDrained": drained,
        "rescored": exit_code == 0,
    }
    yield f"[SUMMARY] {json.dumps(summary)}"

    yield "Reset complete."
    logger.info("Demo reset: %s warm-up: %s", counts, warm_up_result)
    broadcaster.publish("control", {
        "action": "reset",
        "counts": counts,
        "warmUp": warm_up_result,
        "rescored": exit_code == 0,
        "cdcDrained": drained,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    yield "[exit 0]"


async def _rescore_churn_lines(project_id: str) -> AsyncIterator[str]:
    """Run the scoring MERGE on its own and stream its output.

    Not a full pipeline run. The model is still valid -- nothing about a reset
    changes what churn looks like -- so retraining it would cost a minute to
    arrive at the same coefficients. What has changed is the feature data, and
    the MERGE rewrites every scored row from it.

    ``project_id`` is accepted and ignored, as in ``recalculate_churn_lines``.
    """
    async for line in _stream_churn_function(CHURN_MODE_RESCORE, report=True):
        yield line



def recent_sessions_query(db: Any, limit: int = 10) -> Any:
    """The console's live view of logins: newest first."""
    return (
        db.collection(SESSIONS_COLLECTION)
        .order_by("loginTimestamp", direction="DESCENDING")
        .limit(limit)
    )


def recent_offers_query(db: Any, limit: int = 10) -> Any:
    """The console's live view of offers: newest first."""
    return (
        db.collection(OFFERS_COLLECTION)
        .order_by("createdAt", direction="DESCENDING")
        .limit(limit)
    )


def recent_traces_query(db: Any, limit: int = 10) -> Any:
    """The console's live view of per-step latencies: newest first.

    Two shapes share this collection, told apart by their ``kind`` field.
    ``SESSION`` traces are keyed by session id and written by three parties --
    the bridge on receipt, the agent as it runs, and the bridge again when the
    agent returns -- so one arrives in three parts and the console sees three
    snapshots. ``ORDER`` traces are keyed ``order_{orderId}`` and written once
    by cdc_service when the order reaches BigQuery.

    The limit covers both, which is why it is not 5: a demo run produces a
    session trace per login and an order trace per order, and the console
    needs the current one of each still inside the window.

    Ordered on a single field, which Firestore indexes automatically -- no
    composite index is needed for this one.
    """
    return (
        db.collection(TRACES_COLLECTION)
        .order_by("recordedAt", direction="DESCENDING")
        .limit(limit)
    )


def recent_mobile_orders_query(db: Any, limit: int = 25) -> Any:
    """Orders this app wrote, for the console's live view.

    Scoped by the ORD-26-MOB- id prefix rather than by customer: a range on one
    field is answered by the automatic single-field index, and only this app
    writes ids with this prefix. The whole seeded collection would otherwise
    arrive on the first snapshot and bury the one order that matters.
    """
    orders_collection = os.getenv("FIRESTORE_COLLECTION", "retail")
    return (
        db.collection(orders_collection)
        .where(filter=FieldFilter("orderId", ">=", MOBILE_ORDER_ID_PREFIX))
        .where(filter=FieldFilter("orderId", "<", MOBILE_ORDER_ID_PREFIX + "\uf8ff"))
        .limit(limit)
    )



def session_offers_query(db: Any, session_id: str) -> Any:
    """Offers belonging to one session, for the mobile client's stream."""
    return db.collection(OFFERS_COLLECTION).where(
        filter=FieldFilter("sessionId", "==", session_id)
    )


def optional_str(value: Optional[Any]) -> Optional[str]:
    """Normalise a Firestore value that may be a timestamp or a string."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

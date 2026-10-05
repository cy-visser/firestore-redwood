"""Redwood Retail CDC service entrypoint for Eventarc CloudEvents."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bq_cdc_writer import CdcSink, extract_row, sequence_number  # noqa: E402
from firestore_event import (  # noqa: E402
    DocumentEvent,
    parse_document_event,
    parse_event_body,
)
from schemas import ColumnContext, TableSpec, build_routes  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("redwood.cdc")

# Firestore collection the console reads latency traces from. The agent bridge
# and the loyalty agent write session traces here; this service writes the
# order replication leg.
TRACES_COLLECTION = os.getenv("FIRESTORE_TRACES_COLLECTION", "pipeline_traces")
TRACE_TTL_DAYS = 30

# Only documents whose id starts with this prefix are traced, which in
# practice means orders placed from the mobile app. Tracing every replicated
# document would write 400 trace records during a backfill of the seeded
# dataset and bury the one the presenter just created. Set empty to disable.
TRACE_DOCUMENT_ID_PREFIX = os.getenv("TRACE_DOCUMENT_ID_PREFIX", "ORD-26-MOB-")

_firestore = None
_firestore_lock = threading.Lock()


def get_firestore():
    """Get or create the Firestore client used for trace recording."""
    global _firestore
    if _firestore is not None:
        return _firestore

    with _firestore_lock:
        if _firestore is None:
            from google.cloud import firestore

            _firestore = firestore.Client(
                project=config.project_id, database=config.database_id
            )
        return _firestore


class Config:
    """Runtime configuration loaded from environment variables."""

    def __init__(self) -> None:
        self.project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.dataset = os.getenv("BIGQUERY_DATASET")
        self.orders_collection = os.getenv("FIRESTORE_COLLECTION", "retail")
        self.customers_collection = os.getenv("FIRESTORE_CUSTOMERS_COLLECTION", "customers")
        self.orders_cdc_table = os.getenv("BIGQUERY_CDC_TABLE", "retail_cdc")
        self.database_id = os.getenv("FIRESTORE_DATABASE_ID", "(default)")
        self.ensure_tables = os.getenv("ENSURE_TABLES", "true").lower() == "true"

        missing = [
            name
            for name, value in (
                ("GCP_PROJECT_ID", self.project_id),
                ("BIGQUERY_DATASET", self.dataset),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )


config = Config()
routes = build_routes(
    orders_collection=config.orders_collection,
    orders_cdc_table=config.orders_cdc_table,
    customers_collection=config.customers_collection,
)
sink = CdcSink(config.project_id, config.dataset)

app = Flask(__name__)

_startup: Dict[str, Any] = {"tables": None, "error": None}


def _all_specs() -> List[TableSpec]:
    return [spec for route in routes.values() for spec in route.tables]


def _bootstrap() -> None:
    """Create destination tables once, at startup."""
    if not config.ensure_tables or _startup["tables"] is not None:
        return
    try:
        _startup["tables"] = dict(sink.ensure_tables(_all_specs()))
        logger.info("Destination tables: %s", _startup["tables"])
    except Exception as err:  # noqa: BLE001 - surface via /healthz, keep serving
        _startup["error"] = str(err)
        logger.exception("Table bootstrap failed")


_bootstrap()


def _cloud_event_headers() -> Tuple[str, str, Optional[datetime]]:
    """Pull id, type and time out of the CloudEvent binary-mode headers."""
    event_id = request.headers.get("ce-id", "")
    event_type = request.headers.get("ce-type", "")
    raw_time = request.headers.get("ce-time")

    event_time: Optional[datetime] = None
    if raw_time:
        text = raw_time[:-1] + "+00:00" if raw_time.endswith("Z") else raw_time
        try:
            event_time = datetime.fromisoformat(text)
        except ValueError:
            event_time = None

    return event_id, event_type, event_time


def _write_event(event: DocumentEvent) -> Dict[str, str]:
    """Fan one document change out to every table its collection routes to."""
    route = routes.get(event.collection)
    if route is None:
        return {}

    document = event.effective_data
    ctx = ColumnContext(
        document=document,
        document_id=event.document_id,
        operation=event.operation,
        change_timestamp=event.event_time,
        raw_json=json.dumps(document, default=str),
    )

    change_type = "DELETE" if event.is_delete else "UPSERT"
    sequence = sequence_number(event.event_time)

    outcome: Dict[str, str] = {}
    for spec in route.tables:
        table_writer = sink.writer_for(spec)
        values = extract_row(spec, ctx)
        row = table_writer.build_row(
            values,
            change_type=change_type if spec.supports_cdc else None,
            change_sequence=sequence if spec.supports_cdc else None,
        )
        table_writer.append([row])
        outcome[spec.table_id] = change_type if spec.supports_cdc else "APPEND"

    return outcome


@app.get("/healthz")
def healthz():
    return jsonify(
        status="ok" if not _startup["error"] else "degraded",
        project=config.project_id,
        dataset=config.dataset,
        database=config.database_id,
        collections=sorted(routes.keys()),
        tables=_startup["tables"],
        error=_startup["error"],
    ), 200


def _write_order_trace(
    event: DocumentEvent,
    event_id: str,
    event_time: Optional[datetime],
    received_at: datetime,
    bq_write_ms: float,
    written: Dict[str, str],
) -> None:
    """Record how long an order took to reach BigQuery, for the console.

    Two spans, both owned by this service and both on its own clock: the
    Eventarc delivery hop (ce-time to arrival here, approximate for the same
    reason the agent bridge marks its own) and the Storage Write API append.

    Written to ``pipeline_traces``, which no Eventarc trigger watches -- the
    CDC triggers are scoped by path pattern to the orders and customers
    collections, so this cannot feed back into itself.

    Failures are logged and swallowed. Raising here would return a 500 to
    Eventarc, which would redeliver the order and append the BigQuery row a
    second time; a missing measurement is much cheaper than a duplicated row.
    """
    if not TRACE_DOCUMENT_ID_PREFIX:
        return
    if not event.document_id.startswith(TRACE_DOCUMENT_ID_PREFIX):
        return

    delivery_ms: Optional[float] = None
    if event_time is not None:
        delivery_ms = max(0.0, (received_at - event_time).total_seconds() * 1000.0)

    try:
        document = {
            "kind": "ORDER",
            "orderId": event.document_id,
            "customerId": (event.effective_data or {}).get("customerId"),
            "eventId": event_id,
            "eventTime": event_time,
            "cdcReceivedAt": received_at,
            "cdcDeliveryMs": round(delivery_ms, 2) if delivery_ms is not None else None,
            "bqWriteMs": round(bq_write_ms, 2),
            "bqTotalMs": round((delivery_ms or 0.0) + bq_write_ms, 2),
            "bqTables": sorted(written),
            "operation": event.operation,
            "recordedAt": received_at,
            "expireAt": received_at + timedelta(days=TRACE_TTL_DAYS),
        }
        get_firestore().collection(TRACES_COLLECTION).document(
            f"order_{event.document_id}"
        ).set(document, merge=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not write order trace for %s: %s", event.document_id, exc
        )


@app.post("/")
def receive_event():
    """Eventarc CloudEvent receiver endpoint."""
    received_at = datetime.now(timezone.utc)
    event_id, event_type, event_time = _cloud_event_headers()

    if not event_type:
        logger.warning("Request without ce-type header; ignoring")
        return jsonify(status="ignored", reason="not a CloudEvent"), 200

    try:
        payload = parse_event_body(request.get_data(), request.content_type)
    except Exception as err:  # noqa: BLE001
        logger.error("Unparseable event body for %s: %s", event_id, err)
        return jsonify(status="dropped", reason="malformed payload"), 200

    try:
        event = parse_document_event(payload, event_id, event_type, event_time)
    except Exception as err:  # noqa: BLE001
        logger.exception("Could not decode event %s", event_id)
        return jsonify(status="dropped", reason=f"decode failed: {err}"), 200

    if event.collection not in routes:
        logger.debug("No route for collection %r; ignoring", event.collection)
        return jsonify(status="ignored", collection=event.collection), 200

    # Around the BigQuery append alone. Decoding the event above is this
    # service's own work and is not what "replicated into BigQuery" means.
    write_started = time.monotonic()
    try:
        written = _write_event(event)
    except Exception as err:  # noqa: BLE001 - retryable, let Eventarc redeliver
        logger.exception("BigQuery write failed for %s", event.document_path)
        return jsonify(status="error", reason=str(err)), 500
    bq_write_ms = (time.monotonic() - write_started) * 1000.0

    _write_order_trace(
        event, event_id, event_time, received_at, bq_write_ms, written
    )

    logger.info(
        "%s %s/%s -> %s in %.0fms",
        event.operation,
        event.collection,
        event.document_id,
        ",".join(f"{k}:{v}" for k, v in written.items()),
        bq_write_ms,
    )
    return jsonify(
        status="ok",
        operation=event.operation,
        collection=event.collection,
        document_id=event.document_id,
        written=written,
        bqWriteMs=round(bq_write_ms, 2),
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

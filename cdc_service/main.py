"""
Redwood Retail CDC service.

Receives Firestore document change events from Eventarc and replicates them
into BigQuery through the Storage Write API. This replaces the Dataflow
streaming job, which could not actually tail Firestore: the Beam Python SDK has
no Firestore connector, Datastream does not list Firestore as a source, and the
MongoDB-compatible change streams that would have worked are unavailable
because the ``redwood`` database is Enterprise *Native* mode, which is mutually
exclusive with MongoDB compatibility. The old job was therefore polling, with
all the latency and cost that implies, and could not observe deletes at all.

Eventarc gives us the real change stream. The trade is that delivery is
at-least-once and unordered, so every write here has to be idempotent: the
ledger is append-only and de-duplicable by event id, and the mirror tables are
keyed and sequenced so a late duplicate cannot resurrect an old version.

Endpoints
    ``POST /``                 CloudEvent sink for Eventarc.
    ``GET  /healthz``          Liveness and readiness.
    ``POST /admin/backfill``   Replicate a collection's current contents.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
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


class Config:
    """Runtime configuration, entirely from the environment.

    Nothing here has a project- or dataset-specific default. The same image is
    deployed to any project by changing environment variables, which is why the
    service refuses to start rather than falling back to a guess.
    """

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

    # The ledger has no primary key, so a delete lands there as an ordinary row
    # tagged operation_type='delete'; only the keyed mirrors remove anything.
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


@app.post("/")
def receive_event():
    """Eventarc sink.

    Returns 2xx for anything that must not be retried. Eventarc retries on 5xx,
    so malformed payloads and unroutable collections are acknowledged rather
    than looped forever; only genuine write failures are surfaced as errors.
    """
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

    try:
        written = _write_event(event)
    except Exception as err:  # noqa: BLE001 - retryable, let Eventarc redeliver
        logger.exception("BigQuery write failed for %s", event.document_path)
        return jsonify(status="error", reason=str(err)), 500

    logger.info(
        "%s %s/%s -> %s",
        event.operation,
        event.collection,
        event.document_id,
        ",".join(f"{k}:{v}" for k, v in written.items()),
    )
    return jsonify(
        status="ok",
        operation=event.operation,
        collection=event.collection,
        document_id=event.document_id,
        written=written,
    ), 200


@app.post("/admin/backfill")
def backfill():
    """Replicate a collection's current contents into BigQuery.

    Eventarc only delivers changes that happen after the trigger exists, so a
    collection seeded beforehand would be invisible. Rather than require a
    specific ordering in deploy.sh, this lets state be reconciled at any point.
    Writes go through the same UPSERT path, so running it twice is harmless.
    """
    body = request.get_json(silent=True) or {}
    collection = body.get("collection") or config.orders_collection
    limit = int(body.get("limit") or 0)

    if collection not in routes:
        return jsonify(status="error", reason=f"no route for {collection}"), 400

    from google.cloud import firestore

    client = firestore.Client(project=config.project_id, database=config.database_id)
    query = client.collection(collection)
    if limit:
        query = query.limit(limit)

    now = datetime.now(timezone.utc)
    processed = failed = 0
    errors: List[str] = []

    for snapshot in query.stream():
        document = snapshot.to_dict() or {}
        ctx = ColumnContext(
            document=document,
            document_id=snapshot.id,
            operation="insert",
            change_timestamp=_snapshot_time(snapshot, now),
            raw_json=json.dumps(document, default=str),
        )
        sequence = sequence_number(ctx.change_timestamp)
        try:
            for spec in routes[collection].tables:
                table_writer = sink.writer_for(spec)
                row = table_writer.build_row(
                    extract_row(spec, ctx),
                    change_type="UPSERT" if spec.supports_cdc else None,
                    change_sequence=sequence if spec.supports_cdc else None,
                )
                table_writer.append([row])
            processed += 1
        except Exception as err:  # noqa: BLE001
            failed += 1
            if len(errors) < 5:
                errors.append(f"{snapshot.id}: {err}")

    client.close()
    logger.info("Backfill of %s: %d ok, %d failed", collection, processed, failed)
    return jsonify(
        status="ok" if not failed else "partial",
        collection=collection,
        processed=processed,
        failed=failed,
        errors=errors,
    ), 200


def _snapshot_time(snapshot, fallback: datetime) -> datetime:
    """Use the document's own update time so backfilled rows sequence correctly."""
    value = getattr(snapshot, "update_time", None)
    if value is None:
        return fallback
    converted = getattr(value, "ToDatetime", None)
    if callable(converted):
        result = converted()
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return fallback


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

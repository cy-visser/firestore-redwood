"""Redwood Retail agent bridge forwarding Eventarc CloudEvents to Agent Engine."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from firestore_event import (  # noqa: E402
    DocumentEvent,
    parse_document_event,
    parse_event_body,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent_bridge")

app = Flask(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT")
REGION = os.getenv("GCP_REGION", "europe-west4")
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "redwood")
SESSIONS_COLLECTION = os.getenv("FIRESTORE_SESSIONS_COLLECTION", "customer_sessions")

# Firestore collection for latency traces.
TRACES_COLLECTION = os.getenv("FIRESTORE_TRACES_COLLECTION", "pipeline_traces")
TRACE_TTL_DAYS = 30
AGENT_DISPLAY_NAME = os.getenv("AGENT_DISPLAY_NAME", "redwood-loyalty-agent")
PENDING_STATUS = "PENDING"

_engine = None
_engine_lock = threading.Lock()

_firestore = None
_firestore_lock = threading.Lock()


def get_firestore():
    """Get or create the Firestore client for trace recording."""
    global _firestore
    if _firestore is not None:
        return _firestore

    with _firestore_lock:
        if _firestore is None:
            from google.cloud import firestore

            _firestore = firestore.Client(project=PROJECT_ID, database=DATABASE_ID)
        return _firestore


def _write_trace(session_id: str, fields: Dict[str, Any]) -> None:
    """Merge latency metrics into pipeline_traces/{session_id}.

    Called more than once per event, and merged rather than set: the agent
    writes its own spans into the same document from inside Agent Engine, and
    this service writes twice, once on receipt and once when the agent
    returns.
    """
    try:
        now = datetime.now(timezone.utc)
        document = dict(fields)
        document["sessionId"] = session_id
        # recordedAt is the collection's ordering key and is shared with the
        # agent. bridgeRecordedAt is this service's own, so which half of the
        # trace landed last stays legible.
        document["recordedAt"] = now
        document["bridgeRecordedAt"] = now
        document["expireAt"] = now + timedelta(days=TRACE_TTL_DAYS)

        get_firestore().collection(TRACES_COLLECTION).document(session_id).set(
            document, merge=True
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write trace for session %s: %s", session_id, exc)


def get_engine():
    """Resolve and cache the deployed agent engine."""
    global _engine
    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:
            return _engine

        import vertexai
        from vertexai import agent_engines

        vertexai.init(project=PROJECT_ID, location=REGION)
        matches = [e for e in agent_engines.list()
                   if e.display_name == AGENT_DISPLAY_NAME]

        if not matches:
            raise RuntimeError(
                f"No Agent Engine deployment named '{AGENT_DISPLAY_NAME}' in "
                f"{PROJECT_ID}/{REGION}. Deploy it with "
                f"scripts/deploy_agent_engine.py."
            )
        if len(matches) > 1:
            raise RuntimeError(
                f"{len(matches)} Agent Engine deployments share the display name "
                f"'{AGENT_DISPLAY_NAME}'; cannot choose between them."
            )

        _engine = matches[0]
        logger.info("Resolved agent engine %s", _engine.resource_name)
        return _engine


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


def should_process(event: DocumentEvent) -> Tuple[bool, str]:
    """Decide whether this change is a new session awaiting the agent."""
    if event.collection != SESSIONS_COLLECTION:
        return False, f"not a session collection ({event.collection})"

    if event.is_delete:
        return False, "delete"

    document = event.effective_data or {}
    status = document.get("agentProcessingStatus")

    if status != PENDING_STATUS:
        return False, f"status is {status!r}, not {PENDING_STATUS}"

    if not document.get("customerId"):
        return False, "no customerId"

    return True, "pending session"


@app.get("/healthz")
def healthz():
    return jsonify(
        status="ok",
        project=PROJECT_ID,
        region=REGION,
        collection=SESSIONS_COLLECTION,
        agent=AGENT_DISPLAY_NAME,
        agent_resolved=_engine is not None,
    ), 200


@app.post("/")
def receive_event():
    received = time.monotonic()
    received_at = datetime.now(timezone.utc)

    event_id, event_type, event_time = _cloud_event_headers()

    try:
        payload = parse_event_body(request.get_data(), request.content_type)
        event = parse_document_event(payload, event_id, event_type, event_time)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not parse event %s: %s", event_id, exc)
        return jsonify(status="dropped", reason="unparseable", eventId=event_id), 200

    process, reason = should_process(event)
    if not process:
        logger.info("Ignoring %s/%s: %s",
                    event.collection, event.document_id, reason)
        return jsonify(
            status="ignored",
            reason=reason,
            sessionId=event.document_id,
        ), 200

    session_id = event.document_id
    customer_id = (event.effective_data or {}).get("customerId")
    logger.info("Invoking agent for session %s", session_id)

    delivery_ms: Optional[float] = None
    if event_time is not None:
        delivery_ms = max(0.0, (received_at - event_time).total_seconds() * 1000.0)

    overhead_ms = (time.monotonic() - received) * 1000.0

    # Written before the agent is called, not after. The delivery hop and this
    # service's own overhead are both known now, and engine.query() can take
    # twenty seconds on a cold start -- the console should light up the
    # Eventarc and Cloud Run nodes immediately rather than staying blank until
    # the agent is finished.
    _write_trace(session_id, {
        "kind": "SESSION",
        "customerId": customer_id,
        "eventId": event_id,
        "eventTime": event_time,
        "bridgeReceivedAt": received_at,
        "eventarcDeliveryMs": delivery_ms,
        "bridgeOverheadMs": overhead_ms,
        "bridgeStatus": "DISPATCHED",
    })

    call_started = time.monotonic()
    try:
        engine = get_engine()
        result = engine.query(session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent call failed for session %s: %s", session_id, exc)
        # A failed call is still a measurement, and a console showing
        # "AGENT_ERROR" after 30s is far more use than one showing nothing.
        _write_trace(session_id, {
            "agentCallMs": (time.monotonic() - call_started) * 1000.0,
            "bridgeStatus": "AGENT_ERROR",
            "bridgeError": str(exc)[:500],
        })
        return jsonify(status="error", sessionId=session_id, error=str(exc)), 500

    agent_call_ms = (time.monotonic() - call_started) * 1000.0

    action = _result_field(result, "action")
    logger.info(
        "Session %s -> %s (delivery~%sms, overhead %.0fms, agent %.0fms)",
        session_id,
        action,
        f"{delivery_ms:.0f}" if delivery_ms is not None else "?",
        overhead_ms,
        agent_call_ms,
    )

    _write_trace(session_id, {
        "agentCallMs": agent_call_ms,
        "bridgeStatus": "COMPLETE",
        "bridgeAction": action,
    })

    return jsonify(
        status="ok",
        sessionId=session_id,
        action=action,
    ), 200


def _result_field(result: Any, key: str) -> Any:
    """Safely extract a field from the agent's query result."""
    if isinstance(result, dict):
        return result.get(key)
    return getattr(result, key, None)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

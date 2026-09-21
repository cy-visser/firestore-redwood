"""
Redwood Retail agent bridge.

Sits between a Firestore session write and the loyalty agent. Agent Engine
cannot be an Eventarc destination, so something has to receive the CloudEvent
and turn it into an agent call; this is that thing and nothing more. All the
retention logic lives in the agent.

The shape is deliberately the same as the CDC service, which already proved
Eventarc delivery works against this named Enterprise Native database:
protobuf encoded CloudEvents in binary mode, one trigger per collection. It
reuses the CDC service's event parser rather than reimplementing the protojson
decoding, so there is one decoder to be correct.

Delivery is at-least-once and unordered, so the same session can arrive twice.
That is handled by the agent rather than here: process_session claims a session
before working on it and ignores one that is already claimed. This service
therefore does not try to deduplicate, because a second opinion about what
counts as a duplicate is a second thing that can disagree.

Endpoints
    ``POST /``          CloudEvent sink for Eventarc.
    ``GET  /healthz``   Liveness and readiness.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime
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
SESSIONS_COLLECTION = os.getenv("FIRESTORE_SESSIONS_COLLECTION", "customer_sessions")

# The engine is found by display name rather than by resource id. An id
# recorded in configuration goes stale the moment the agent is redeployed, and
# the previous version of this stack shipped exactly that: a Terraform default
# pointing at a reasoning engine in a different project.
AGENT_DISPLAY_NAME = os.getenv("AGENT_DISPLAY_NAME", "redwood-loyalty-agent")

# Only act on sessions that are waiting to be processed. Without this the agent
# is re-invoked every time it writes its own result back to the session
# document, since that write raises another change event.
PENDING_STATUS = "PENDING"

_engine = None
_engine_lock = threading.Lock()


def get_engine():
    """Resolve the deployed agent once and keep it for the process lifetime.

    Listing engines costs a round trip, so it is done on first use rather than
    per request, but not at import: a lookup failure at import would leave the
    container unable to start and hide the reason behind a startup probe
    timeout.
    """
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

    # A session the agent has already handled, or is handling, writes its
    # status back. Reacting to that write would loop.
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
    event_id, event_type, event_time = _cloud_event_headers()

    try:
        payload = parse_event_body(request.get_data(), request.content_type)
        event = parse_document_event(payload, event_id, event_type, event_time)
    except Exception as exc:  # noqa: BLE001
        # A malformed event will never parse, so returning an error only makes
        # Eventarc redeliver it until it expires. Acknowledge and record it.
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
    logger.info("Invoking agent for session %s", session_id)

    try:
        engine = get_engine()
        result = engine.query(session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        # Returning 500 makes Eventarc retry, which is what we want for a
        # transient Vertex AI or Firestore failure. The agent claims sessions
        # before working, so a retry cannot double-issue an offer.
        logger.exception("Agent call failed for session %s: %s", session_id, exc)
        return jsonify(status="error", sessionId=session_id, error=str(exc)), 500

    action = _result_field(result, "action")
    logger.info("Session %s -> %s", session_id, action)

    return jsonify(
        status="ok",
        sessionId=session_id,
        action=action,
    ), 200


def _result_field(result: Any, key: str) -> Any:
    """Read a field from the agent's reply without assuming its container type.

    query() returns a plain dict today, but the SDK has wrapped responses in an
    object before, and the only thing this service does with the reply is log
    it. Failing the request over an attribute lookup would be a poor trade.
    """
    if isinstance(result, dict):
        return result.get(key)
    return getattr(result, key, None)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

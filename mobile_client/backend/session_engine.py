"""
Customer login sessions for the Redwood Retail mobile client.

A login is a Firestore write and nothing else. The mobile client posts to
``/api/session/login``, this module writes one ``customer_sessions`` document,
and Eventarc carries it to the bridge and then to the loyalty agent. Nothing
here calls the agent, and nothing here waits for it: the browser watches the
session document instead, over the SSE bridge in ``sse.py``.

The document shape is the one ``scripts/verify_demo_flow.py`` already writes,
so the verification script and the real client exercise the same path.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from google.cloud.firestore_v1.base_query import FieldFilter

from mobile_client.backend.order_engine import DEMO_PRINCIPALS

logger = logging.getLogger("redwood-mobile-api.session")

SESSIONS_COLLECTION = "customer_sessions"
OFFERS_COLLECTION = "loyalty_offers"

# Session TTL in days (matching Firestore index TTL policy).
SESSION_TTL_DAYS = 30

# In-flight session statuses eligible for reuse.
IN_FLIGHT_STATUSES = ("PENDING", "PROCESSING")


def session_reuse_max_age_seconds() -> float:
    """Maximum age in seconds for an in-flight session to be eligible for reuse."""
    raw = os.getenv("SESSION_REUSE_MAX_AGE_SECONDS", "").strip()
    if not raw:
        return 120.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "SESSION_REUSE_MAX_AGE_SECONDS=%r is not a number; using 120s.", raw
        )
        return 120.0
    return value if value > 0 else 120.0


# Maximum in-flight sessions to scan when finding latest session.
IN_FLIGHT_SCAN_LIMIT = 10

# Maximum offers to scan when resolving pricing.
OFFER_SCAN_LIMIT = 5


class UnknownPrincipalError(ValueError):
    """Raised when a login names a principal the demo does not have."""


class OfferNotFoundError(LookupError):
    """Raised when a claim names an offer that does not exist."""


class OfferNotClaimableError(ValueError):
    """Raised when a claim names an offer that is not ACTIVE."""


def resolve_customer_id(principal_id: str) -> str:
    """Map an IAM principal short name onto its seeded customer id.

    ``demo1`` is the principal; ``cust_demo1`` is the customer the churn model
    and the agent know about. Conflating the two is what made an earlier
    version of the login find no churn row at all.
    """
    profile = DEMO_PRINCIPALS.get(principal_id)
    if profile is None:
        raise UnknownPrincipalError(
            f"Unknown principal '{principal_id}'. Expected one of: "
            f"{', '.join(sorted(DEMO_PRINCIPALS))}."
        )
    return profile["customerId"]


def build_session_doc(
    principal_id: str,
    session_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the session document a login writes."""
    customer_id = resolve_customer_id(principal_id)
    profile = DEMO_PRINCIPALS[principal_id]
    now = now or datetime.now(timezone.utc)
    session_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"

    return {
        "sessionId": session_id,
        "customerId": customer_id,
        "principalId": principal_id,
        "iamPrincipal": profile.get("iamPrincipal"),
        "customerName": profile.get("displayName"),
        "agentProcessingStatus": "PENDING",
        "status": "PENDING",
        "loginTimestamp": now,
        "loginAt": now,
        "createdAt": now,
        "expireAt": now + timedelta(days=SESSION_TTL_DAYS),
        "channel": "MOBILE_APP",
        "offerId": None,
        "activeOfferId": None,
        "skipReason": None,
    }


def as_datetime(value: Any) -> Optional[datetime]:
    """Coerce a Firestore value to an aware UTC datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def session_started_at(session: Dict[str, Any]) -> Optional[datetime]:
    """Extract when the login that produced this session happened."""
    for field in ("loginTimestamp", "loginAt", "createdAt"):
        started = as_datetime(session.get(field))
        if started is not None:
            return started
    return None


def find_in_flight_session(
    db: Any,
    customer_id: str,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Return the customer's unfinished *recent* session, if they have one.

    Eventarc delivery is at-least-once and a customer can tap sign in twice.
    The agent's transactional claim makes a single session safe against
    redelivery, but it cannot help with two *different* sessions for one
    customer arriving together: both are legitimately claimable, and because
    neither offer exists yet both pass the cooldown check and issue one.
    Reusing the session already in flight removes the only way a user can
    cause that.

    The reuse is bounded by ``session_reuse_max_age_seconds``. Without a bound
    a session that never got answered -- the bridge was down, the agent
    crashed -- is reused by every later login forever, and the app sits on a
    spinner waiting for a document nothing is ever going to touch again.

    Served by the (agentProcessingStatus ASC, loginTimestamp ASC) index, which
    is one more reason the login writes loginTimestamp.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=session_reuse_max_age_seconds())

    fresh: List[Dict[str, Any]] = []
    for status in IN_FLIGHT_STATUSES:
        query = (
            db.collection(SESSIONS_COLLECTION)
            .where(filter=FieldFilter("customerId", "==", customer_id))
            .where(filter=FieldFilter("agentProcessingStatus", "==", status))
            .limit(IN_FLIGHT_SCAN_LIMIT)
        )
        for snap in query.stream():
            data = snap.to_dict() or {}
            data.setdefault("sessionId", snap.id)
            started = session_started_at(data)
            if started is None or started < cutoff:
                continue
            fresh.append(data)

    if not fresh:
        return None
    return max(fresh, key=lambda item: session_started_at(item) or cutoff)


def start_session(
    db: Any,
    principal_id: str,
    now: Optional[datetime] = None,
    on_commit: Optional[Callable[[str, float], None]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Log a principal in, reusing a recent in-flight session where one exists.

    ``on_commit`` is called with ``(session_id, elapsed_ms)`` when a new session
    document is actually written, and not at all when an in-flight one is
    reused. It exists so the console can report the client-to-Firestore write
    latency without this module having to know the console exists -- the
    console module already imports this one, so the dependency cannot go the
    other way.

    Returns ``(session, reused)``.
    """
    customer_id = resolve_customer_id(principal_id)

    existing = find_in_flight_session(db, customer_id, now=now)
    if existing is not None:
        logger.info(
            "Login for %s reused in-flight session %s (%s).",
            customer_id, existing.get("sessionId"), existing.get("agentProcessingStatus"),
        )
        return existing, True

    doc = build_session_doc(principal_id, now=now)

    started = time.monotonic()
    db.collection(SESSIONS_COLLECTION).document(doc["sessionId"]).set(doc)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    logger.info(
        "Login for %s wrote session %s in %.0fms.",
        customer_id, doc["sessionId"], elapsed_ms,
    )

    if on_commit is not None:
        try:
            on_commit(doc["sessionId"], elapsed_ms)
        except Exception as exc:  # noqa: BLE001
            logger.warning("start_session on_commit hook failed: %s", exc)

    return doc, False


def get_session(db: Any, session_id: str) -> Optional[Dict[str, Any]]:
    """Read one session document, or None when it does not exist."""
    snap = db.collection(SESSIONS_COLLECTION).document(session_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data.setdefault("sessionId", snap.id)
    return data


def get_offer(db: Any, offer_id: str) -> Optional[Dict[str, Any]]:
    """Read one offer document, or None when it does not exist."""
    snap = db.collection(OFFERS_COLLECTION).document(offer_id).get()
    if not snap.exists:
        return None
    return snap.to_dict() or {}


def offers_for_session(db: Any, session_id: str) -> List[Dict[str, Any]]:
    """Return the offers written for a session."""
    query = db.collection(OFFERS_COLLECTION).where(
        filter=FieldFilter("sessionId", "==", session_id)
    )
    return [doc.to_dict() or {} for doc in query.stream()]


def offer_is_spendable(
    offer: Optional[Dict[str, Any]],
    customer_id: str,
    now: Optional[datetime] = None,
) -> bool:
    """Whether this offer may price an order for this customer."""
    if not offer:
        return False
    if offer.get("customerId") != customer_id:
        return False
    if offer.get("orderId"):
        return False

    status = offer.get("status")
    if status not in ("ACTIVE", "REDEEMED"):
        return False

    now = now or datetime.now(timezone.utc)
    valid_until = as_datetime(offer.get("validUntil"))
    if valid_until is not None and valid_until < now:
        return False
    return True


def find_spendable_offer(
    db: Any,
    customer_id: str,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Find the latest spendable offer for this customer."""
    query = (
        db.collection(OFFERS_COLLECTION)
        .where(filter=FieldFilter("customerId", "==", customer_id))
        .order_by("createdAt", direction="DESCENDING")
        .limit(OFFER_SCAN_LIMIT)
    )
    for snap in query.stream():
        offer = snap.to_dict() or {}
        offer.setdefault("offerId", snap.id)
        if offer_is_spendable(offer, customer_id, now=now):
            return offer
    return None


def resolve_offer_for_order(
    db: Any,
    customer_id: str,
    offer_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Find the offer that prices this order server side."""
    if offer_id:
        offer = get_offer(db, offer_id)
        if offer is None:
            logger.info("Offer %s not found; pricing at list.", offer_id)
            return None
        offer.setdefault("offerId", offer_id)
        if not offer_is_spendable(offer, customer_id, now=now):
            logger.info(
                "Offer %s is not spendable for %s (status=%s, orderId=%s); "
                "pricing at list.",
                offer_id, customer_id, offer.get("status"), offer.get("orderId"),
            )
            return None
        return offer

    return find_spendable_offer(db, customer_id, now=now)


def claim_offer(
    db: Any,
    offer_id: str,
    order_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Redeem an offer, optionally recording the order it was spent on."""
    now = now or datetime.now(timezone.utc)
    ref = db.collection(OFFERS_COLLECTION).document(offer_id)
    snap = ref.get()
    if not snap.exists:
        raise OfferNotFoundError(f"Offer {offer_id} not found")

    offer = snap.to_dict() or {}
    status = offer.get("status")
    spending_a_claimed_offer = (
        status == "REDEEMED" and bool(order_id) and not offer.get("orderId")
    )
    if status != "ACTIVE" and not spending_a_claimed_offer:
        raise OfferNotClaimableError(
            f"Offer {offer_id} is {status}, not ACTIVE"
        )

    update: Dict[str, Any] = {"status": "REDEEMED", "claimedAt": now.isoformat()}
    if order_id:
        update["orderId"] = order_id

    ref.update(update)
    offer.update(update)
    logger.info("Offer %s claimed.", offer_id)
    return offer

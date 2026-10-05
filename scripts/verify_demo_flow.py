#!/usr/bin/env python3
"""Verify the Redwood Retail demo flow against a deployed environment."""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from google.cloud import firestore  # noqa: E402
from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: E402

SESSIONS_COLLECTION = "customer_sessions"
OFFERS_COLLECTION = "loyalty_offers"

# Expected offer outcome per customer based on seeded order histories.
EXPECTATIONS = {
    "cust_demo2": True,
    "cust_demo1": False,
}

NON_TERMINAL_STATUSES = {None, "", "PENDING", "PROCESSING"}


def wait_for_agent(
    fs: firestore.Client, session_id: str, timeout: int
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Poll the session until the agent records a terminal status."""
    deadline = time.time() + timeout
    data: Dict[str, Any] = {}
    while time.time() < deadline:
        data = fs.collection(SESSIONS_COLLECTION).document(session_id).get().to_dict() or {}
        status = data.get("agentProcessingStatus")
        if status not in NON_TERMINAL_STATUSES:
            return status, data
        time.sleep(2)
    return None, data


def offers_for(fs: firestore.Client, customer_id: str) -> Dict[str, Dict[str, Any]]:
    query = fs.collection(OFFERS_COLLECTION).where(
        filter=FieldFilter("customerId", "==", customer_id)
    )
    return {d.id: (d.to_dict() or {}) for d in query.stream()}


def run_case(
    fs: firestore.Client, customer_id: str, timeout: int, keep: bool
) -> bool:
    expect_offer = EXPECTATIONS.get(customer_id)
    session_id = f"sess_verify_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)

    print(f"\n[{customer_id}]")
    before = set(offers_for(fs, customer_id))

    fs.collection(SESSIONS_COLLECTION).document(session_id).set({
        "sessionId": session_id,
        "customerId": customer_id,
        "agentProcessingStatus": "PENDING",
        "status": "PENDING",
        "loginTimestamp": now,
        "loginAt": now,
        "createdAt": now,
        "expireAt": now + timedelta(days=1),
        "channel": "VERIFICATION",
    })
    print(f"  wrote {SESSIONS_COLLECTION}/{session_id}")

    started = time.time()
    status, data = wait_for_agent(fs, session_id, timeout)
    elapsed = time.time() - started

    ok = True
    if status is None:
        if data.get("agentProcessingStatus") == "PROCESSING":
            worker = data.get("agentWorkerId") or "unknown"
            print(f"  FAIL  claimed by {worker} but never finished within {timeout}s")
            print("        The event was delivered, so the fault is inside the agent.")
            print("        Check the ReasoningEngine logs for the failing step.")
        else:
            print(f"  FAIL  never claimed within {timeout}s")
            print("        The usual cause is the Eventarc trigger not reaching the")
            print("        bridge, or the bridge failing to resolve the agent.")
        ok = False
    else:
        print(f"  agent responded in {elapsed:.0f}s: {status}"
              + (f" ({data['skipReason']})" if data.get("skipReason") else ""))

        after = offers_for(fs, customer_id)
        new_ids = set(after) - before
        got_offer = bool(new_ids) or bool(data.get("offerId"))

        if expect_offer is None:
            print(f"  note  no expectation recorded for {customer_id}, "
                  f"offer issued: {got_offer}")
        elif got_offer != expect_offer:
            print(f"  FAIL  expected offer={expect_offer}, got offer={got_offer}")
            ok = False
        else:
            print(f"  ok    offer={got_offer} as expected")

        for oid in new_ids:
            offer = after[oid]
            print(f"        {oid}: {offer.get('discountPercent')}% "
                  f"{offer.get('status')} "
                  f"churn={offer.get('churnProbability')} "
                  f"tier={offer.get('churnTier')}")

    if not keep:
        fs.collection(SESSIONS_COLLECTION).document(session_id).delete()
        for oid in set(offers_for(fs, customer_id)) - before:
            fs.collection(OFFERS_COLLECTION).document(oid).delete()
        print("  cleaned up")

    return ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Redwood Retail demo flow end to end"
    )
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"))
    parser.add_argument(
        "--database",
        default=os.getenv("FIRESTORE_DATABASE_ID", "redwood"),
    )
    parser.add_argument(
        "--customer",
        action="append",
        dest="customers",
        help="Customer to test; repeatable. Defaults to both demo customers.",
    )
    parser.add_argument(
        "--timeout", type=int, default=180,
        help="Seconds to wait for the agent. The first call of the day pays "
             "an Agent Engine cold start.",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="Leave the test session and any offer in place.",
    )
    args = parser.parse_args()

    if not args.project:
        raise SystemExit(
            "--project is required (set GCP_PROJECT_ID in .env or pass it)."
        )

    customers: List[str] = args.customers or ["cust_demo2", "cust_demo1"]

    print("=" * 62)
    print(" Redwood Retail: demo flow verification")
    print("=" * 62)
    print(f"Project:   {args.project}")
    print(f"Database:  {args.database}")
    print(f"Customers: {', '.join(customers)}")

    fs = firestore.Client(project=args.project, database=args.database)

    results = {c: run_case(fs, c, args.timeout, args.keep) for c in customers}

    print("\n" + "=" * 62)
    failed = [c for c, ok in results.items() if not ok]
    if failed:
        print(f" FAILED: {', '.join(failed)}")
        return 1
    print(" Demo flow verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

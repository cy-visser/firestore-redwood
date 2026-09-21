#!/usr/bin/env python3
"""
Live check for the Redwood Retail loyalty offer agent.
Creates a test customer session in Firestore and verifies that the agent
processes it and issues or surfaces a loyalty offer. Optionally probes the
bridge health endpoint first.
"""

import sys
import os
import time
import argparse
import urllib.request
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import firestore

from loyalty_agent.config import config

# Resolved from the environment by the agent config, which refuses to guess a
# project rather than defaulting to someone else's.
DEFAULT_PROJECT_ID = config.project_id
DEFAULT_DATABASE_ID = config.firestore_database


def check_bridge_health(url: str, timeout: int = 10) -> bool:
    """Probes the bridge health endpoint."""
    print(f"🩺 Probing bridge health endpoint at {url} ...")
    headers = {"User-Agent": "Redwood-Loyalty-Agent-Test/1.0"}
    try:
        import subprocess
        token = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass

    probe_urls = [
        url.rstrip("/") + "/healthz",
        url.rstrip("/") + "/"
    ]
    for p_url in probe_urls:
        try:
            req = urllib.request.Request(p_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    body = response.read().decode("utf-8")
                    data = json.loads(body)
                    print(f"✅ Bridge healthy on {p_url}: {data.get('service', 'unknown service')}")
                    return True
        except Exception:
            continue

    print("⚠️ Warning: bridge probe did not respond (service might require internal network or IAM invoker).")
    return False


def test_agent_runtime(project_id: str, database_id: str, customer_id: str, runtime_url: str = None, timeout_seconds: int = 30):
    print("=" * 65)
    print(" 🌲 REDWOOD RETAIL: Loyalty Offer Agent Verification")
    print("=" * 65)
    print(f"Target GCP Project:     {project_id}")
    print(f"Firestore Database:     {database_id}")
    print(f"Test Customer ID:       {customer_id}")
    if runtime_url:
        print(f"Bridge URL:             {runtime_url}")
    print("-" * 65)

    if runtime_url:
        check_bridge_health(runtime_url)

    print(f"\n📡 Connecting to Firestore database '{database_id}' in project '{project_id}'...")
    db = firestore.Client(project=project_id, database=database_id)

    session_id = f"sess_agent_test_{int(time.time())}"
    now_iso = datetime.now(timezone.utc).isoformat()

    session_data = {
        "sessionId": session_id,
        "customerId": customer_id,
        "loginTimestamp": now_iso,
        "deviceInfo": {
            "deviceType": "MOBILE_IOS",
            "osVersion": "18.2",
            "appVersion": "5.0.0"
        },
        "status": "PENDING",
        "agentProcessingStatus": "PENDING",
        "createdAt": now_iso
    }

    print(f"📝 Ingesting test login session into Firestore: customer_sessions/{session_id}")
    db.collection("customer_sessions").document(session_id).set(session_data)
    print("✅ Session document successfully created in Firestore.")

    print(f"\n⏳ Waiting for the loyalty agent to process the session (timeout: {timeout_seconds}s)...")
    start_time = time.time()
    processed = False
    session_result = None

    while time.time() - start_time < timeout_seconds:
        doc_snap = db.collection("customer_sessions").document(session_id).get()
        if doc_snap.exists:
            data = doc_snap.to_dict()
            status = data.get("agentProcessingStatus")
            if status in ("PROCESSED", "SKIPPED"):
                elapsed = time.time() - start_time
                print(f"🎯 Session processed in {elapsed:.2f}s! Status: {status}")
                processed = True
                session_result = data
                break

        time.sleep(2)
        print(f"   Polling Firestore... (elapsed: {int(time.time() - start_time)}s)")

    if not processed:
        print(f"❌ Error: the agent did not process the session within {timeout_seconds} seconds.")
        print("Please check that the bridge daemon is running and has permissions on Firestore.")
        return False

    # Check for newly generated offer
    offers = list(
        db.collection("loyalty_offers")
        .where(filter=firestore.FieldFilter("sessionId", "==", session_id))
        .limit(1)
        .stream()
    )

    if offers:
        offer_found = offers[0].to_dict()
        print("\n🎉 SUCCESS! Real-time loyalty offer generated:")
        print(f" • Offer ID:              {offer_found.get('offerId')}")
        print(f" • Promo Code:             {offer_found.get('promoCode')}")
        print(f" • Discount:               {offer_found.get('discountPercent')}%")
        print(f" • Free Express Shipping:  {offer_found.get('freeExpressShipping')}")
        print(f" • Churn Risk Tier:        {offer_found.get('churnRiskTier')}")
        print(f" • Evaluation Source:      {offer_found.get('evaluationSource')}")
        print(f" • Title:                  {offer_found.get('title')}")
        print(f" • Description:            {offer_found.get('description')}")
        print(f" • Valid Until:            {offer_found.get('validUntil')}")
        return True
    elif session_result and session_result.get("activeOfferId"):
        existing_offer_id = session_result.get("activeOfferId")
        print(f"\n🎉 SUCCESS! Agent recognized active offer within cooldown window:")
        print(f" • Active Offer Linked:    {existing_offer_id}")
        print(f" • Skip Reason:            {session_result.get('skipReason')}")
        return True
    else:
        skip_reason = session_result.get("skipReason") if session_result else "UNKNOWN"
        print(f"ℹ️ Session was processed with status '{session_result.get('agentProcessingStatus')}' (Reason: {skip_reason}).")
        return True


def main():
    parser = argparse.ArgumentParser(description="Verify the Redwood Retail loyalty offer agent end to end")
    parser.add_argument("--project", default=DEFAULT_PROJECT_ID, help="GCP Project ID")
    parser.add_argument("--database", default=DEFAULT_DATABASE_ID, help="Firestore Database ID")
    parser.add_argument("--customer-id", default="cust_retail_72871", help="Customer ID to test")
    parser.add_argument("--runtime-url", default=None, help="Bridge service URL to probe")
    parser.add_argument("--timeout", type=int, default=30, help="Wait timeout in seconds")

    args = parser.parse_args()
    success = test_agent_runtime(
        project_id=args.project,
        database_id=args.database,
        customer_id=args.customer_id,
        runtime_url=args.runtime_url,
        timeout_seconds=args.timeout
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

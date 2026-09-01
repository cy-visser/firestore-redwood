#!/usr/bin/env python3
"""
High-Performance Retail Dataset Generator for Firestore Enterprise & BigQuery CDC.
Generates configurable volumes (from 1k to 1M+ documents) of rich retail transaction,
sentiment, and churn analysis data into Firestore Enterprise in Native Mode.
"""

import os
import sys
import time
import math
import json
import random
import signal
import argparse
from datetime import datetime, timezone, timedelta
from multiprocessing import Manager, Process, Queue
from queue import Empty
from dotenv import load_dotenv, find_dotenv
from google.cloud import firestore
from google.api_core.exceptions import GoogleAPICallError, RetryError

# Load environment configuration from .env if present
load_dotenv(find_dotenv(usecwd=True))

# Import catalog and synthesis data
sys.path.insert(0, os.path.dirname(__file__))
from retail_catalog import (
    CATALOG_ITEMS, SHIPPING_CITIES, WAREHOUSES, CARRIERS,
    POSITIVE_FEEDBACK, NEUTRAL_FEEDBACK, NEGATIVE_FEEDBACK, COMPLAINT_REASONS,
    LOYALTY_TIERS, FEEDBACK_SENTIMENT_RANGES
)
from firestore_auth import get_firestore_native_client

DEFAULT_PROJECT = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT")
DEFAULT_REGION = os.getenv("GCP_REGION")
DEFAULT_DATABASE = os.getenv("FIRESTORE_DATABASE_ID") or os.getenv("FIRESTORE_DATABASE")
DEFAULT_COLLECTION = os.getenv("FIRESTORE_COLLECTION")
DEFAULT_CHUNK_SIZE = 500
DEFAULT_WORKERS = min(os.cpu_count() or 8, 16)



def generate_single_order(worker_id, order_index, base_time):
    """Generate a single richly structured retail order document with 4-pillar ML churn features."""
    rand_id = f"{random.randint(100000, 999999)}{random.choice(['A', 'B', 'C', 'D', 'E'])}{random.randint(10, 99)}"
    order_id = f"ORD-26-W{worker_id}-{rand_id}-IDX{order_index:07d}"
    customer_num = random.randint(1000, 99999)
    customer_id = f"cust_retail_{customer_num:05d}"
    
    # 1. Demographics & Loyalty Tier Distribution
    loyalty_weights = [t["weight"] for t in LOYALTY_TIERS]
    loyalty_choice = random.choices(LOYALTY_TIERS, weights=loyalty_weights, k=1)[0]
    loyalty_tier = loyalty_choice["tier"]
    is_loyalty_member = loyalty_choice["isMember"]
    tier_discount_rate = loyalty_choice["discountRate"]
    account_age_days = random.randint(45, 1800)

    # Customer segment mapping
    if loyalty_tier == "ENTERPRISE_VIP":
        customer_segment = "ENTERPRISE_VIP"
        num_items = random.randint(3, 8)
        qty_multiplier = random.randint(2, 6)
    elif loyalty_tier in ["GOLD", "PLATINUM"]:
        customer_segment = "RETAIL_PRO"
        num_items = random.randint(2, 5)
        qty_multiplier = random.randint(1, 3)
    elif loyalty_tier in ["SILVER", "BRONZE"]:
        customer_segment = "STANDARD_LOYALTY"
        num_items = random.randint(1, 3)
        qty_multiplier = 1
    else:
        customer_segment = "CASUAL_SHOPPER"
        num_items = random.randint(1, 2)
        qty_multiplier = 1

    # Line Items
    selected_products = random.sample(CATALOG_ITEMS, min(num_items, len(CATALOG_ITEMS)))
    line_items = []
    subtotal = 0.0
    total_cost = 0.0
    total_weight = 0.0

    for prod in selected_products:
        qty = random.randint(1, 3) * qty_multiplier
        item_total = round(qty * prod["unitPrice"], 2)
        subtotal += item_total
        total_cost += round(qty * prod["cost"], 2)
        total_weight += round(qty * random.uniform(1.2, 14.5), 2)
        
        line_items.append({
            "sku": prod["sku"],
            "name": prod["name"],
            "category": prod["category"],
            "quantity": qty,
            "unitPrice": prod["unitPrice"],
            "totalPrice": item_total,
            "allocatedWarehouse": random.choice(WAREHOUSES)
        })

    # Financials
    tax_rate = 0.21
    tax_amount = round(subtotal * tax_rate, 2)
    shipping_fee = round(random.choice([0.0, 45.0, 95.0, 150.0, 220.0]), 2)
    
    # Discount calculation
    discount_total = round(subtotal * tier_discount_rate, 2)
    if discount_total == 0.0 and random.random() < 0.20:
        discount_total = round(subtotal * 0.05, 2)

    grand_total = round(subtotal - discount_total + tax_amount + shipping_fee, 2)
    profit_margin = round((grand_total - total_cost - tax_amount) / max(grand_total, 1.0), 3)

    # 2. Correlated Operational, Engagement, Support & Transactional Profiles
    profile_roll = random.random()

    if profile_roll < 0.22:
        # Disengaged / High Friction Operational Profile
        days_since_last_purchase = random.randint(65, 180)
        total_spend_90d = round(grand_total * random.uniform(0.2, 0.8), 2)
        orders_count_12m = random.randint(1, 3)
        avg_order_value = round(grand_total * random.uniform(0.8, 1.2), 2)
        purchase_frequency_monthly = round(random.uniform(0.05, 0.25), 2)

        login_frequency_monthly = random.randint(0, 3)
        avg_session_duration_minutes = round(random.uniform(1.0, 4.2), 1)
        app_engagement_score = round(random.uniform(0.05, 0.30), 2)
        app_sessions_last_30d = random.randint(0, 3)
        cart_abandonment_count = random.randint(3, 9)
        abandoned_cart_value_90d = round(random.uniform(600.0, 5200.0), 2)

        support_tickets_count = random.randint(3, 8)
        open_support_tickets_count = random.randint(1, 4)
        complaints_count = random.randint(1, 4)
        return_frequency = random.randint(2, 6)
        return_rate_pct = round(random.uniform(15.0, 42.0), 1)

        feedback_text = random.choice(NEGATIVE_FEEDBACK)
        feedback_rating = random.choice([1, 2])
        sent_min, sent_max = FEEDBACK_SENTIMENT_RANGES["NEGATIVE"]
        sentiment_score = round(random.uniform(sent_min, sent_max), 3)

        order_status = random.choice(["PROCESSING", "DELIVERED", "RETURN_REQUESTED", "CANCELLED"])
        payment_status = random.choice(["SETTLED", "REFUNDED", "DISPUTED"])

    elif profile_roll < 0.50:
        # Moderate / Inactive Risk Operational Profile
        days_since_last_purchase = random.randint(30, 68)
        total_spend_90d = round(grand_total * random.uniform(1.0, 2.2), 2)
        orders_count_12m = random.randint(4, 9)
        avg_order_value = round(grand_total * random.uniform(0.9, 1.3), 2)
        purchase_frequency_monthly = round(random.uniform(0.35, 0.85), 2)

        login_frequency_monthly = random.randint(4, 10)
        avg_session_duration_minutes = round(random.uniform(4.5, 9.5), 1)
        app_engagement_score = round(random.uniform(0.32, 0.65), 2)
        app_sessions_last_30d = random.randint(4, 15)
        cart_abandonment_count = random.randint(1, 3)
        abandoned_cart_value_90d = round(random.uniform(200.0, 1800.0), 2)

        support_tickets_count = random.randint(1, 3)
        open_support_tickets_count = random.choice([0, 1])
        complaints_count = random.choice([0, 1])
        return_frequency = random.randint(1, 2)
        return_rate_pct = round(random.uniform(5.0, 14.0), 1)

        feedback_text = random.choice(NEUTRAL_FEEDBACK)
        feedback_rating = 3
        sent_min, sent_max = FEEDBACK_SENTIMENT_RANGES["NEUTRAL"]
        sentiment_score = round(random.uniform(sent_min, sent_max), 3)

        order_status = random.choice(["DELIVERED", "SHIPPED", "PROCESSING"])
        payment_status = "SETTLED"

    else:
        # High Engagement / Healthy Customer Profile
        days_since_last_purchase = random.randint(1, 26)
        total_spend_90d = round(grand_total * random.uniform(2.5, 6.5), 2)
        orders_count_12m = random.randint(8, 28)
        avg_order_value = round(grand_total * random.uniform(1.0, 1.8), 2)
        purchase_frequency_monthly = round(random.uniform(1.0, 3.5), 2)

        login_frequency_monthly = random.randint(12, 38)
        avg_session_duration_minutes = round(random.uniform(9.0, 25.0), 1)
        app_engagement_score = round(random.uniform(0.68, 0.98), 2)
        app_sessions_last_30d = random.randint(16, 65)
        cart_abandonment_count = random.choice([0, 1])
        abandoned_cart_value_90d = round(random.uniform(0.0, 450.0), 2)

        support_tickets_count = random.choice([0, 1])
        open_support_tickets_count = 0
        complaints_count = 0
        return_frequency = random.choice([0, 1])
        return_rate_pct = round(random.uniform(0.0, 4.0), 1)

        feedback_text = random.choice(POSITIVE_FEEDBACK)
        feedback_rating = random.choice([4, 5])
        sent_min, sent_max = FEEDBACK_SENTIMENT_RANGES["POSITIVE"]
        sentiment_score = round(random.uniform(sent_min, sent_max), 3)

        order_status = random.choice(["DELIVERED", "SHIPPED"])
        payment_status = "SETTLED"

    lifetime_spend = round(total_spend_90d * random.uniform(1.6, 5.2) + grand_total, 2)

    # Shipping Address
    city_data = random.choice(SHIPPING_CITIES)
    
    # Timestamps
    created_at = base_time + timedelta(seconds=order_index * 1.5)
    updated_at = created_at + timedelta(minutes=random.randint(5, 45))
    est_delivery = created_at + timedelta(days=random.randint(1, 4))

    doc = {
        "_id": order_id,
        "orderId": order_id,
        "customerId": customer_id,
        "customerName": f"Enterprise Customer #{customer_num}",
        "customerEmail": f"client.{customer_num}@enterprise-logistics.eu",
        "customerSegment": customer_segment,
        "orderStatus": order_status,
        "paymentStatus": payment_status,
        "paymentMethod": random.choice(["INVOICE_NET30", "CREDIT_CARD", "IDEAL", "SEPA_DIRECT_DEBIT"]),
        "currency": "EUR",
        "financials": {
            "subtotal": subtotal,
            "taxAmount": tax_amount,
            "shippingFee": shipping_fee,
            "discountTotal": discount_total,
            "grandTotal": grand_total,
            "profitMargin": profit_margin
        },
        "transactionalMetrics": {
            "totalSpend90d": total_spend_90d,
            "lifetimeSpend": lifetime_spend,
            "avgOrderValue": avg_order_value,
            "purchaseFrequencyMonthly": purchase_frequency_monthly,
            "daysSinceLastPurchase": days_since_last_purchase,
            "ordersCountLast12m": orders_count_12m
        },
        "engagement": {
            "loginFrequencyMonthly": login_frequency_monthly,
            "avgSessionDurationMinutes": avg_session_duration_minutes,
            "appEngagementScore": app_engagement_score,
            "appSessionsLast30d": app_sessions_last_30d,
            "cartAbandonmentCount": cart_abandonment_count,
            "abandonedCartValue90d": abandoned_cart_value_90d
        },
        "supportMetrics": {
            "supportTicketsCount": support_tickets_count,
            "openSupportTicketsCount": open_support_tickets_count,
            "complaintsCount": complaints_count,
            "returnFrequency": return_frequency,
            "returnRatePercent": return_rate_pct,
            "sentimentScore": sentiment_score,
            "hasActiveComplaint": feedback_rating <= 2,
            "primaryComplaintReason": random.choice(COMPLAINT_REASONS) if feedback_rating <= 2 else None
        },
        "accountState": {
            "loyaltyTier": loyalty_tier,
            "isLoyaltyMember": is_loyalty_member,
            "accountAgeDays": account_age_days,
            "customerSegment": customer_segment
        },
        "logistics": {
            "carrierCode": random.choice(CARRIERS),
            "serviceLevel": random.choice(["NEXT_DAY_AIR", "STANDARD_FREIGHT", "EXPRESS_PARCEL"]),
            "originHub": random.choice(WAREHOUSES),
            "totalWeightKg": round(total_weight, 2),
            "requireSignature": True
        },
        "shippingAddress": {
            "streetAddress": f"Industrial Park Way {random.randint(10, 800)}",
            "city": city_data["city"],
            "province": city_data["province"],
            "postalCode": city_data["postalCode"],
            "countryCode": city_data["countryCode"]
        },
        "lineItems": line_items,
        "customerFeedback": {
            "feedbackText": feedback_text,
            "rating": feedback_rating,
            "sentimentScore": sentiment_score,
            "channel": random.choice(["MOBILE_APP", "CHATBOT", "EMAIL_SURVEY", "WEB_PORTAL"]),
            "hasActiveComplaint": feedback_rating <= 2,
            "primaryComplaintReason": random.choice(COMPLAINT_REASONS) if feedback_rating <= 2 else None,
            "feedbackTimestamp": created_at.isoformat()
        },
        "metadata": {
            "apiVersion": "v2.0",
            "sourcePlatform": random.choice(["ERP_SAP_CONNECTOR", "CUSTOM_MOBILE_APP", "COMMERCE_PORTAL"]),
            "clientIpAddress": f"10.{random.randint(10,99)}.{random.randint(1,254)}.{random.randint(1,254)}",
            "retryCount": 0
        },
        "createdAt": created_at.isoformat(),
        "updatedAt": updated_at.isoformat(),
        "estimatedDeliveryDate": est_delivery.isoformat()
    }

    return doc


def worker_task(worker_id, total_docs, chunk_size, progress_queue, project_id=DEFAULT_PROJECT, database_id=DEFAULT_DATABASE, region=DEFAULT_REGION, dry_run=False, collection_name=DEFAULT_COLLECTION):
    """Worker process function: streams chunks of orders into Firestore Native via ADC with error suppression."""
    client = None
    collection = None

    if not dry_run:
        try:
            client = get_firestore_native_client(project_id, database_id)
            collection = client.collection(collection_name)
        except Exception as e:
            if progress_queue:
                progress_queue.put({
                    "type": "warning",
                    "worker_id": worker_id,
                    "msg": f"Worker {worker_id} connection error: {type(e).__name__}: {str(e)[:120]}"
                })
                progress_queue.put({
                    "type": "progress",
                    "worker_id": worker_id,
                    "inserted": 0,
                    "failed": total_docs
                })
                progress_queue.put({
                    "type": "worker_done",
                    "worker_id": worker_id,
                    "inserted": 0,
                    "failed": total_docs
                })
            return 0, total_docs

    base_time = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)
    steps = math.ceil(total_docs / chunk_size)
    total_inserted = 0
    total_failed = 0

    for step in range(steps):
        current_chunk_size = min(chunk_size, total_docs - (step * chunk_size))
        if current_chunk_size <= 0:
            break

        chunk = []
        for i in range(current_chunk_size):
            global_index = (worker_id * 10_000_000) + (step * chunk_size) + i
            order = generate_single_order(worker_id, global_index, base_time)
            chunk.append(order)

        chunk_inserted = 0
        chunk_failed = 0

        if dry_run:
            chunk_inserted = len(chunk)
            chunk_failed = 0
        elif chunk:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    batch = client.batch()
                    for order in chunk:
                        doc_id = order.get("orderId")
                        doc_ref = collection.document(doc_id)
                        batch.set(doc_ref, order)
                    batch.commit()
                    chunk_inserted += len(chunk)
                    break
                except (GoogleAPICallError, RetryError) as gerr:
                    if attempt < max_retries - 1:
                        time.sleep(1.5 * (attempt + 1))
                    else:
                        chunk_failed += len(chunk)
                        if progress_queue:
                            progress_queue.put({
                                "type": "warning",
                                "worker_id": worker_id,
                                "msg": f"Worker {worker_id}: Batch write failed after {max_retries} attempts: {type(gerr).__name__}: {str(gerr)[:100]}"
                            })
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(2.0)
                    else:
                        chunk_failed += len(chunk)
                        if progress_queue:
                            progress_queue.put({
                                "type": "warning",
                                "worker_id": worker_id,
                                "msg": f"Worker {worker_id}: Unexpected error: {type(e).__name__}: {str(e)[:100]}"
                            })

        total_inserted += chunk_inserted
        total_failed += chunk_failed

        if progress_queue:
            progress_queue.put({
                "type": "progress",
                "worker_id": worker_id,
                "inserted": chunk_inserted,
                "failed": chunk_failed
            })

    if progress_queue:
        progress_queue.put({
            "type": "worker_done",
            "worker_id": worker_id,
            "inserted": total_inserted,
            "failed": total_failed
        })

    return total_inserted, total_failed


def run_generator(total_count, num_workers, chunk_size, database, collection_name, project_id=DEFAULT_PROJECT, region=DEFAULT_REGION, dry_run=False, drop_existing=False):
    """Main coordinator: spawns workers, manages inter-process progress queue, and displays live metrics."""
    print("=================================================================")
    print(" Redwood Retail: High-Performance Dataset Generation Pipeline    ")
    print(" Auth Mode: Google Cloud IAM & Application Default Credentials   ")
    print("=================================================================")
    print(f"Target Database:    {database} (Firestore Enterprise Native)")
    print(f"Collection:         {collection_name}")
    print(f"Total Documents:    {total_count:,}")
    print(f"Parallel Workers:   {num_workers}")
    print(f"Batch Chunk Size:   {chunk_size:,} docs/batch")
    print(f"Dry Run Mode:       {dry_run}")
    print("=================================================================")

    if not dry_run:
        try:
            test_client = get_firestore_native_client(project_id, database)
            coll_ref = test_client.collection(collection_name)
            if drop_existing:
                print(f"Clearing existing collection '{collection_name}'...")
                docs = coll_ref.limit(500).stream()
                batch = test_client.batch()
                deleted = 0
                for doc in docs:
                    batch.delete(doc.reference)
                    deleted += 1
                if deleted > 0:
                    batch.commit()
            print("✅ Successfully verified Firestore Enterprise Native connectivity via ADC.")
        except Exception as e:
            print(f"❌ Connection error: {e}")
            sys.exit(1)

    start_time = time.time()
    docs_per_worker = math.ceil(total_count / num_workers)
    
    manager = Manager()
    progress_queue = manager.Queue()
    workers = []
    
    for w_id in range(num_workers):
        if w_id == num_workers - 1:
            allocation = total_count - (docs_per_worker * (num_workers - 1))
        else:
            allocation = docs_per_worker

        if allocation <= 0:
            continue

        p = Process(
            target=worker_task,
            args=(
                w_id, allocation, chunk_size, progress_queue,
                project_id, database, region, dry_run, collection_name
            )
        )
        p.daemon = True
        workers.append(p)

    print(f"\nDispatched {len(workers)} parallel workers. Upload in progress...\n")
    for p in workers:
        p.start()

    total_inserted = 0
    total_failed = 0
    active_workers = len(workers)
    last_print_time = 0.0
    last_completed = 0

    try:
        while active_workers > 0 or not progress_queue.empty():
            try:
                msg = progress_queue.get(timeout=0.2)
                msg_type = msg.get("type")

                if msg_type == "progress":
                    total_inserted += msg.get("inserted", 0)
                    total_failed += msg.get("failed", 0)
                elif msg_type == "warning":
                    print(f"⚠️  {msg.get('msg')}")
                elif msg_type == "worker_done":
                    active_workers -= 1

            except Empty:
                pass

            now = time.time()
            completed = total_inserted + total_failed
            if completed > 0 and completed != last_completed and (now - last_print_time >= 0.3 or completed >= total_count or active_workers == 0):
                last_print_time = now
                last_completed = completed
                elapsed = max(now - start_time, 0.001)
                speed = total_inserted / elapsed
                pct = (completed / max(total_count, 1)) * 100.0
                rem_docs = max(total_count - completed, 0)
                eta = rem_docs / max(speed, 1.0) if speed > 0 else 0.0

                print(
                    f"Progress: [{completed:>8,}/{total_count:,}] ({pct:>5.1f}%) "
                    f"| Inserted: {total_inserted:>8,} | Failed: {total_failed:>4,} "
                    f"| Rate: {speed:>7.1f} docs/sec | Elapsed: {elapsed:>5.1f}s | ETA: {eta:>5.1f}s"
                )

        for p in workers:
            p.join(timeout=1.0)

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user (Ctrl+C). Terminating workers gracefully...")
        for p in workers:
            if p.is_alive():
                p.terminate()
                p.join(timeout=1.0)

    total_time = max(time.time() - start_time, 0.001)
    overall_speed = total_inserted / total_time
    print("\n=================================================================")
    print("🎉 Pipeline Completed!")
    print("-----------------------------------------------------------------")
    print(f"Target Database:        {database} (Firestore Enterprise)")
    print(f"Target Collection:      {collection_name}")
    print(f"Total Requested:        {total_count:,} documents")
    print(f"Successfully Inserted:  {total_inserted:,} documents")
    print(f"Failed / Skipped:       {total_failed:,} documents")
    print(f"Total Elapsed Time:     {total_time:.2f} seconds")
    print(f"Average Throughput:     {overall_speed:.1f} docs/sec")
    print("=================================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate large-scale retail order dataset for Firestore & BigQuery")
    parser.add_argument("-n", "--count", type=int, default=10000, help="Total number of documents to generate (e.g. 10000, 100000, 1000000)")
    parser.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS, help="Number of parallel worker processes")
    parser.add_argument("-c", "--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Batch chunk size for insert_many")
    parser.add_argument("-p", "--project", type=str, default=DEFAULT_PROJECT, help="Google Cloud project ID")
    parser.add_argument("-r", "--region", type=str, default=DEFAULT_REGION, help="Google Cloud region")
    parser.add_argument("-d", "--database", type=str, default=DEFAULT_DATABASE, help="Target Firestore database name")
    parser.add_argument("--collection", type=str, default=DEFAULT_COLLECTION, help="Target collection name")
    parser.add_argument("--dry-run", action="store_true", help="Synthesize data in memory without writing to database")
    parser.add_argument("--drop-existing", action="store_true", help="Drop existing collection before seeding")

    args = parser.parse_args()

    run_generator(
        total_count=args.count,
        num_workers=args.workers,
        chunk_size=args.chunk_size,
        database=args.database,
        collection_name=args.collection,
        project_id=args.project,
        region=args.region,
        dry_run=args.dry_run,
        drop_existing=args.drop_existing
    )



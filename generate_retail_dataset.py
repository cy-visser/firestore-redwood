#!/usr/bin/env python3
"""
High-Performance Retail Dataset Generator for Firestore Enterprise & BigQuery CDC.
Generates configurable volumes (from 1k to 1M+ documents) of rich retail transaction,
sentiment, and churn analysis data into Firestore via the MongoDB API.
"""

import os
import sys
import time
import math
import json
import random
import argparse
from datetime import datetime, timezone, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed
from pymongo import MongoClient

# Import catalog and synthesis data
sys.path.insert(0, os.path.dirname(__file__))
from retail_catalog import (
    CATALOG_ITEMS, SHIPPING_CITIES, WAREHOUSES, CARRIERS,
    POSITIVE_FEEDBACK, NEUTRAL_FEEDBACK, NEGATIVE_FEEDBACK, COMPLAINT_REASONS
)
from firestore_auth import get_firestore_mongo_client

DEFAULT_PROJECT = os.environ.get("GCP_PROJECT", "elevate-cyvisser")
DEFAULT_REGION = os.environ.get("GCP_REGION", "europe-west4")
DEFAULT_DATABASE = "redwood"
DEFAULT_COLLECTION = "orders"
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_WORKERS = min(os.cpu_count() or 8, 16)


def generate_single_order(worker_id, order_index, base_time):
    """Generate a single richly structured retail order document."""
    rand_id = f"{random.randint(100000, 999999)}{random.choice(['A', 'B', 'C', 'D', 'E'])}"
    order_id = f"ORD-26-W{worker_id}-{rand_id}-IDX{order_index:07d}"
    customer_num = random.randint(1000, 99999)
    customer_id = f"cust_retail_{customer_num:05d}"
    
    # Customer segment and persona distribution
    seg_roll = random.random()
    if seg_roll < 0.15:
        customer_segment = "ENTERPRISE_VIP"
        num_items = random.randint(3, 8)
        qty_multiplier = random.randint(2, 6)
    elif seg_roll < 0.45:
        customer_segment = "RETAIL_PRO"
        num_items = random.randint(2, 5)
        qty_multiplier = random.randint(1, 3)
    elif seg_roll < 0.80:
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
    
    # Discount
    discount_total = 0.0
    if customer_segment in ["ENTERPRISE_VIP", "RETAIL_PRO"] or random.random() < 0.35:
        discount_rate = random.choice([0.05, 0.10, 0.15, 0.20, 0.25])
        discount_total = round(subtotal * discount_rate, 2)

    grand_total = round(subtotal - discount_total + tax_amount + shipping_fee, 2)
    profit_margin = round((grand_total - total_cost - tax_amount) / max(grand_total, 1.0), 3)

    # Churn & Sentiment Risk Correlation
    churn_roll = random.random()
    if churn_roll < 0.20:
        # High Risk Churner / Dissatisfied
        churn_risk_score = round(random.uniform(0.72, 0.98), 2)
        churn_category = "CRITICAL_RISK" if churn_risk_score > 0.85 else "HIGH_RISK"
        feedback_text = random.choice(NEGATIVE_FEEDBACK)
        feedback_rating = random.choice([1, 2])
        support_tickets = random.randint(2, 6)
        days_since_last_order = random.randint(60, 180)
        return_rate_pct = round(random.uniform(12.0, 35.0), 1)
        retention_shield_status = "ELIGIBLE_FOR_OFFER"
        suggested_offer = {
            "offerId": f"OFFER-RETENTION-{random.randint(100,999)}",
            "offerCode": f"RETAIN{int(churn_risk_score*100)}",
            "discountPercent": 25,
            "description": "Instant 25% discount & free priority freight applied to active cart"
        }
        order_status = random.choice(["PROCESSING", "DELIVERED", "RETURN_REQUESTED", "CANCELLED"])
        payment_status = random.choice(["SETTLED", "REFUNDED", "DISPUTED"])
    elif churn_roll < 0.45:
        # Moderate Risk / Neutral
        churn_risk_score = round(random.uniform(0.35, 0.70), 2)
        churn_category = "MODERATE_RISK"
        feedback_text = random.choice(NEUTRAL_FEEDBACK)
        feedback_rating = 3
        support_tickets = random.randint(1, 2)
        days_since_last_order = random.randint(30, 75)
        return_rate_pct = round(random.uniform(5.0, 12.0), 1)
        retention_shield_status = "MONITORING"
        suggested_offer = None
        order_status = random.choice(["DELIVERED", "SHIPPED", "PROCESSING"])
        payment_status = "SETTLED"
    else:
        # Low Risk / Loyal Customer
        churn_risk_score = round(random.uniform(0.02, 0.30), 2)
        churn_category = "LOYAL" if churn_risk_score < 0.15 else "LOW_RISK"
        feedback_text = random.choice(POSITIVE_FEEDBACK)
        feedback_rating = random.choice([4, 5])
        support_tickets = 0
        days_since_last_order = random.randint(2, 25)
        return_rate_pct = round(random.uniform(0.0, 4.0), 1)
        retention_shield_status = "NONE"
        suggested_offer = None
        order_status = random.choice(["DELIVERED", "SHIPPED"])
        payment_status = "SETTLED"

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
            "channel": random.choice(["MOBILE_APP", "CHATBOT", "EMAIL_SURVEY", "WEB_PORTAL"]),
            "hasActiveComplaint": feedback_rating <= 2,
            "primaryComplaintReason": random.choice(COMPLAINT_REASONS) if feedback_rating <= 2 else None,
            "feedbackTimestamp": created_at.isoformat()
        },
        "churnAnalysis": {
            "daysSinceLastOrder": days_since_last_order,
            "ordersCountLast12m": random.randint(1, 24),
            "lifetimeSpend": round(grand_total * random.uniform(1.5, 8.0), 2),
            "returnRatePercent": return_rate_pct,
            "supportTicketsCount": support_tickets,
            "discountSensitivity": round(random.uniform(0.2, 0.95), 2),
            "churnRiskScore": churn_risk_score,
            "churnCategory": churn_category,
            "retentionShieldStatus": retention_shield_status
        },
        "suggestedOffer": suggested_offer,
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


def worker_task(worker_id, total_docs, chunk_size, project_id=DEFAULT_PROJECT, database_id=DEFAULT_DATABASE, region=DEFAULT_REGION, dry_run=False, collection_name=DEFAULT_COLLECTION):
    """Worker process function: streams chunks of orders into Firestore MongoDB via IAM."""
    if not dry_run:
        client = get_firestore_mongo_client(project_id, database_id, region)
        db = client[database_id]
        collection = db[collection_name]
    else:
        client = None
        collection = None

    base_time = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)
    steps = math.ceil(total_docs / chunk_size)
    inserted_count = 0

    for step in range(steps):
        current_chunk_size = min(chunk_size, total_docs - (step * chunk_size))
        chunk = []
        
        for i in range(current_chunk_size):
            global_index = (worker_id * 1_000_000) + (step * chunk_size) + i
            order = generate_single_order(worker_id, global_index, base_time)
            chunk.append(order)

        if not dry_run and chunk:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    collection.insert_many(chunk, ordered=False)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(2)
                    else:
                        raise e

        inserted_count += len(chunk)

    if client:
        client.close()

    return inserted_count


def run_generator(total_count, num_workers, chunk_size, database, collection_name, project_id=DEFAULT_PROJECT, region=DEFAULT_REGION, dry_run=False, drop_existing=False):
    """Main coordinator: spawns workers and manages live progress."""
    print("=================================================================")
    print(" Redwood Retail: High-Performance Dataset Generation Pipeline    ")
    print(" Auth Mode: Google Cloud Service Account & IAM (MONGODB-OIDC)    ")
    print("=================================================================")
    print(f"Target Database:    {database} (Firestore Enterprise via MongoDB API)")
    print(f"Collection:         {collection_name}")
    print(f"Total Documents:    {total_count:,}")
    print(f"Parallel Workers:   {num_workers}")
    print(f"Batch Chunk Size:   {chunk_size:,} docs/batch")
    print(f"Dry Run Mode:       {dry_run}")
    print("=================================================================")

    if not dry_run:
        try:
            test_client = get_firestore_mongo_client(project_id, database, region)
            test_db = test_client[database]
            if drop_existing:
                print(f"Clearing existing collection '{collection_name}'...")
                test_db[collection_name].drop()
            test_client.close()
            print("✅ Successfully verified Firestore MongoDB connectivity via IAM.")
        except Exception as e:
            print(f"❌ Connection error: {e}")
            sys.exit(1)

    start_time = time.time()
    docs_per_worker = math.ceil(total_count / num_workers)
    futures = []
    total_completed = 0

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        for w_id in range(num_workers):
            if w_id == num_workers - 1:
                allocation = total_count - (docs_per_worker * (num_workers - 1))
            else:
                allocation = docs_per_worker

            futures.append(
                executor.submit(
                    worker_task,
                    w_id, allocation, chunk_size, project_id, database, region, dry_run, collection_name
                )
            )


        print(f"\nDispatched {num_workers} parallel workers. Upload in progress...\n")
        
        for future in as_completed(futures):
            result = future.result()
            total_completed += result
            elapsed = max(time.time() - start_time, 0.001)
            speed = total_completed / elapsed
            pct = (total_completed / total_count) * 100
            rem_docs = total_count - total_completed
            eta = rem_docs / max(speed, 1.0)
            
            print(
                f"Progress: [{total_completed:>8,}/{total_count:,}] ({pct:>5.1f}%) "
                f"| Rate: {speed:>7.1f} docs/sec | Elapsed: {elapsed:>5.1f}s | ETA: {eta:>5.1f}s"
            )

    total_time = max(time.time() - start_time, 0.001)
    overall_speed = total_completed / total_time
    print("\n=================================================================")
    print(f"🎉 Pipeline Completed: {total_completed:,} documents successfully stored!")
    print(f"⏱️ Total Elapsed Time: {total_time:.2f} seconds")
    print(f"⚡ Average Throughput:  {overall_speed:.1f} docs/sec")
    print("=================================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate large-scale retail order dataset for Firestore & BigQuery")
    parser.add_argument("-n", "--count", type=int, default=10000, help="Total number of documents to generate (e.g. 10000, 100000, 1000000)")
    parser.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS, help="Number of parallel worker processes")
    parser.add_argument("-c", "--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Batch chunk size for insert_many")
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
        dry_run=args.dry_run,
        drop_existing=args.drop_existing
    )

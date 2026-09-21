#!/usr/bin/env python3
"""
Retail dataset generator for Firestore Enterprise Native and BigQuery CDC.

Seeds a reproducible population of customers, each with a real multi-order
history, into Firestore. Two collections are written:

  /retail/{orderId}        - the order stream replicated to BigQuery
  /customers/{customerId}  - customer profiles read by the loyalty agent

The unit of work is the *customer*, not the document. Previously the generator
emitted N independent orders with randomly rolled customer ids, which meant no
customer had a history, demo customers were not addressable, and churn could
only be faked with a rule. Seeding whole customers instead lets churn be
derived from observed purchase behaviour; see customer_profiles.py for the
feature/label windowing.

The demo personas (demo1-user, demo2-user) are always seeded, regardless of
--count, and are pinned to the IAM service accounts declared in
terraform/iam.tf.
"""

import os
import sys
import time
import math
import random
import argparse
from datetime import datetime, timezone
from multiprocessing import Manager, Process
from queue import Empty

from dotenv import load_dotenv, find_dotenv
from google.api_core.exceptions import GoogleAPICallError, RetryError

# Load environment configuration from .env if present
load_dotenv(find_dotenv(usecwd=True))

sys.path.insert(0, os.path.dirname(__file__))
from firestore_auth import get_firestore_native_client
from customer_profiles import (
    build_customer_roster,
    roster_summary,
    reference_now,
    _stable_seed,
    HISTORY_MONTHS,
    LABEL_WINDOW_DAYS,
)
from order_factory import build_customer_orders, build_customer_document

DEFAULT_PROJECT = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT")
DEFAULT_REGION = os.getenv("GCP_REGION")
DEFAULT_DATABASE = os.getenv("FIRESTORE_DATABASE_ID") or os.getenv("FIRESTORE_DATABASE")
DEFAULT_COLLECTION = os.getenv("FIRESTORE_COLLECTION") or "retail"
DEFAULT_CUSTOMERS_COLLECTION = os.getenv("FIRESTORE_CUSTOMERS_COLLECTION") or "customers"
DEFAULT_SEED = 20260921
DEFAULT_CHUNK_SIZE = 400  # Firestore caps a batch at 500 writes.
DEFAULT_WORKERS = min(os.cpu_count() or 8, 16)


def _commit_with_retry(client, docs, collection, id_field, max_retries=3):
    """Commit a batch of documents, retrying transient Firestore errors.

    Returns (inserted, failed, error_message_or_None).
    """
    for attempt in range(max_retries):
        try:
            batch = client.batch()
            for doc in docs:
                batch.set(collection.document(doc[id_field]), doc)
            batch.commit()
            return len(docs), 0, None
        except (GoogleAPICallError, RetryError) as err:
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
            else:
                return 0, len(docs), f"{type(err).__name__}: {str(err)[:120]}"
        except Exception as err:  # noqa: BLE001 - surface anything else the same way
            if attempt < max_retries - 1:
                time.sleep(2.0)
            else:
                return 0, len(docs), f"{type(err).__name__}: {str(err)[:120]}"
    return 0, len(docs), "exhausted retries"


def worker_task(
    worker_id,
    customer_slice,
    progress_queue,
    project_id,
    database_id,
    collection_name,
    customers_collection_name,
    seed,
    now_iso,
    dry_run=False,
):
    """Generate and write the order history for an assigned slice of customers."""
    now = datetime.fromisoformat(now_iso)
    client = None
    orders_coll = None
    customers_coll = None

    if not dry_run:
        try:
            client = get_firestore_native_client(project_id, database_id)
            orders_coll = client.collection(collection_name)
            customers_coll = client.collection(customers_collection_name)
        except Exception as e:  # noqa: BLE001
            progress_queue.put({
                "type": "warning",
                "msg": f"Worker {worker_id} connection error: {type(e).__name__}: {str(e)[:120]}",
            })
            progress_queue.put({"type": "worker_done", "worker_id": worker_id})
            return

    pending_orders = []
    pending_customers = []
    inserted = failed = 0

    def flush(force=False):
        nonlocal pending_orders, pending_customers, inserted, failed
        if dry_run:
            inserted += len(pending_orders) + len(pending_customers)
            progress_queue.put({
                "type": "progress",
                "inserted": len(pending_orders) + len(pending_customers),
                "failed": 0,
            })
            pending_orders, pending_customers = [], []
            return

        while pending_orders and (force or len(pending_orders) >= DEFAULT_CHUNK_SIZE):
            chunk, pending_orders = (
                pending_orders[:DEFAULT_CHUNK_SIZE],
                pending_orders[DEFAULT_CHUNK_SIZE:],
            )
            ok, bad, err = _commit_with_retry(client, chunk, orders_coll, "orderId")
            inserted += ok
            failed += bad
            if err:
                progress_queue.put({"type": "warning", "msg": f"Worker {worker_id} orders: {err}"})
            progress_queue.put({"type": "progress", "inserted": ok, "failed": bad})

        while pending_customers and (force or len(pending_customers) >= DEFAULT_CHUNK_SIZE):
            chunk, pending_customers = (
                pending_customers[:DEFAULT_CHUNK_SIZE],
                pending_customers[DEFAULT_CHUNK_SIZE:],
            )
            ok, bad, err = _commit_with_retry(client, chunk, customers_coll, "customerId")
            inserted += ok
            failed += bad
            if err:
                progress_queue.put({"type": "warning", "msg": f"Worker {worker_id} customers: {err}"})
            progress_queue.put({"type": "progress", "inserted": ok, "failed": bad})

    for customer in customer_slice:
        # Seeded per customer id, so a customer's orders are identical no matter
        # which worker happens to generate them.
        rng = random.Random(_stable_seed(seed, customer.customer_id, "orders"))
        orders = build_customer_orders(customer, rng)
        pending_orders.extend(orders)
        pending_customers.append(build_customer_document(customer, orders, now))
        flush()

    flush(force=True)
    progress_queue.put({"type": "worker_done", "worker_id": worker_id})


def _purge_collection(client, coll_ref, label):
    """Delete every document in a collection, in batches.

    The previous implementation deleted only the first 500 documents, so
    reseeding silently accumulated stale data.
    """
    deleted = 0
    while True:
        docs = list(coll_ref.limit(400).stream())
        if not docs:
            break
        batch = client.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        deleted += len(docs)
        print(f"   {label}: deleted {deleted:,}...", end="\r", flush=True)
    if deleted:
        print(f"   {label}: deleted {deleted:,} documents." + " " * 20)
    return deleted


def run_generator(
    customer_count,
    num_workers,
    database,
    collection_name,
    customers_collection_name=DEFAULT_CUSTOMERS_COLLECTION,
    project_id=DEFAULT_PROJECT,
    region=DEFAULT_REGION,
    seed=DEFAULT_SEED,
    dry_run=False,
    drop_existing=False,
):
    """Build the customer roster and write it to Firestore in parallel."""
    now = reference_now()
    roster = build_customer_roster(
        customer_count, seed=seed, project_id=project_id, now=now
    )
    summary = roster_summary(roster)

    print("=================================================================")
    print(" Redwood Retail: Customer-Centric Dataset Generation             ")
    print(" Auth Mode: Google Cloud IAM & Application Default Credentials   ")
    print("=================================================================")
    print(f"Target Database:     {database} (Firestore Enterprise Native)")
    print(f"Orders Collection:   {collection_name}")
    print(f"Customers Coll.:     {customers_collection_name}")
    print(f"Customers:           {summary['customers']:,}")
    print(f"Orders:              {summary['orders']:,}")
    print(f"History Window:      {HISTORY_MONTHS} months, ending {now.date()}")
    print(f"Label Window:        trailing {LABEL_WINDOW_DAYS} days")
    print(f"Churned / Retained:  {summary['churned']:,} / {summary['retained']:,} "
          f"({summary['churn_rate']:.1%} churn)")
    print(f"Archetypes:          {summary['by_archetype']}")
    print(f"Parallel Workers:    {num_workers}")
    print(f"Random Seed:         {seed} (deterministic)")
    print(f"Dry Run Mode:        {dry_run}")
    print("=================================================================")

    if not dry_run:
        try:
            client = get_firestore_native_client(project_id, database)
            if drop_existing:
                print("Clearing existing collections...")
                _purge_collection(client, client.collection(collection_name), collection_name)
                _purge_collection(
                    client, client.collection(customers_collection_name), customers_collection_name
                )
            print("Verified Firestore Enterprise Native connectivity via ADC.")
        except Exception as e:  # noqa: BLE001
            print(f"Connection error: {e}")
            sys.exit(1)

    total_docs = summary["orders"] + summary["customers"]
    start_time = time.time()

    # Partition customers round-robin so workers get comparable order volumes
    # even though history length varies a lot between archetypes.
    slices = [roster[i::num_workers] for i in range(num_workers)]
    slices = [s for s in slices if s]

    manager = Manager()
    progress_queue = manager.Queue()
    workers = []

    for w_id, customer_slice in enumerate(slices):
        p = Process(
            target=worker_task,
            args=(
                w_id, customer_slice, progress_queue, project_id, database,
                collection_name, customers_collection_name, seed,
                now.isoformat(), dry_run,
            ),
        )
        p.daemon = True
        workers.append(p)

    print(f"\nDispatched {len(workers)} parallel workers. Upload in progress...\n")
    for p in workers:
        p.start()

    total_inserted = total_failed = 0
    active_workers = len(workers)
    last_print = 0.0

    try:
        while active_workers > 0 or not progress_queue.empty():
            try:
                msg = progress_queue.get(timeout=0.2)
                if msg["type"] == "progress":
                    total_inserted += msg.get("inserted", 0)
                    total_failed += msg.get("failed", 0)
                elif msg["type"] == "warning":
                    print(f"WARNING: {msg.get('msg')}")
                elif msg["type"] == "worker_done":
                    active_workers -= 1
            except Empty:
                pass

            now_t = time.time()
            completed = total_inserted + total_failed
            if completed and (now_t - last_print >= 0.3 or active_workers == 0):
                last_print = now_t
                elapsed = max(now_t - start_time, 0.001)
                speed = total_inserted / elapsed
                pct = (completed / max(total_docs, 1)) * 100.0
                eta = max(total_docs - completed, 0) / max(speed, 1.0)
                print(
                    f"Progress: [{completed:>8,}/{total_docs:,}] ({pct:>5.1f}%) "
                    f"| Inserted: {total_inserted:>8,} | Failed: {total_failed:>4,} "
                    f"| Rate: {speed:>7.1f} docs/sec | Elapsed: {elapsed:>5.1f}s "
                    f"| ETA: {eta:>5.1f}s"
                )

        for p in workers:
            p.join(timeout=2.0)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Terminating workers...")
        for p in workers:
            if p.is_alive():
                p.terminate()
                p.join(timeout=1.0)

    total_time = max(time.time() - start_time, 0.001)
    print("\n=================================================================")
    print("Seeding complete.")
    print("-----------------------------------------------------------------")
    print(f"Database:               {database}")
    print(f"Customers seeded:       {summary['customers']:,}")
    print(f"Orders seeded:          {summary['orders']:,}")
    print(f"Documents inserted:     {total_inserted:,}")
    print(f"Failed / skipped:       {total_failed:,}")
    print(f"Elapsed:                {total_time:.2f}s "
          f"({total_inserted / total_time:.1f} docs/sec)")
    print("-----------------------------------------------------------------")
    print("Demo personas:")
    for c in roster:
        if c.is_demo_persona:
            state = "RETAINED (low risk)" if c.purchases_in_label_window else "CHURNED (high risk)"
            print(f"  {c.customer_id:<14} {c.archetype.name:<14} "
                  f"{len(c.order_dates):>3} orders  last={c.order_dates[-1].date()}  {state}")
            print(f"  {'':<14} {c.iam_principal}")
    print("=================================================================")

    return total_failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a customer-centric retail dataset for Firestore and BigQuery"
    )
    parser.add_argument(
        "-n", "--count", type=int, default=400,
        help="Number of synthetic customers to generate. The two demo personas "
             "are always seeded in addition to this count. Each customer "
             "produces roughly 4-20 orders depending on archetype.",
    )
    parser.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS,
                        help="Number of parallel worker processes")
    parser.add_argument("-p", "--project", type=str, default=DEFAULT_PROJECT,
                        help="Google Cloud project ID")
    parser.add_argument("-r", "--region", type=str, default=DEFAULT_REGION,
                        help="Google Cloud region")
    parser.add_argument("-d", "--database", type=str, default=DEFAULT_DATABASE,
                        help="Target Firestore database name")
    parser.add_argument("--collection", type=str, default=DEFAULT_COLLECTION,
                        help="Target orders collection name")
    parser.add_argument("--customers-collection", type=str,
                        default=DEFAULT_CUSTOMERS_COLLECTION,
                        help="Target customer profiles collection name")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="Random seed; identical seeds reproduce identical data")
    parser.add_argument("--dry-run", action="store_true",
                        help="Synthesize data in memory without writing to Firestore")
    parser.add_argument("--drop-existing", action="store_true",
                        help="Delete all existing documents in both collections first")

    args = parser.parse_args()

    ok = run_generator(
        customer_count=args.count,
        num_workers=args.workers,
        database=args.database,
        collection_name=args.collection,
        customers_collection_name=args.customers_collection,
        project_id=args.project,
        region=args.region,
        seed=args.seed,
        dry_run=args.dry_run,
        drop_existing=args.drop_existing,
    )
    sys.exit(0 if ok else 1)

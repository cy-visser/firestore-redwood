#!/usr/bin/env python3
"""
Reconcile a Firestore collection into BigQuery.

Eventarc only delivers changes that happen after a trigger exists, so anything
seeded beforehand is invisible to the CDC service. There is a second, less
obvious gap: Firestore raises no change event when a write leaves the document
byte-identical, and the dataset generator is deterministic, so re-running it
over an existing collection produces thousands of no-op writes and not one
event. Either way the fix is the same, and this is it.

This runs as a local CLI rather than as an endpoint on the service. An HTTP
backfill would have to stream a whole collection inside a single request, and
a few thousand documents comfortably outlives the Cloud Run request timeout,
so the endpoint would fail precisely on the collections big enough to need it.

Writes go through the same schema mapping and the same UPSERT path as live
events, so running this repeatedly is harmless and cannot produce a row that a
live event would not have produced. Rows are sequenced on each document's own
update time, so a backfill racing a live event cannot overwrite newer data
with older.

Usage:
    python cdc_service/backfill.py --collection retail
    python cdc_service/backfill.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))

from bq_cdc_writer import CdcSink, extract_row, sequence_number  # noqa: E402
from schemas import ColumnContext, build_routes  # noqa: E402

# Rows per append. The Storage Write API caps a single AppendRows request at
# 10 MB; batching this way is what makes a backfill minutes rather than hours,
# since each append otherwise costs a full round trip.
BATCH_ROWS = 200


def _snapshot_time(snapshot, fallback: datetime) -> datetime:
    """The document's own update time, so backfilled rows sequence correctly."""
    value = getattr(snapshot, "update_time", None)
    if value is None:
        return fallback
    to_datetime = getattr(value, "ToDatetime", None)
    if callable(to_datetime):
        result = to_datetime()
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return fallback


def backfill_collection(
    sink: CdcSink,
    routes,
    firestore_client,
    collection: str,
    limit: int = 0,
) -> Dict[str, Any]:
    """Replicate one collection's current contents into its destination tables."""
    route = routes.get(collection)
    if route is None:
        raise SystemExit(f"No route configured for collection '{collection}'.")

    query = firestore_client.collection(collection)
    if limit:
        query = query.limit(limit)

    now = datetime.now(timezone.utc)
    started = time.time()

    # Accumulate per table so each append carries a full batch.
    pending: Dict[str, List[bytes]] = {spec.table_id: [] for spec in route.tables}
    processed = 0
    failed = 0
    errors: List[str] = []

    def flush(table_id: str, force: bool = False) -> None:
        nonlocal failed
        rows = pending[table_id]
        if not rows or (not force and len(rows) < BATCH_ROWS):
            return
        spec = next(s for s in route.tables if s.table_id == table_id)
        try:
            sink.writer_for(spec).append(rows)
        except Exception as err:  # noqa: BLE001 - report and keep going
            failed += len(rows)
            if len(errors) < 5:
                errors.append(f"{table_id}: {type(err).__name__}: {str(err)[:160]}")
        pending[table_id] = []

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

        for spec in route.tables:
            writer = sink.writer_for(spec)
            row = writer.build_row(
                extract_row(spec, ctx),
                change_type="UPSERT" if spec.supports_cdc else None,
                change_sequence=sequence if spec.supports_cdc else None,
            )
            pending[spec.table_id].append(row)
            flush(spec.table_id)

        processed += 1
        if processed % 500 == 0:
            rate = processed / max(time.time() - started, 0.001)
            print(f"   {collection}: {processed:,} documents ({rate:,.0f}/s)")

    for spec in route.tables:
        flush(spec.table_id, force=True)

    elapsed = max(time.time() - started, 0.001)
    return {
        "collection": collection,
        "documents": processed,
        "failed": failed,
        "tables": [spec.table_id for spec in route.tables],
        "elapsed": elapsed,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile Firestore collections into BigQuery via the CDC mapping"
    )
    parser.add_argument("--collection", action="append", default=[],
                        help="Collection to backfill; repeatable")
    parser.add_argument("--all", action="store_true",
                        help="Backfill every configured collection")
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after this many documents per collection")
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"))
    parser.add_argument("--database", default=os.getenv("FIRESTORE_DATABASE_ID"))
    parser.add_argument("--dataset", default=os.getenv("BIGQUERY_DATASET"))
    parser.add_argument("--orders-collection",
                        default=os.getenv("FIRESTORE_COLLECTION", "retail"))
    parser.add_argument("--customers-collection",
                        default=os.getenv("FIRESTORE_CUSTOMERS_COLLECTION", "customers"))
    parser.add_argument("--cdc-table", default=os.getenv("BIGQUERY_CDC_TABLE", "retail_cdc"))
    parser.add_argument("--ensure-tables", action="store_true",
                        help="Create destination tables if they are missing")

    args = parser.parse_args()

    for name, value in (("--project", args.project),
                        ("--dataset", args.dataset),
                        ("--database", args.database)):
        if not value:
            raise SystemExit(f"{name} is required (set it in .env or pass it explicitly).")

    routes = build_routes(
        orders_collection=args.orders_collection,
        orders_cdc_table=args.cdc_table,
        customers_collection=args.customers_collection,
    )

    targets = args.collection or ([args.orders_collection, args.customers_collection]
                                  if args.all else [])
    if not targets:
        raise SystemExit("Specify --collection <name> at least once, or --all.")

    unknown = [c for c in targets if c not in routes]
    if unknown:
        raise SystemExit(
            f"No route for: {', '.join(unknown)}. Known: {', '.join(sorted(routes))}"
        )

    from google.cloud import firestore

    print("=" * 65)
    print(" Redwood Retail: Firestore to BigQuery backfill")
    print("=" * 65)
    print(f"Project:      {args.project}")
    print(f"Database:     {args.database}")
    print(f"Dataset:      {args.dataset}")
    print(f"Collections:  {', '.join(targets)}")
    print("=" * 65)

    sink = CdcSink(args.project, args.dataset)

    if args.ensure_tables:
        specs = [s for c in targets for s in routes[c].tables]
        for table_id, status in sink.ensure_tables(specs):
            print(f"   {table_id}: {status}")

    firestore_client = firestore.Client(project=args.project, database=args.database)

    total_failed = 0
    try:
        for collection in targets:
            print(f"\nBackfilling '{collection}'...")
            result = backfill_collection(
                sink, routes, firestore_client, collection, args.limit
            )
            total_failed += result["failed"]
            rate = result["documents"] / result["elapsed"]
            print(
                f"   {result['documents']:,} documents -> "
                f"{', '.join(result['tables'])} "
                f"in {result['elapsed']:.1f}s ({rate:,.0f}/s)"
            )
            if result["failed"]:
                print(f"   {result['failed']:,} rows failed")
                for err in result["errors"]:
                    print(f"     {err}")
    finally:
        firestore_client.close()
        sink.close()

    print("\nBackfill complete." if not total_failed
          else f"\nBackfill finished with {total_failed:,} failed rows.")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

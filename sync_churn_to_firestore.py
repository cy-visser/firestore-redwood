#!/usr/bin/env python3
"""
BigQuery to Firestore Enterprise Native Reverse-ETL Sync Pipeline.
Materializes daily customer churn predictions and risk vectors from BigQuery
(`customer_churn_risk`) into Firestore customer profiles (`/customers/{customerId}`)
to enable sub-15ms fast-path OLTP retrieval on login without hitting BigQuery.

Features:
- High-throughput batched writes (up to 500 docs per Firestore batch limit)
- Concurrent batch workers with bounded ThreadPoolExecutor
- Transient error resilience with exponential backoff
- Preserves existing customer profile fields via `merge=True`
- Idempotent execution and dry-run preview capabilities
"""

import os
import sys
import time
import math
import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv, find_dotenv
from google.cloud import bigquery, firestore
from google.api_core.exceptions import GoogleAPICallError, RetryError, ServiceUnavailable

# Load environment configuration from .env if present
load_dotenv(find_dotenv(usecwd=True))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("sync_churn_to_firestore")

# Default Environment Constants
DEFAULT_PROJECT = os.getenv("GCP_PROJECT_ID", "redwood-retail-949ec9")
DEFAULT_DATABASE = os.getenv("FIRESTORE_DATABASE_ID", "redwood")
DEFAULT_DATASET = os.getenv("BIGQUERY_DATASET", "redwood_retail")
DEFAULT_TABLE = os.getenv("BIGQUERY_PREDICTIONS_TABLE", "customer_churn_risk")
DEFAULT_COLLECTION = os.getenv("FIRESTORE_CUSTOMERS_COLLECTION", "customers")
DEFAULT_BATCH_SIZE = 500  # Firestore max batch limit
DEFAULT_MAX_WORKERS = 8


@dataclass
class SyncSummary:
    """Summary metrics of the Reverse-ETL sync execution."""
    total_fetched: int = 0
    total_synced: int = 0
    total_failed: int = 0
    total_batches: int = 0
    successful_batches: int = 0
    failed_batches: int = 0
    duration_seconds: float = 0.0
    throughput_records_per_sec: float = 0.0
    dry_run: bool = False


class BigQueryToFirestoreChurnSync:
    """
    Orchestrates the extraction of customer churn predictions from BigQuery
    and batch ingestion into Firestore Enterprise Native.
    """

    def __init__(
        self,
        project_id: str = DEFAULT_PROJECT,
        database_id: str = DEFAULT_DATABASE,
        dataset_id: str = DEFAULT_DATASET,
        table_id: str = DEFAULT_TABLE,
        collection_name: str = DEFAULT_COLLECTION,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_workers: int = DEFAULT_MAX_WORKERS,
        bigquery_client: Optional[bigquery.Client] = None,
        firestore_client: Optional[firestore.Client] = None,
    ):
        self.project_id = project_id
        self.database_id = database_id
        self.dataset_id = dataset_id
        self.table_id = table_id
        self.collection_name = collection_name
        self.batch_size = max(1, min(batch_size, 500))  # Enforce Firestore 500 limit
        self.max_workers = max(1, min(max_workers, 32))

        self.bq_client = bigquery_client
        self.fs_client = firestore_client

    def _get_bq_client(self) -> bigquery.Client:
        if self.bq_client is None:
            logger.info("Initializing BigQuery client for project: %s", self.project_id)
            self.bq_client = bigquery.Client(project=self.project_id)
        return self.bq_client

    def _get_fs_client(self) -> firestore.Client:
        if self.fs_client is None:
            logger.info(
                "Initializing Firestore Native client (project: %s, database: %s)",
                self.project_id,
                self.database_id
            )
            self.fs_client = firestore.Client(
                project=self.project_id,
                database=self.database_id
            )
        return self.fs_client

    def build_extraction_query(
        self,
        customer_id: Optional[str] = None,
        latest_only: bool = True
    ) -> str:
        """Constructs the BigQuery extraction query."""
        table_fqn = f"`{self.project_id}.{self.dataset_id}.{self.table_id}`"
        conditions = []

        if customer_id:
            conditions.append(f"customer_id = '{customer_id}'")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Idiomatic analytical deduplication: extract latest record per customer
        if latest_only:
            query = f"""
            SELECT
                customer_id,
                customer_name,
                customer_email,
                customer_segment,
                loyalty_tier,
                predicted_is_churned,
                churn_probability,
                churn_risk_tier,
                total_spend_90d,
                days_since_last_purchase,
                cart_abandonment_count,
                support_tickets_count,
                sentiment_score,
                automated_retention_action,
                COALESCE(calculation_timestamp, CURRENT_TIMESTAMP()) AS calculation_timestamp
            FROM {table_fqn}
            {where_clause}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY calculation_timestamp DESC) = 1
            """
        else:
            query = f"""
            SELECT
                customer_id,
                customer_name,
                customer_email,
                customer_segment,
                loyalty_tier,
                predicted_is_churned,
                churn_probability,
                churn_risk_tier,
                total_spend_90d,
                days_since_last_purchase,
                cart_abandonment_count,
                support_tickets_count,
                sentiment_score,
                automated_retention_action,
                COALESCE(calculation_timestamp, CURRENT_TIMESTAMP()) AS calculation_timestamp
            FROM {table_fqn}
            {where_clause}
            """
        return query.strip()

    def fetch_prediction_records(
        self,
        customer_id: Optional[str] = None,
        latest_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Executes BigQuery extraction and returns formatted rows."""
        client = self._get_bq_client()
        query = self.build_extraction_query(customer_id=customer_id, latest_only=latest_only)
        logger.info("Executing BigQuery extraction query:\n%s", query)

        query_job = client.query(query)
        rows = list(query_job.result())
        logger.info("Fetched %d records from BigQuery table %s", len(rows), self.table_id)

        records = []
        for row in rows:
            record_dict = dict(row.items()) if hasattr(row, "items") else dict(row)
            records.append(record_dict)
        return records

    @staticmethod
    def map_to_firestore_document(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transforms BigQuery snake_case row into camelCase Firestore customer document.
        Sets `baselineChurnRisk` to enable the Loyalty Agent's fast-path cache.
        """
        customer_id = str(row["customer_id"])
        churn_prob = float(row.get("churn_probability") or 0.0)
        calc_time = row.get("calculation_timestamp")

        if hasattr(calc_time, "isoformat"):
            evaluated_at = calc_time.isoformat()
        elif calc_time:
            evaluated_at = str(calc_time)
        else:
            evaluated_at = datetime.now(timezone.utc).isoformat()

        doc: Dict[str, Any] = {
            "customerId": customer_id,
            "baselineChurnRisk": round(churn_prob, 4),
            "churnProbability": round(churn_prob, 4),
            "churnRiskTier": row.get("churn_risk_tier") or "LOW",
            "churnEvaluatedAt": evaluated_at,
            "lastSyncedAt": datetime.now(timezone.utc).isoformat(),
        }

        # Optional demographic & transactional vectors (preserve if present)
        if row.get("customer_name"):
            doc["customerName"] = row["customer_name"]
        if row.get("customer_email"):
            doc["customerEmail"] = row["customer_email"]
        if row.get("customer_segment"):
            doc["customerSegment"] = row["customer_segment"]
        if row.get("loyalty_tier"):
            doc["loyaltyTier"] = row["loyalty_tier"]
        if row.get("total_spend_90d") is not None:
            doc["totalSpend90d"] = float(row["total_spend_90d"])
        if row.get("days_since_last_purchase") is not None:
            doc["daysSinceLastPurchase"] = int(row["days_since_last_purchase"])
        if row.get("cart_abandonment_count") is not None:
            doc["cartAbandonmentCount"] = int(row["cart_abandonment_count"])
        if row.get("support_tickets_count") is not None:
            doc["supportTicketsCount"] = int(row["support_tickets_count"])
        if row.get("sentiment_score") is not None:
            doc["sentimentScore"] = float(row["sentiment_score"])
        if row.get("automated_retention_action"):
            doc["automatedRetentionAction"] = row["automated_retention_action"]

        return doc

    def _commit_single_batch(
        self,
        batch_id: int,
        chunk: List[Dict[str, Any]],
        dry_run: bool = False
    ) -> int:
        """Commits a single batch of up to 500 documents with retry logic."""
        if dry_run:
            logger.info("[DRY-RUN] Batch %d: Would write %d documents", batch_id, len(chunk))
            return len(chunk)

        client = self._get_fs_client()
        coll = client.collection(self.collection_name)
        max_retries = 3

        for attempt in range(max_retries):
            try:
                batch = client.batch()
                for doc in chunk:
                    doc_id = doc["customerId"]
                    doc_ref = coll.document(doc_id)
                    # merge=True prevents overwriting existing live fields (recent friction, cart, etc.)
                    batch.set(doc_ref, doc, merge=True)

                batch.commit()
                return len(chunk)
            except (GoogleAPICallError, RetryError, ServiceUnavailable) as exc:
                if attempt < max_retries - 1:
                    backoff = 1.5 * (attempt + 1)
                    logger.warning(
                        "Batch %d attempt %d failed: %s. Retrying in %.1fs...",
                        batch_id, attempt + 1, exc, backoff
                    )
                    time.sleep(backoff)
                else:
                    logger.error("Batch %d permanently failed after %d retries: %s", batch_id, max_retries, exc)
                    raise

        return 0

    def sync(
        self,
        dry_run: bool = False,
        customer_id: Optional[str] = None,
        latest_only: bool = True
    ) -> SyncSummary:
        """
        Executes the full Reverse-ETL sync pipeline.
        Returns a SyncSummary with execution telemetry.
        """
        start_time = time.time()
        records = self.fetch_prediction_records(customer_id=customer_id, latest_only=latest_only)
        total_records = len(records)

        summary = SyncSummary(
            total_fetched=total_records,
            dry_run=dry_run
        )

        if total_records == 0:
            logger.info("No records to sync.")
            summary.duration_seconds = round(time.time() - start_time, 2)
            return summary

        # Transform to Firestore document schemas
        documents = [self.map_to_firestore_document(r) for r in records]

        # Chunk into batches of up to batch_size (max 500)
        chunks = [
            documents[i:i + self.batch_size]
            for i in range(0, total_records, self.batch_size)
        ]
        summary.total_batches = len(chunks)
        logger.info(
            "Prepared %d documents into %d batches (batch_size: %d, max_workers: %d)",
            total_records, summary.total_batches, self.batch_size, self.max_workers
        )

        # Dispatch batches concurrently
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_batch = {
                executor.submit(self._commit_single_batch, idx + 1, chunk, dry_run): idx + 1
                for idx, chunk in enumerate(chunks)
            }

            for future in as_completed(future_to_batch):
                batch_id = future_to_batch[future]
                try:
                    committed_count = future.result()
                    summary.total_synced += committed_count
                    summary.successful_batches += 1
                except Exception as exc:
                    failed_chunk_size = len(chunks[batch_id - 1])
                    summary.total_failed += failed_chunk_size
                    summary.failed_batches += 1
                    logger.error("Batch %d encountered fatal error: %s", batch_id, exc)

        duration = time.time() - start_time
        summary.duration_seconds = round(duration, 2)
        summary.throughput_records_per_sec = (
            round(summary.total_synced / duration, 1) if duration > 0 else 0.0
        )

        logger.info(
            "Sync Complete: %d/%d synced, %d failed across %d batches in %.2fs (%.1f docs/sec)",
            summary.total_synced,
            total_records,
            summary.total_failed,
            summary.total_batches,
            summary.duration_seconds,
            summary.throughput_records_per_sec
        )

        return summary


def main():
    parser = argparse.ArgumentParser(
        description="Reverse-ETL: Sync BigQuery customer churn risk predictions into Firestore customer profiles."
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Google Cloud project ID")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="Firestore database ID")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="BigQuery dataset ID")
    parser.add_argument("--table", default=DEFAULT_TABLE, help="BigQuery predictions table name")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Firestore target collection name")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Firestore batch size (1..500)")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS, help="Parallel worker threads")
    parser.add_argument("--customer-id", help="Sync a specific customer ID only")
    parser.add_argument("--all", dest="latest_only", action="store_false", help="Sync all rows instead of latest only")
    parser.add_argument("--dry-run", action="store_true", help="Preview records without modifying Firestore")
    parser.set_defaults(latest_only=True)

    args = parser.parse_args()

    pipeline = BigQueryToFirestoreChurnSync(
        project_id=args.project,
        database_id=args.database,
        dataset_id=args.dataset,
        table_id=args.table,
        collection_name=args.collection,
        batch_size=args.batch_size,
        max_workers=args.max_workers
    )

    summary = pipeline.sync(
        dry_run=args.dry_run,
        customer_id=args.customer_id,
        latest_only=args.latest_only
    )

    if summary.total_failed > 0:
        logger.error("Sync completed with %d failures.", summary.total_failed)
        sys.exit(1)
    else:
        logger.info("Sync pipeline finished successfully.")
        sys.exit(0)


if __name__ == "__main__":
    main()

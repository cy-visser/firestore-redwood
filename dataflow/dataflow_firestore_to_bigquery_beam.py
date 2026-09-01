#!/usr/bin/env python3
"""
Apache Beam Cloud Dataflow Streaming Pipeline:
Replicates Firestore Enterprise Native collection documents into BigQuery CDC table.
"""

import os
import argparse
import json
import logging
import time
from datetime import datetime, timezone
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass

import apache_beam as beam
from apache_beam.options.pipeline_options import (
    PipelineOptions,
    SetupOptions,
    StandardOptions,
    GoogleCloudOptions,
    WorkerOptions,
)

DEFAULT_PROJECT = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT")
DEFAULT_DATABASE = os.getenv("FIRESTORE_DATABASE_ID") or os.getenv("FIRESTORE_DATABASE")
DEFAULT_REGION = os.getenv("GCP_REGION")
DEFAULT_COLLECTION = os.getenv("FIRESTORE_COLLECTION")
DEFAULT_DATASET = os.getenv("BIGQUERY_DATASET")
DEFAULT_CDC_TABLE = os.getenv("BIGQUERY_CDC_TABLE")
DEFAULT_OUTPUT_TABLE = os.getenv("BIGQUERY_OUTPUT_TABLE") or (f"{DEFAULT_PROJECT}:{DEFAULT_DATASET}.{DEFAULT_CDC_TABLE}" if DEFAULT_PROJECT and DEFAULT_DATASET and DEFAULT_CDC_TABLE else None)


from apache_beam.transforms.periodicsequence import PeriodicImpulse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DataflowFirestoreBeam")


class ReadFirestoreNativeEventsFn(beam.DoFn):
    """
    Beam DoFn that continuously streams documents from Firestore Enterprise Native on each impulse.
    """
    def __init__(self, project_id: str, database_id: str, collection_name: str):
        self.project_id = project_id
        self.database_id = database_id
        self.collection_name = collection_name
        self.seen_doc_versions = set()

    def process(self, timestamp):
        from firestore_auth import get_firestore_native_client
        client = get_firestore_native_client(
            project_id=self.project_id,
            database_id=self.database_id
        )
        coll_ref = client.collection(self.collection_name)
        
        try:
            docs = coll_ref.stream()
            for doc in docs:
                doc_id = doc.id
                doc_dict = doc.to_dict() or {}
                update_time = doc.update_time or doc.create_time
                update_iso = update_time.isoformat() if update_time else datetime.now(timezone.utc).isoformat()
                version_key = f"{doc_id}:{update_iso}"
                
                if version_key not in self.seen_doc_versions:
                    self.seen_doc_versions.add(version_key)
                    is_insert = bool(doc.create_time and doc.update_time and doc.create_time == doc.update_time)
                    yield {
                        "operation_type": "insert" if is_insert else "update",
                        "document_id": doc_id,
                        "document_data": doc_dict,
                        "change_timestamp": update_iso,
                    }
        except Exception as e:
            logger.warning(f"Error reading from Firestore collection '{self.collection_name}': {e}")


class TransformFirestoreEventDoFn(beam.DoFn):
    """
    Transforms Firestore Native document events into BigQuery CDC table row dictionaries.
    """
    def process(self, event):
        op_type = event.get("operation_type", "insert")
        doc_id = event.get("document_id", "")
        doc_data = event.get("document_data") or {}
        order_id = str(doc_data.get("orderId") or doc_id)

        change_timestamp = event.get("change_timestamp") or datetime.now(timezone.utc).isoformat()
        financials = doc_data.get("financials") or {}

        doc_data_json = None
        if doc_data:
            doc_data_json = json.dumps(doc_data, default=str)

        row = {
            "order_id": order_id,
            "operation_type": op_type,
            "customer_id": doc_data.get("customerId"),
            "customer_name": doc_data.get("customerName"),
            "customer_email": doc_data.get("customerEmail"),
            "customer_segment": doc_data.get("customerSegment"),
            "order_status": doc_data.get("orderStatus"),
            "payment_status": doc_data.get("paymentStatus"),
            "payment_method": doc_data.get("paymentMethod"),
            "currency": doc_data.get("currency"),
            "grand_total": float(financials.get("grandTotal")) if financials.get("grandTotal") is not None else None,
            "subtotal": float(financials.get("subtotal")) if financials.get("subtotal") is not None else None,
            "profit_margin": float(financials.get("profitMargin")) if financials.get("profitMargin") is not None else None,
            "change_timestamp": change_timestamp,
            "document_data": doc_data_json
        }
        yield row


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--firestore_project", default=DEFAULT_PROJECT, help="Google Cloud project ID for Firestore")
    parser.add_argument("--firestore_database", default=DEFAULT_DATABASE, help="Firestore database ID")
    parser.add_argument("--firestore_region", default=DEFAULT_REGION, help="Firestore region")
    parser.add_argument("--firestore_collection", default=DEFAULT_COLLECTION, help="Firestore collection name")
    parser.add_argument("--output_table", default=DEFAULT_OUTPUT_TABLE, help="Destination BigQuery table (PROJECT:DATASET.TABLE)")

    known_args, pipeline_args = parser.parse_known_args()

    pipeline_options = PipelineOptions(pipeline_args)
    pipeline_options.view_as(SetupOptions).save_main_session = True

    p = beam.Pipeline(options=pipeline_options)
    (
        p
        | "PeriodicTrigger" >> PeriodicImpulse(
            start_timestamp=time.time(),
            stop_timestamp=time.time() + (86400 * 365 * 10),
            fire_interval=5.0,
            apply_windowing=False
        )
        | "ReadFirestoreEvents" >> beam.ParDo(
            ReadFirestoreNativeEventsFn(
                project_id=known_args.firestore_project,
                database_id=known_args.firestore_database,
                collection_name=known_args.firestore_collection
            )
        )
        | "TransformEvents" >> beam.ParDo(TransformFirestoreEventDoFn())
        | "WriteToBigQuery" >> beam.io.WriteToBigQuery(
            known_args.output_table,
            schema={
                "fields": [
                    {"name": "order_id", "type": "STRING", "mode": "REQUIRED"},
                    {"name": "operation_type", "type": "STRING", "mode": "REQUIRED"},
                    {"name": "customer_id", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "customer_name", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "customer_email", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "customer_segment", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "order_status", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "payment_status", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "payment_method", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "currency", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "grand_total", "type": "FLOAT", "mode": "NULLABLE"},
                    {"name": "subtotal", "type": "FLOAT", "mode": "NULLABLE"},
                    {"name": "profit_margin", "type": "FLOAT", "mode": "NULLABLE"},
                    {"name": "change_timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
                    {"name": "document_data", "type": "JSON", "mode": "NULLABLE"}
                ]
            },
            write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND,
            create_disposition=beam.io.BigQueryDisposition.CREATE_NEVER,
            method=beam.io.WriteToBigQuery.Method.STREAMING_INSERTS
        )
    )

    result = p.run()
    logger.info("Pipeline submitted successfully!")
    return result

if __name__ == "__main__":
    run()

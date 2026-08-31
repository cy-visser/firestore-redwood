#!/usr/bin/env python3
"""
Apache Beam Cloud Dataflow Streaming Pipeline:
Replicates Firestore Change Stream ('orders-stream') into BigQuery ('redwood_retail.orders_cdc').
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
import apache_beam as beam
from apache_beam.options.pipeline_options import (
    PipelineOptions,
    SetupOptions,
    StandardOptions,
    GoogleCloudOptions,
    WorkerOptions,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DataflowFirestoreBeam")


class ReadFirestoreChangeStreamFn(beam.DoFn):
    """
    Beam DoFn that opens the Firestore Change Stream and yields change events.
    """
    def __init__(self, project_id: str, database_id: str, region: str, collection_name: str):
        self.project_id = project_id
        self.database_id = database_id
        self.region = region
        self.collection_name = collection_name

    def process(self, element):
        from firestore_auth import get_firestore_mongo_client
        logger.info(f"Opening Firestore change stream for {self.database_id}.{self.collection_name}...")
        client = get_firestore_mongo_client(
            project_id=self.project_id,
            database_id=self.database_id,
            region=self.region
        )
        coll = client[self.database_id][self.collection_name]
        with coll.watch(full_document="updateLookup") as stream:
            for event in stream:
                yield event


class TransformChangeStreamEventDoFn(beam.DoFn):
    """
    Transforms Firestore Change Stream events into BigQuery table row dictionaries.
    """
    def process(self, event):
        op_type = event.get("operationType", "unknown")
        doc_key = event.get("documentKey", {}).get("_id", "")
        full_doc = event.get("fullDocument") or {}
        order_id = str(full_doc.get("orderId") or doc_key or "")

        cluster_time = event.get("clusterTime")
        if cluster_time:
            event_time_sec = getattr(cluster_time, "time", int(time.time()))
            event_dt = datetime.fromtimestamp(event_time_sec, timezone.utc)
        else:
            event_dt = datetime.now(timezone.utc)

        change_timestamp_iso = event_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        financials = full_doc.get("financials") or {}

        doc_data_json = None
        if full_doc:
            cleaned_doc = {}
            for k, v in full_doc.items():
                if isinstance(v, datetime):
                    cleaned_doc[k] = v.isoformat()
                elif hasattr(v, "__str__") and type(v).__name__ in ("ObjectId", "Timestamp", "Decimal128"):
                    cleaned_doc[k] = str(v)
                else:
                    cleaned_doc[k] = v
            doc_data_json = json.dumps(cleaned_doc)

        row = {
            "order_id": order_id,
            "operation_type": op_type,
            "customer_id": full_doc.get("customerId"),
            "customer_name": full_doc.get("customerName"),
            "customer_email": full_doc.get("customerEmail"),
            "customer_segment": full_doc.get("customerSegment"),
            "order_status": full_doc.get("orderStatus"),
            "payment_status": full_doc.get("paymentStatus"),
            "payment_method": full_doc.get("paymentMethod"),
            "currency": full_doc.get("currency"),
            "grand_total": float(financials.get("grandTotal")) if financials.get("grandTotal") is not None else None,
            "subtotal": float(financials.get("subtotal")) if financials.get("subtotal") is not None else None,
            "profit_margin": float(financials.get("profitMargin")) if financials.get("profitMargin") is not None else None,
            "change_timestamp": change_timestamp_iso,
            "document_data": doc_data_json
        }
        yield row


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--firestore_project", default="elevate-cyvisser")
    parser.add_argument("--firestore_database", default="redwood")
    parser.add_argument("--firestore_region", default="europe-west4")
    parser.add_argument("--firestore_collection", default="orders")
    parser.add_argument("--output_table", default="elevate-cyvisser:redwood_retail.orders_cdc")
    
    known_args, pipeline_args = parser.parse_known_args()

    pipeline_options = PipelineOptions(pipeline_args)
    pipeline_options.view_as(SetupOptions).save_main_session = True

    p = beam.Pipeline(options=pipeline_options)
    (
        p
        | "CreateTrigger" >> beam.Create([None])
        | "ReadChangeStream" >> beam.ParDo(
            ReadFirestoreChangeStreamFn(
                project_id=known_args.firestore_project,
                database_id=known_args.firestore_database,
                region=known_args.firestore_region,
                collection_name=known_args.firestore_collection
            )
        )
        | "TransformEvents" >> beam.ParDo(TransformChangeStreamEventDoFn())
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
            create_disposition=beam.io.BigQueryDisposition.CREATE_NEVER
        )
    )

    result = p.run()
    logger.info("Pipeline submitted successfully!")
    return result

if __name__ == "__main__":
    run()

# Redwood Retail: Real-Time CDC from Firestore with MongoDB Compatibility to BigQuery via Cloud Dataflow

This project implements a production-ready Change Data Capture (CDC) streaming pipeline on Google Cloud. It synchronizes real-time change events from **Firestore Enterprise with MongoDB Compatibility** into **BigQuery** using a managed **Google Cloud Dataflow** streaming pipeline.

---

## 1. System Overview

* **Source Database**: Google Cloud Firestore Enterprise (Database ID: `redwood`, Region: `europe-west4`) with MongoDB API compatibility enabled.
* **Change Stream**: Firestore Native Change Stream (`orders-stream`) scoped to the `orders` collection group with a 7-day retention period.
* **Streaming Engine**: Google Cloud Dataflow (Apache Beam 2.75 Python Streaming Runner) executing on autoscaling worker VMs within a dedicated private VPC.
* **Target Sink**: Google BigQuery dataset `redwood_retail` with a DAY-partitioned and multi-column clustered table `orders_cdc`.
* **Security & Authentication**: Zero hardcoded secrets. All applications and pipeline workers authenticate via Google Cloud Application Default Credentials (ADC) and IAM Service Accounts using native `MONGODB-OIDC` authentication.

---

## 2. Prerequisites

Ensure you have the following installed and authenticated:

1. **Google Cloud SDK (`gcloud`)**:
   ```bash
   gcloud components update
   gcloud components install alpha
   gcloud auth login
   gcloud auth application-default login
   gcloud config set project elevate-cyvisser
   ```

2. **Terraform CLI** ($\ge$ 1.5.0):
   ```bash
   terraform -version
   ```

3. **Python 3.11+ Environment**:
   ```bash
   python3 -m venv /usr/local/google/home/cyvisser/source/firestore/venv
   source /usr/local/google/home/cyvisser/source/firestore/venv/bin/activate
   pip install "apache-beam[gcp]>=2.75.0" "pymongo>=4.8.0" "google-cloud-firestore>=2.20.0" "google-cloud-bigquery>=3.25.0"
   ```

---

## 3. Greenfield Deployment Guide

The entire infrastructure—including Firestore Enterprise, Change Streams, dedicated VPC networking, IAM service accounts, BigQuery tables, and the Cloud Dataflow streaming job—is provisioned deterministically with Terraform.

### Step 1: Review Terraform Variables

Inspect `terraform/terraform.tfvars`:
```hcl
project_id                     = "elevate-cyvisser"
region                         = "europe-west4"
firestore_database_id          = "redwood"
firestore_edition              = "ENTERPRISE"
enable_pitr                    = true
bigquery_dataset_id            = "redwood_retail"
gcs_bucket_name_prefix         = "redwood-retail"
service_account_id             = "dataflow-redwood-sa"
service_account_display_name   = "Dataflow Redwood Retail Service Account"
enable_change_stream           = true
change_stream_name             = "orders-stream"
change_stream_collection_group = "orders"
change_stream_retention        = "7d"
dataflow_job_name              = "firestore-orders-to-bigquery"
```

### Step 2: Initialize and Apply Terraform

Navigate to the `terraform/` directory and execute:
```bash
cd /usr/local/google/home/cyvisser/source/firestore/redwood/terraform
terraform init
terraform apply -auto-approve
```

#### Provisioning Lifecycle Order:
1. **API Enablement**: Enables `firestore`, `dataflow`, `compute`, `bigquery`, `storage`, `iam`.
2. **Firestore Enterprise Database**: Creates database `redwood` in `europe-west4` with PITR.
3. **Firestore Change Stream**: Creates `orders-stream` and deterministically waits until `startTime` activation has elapsed (~5 minutes).
4. **VPC Networking**: Creates VPC `redwood-dataflow-net`, subnet `redwood-dataflow-subnet`, Cloud Router, Cloud NAT, and internal firewall.
5. **IAM Service Account**: Creates `dataflow-redwood-sa` with `roles/datastore.owner`, `roles/dataflow.worker`, `roles/bigquery.dataEditor`, and `roles/storage.objectAdmin`.
6. **BigQuery Target**: Provisions dataset `redwood_retail` and table `orders_cdc`.
7. **Cloud Dataflow Pipeline**: Packages dependencies and submits the streaming job to Cloud Dataflow using `DataflowRunner`.

---

## 4. Verification & Monitoring

### 1. Verify Firestore Change Stream
```bash
gcloud alpha firestore change-streams list --database=redwood --project=elevate-cyvisser
```
*Expected Status*: Returns `orders-stream` with scope `orders` and retention `604800s` (7d).

### 2. Verify Cloud Dataflow Streaming Job
```bash
gcloud dataflow jobs list --region=europe-west4 --project=elevate-cyvisser --status=active
```
*Console URL*: Open [https://console.cloud.google.com/dataflow/jobs](https://console.cloud.google.com/dataflow/jobs) to view the real-time execution graph, worker autoscaling, and throughput metrics.

### 3. Verify BigQuery CDC Table Schema
```bash
bq show elevate-cyvisser:redwood_retail.orders_cdc
```
*Attributes Verified*:
* Partitioning: `DAY` on `change_timestamp`
* Clustering: `order_id`, `customer_id`, `order_status`
* Schema: Structured columns (`order_id`, `operation_type`, `customer_id`, `customer_name`, `order_status`, `payment_status`, `grand_total`, `currency`) plus full JSON document payload in `document_data`.

---

## 5. Testing Real-Time Replication

### A. Run Interactive Change Stream Test
Execute [`test_change_stream_watch.py`](file:///usr/local/google/home/cyvisser/source/firestore/redwood/test_change_stream_watch.py) to watch live `insert`, `update`, and `delete` events captured over IAM authentication:
```bash
/usr/local/google/home/cyvisser/source/firestore/venv/bin/python3 /usr/local/google/home/cyvisser/source/firestore/redwood/test_change_stream_watch.py
```

### B. Generate Batch Retail Orders
Execute [`generate_retail_dataset.py`](file:///usr/local/google/home/cyvisser/source/firestore/redwood/generate_retail_dataset.py) to write synthetic e-commerce orders into Firestore:
```bash
/usr/local/google/home/cyvisser/source/firestore/venv/bin/python3 /usr/local/google/home/cyvisser/source/firestore/redwood/generate_retail_dataset.py --count 50 --workers 4
```

### C. Query Replicated CDC Records in BigQuery
Run a query against BigQuery to confirm events were streamed in real time:
```sql
SELECT 
  order_id, 
  operation_type, 
  customer_name, 
  order_status, 
  payment_status, 
  grand_total, 
  currency, 
  change_timestamp,
  JSON_VALUE(document_data, "$.paymentMethod") AS payment_method
FROM `elevate-cyvisser.redwood_retail.orders_cdc`
ORDER BY change_timestamp DESC
LIMIT 10;
```

---

## 6. BigQuery Retail Analytics

The file [`bigquery_churn_sentiment_analysis.sql`](file:///usr/local/google/home/cyvisser/source/firestore/redwood/bigquery_churn_sentiment_analysis.sql) provides comprehensive analytical queries including:

* **Customer Churn Risk Identification**: Aggregates return rates, order cancellations, and delayed shipping against customer spend tiers.
* **Profit Margin Analysis**: Computes real-time profit margins across customer segments using JSON extraction (`$.financials.profitMargin`).
* **Real-time Order State Transitions**: Reconstructs complete order lifecycle histories from change stream operations (`insert` $\rightarrow$ `update` $\rightarrow$ `delete`).

---

## 7. Teardown & Cleanup

To destroy all cloud resources and stop all billing:

```bash
cd /usr/local/google/home/cyvisser/source/firestore/redwood/terraform
terraform destroy -auto-approve
```

#### Teardown Process:
1. Automatically drains and cancels the active Cloud Dataflow streaming job.
2. Removes the BigQuery table and dataset.
3. Deletes the VPC network, subnetwork, Cloud Router, and NAT gateway.
4. Deletes the Cloud Storage staging bucket and IAM role assignments.
5. Deletes the Firestore Change Stream and Firestore Enterprise database.

> [!NOTE]
> **Firestore Database ID Re-creation Cooldown**: Google Cloud enforces a 5-minute cooldown period on database IDs following deletion. If re-deploying immediately to the same project, wait 5 minutes before executing `terraform apply`.

---

## 8. Security & Authentication Architecture

* **Zero-Secret Design**: No usernames, passwords, or `.env` files are stored in code or on disk.
* **IAM `MONGODB-OIDC` Authentication**: Python applications and Beam worker VMs exchange Google Cloud OAuth2 / Application Default Credentials tokens with Firestore's MongoDB endpoint on port 443 over TLS.
* **Private Worker Isolation**: Dataflow worker VMs run with private IP addresses (`--no_use_public_ips`) inside `redwood-dataflow-subnet` and route external requests through Cloud NAT.

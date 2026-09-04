# Redwood Retail: Real-Time CDC from Firestore Enterprise Native to BigQuery via Cloud Dataflow

This project implements a production-ready Change Data Capture (CDC) streaming pipeline on Google Cloud. It synchronizes real-time change events from **Firestore Enterprise in Native Mode** into **BigQuery** using a managed **Google Cloud Dataflow** streaming pipeline.

![Redwood Architecture](docs/images/redwood_bootstrap_architecture.jpg)

> [!NOTE]
> For a detailed technical architecture deep dive, two-phase bootstrap documentation, Dataflow IAM delegation model, and troubleshooting steps, see the **[Architecture, Security & Operational Deployment Guide](docs/architecture_and_deployment_guide.md)**.

---

## 1. System Overview

* **Source Database**: Google Cloud Firestore Enterprise in Native Mode (`FIRESTORE_NATIVE`, Database ID: `redwood`, Region: `europe-west4`, Collection: `retail`).
* **Streaming Engine**: Google Cloud Dataflow streaming job (`DataflowRunner`) continuously ingesting document changes using native `google-cloud-firestore`.
* **Target Sink**: Google BigQuery dataset `redwood_retail` with a DAY-partitioned and multi-column clustered table `retail_cdc`.
* **Security & Authentication**: All applications and pipeline workers authenticate via Google Cloud Application Default Credentials (ADC) and IAM Service Accounts.

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
   python3 -m venv .venv
   source .venv/bin/activate
   pip install "apache-beam[gcp]>=2.75.0" "google-cloud-firestore>=2.20.0" "google-cloud-bigquery>=3.25.0" "python-dotenv>=1.0.0"
   ```

4. **Environment Configuration (`.env`)**:
   Copy `.env.example` to `.env` and adjust the variables for your Google Cloud project and resources:
   ```bash
   cp .env.example .env
   ```
   All Python scripts, BigQuery SQL runners, and Dataflow pipelines automatically load configuration from `.env`.

---

## 3. Automated Deployment

You can deploy the entire end-to-end architecture (APIs, Firestore Enterprise Native database, VPC network, BigQuery dataset/table, Cloud Dataflow streaming pipeline, synthetic order seeding, and BigQuery ML models) by running:

```bash
./deploy.sh
```

### Deployment CLI Options:
* `./deploy.sh`: Runs full deployment, seeds 250 initial synthetic orders, and trains the BigQuery ML churn model.
* `./deploy.sh --run-tests`: Executes the automated test suite (13/13 tests) verifying the agent, tools, and schemas.
* `./deploy.sh --build-agent-image`: Builds and publishes the Loyalty Agent Daemon container to Google Artifact Registry.
* `./deploy.sh --deploy-agent`: Provisions and deploys the persistent Cloud Run Loyalty Agent Daemon via Terraform.
* `./deploy.sh --test-agent-daemon`: Executes end-to-end validation: probes `/healthz` and verifies live Firestore session offer generation.
* `./deploy.sh --run-agent`: Runs the Loyalty Agent daemon locally on your workstation with real-time Firestore listeners.
* `./deploy.sh --create-project`: Bootstraps a fresh Google Cloud project using `terraform/bootstrap` before deploying components.
* `./deploy.sh --seed-count 1000`: Seeds 1,000 synthetic transaction documents into Firestore.
* `./deploy.sh --dry-run`: Runs Terraform plan and config verification without altering GCP resources.
* `./deploy.sh --skip-seed`: Deploys infrastructure without seeding sample orders.
* `./deploy.sh --skip-bqml`: Deploys infrastructure without training BigQuery ML models.
* `./deploy.sh --teardown` (or `./teardown.sh`): Cleanly destroys all infrastructure and drains Dataflow jobs.

> [!TIP]
> **Mobile Frontend & Cloud Run Daemon Guide**: For step-by-step instructions on deploying the Autonomous Loyalty Agent daemon on Cloud Run for Cyrus's mobile app, see the **[Mobile Frontend & Cloud Run Loyalty Agent Deployment Guide](docs/mobile_deployment_guide.md)**.

#### Automated Provisioning Lifecycle:
1. **(Optional) Project Bootstrap**: Provisions a new Google Cloud project via `terraform/bootstrap`, attaches billing, enables base APIs, and populates `.env`.
2. **Prerequisites & .env Loading**: Maps `.env` variables automatically to Terraform (`TF_VAR_*`).
3. **API Enablement**: Enables `firestore`, `dataflow`, `compute`, `bigquery`, `storage`, `iam`.
4. **Firestore Enterprise Native Database**: Creates database `redwood` with Point-in-Time Recovery (PITR) and Firestore API Data Access enabled.
5. **VPC Networking**: Creates VPC `redwood-dataflow-net`, private subnet, Cloud Router, and Cloud NAT.
6. **IAM Service Account**: Configures `dataflow-redwood-sa` with required Datastore, Dataflow, and BigQuery roles.
7. **BigQuery Target**: Provisions dataset `redwood_retail` and CDC table `retail_cdc`.
8. **Cloud Dataflow Pipeline**: Submits the streaming pipeline using `DataflowRunner`.
9. **Data Seeding & CDC Replicate**: Seeds synthetic e-commerce orders into Firestore via native `WriteBatch`.
10. **BigQuery ML Training**: Builds the feature view and trains the Logistic Regression churn prediction model.

### Creating a New Project (Altostrat / Sandbox):
To provision a brand new project before deploying components:
```bash
# 1. Configure bootstrap variables with your billing account
cp terraform/bootstrap/terraform.tfvars.example terraform/bootstrap/terraform.tfvars

# 2. Run deploy with project bootstrap flag
./deploy.sh --create-project
```
See [`terraform/bootstrap/README.md`](terraform/bootstrap/README.md) for full details on standalone bootstrap execution.

---

## 4. Verification & Monitoring

### 1. Verify Firestore Enterprise Native Database
```bash
gcloud alpha firestore databases describe --database=redwood --project=elevate-cyvisser \
  --format="table(name,databaseEdition,type,firestoreDataAccessMode,realtimeUpdatesMode)"
```

### 2. Verify Cloud Dataflow Streaming Job
```bash
gcloud dataflow jobs list --region=europe-west4 --project=elevate-cyvisser --status=active
```
*Console URL*: Open [https://console.cloud.google.com/dataflow/jobs](https://console.cloud.google.com/dataflow/jobs) to view the real-time execution graph, worker autoscaling, and throughput metrics.

### 3. Verify BigQuery CDC Table Schema
```bash
bq show elevate-cyvisser:redwood_retail.retail_cdc
```

---

## 5. Testing Real-Time Replication

### A. Generate Batch Retail Orders
Execute [`generate_retail_dataset.py`](file:///usr/local/google/home/cyvisser/source/firestore/redwood/generate_retail_dataset.py) to write synthetic e-commerce orders into Firestore Native:
```bash
python3 generate_retail_dataset.py --count 100 --workers 4
```

### B. Query Replicated CDC Records in BigQuery
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
FROM `elevate-cyvisser.redwood_retail.retail_cdc`
ORDER BY change_timestamp DESC
LIMIT 10;
```

---

## 6. BigQuery ML Customer Churn Prediction & Analytics

The file [`bigquery_churn_sentiment_analysis.sql`](file:///usr/local/google/home/cyvisser/source/firestore/redwood/bigquery_churn_sentiment_analysis.sql) implements an end-to-end Machine Learning pipeline directly inside Google BigQuery using **BigQuery ML (`logistic_reg`)**.

### Running BigQuery ML Pipeline with `.env`:
```bash
# 1. Preview / Dry-run rendered SQL statements with .env parameters:
python3 run_bigquery_analysis.py --dry-run

# 2. Execute all views, models, and predictions against BigQuery:
python3 run_bigquery_analysis.py --execute
```

---

## 7. Teardown & Cleanup

To destroy all cloud resources, stop billing, and drain Dataflow streaming pipelines:

```bash
./teardown.sh
```

---

## 8. Security & Authentication Architecture

* **Zero-Secret Design**: No usernames, passwords, or credentials stored on disk.
* **IAM & Application Default Credentials (ADC)**: All Python applications and Beam worker VMs authenticate with native Google Cloud IAM.
* **Private Worker Isolation**: Dataflow worker VMs run with private IP addresses (`--no_use_public_ips`) inside `redwood-dataflow-subnet` and route external requests through Cloud NAT.

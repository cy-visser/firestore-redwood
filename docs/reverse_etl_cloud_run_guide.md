# Automated 24-Hour Reverse-ETL: BigQuery to Firestore Churn Sync

This guide documents the architecture, Terraform infrastructure, containerization, and operational runbook for the automated Reverse-ETL pipeline synchronizing daily BigQuery customer churn predictions to Google Cloud Firestore Enterprise Native.

---

## 1. Architectural Architecture & Data Flow

![Automated 24-Hour Reverse-ETL Pipeline Architecture](images/reverse_etl_cloud_run_architecture.jpg)


> [!NOTE]
> The BigQuery Scheduled Query executes daily at 02:00 UTC to evaluate customer behavioral features and predict churn propensity. The Cloud Run Reverse-ETL Job is scheduled via Cloud Scheduler at 03:00 UTC (1 hour later) to ingest the fresh predictions directly into Firestore customer profiles.

---

## 2. Key Pipeline Capabilities

### A. Analytical Extraction & Deduplication
The pipeline queries the latest date partition of the churn predictions table in BigQuery using window functions:
```sql
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
FROM `[PROJECT_ID].[DATASET].customer_churn_predictions`
QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY calculation_timestamp DESC) = 1
```
* Supports both `customer_churn_predictions` (from scheduled queries) and `customer_churn_risk` (from manual BQML scoring).
* Deduplicates to guarantee only the latest model calculation is synchronized.

### B. Operational State Preservation (`merge=True`)
In Firestore, customer documents contain active operational state such as recent friction events, open shopping carts, and support tickets. The Reverse-ETL pipeline strictly uses `merge=True` on all `WriteBatch.set()` operations:
* New fields (`baselineChurnRisk`, `churnRiskTier`, `lastSyncedAt`, `churnEvaluatedAt`) are added.
* Existing real-time operational attributes are 100% preserved without overwriting or data loss.

### C. Resilient High-Throughput Batching
* **Batch Sizing**: Records are chunked into batches of up to 500 documents (enforcing Firestore's atomic batch limit).
* **Parallel Commit Workers**: Batches are committed concurrently across a bounded `ThreadPoolExecutor` (default: 8 workers).
* **Exponential Backoff**: Transient errors (`ServiceUnavailable`, `DeadlineExceeded`) are automatically retried with jittered exponential backoff.

---

## 3. Declarative Terraform Infrastructure

All components are declared in [`terraform/cloud_run.tf`](../terraform/cloud_run.tf), [`terraform/iam.tf`](../terraform/iam.tf), and [`terraform/services.tf`](../terraform/services.tf).

### Provisioned Resources

| Resource Type | Resource Name | Purpose |
| :--- | :--- | :--- |
| `google_artifact_registry_repository` | `pipeline-images` | Docker container registry for pipeline images |
| `google_cloud_run_v2_job` | `churn-sync-job` | Serverless batch container executing `sync_churn_to_firestore.py` |
| `google_cloud_scheduler_job` | `daily-churn-sync-scheduler` | Triggers Cloud Run Job on a 24-hour schedule (`0 3 * * *` UTC) |
| `google_project_iam_member` | `sa_run_invoker` | Grants `roles/run.invoker` to `pipeline_sa` for Cloud Scheduler |
| `google_project_iam_member` | `sa_run_developer` | Grants `roles/run.developer` to `pipeline_sa` for job management |
| `google_project_service` | `run.googleapis.com` | Enables Cloud Run Admin API |
| `google_project_service` | `cloudscheduler.googleapis.com` | Enables Cloud Scheduler API |
| `google_project_service` | `artifactregistry.googleapis.com` | Enables Artifact Registry API |

### Terraform Variables & Customization

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enable_churn_sync_job` | `bool` | `true` | Enables or disables the Cloud Run Job and Cloud Scheduler |
| `churn_sync_schedule` | `string` | `"every 24 hours"` | Cron expression for recurring sync execution |
| `churn_sync_image` | `string` | `""` | Container image URI (defaults to Artifact Registry) |

---

## 4. Container Packaging

The pipeline is packaged into a container using [`Dockerfile`](../Dockerfile) and [`requirements.txt`](../requirements.txt):

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY sync_churn_to_firestore.py ./

ENTRYPOINT ["python", "sync_churn_to_firestore.py"]
```

---

## 5. CLI & Operational Runbook

### Build & Deploy Container Image
To build the container and push it to Artifact Registry via Google Cloud Build:
```bash
./deploy.sh --build-sync-image
```

### Run Dry-Run Preview
To preview record counts and execution plan without writing to Firestore:
```bash
./deploy.sh --sync-churn-dry-run
```

### Run Ad-Hoc Sync Locally
To execute the sync pipeline immediately using your local environment and ADC:
```bash
./deploy.sh --sync-churn
```

### Run Targeted Customer Point-Lookup
To sync a single customer record for troubleshooting:
```bash
python3 sync_churn_to_firestore.py --customer-id cust_retail_32822
```

### Trigger Cloud Run Job On-Demand
To trigger execution of the provisioned Cloud Run Job in Google Cloud:
```bash
./deploy.sh --run-sync-job
```
Or via standard `gcloud`:
```bash
gcloud run jobs execute churn-sync-job --region europe-west4 --wait
```

---

## 6. Verification & Cloud Console Links

1. **Cloud Run Jobs**:
   Verify job execution status, container metrics, and execution logs:
   `https://console.cloud.google.com/run/jobs?project=[PROJECT_ID]`
2. **Cloud Scheduler**:
   Verify schedule, last run time, next run time, and execution logs:
   `https://console.cloud.google.com/cloudscheduler?project=[PROJECT_ID]`
3. **Artifact Registry**:
   Inspect built container images and security scans:
   `https://console.cloud.google.com/artifacts?project=[PROJECT_ID]`
4. **Firestore Database**:
   Verify synced customer documents and `baselineChurnRisk` fields:
   `https://console.cloud.google.com/firestore/databases/[DATABASE_ID]/data?project=[PROJECT_ID]`

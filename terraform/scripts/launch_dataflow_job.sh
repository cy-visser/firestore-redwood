#!/usr/bin/env bash
# ==============================================================================
# Launch Cloud Dataflow Job via Apache Beam & DataflowRunner
# ==============================================================================
set -euo pipefail

PROJECT="$1"
REGION="$2"
DATABASE="$3"
DATASET="$4"
BUCKET="$5"
SA_EMAIL="$6"
JOB_NAME="$7"

PYTHON_EXEC="/usr/local/google/home/cyvisser/source/firestore/venv/bin/python3"
REDWOOD_DIR="/usr/local/google/home/cyvisser/source/firestore/redwood"

echo "=========================================================="
echo "Submitting Cloud Dataflow Streaming Pipeline to GCP"
echo "Project:        $PROJECT"
echo "Region:         $REGION"
echo "Job Name:       $JOB_NAME"
echo "Service Account:$SA_EMAIL"
echo "Temp Bucket:    gs://$BUCKET/temp"
echo "VPC Subnet:     redwood-dataflow-subnet"
echo "=========================================================="

# Check if job is already running
EXISTING_JOB_ID=$(gcloud dataflow jobs list \
  --region="$REGION" \
  --project="$PROJECT" \
  --status=active \
  --filter="name=$JOB_NAME" \
  --format="value(id)" 2>/dev/null | head -n 1 || true)

if [[ -n "$EXISTING_JOB_ID" ]]; then
  echo "Dataflow job '$JOB_NAME' is already running with ID: $EXISTING_JOB_ID"
  exit 0
fi

DATAFLOW_DIR="$REDWOOD_DIR/dataflow"

cd "$DATAFLOW_DIR"

SUBNET_URI="https://www.googleapis.com/compute/v1/projects/${PROJECT}/regions/${REGION}/subnetworks/redwood-dataflow-subnet"

"$PYTHON_EXEC" "$DATAFLOW_DIR/dataflow_firestore_to_bigquery_beam.py" \
  --runner=DataflowRunner \
  --project="$PROJECT" \
  --region="$REGION" \
  --job_name="$JOB_NAME" \
  --temp_location="gs://$BUCKET/temp" \
  --staging_location="gs://$BUCKET/staging" \
  --service_account_email="$SA_EMAIL" \
  --subnetwork="$SUBNET_URI" \
  --no_use_public_ips \
  --firestore_project="$PROJECT" \
  --firestore_database="$DATABASE" \
  --firestore_region="$REGION" \
  --firestore_collection="orders" \
  --output_table="${PROJECT}:${DATASET}.orders_cdc" \
  --setup_file="$DATAFLOW_DIR/setup.py" \
  --streaming \
  --no_auth_cache \
  --experiments=use_runner_v2

echo "Dataflow job '$JOB_NAME' successfully launched!"

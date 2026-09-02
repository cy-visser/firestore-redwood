#!/usr/bin/env bash
# ==============================================================================
# Launch Cloud Dataflow Job via Apache Beam & DataflowRunner
# ==============================================================================
set -euo pipefail

REDWOOD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Source .env if present
if [[ -f "$REDWOOD_DIR/.env" ]]; then
  set -a
  source "$REDWOOD_DIR/.env"
  set +a
fi

PROJECT="${1:-${GCP_PROJECT_ID}}"
REGION="${2:-${GCP_REGION}}"
DATABASE="${3:-${FIRESTORE_DATABASE_ID}}"
DATASET="${4:-${BIGQUERY_DATASET}}"
BUCKET="${5:-${GCS_BUCKET_NAME:-${GCS_BUCKET_PREFIX}-${PROJECT}}}"
SA_EMAIL="${6:-${DATAFLOW_SERVICE_ACCOUNT_EMAIL:-${DATAFLOW_SERVICE_ACCOUNT}@${PROJECT}.iam.gserviceaccount.com}}"
JOB_NAME="${7:-${DATAFLOW_JOB_NAME}}"
COLLECTION="${8:-${FIRESTORE_COLLECTION}}"
CDC_TABLE="${9:-${BIGQUERY_CDC_TABLE}}"

if [[ -z "${PYTHON_EXEC:-}" || ! -x "$PYTHON_EXEC" ]]; then
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
    PYTHON_EXEC="${VIRTUAL_ENV}/bin/python3"
  elif [[ -x "$REDWOOD_DIR/.venv/bin/python3" ]]; then
    PYTHON_EXEC="$REDWOOD_DIR/.venv/bin/python3"
  elif command -v python3 &>/dev/null; then
    PYTHON_EXEC="python3"
  else
    echo "Error: Python 3 executable not found." >&2
    exit 1
  fi
fi

if [[ -z "$PROJECT" ]]; then
  echo "Error: GCP Project ID is required. Pass as argument or define GCP_PROJECT_ID in .env" >&2
  exit 1
fi

echo "=========================================================="
echo "Submitting Cloud Dataflow Streaming Pipeline to GCP"
echo "Project:        $PROJECT"
echo "Region:         $REGION"
echo "Job Name:       $JOB_NAME"
echo "Service Account:$SA_EMAIL"
echo "Temp Bucket:    gs://$BUCKET/temp"
echo "VPC Subnet:     redwood-dataflow-subnet"
echo "Collection:     $COLLECTION"
echo "BQ Target:      ${PROJECT}:${DATASET}.${CDC_TABLE}"
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
  --firestore_collection="$COLLECTION" \
  --output_table="${PROJECT}:${DATASET}.${CDC_TABLE}" \
  --setup_file="$DATAFLOW_DIR/setup.py" \
  --streaming \
  --experiments=use_runner_v2

echo "Dataflow job '$JOB_NAME' successfully launched!"


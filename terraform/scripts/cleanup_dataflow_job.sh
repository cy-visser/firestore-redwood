#!/usr/bin/env bash
# ==============================================================================
# Cleanly Cancel Cloud Dataflow Job on Terraform Destroy
# ==============================================================================
set -euo pipefail

PROJECT="$1"
REGION="$2"
JOB_NAME="$3"

echo "Checking for active Dataflow jobs with name '$JOB_NAME' in project '$PROJECT' ($REGION)..."

JOB_IDS=$(gcloud dataflow jobs list \
  --region="$REGION" \
  --project="$PROJECT" \
  --status=active \
  --filter="name=$JOB_NAME" \
  --format="value(id)" || true)

if [[ -n "$JOB_IDS" ]]; then
  for JOB_ID in $JOB_IDS; do
    echo "Draining/Cancelling Dataflow job $JOB_ID..."
    gcloud dataflow jobs cancel "$JOB_ID" --region="$REGION" --project="$PROJECT" || true
  done
  echo "Dataflow job cancellation requested."
else
  echo "No active Dataflow jobs found for '$JOB_NAME'."
fi

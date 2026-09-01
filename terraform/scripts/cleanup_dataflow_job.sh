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
  --format="value(id)" 2>/dev/null || true)

if [[ -n "$JOB_IDS" ]]; then
  for JOB_ID in $JOB_IDS; do
    echo "Draining/Cancelling Dataflow job $JOB_ID..."
    gcloud dataflow jobs cancel "$JOB_ID" --region="$REGION" --project="$PROJECT" 2>/dev/null || true
  done
  echo "Dataflow job cancellation requested."

  # 1. Poll until all Dataflow jobs have reached a terminal state
  echo "⏳ Waiting for Dataflow job(s) to transition to terminal state (Cancelled/Drained/Failed)..."
  MAX_WAIT_JOBS=180
  START_TIME=$(date +%s)
  while true; do
    ACTIVE_COUNT=$(gcloud dataflow jobs list \
      --region="$REGION" \
      --project="$PROJECT" \
      --status=active \
      --filter="name=$JOB_NAME" \
      --format="value(id)" 2>/dev/null | wc -l || echo 0)

    if [[ "$ACTIVE_COUNT" -eq 0 ]]; then
      echo "✅ All Dataflow jobs for '$JOB_NAME' have reached terminal state."
      break
    fi

    NOW=$(date +%s)
    ELAPSED=$(( NOW - START_TIME ))
    if [[ $ELAPSED -ge $MAX_WAIT_JOBS ]]; then
      echo "⚠️  Timeout ($MAX_WAIT_JOBS s) reached while waiting for Dataflow job cancellation. Continuing..."
      break
    fi

    echo "   Still waiting for $ACTIVE_COUNT active Dataflow job(s) to finish (${ELAPSED}s elapsed)..."
    sleep 5
  done

  # 2. Poll until all GCE worker VM instances are deleted and detached from the subnet
  echo "⏳ Waiting for Dataflow worker VM instances to terminate and release subnet interfaces..."
  MAX_WAIT_VMS=180
  START_TIME_VMS=$(date +%s)
  while true; do
    # Check instances in the redwood-dataflow-subnet or matching the job name
    VM_COUNT=$(gcloud compute instances list \
      --project="$PROJECT" \
      --filter="networkInterfaces.subnetwork ~ redwood-dataflow-subnet OR name ~ firestore-retail" \
      --format="value(name)" 2>/dev/null | wc -l || echo 0)

    if [[ "$VM_COUNT" -eq 0 ]]; then
      echo "✅ All Dataflow worker VM instances have terminated."
      break
    fi

    NOW=$(date +%s)
    ELAPSED=$(( NOW - START_TIME_VMS ))
    if [[ $ELAPSED -ge $MAX_WAIT_VMS ]]; then
      echo "⚠️  Timeout ($MAX_WAIT_VMS s) reached while waiting for VM instances to shut down. Continuing..."
      break
    fi

    echo "   Still waiting for $VM_COUNT worker VM(s) to terminate (${ELAPSED}s elapsed)..."
    sleep 5
  done

  # 3. Stabilization buffer for GCE network interface release
  echo "Stabilizing network interface detachment (5s)..."
  sleep 5
else
  echo "No active Dataflow jobs found for '$JOB_NAME'."
fi


#!/usr/bin/env bash
set -euo pipefail

export CLOUDSDK_METRICS_ENVIRONMENT="datacloud.antigravity"

PROJECT=""
DATABASE=""
STREAM_ID="orders-stream"
COLLECTION_GROUP="orders"
RETENTION="7d"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project=*)
      PROJECT="${1#*=}"
      ;;
    --database=*)
      DATABASE="${1#*=}"
      ;;
    --stream-id=*)
      STREAM_ID="${1#*=}"
      ;;
    --collection-group=*)
      COLLECTION_GROUP="${1#*=}"
      ;;
    --retention=*)
      RETENTION="${1#*=}"
      ;;
    *)
      # Ignore unrecognized flags
      ;;
  esac
  shift
done

echo "=========================================================="
echo "Configuring Firestore MongoDB Change Stream"
echo "Project:          $PROJECT"
echo "Database:         $DATABASE"
echo "Stream ID:        $STREAM_ID"
echo "Scope:            collection_group ('$COLLECTION_GROUP')"
echo "Retention:        $RETENTION"
echo "=========================================================="

if [[ -z "$PROJECT" || -z "$DATABASE" || -z "$STREAM_ID" ]]; then
  echo "Error: Missing required parameters: --project, --database, or --stream-id" >&2
  exit 1
fi

# Check if the change stream already exists
echo "Checking if change stream '$STREAM_ID' exists in database '$DATABASE'..."
EXISTING_STREAM=$(gcloud alpha firestore change-streams describe "$STREAM_ID" \
  --database="$DATABASE" \
  --project="$PROJECT" \
  --format="value(name)" 2>/dev/null || true)

if [[ -n "$EXISTING_STREAM" ]]; then
  echo "Change stream '$STREAM_ID' already exists ($EXISTING_STREAM)."
else
  echo "Creating change stream '$STREAM_ID' for collection group '$COLLECTION_GROUP'..."
  MAX_RETRIES=8
  for ((i=1; i<=MAX_RETRIES; i++)); do
    if gcloud alpha firestore change-streams create "$STREAM_ID" \
      --database="$DATABASE" \
      --project="$PROJECT" \
      --collection-group-scope="$COLLECTION_GROUP" \
      --retention="$RETENTION"; then
      echo "Successfully initiated change stream '$STREAM_ID'."
      break
    else
      if [[ $i -lt $MAX_RETRIES ]]; then
        echo "Database is still initializing. Retrying change stream creation in 5s (attempt $i/$MAX_RETRIES)..."
        sleep 5
      else
        echo "Failed to create change stream after $MAX_RETRIES attempts." >&2
        exit 1
      fi
    fi
  done
fi

# Wait for Change Stream activation startTime if in the future
PYTHON_EXEC="/usr/local/google/home/cyvisser/source/firestore/venv/bin/python3"
if [[ ! -x "$PYTHON_EXEC" ]]; then
  PYTHON_EXEC="python3"
fi

echo "Verifying change stream activation status..."
"$PYTHON_EXEC" - <<PYEOF
import json
import sys
import time
import subprocess
from datetime import datetime, timezone

project = "${PROJECT}"
database = "${DATABASE}"
stream_id = "${STREAM_ID}"

cmd = [
    "gcloud", "alpha", "firestore", "change-streams", "describe", stream_id,
    f"--database={database}", f"--project={project}", "--format=json"
]
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode == 0:
    data = json.loads(res.stdout)
    start_time_str = data.get("startTime")
    if start_time_str:
        start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        now_dt = datetime.now(timezone.utc)
        diff = (start_dt - now_dt).total_seconds()
        if diff > 0:
            print(f"⏳ Change stream '{stream_id}' activation starts at {start_time_str}.")
            print(f"   Waiting {diff:.1f}s for stream to become active before continuing...")
            while diff > 0:
                sleep_time = min(diff, 10.0)
                time.sleep(sleep_time)
                now_dt = datetime.now(timezone.utc)
                diff = (start_dt - now_dt).total_seconds()
                if diff > 0:
                    print(f"   Still waiting ({diff:.0f}s remaining)...")
            time.sleep(2)  # 2s stabilization buffer
            print("✅ Change stream activation start time reached!")
        else:
            print("✅ Change stream is already past start time and active.")
else:
    print(f"Warning: Could not describe stream: {res.stderr}")
PYEOF

echo "Describing change stream '$STREAM_ID':"
gcloud alpha firestore change-streams describe "$STREAM_ID" --database="$DATABASE" --project="$PROJECT"

echo "=========================================================="
echo "Firestore Change Stream configuration complete!"
echo "=========================================================="

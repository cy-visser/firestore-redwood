#!/usr/bin/env bash
set -euo pipefail

export CLOUDSDK_METRICS_ENVIRONMENT="datacloud.antigravity"

PROJECT=""
DATABASE=""
STREAM_ID=""

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
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

echo "=========================================================="
echo "Cleaning up Firestore Change Stream"
echo "Project:   $PROJECT"
echo "Database:  $DATABASE"
echo "Stream ID: $STREAM_ID"
echo "=========================================================="

if [[ -n "$PROJECT" && -n "$DATABASE" && -n "$STREAM_ID" ]]; then
  if gcloud alpha firestore change-streams describe "$STREAM_ID" --database="$DATABASE" --project="$PROJECT" &>/dev/null; then
    echo "Deleting Firestore change stream '$STREAM_ID'..."
    gcloud alpha firestore change-streams delete "$STREAM_ID" \
      --database="$DATABASE" \
      --project="$PROJECT" \
      --quiet || true
    echo "Change stream '$STREAM_ID' deleted."
  else
    echo "Change stream '$STREAM_ID' does not exist or already deleted."
  fi
fi

#!/usr/bin/env bash
# ==============================================================================
# Firestore Enterprise Native Database Provisioning & Management
# ==============================================================================
set -euo pipefail

ACTION="${1:-create}" # create or delete
PROJECT="${2:-}"
DATABASE="${3:-}"
LOCATION="${4:-}"
EDITION="${5:-enterprise}"
ENABLE_PITR="${6:-true}"

if [[ -z "$PROJECT" || -z "$DATABASE" ]]; then
  echo "Error: PROJECT and DATABASE arguments are required." >&2
  exit 1
fi

if [[ "$ACTION" == "create" ]]; then
  echo "Checking if Firestore database '$DATABASE' exists in project '$PROJECT'..."
  EXISTING=$(gcloud alpha firestore databases describe --database="$DATABASE" --project="$PROJECT" --format="value(name)" 2>/dev/null || true)
  
  if [[ -n "$EXISTING" ]]; then
    echo "Firestore database '$DATABASE' already exists."
    DATA_ACCESS=$(gcloud alpha firestore databases describe --database="$DATABASE" --project="$PROJECT" --format="value(firestoreDataAccessMode)" 2>/dev/null || true)
    echo "Current Firestore Data Access Mode: $DATA_ACCESS"
    exit 0
  fi

  echo "Creating Firestore Enterprise Native database '$DATABASE' in region '$LOCATION'..."
  PITR_FLAG=""
  if [[ "$ENABLE_PITR" == "true" ]]; then
    PITR_FLAG="--enable-pitr"
  fi

  MAX_RETRIES=5
  for ((i=1; i<=MAX_RETRIES; i++)); do
    if gcloud alpha firestore databases create \
      --project="$PROJECT" \
      --database="$DATABASE" \
      --location="$LOCATION" \
      --edition="$EDITION" \
      --type=firestore-native \
      --enable-firestore-data-access \
      --enable-realtime-updates \
      --no-enable-mongodb-compatible-data-access \
      $PITR_FLAG; then
      echo "✅ Firestore Enterprise Native database '$DATABASE' created successfully."
      break
    else
      if [[ $i -lt $MAX_RETRIES ]]; then
        echo "⚠️ Database creation attempt $i failed. Retrying in 5s..."
        sleep 5
      else
        echo "❌ Failed to create Firestore database '$DATABASE' after $MAX_RETRIES attempts." >&2
        exit 1
      fi
    fi
  done

  # Verify created database mode
  echo "Verifying database configuration..."
  gcloud alpha firestore databases describe --database="$DATABASE" --project="$PROJECT" \
    --format="table(name,databaseEdition,type,firestoreDataAccessMode,realtimeUpdatesMode)"

elif [[ "$ACTION" == "delete" ]]; then
  echo "Deleting Firestore database '$DATABASE' in project '$PROJECT'..."
  gcloud alpha firestore databases delete --database="$DATABASE" --project="$PROJECT" --quiet 2>/dev/null || true
  echo "✅ Firestore database '$DATABASE' deleted."
else
  echo "Unknown action: $ACTION. Supported: create, delete" >&2
  exit 1
fi

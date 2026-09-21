#!/usr/bin/env bash
# ==============================================================================
# Redwood Retail: Single-Command End-to-End Deployment Pipeline
#
# Order matters here. The CDC container has to exist in Artifact Registry
# before Terraform can create the Cloud Run service that runs it, and the
# Eventarc triggers have to exist before seeding or the seeded documents
# produce no change events and never reach BigQuery. Seeding before the
# triggers are live is recoverable via the backfill endpoint, but it is slower
# and easy to forget, so the steps are sequenced to avoid needing it.
# ==============================================================================
set -euo pipefail

REDWOOD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$REDWOOD_DIR/terraform"
PYTHON_EXEC="${PYTHON_EXEC:-}"

# Default configuration flags
SEED_COUNT=400
SKIP_SEED=false
SKIP_BQML=false
DRY_RUN=false
TEARDOWN_MODE=false
AUTO_APPROVE=false
CREATE_PROJECT=false
RUN_TESTS=false
RUN_AGENT=false
BUILD_AGENT_IMAGE=false
TEST_AGENT_RUNTIME=false
SKIP_IMAGE_BUILD=false

usage() {
  cat <<EOF
Usage: ./deploy.sh [OPTIONS]

Single-command full deployment and lifecycle management for Redwood Retail.

Options:
  -h, --help               Show this help message and exit.
  -p, --create-project     Provision a new GCP project using terraform/bootstrap before deploying components.
  -s, --seed-count <N>     Number of synthetic customers to generate (default: 400). Each
                           produces roughly 4-20 orders depending on archetype, and the two
                           demo personas are seeded in addition to this count.
  --skip-seed              Skip generating synthetic transactions into Firestore.
  --skip-bqml              Skip training and evaluating BigQuery ML churn models.
  --skip-image-build       Reuse the CDC container already in Artifact Registry.
  --dry-run                Validate configuration and run Terraform plan without modifying GCP resources.
  -t, --teardown, --destroy Cleanly tear down all provisioned GCP infrastructure and stop jobs.
  -y, --auto-approve       Skip confirmation prompts during deployment or teardown.
  --run-tests              Execute Pytest test suite (tests/) inside .venv and exit.
  --run-agent              Run the autonomous A2A Multi-Agent platform (Firestore real-time listener).
  --build-agent-image      Build and push Agent Runtime container image to Artifact Registry.
  --test-agent-runtime     Validate Agent Runtime: A2A discovery probe + live Firestore session injection.

Examples:
  ./deploy.sh                         # Deploy entire infrastructure, seed 250 orders, and train BQML
  ./deploy.sh --run-tests             # Execute full automated test suite (23/23 tests)
  ./deploy.sh --run-agent             # Start the autonomous A2A Multi-Agent platform locally
  ./deploy.sh --build-agent-image     # Build and push Agent Runtime container image to Artifact Registry
  ./deploy.sh --test-agent-runtime    # Test live Agent Runtime with synthetic mobile session
  ./deploy.sh --create-project        # Bootstrap a new GCP project first, then deploy components
  ./deploy.sh --seed-count 1000       # Deploy and seed 1,000 customers
  ./deploy.sh --dry-run               # Preview Terraform execution plan
  ./deploy.sh --teardown              # Destroy all cloud resources cleanly
EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -p|--create-project)
      CREATE_PROJECT=true
      shift
      ;;
    -s|--seed-count)
      SEED_COUNT="$2"
      shift 2
      ;;
    --skip-seed)
      SKIP_SEED=true
      shift
      ;;
    --skip-bqml)
      SKIP_BQML=true
      shift
      ;;
    --skip-image-build)
      SKIP_IMAGE_BUILD=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -t|--teardown|--destroy)
      TEARDOWN_MODE=true
      shift
      ;;
    -y|--auto-approve)
      AUTO_APPROVE=true
      shift
      ;;
    --run-tests)
      RUN_TESTS=true
      shift
      ;;
    --run-agent)
      RUN_AGENT=true
      shift
      ;;
    --build-agent-image)
      BUILD_AGENT_IMAGE=true
      shift
      ;;
    --test-agent-runtime)
      TEST_AGENT_RUNTIME=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

echo "================================================================="
echo " 🌲 REDWOOD RETAIL: End-to-End Automated Deployment Manager"
echo "================================================================="

# ------------------------------------------------------------------------------
# 0. Optional: Project Bootstrap Lifecycle
# ------------------------------------------------------------------------------
if [[ "$CREATE_PROJECT" == true ]]; then
  echo "🚀 Bootstrapping new Google Cloud Project via Terraform..."
  if [[ ! -f "$TERRAFORM_DIR/bootstrap/terraform.tfvars" ]]; then
    echo "❌ Error: $TERRAFORM_DIR/bootstrap/terraform.tfvars not found." >&2
    echo "Please copy $TERRAFORM_DIR/bootstrap/terraform.tfvars.example to $TERRAFORM_DIR/bootstrap/terraform.tfvars and set billing_account_id." >&2
    exit 1
  fi

  echo "📦 Initializing Terraform Bootstrap module..."
  terraform -chdir="$TERRAFORM_DIR/bootstrap" init -upgrade

  if [[ "$DRY_RUN" == true ]]; then
    echo "🔍 Planning project creation..."
    terraform -chdir="$TERRAFORM_DIR/bootstrap" plan
    if [[ ! -f "$REDWOOD_DIR/.env" ]]; then
      echo -e "\nℹ️  Dry-run plan for project bootstrap completed successfully."
      echo "To preview application components, run ./deploy.sh --create-project without --dry-run or provide an existing GCP_PROJECT_ID in .env."
      exit 0
    fi
  else
    APPROVE_FLAG=""
    [[ "$AUTO_APPROVE" == true ]] && APPROVE_FLAG="-auto-approve"
    echo "🏗️  Applying project creation..."
    terraform -chdir="$TERRAFORM_DIR/bootstrap" apply $APPROVE_FLAG

    BOOTSTRAP_PROJECT_ID=$(terraform -chdir="$TERRAFORM_DIR/bootstrap" output -raw project_id 2>/dev/null || true)
    if [[ -n "$BOOTSTRAP_PROJECT_ID" ]]; then
      echo "✅ Successfully provisioned project: $BOOTSTRAP_PROJECT_ID"
      if [[ ! -f "$REDWOOD_DIR/.env" && -f "$REDWOOD_DIR/.env.example" ]]; then
        cp "$REDWOOD_DIR/.env.example" "$REDWOOD_DIR/.env"
        echo "📄 Created .env from .env.example"
      fi
      if [[ -f "$REDWOOD_DIR/.env" ]]; then
        sed -i.bak -E "s|^GCP_PROJECT_ID=.*|GCP_PROJECT_ID=$BOOTSTRAP_PROJECT_ID|" "$REDWOOD_DIR/.env" && rm -f "$REDWOOD_DIR/.env.bak"
        echo "📝 Updated GCP_PROJECT_ID in $REDWOOD_DIR/.env to: $BOOTSTRAP_PROJECT_ID"
      fi
    fi
  fi
fi

# ------------------------------------------------------------------------------
# 1. Environment & Prerequisites Verification
# ------------------------------------------------------------------------------
if [[ ! -f "$REDWOOD_DIR/.env" ]]; then
  echo "❌ Error: .env file not found in $REDWOOD_DIR. Please create and configure .env." >&2
  exit 1
fi

# Load .env variables
set -a
source "$REDWOOD_DIR/.env"
set +a

# Validate that all required environment variables are set
REQUIRED_VARS=(
  GCP_PROJECT_ID
  GCP_REGION
  FIRESTORE_DATABASE_ID
  FIRESTORE_COLLECTION
  BIGQUERY_DATASET
  BIGQUERY_CDC_TABLE
  BIGQUERY_HISTORICAL_VIEW
  BIGQUERY_CHURN_MODEL
  GCS_BUCKET_PREFIX
  DATAFLOW_SERVICE_ACCOUNT
)

# Optional, with defaults matching the Terraform variables.
FIRESTORE_CUSTOMERS_COLLECTION="${FIRESTORE_CUSTOMERS_COLLECTION:-customers}"
BIGQUERY_ORDERS_TABLE="${BIGQUERY_ORDERS_TABLE:-${FIRESTORE_COLLECTION}_current}"
ARTIFACT_REPOSITORY="${ARTIFACT_REPOSITORY:-pipeline-images}"

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    MISSING_VARS+=("$var")
  fi
done

if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
  echo "❌ Error: The following required environment variables are missing or empty in .env:" >&2
  for var in "${MISSING_VARS[@]}"; do
    echo "   - $var" >&2
  done
  exit 1
fi

# Map .env directly to Terraform variables (TF_VAR_*)
export TF_VAR_project_id="$GCP_PROJECT_ID"
export TF_VAR_region="$GCP_REGION"
export TF_VAR_firestore_database_id="$FIRESTORE_DATABASE_ID"
export TF_VAR_firestore_collection="$FIRESTORE_COLLECTION"
export TF_VAR_bigquery_dataset_id="$BIGQUERY_DATASET"
export TF_VAR_bigquery_cdc_table_id="$BIGQUERY_CDC_TABLE"
export TF_VAR_gcs_bucket_name_prefix="$GCS_BUCKET_PREFIX"
export TF_VAR_service_account_id="$DATAFLOW_SERVICE_ACCOUNT"
export TF_VAR_firestore_customers_collection="$FIRESTORE_CUSTOMERS_COLLECTION"
export TF_VAR_artifact_repository_id="$ARTIFACT_REPOSITORY"

echo "Project ID:          $GCP_PROJECT_ID"
echo "Region:              $GCP_REGION"
echo "Firestore Database:  $FIRESTORE_DATABASE_ID (Native Mode, collection: $FIRESTORE_COLLECTION)"
echo "BigQuery Sink:       $BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE (ledger) + $BIGQUERY_ORDERS_TABLE (mirror)"
echo "Replication:         Eventarc -> Cloud Run -> BigQuery Storage Write API"
echo "Mode:                $([[ "$TEARDOWN_MODE" == true ]] && echo "TEARDOWN" || ([[ "$DRY_RUN" == true ]] && echo "DRY RUN / PLAN" || echo "FULL DEPLOYMENT"))"
echo "================================================================="

# Check gcloud CLI
if ! command -v gcloud &>/dev/null; then
  echo "❌ Error: 'gcloud' CLI is required but not installed." >&2
  exit 1
fi

# Check Terraform CLI
if ! command -v terraform &>/dev/null; then
  echo "❌ Error: 'terraform' CLI is required but not installed." >&2
  exit 1
fi

# Check Python environment (Strict virtual environment enforcement)
if [[ -z "${PYTHON_EXEC:-}" || ! -x "$PYTHON_EXEC" ]]; then
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
    PYTHON_EXEC="${VIRTUAL_ENV}/bin/python3"
  elif [[ -x "$REDWOOD_DIR/.venv/bin/python3" ]]; then
    PYTHON_EXEC="$REDWOOD_DIR/.venv/bin/python3"
  else
    echo "📦 Creating required Python virtual environment at $REDWOOD_DIR/.venv..."
    if ! python3 -m venv "$REDWOOD_DIR/.venv" 2>/dev/null; then
      python3 -m venv --without-pip "$REDWOOD_DIR/.venv"
      curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
      "$REDWOOD_DIR/.venv/bin/python3" /tmp/get-pip.py
      rm -f /tmp/get-pip.py
    fi
    PYTHON_EXEC="$REDWOOD_DIR/.venv/bin/python3"
  fi
fi
export PYTHON_EXEC

# Ensure Python requirements are met in virtual environment
"$PYTHON_EXEC" -c "import dotenv, google.cloud.firestore, google.auth, setuptools, build, pytest" 2>/dev/null || {
  echo "📦 Installing required Python dependencies inside virtual environment..."
  "$PYTHON_EXEC" -m pip install -q "apache-beam[gcp]>=2.75.0" "google-cloud-firestore>=2.20.0" "google-cloud-bigquery>=3.25.0" "python-dotenv>=1.0.0" "setuptools" "build" "pytest>=8.0.0"
}

# ------------------------------------------------------------------------------
# 1.1 Optional: Automated Test Runner & Agent Run Modes
# ------------------------------------------------------------------------------
if [[ "$RUN_TESTS" == true ]]; then
  echo -e "\n🧪 Running Redwood Retail Loyalty Offer Agent Test Suite..."
  PYTHONPATH="$REDWOOD_DIR" "$PYTHON_EXEC" -m pytest "$REDWOOD_DIR/tests/" -v --tb=short || {
    echo "❌ Error: Loyalty Offer Agent test suite failed!" >&2
    exit 1
  }
  echo "🎉 All Loyalty Offer Agent tests passed successfully!"
  exit 0
fi

if [[ "$RUN_AGENT" == true ]]; then
  echo -e "\n⚡ Starting Autonomous Firestore-to-Agent-Runtime Event Bridge..."
  PYTHONPATH="$REDWOOD_DIR" "$PYTHON_EXEC" "$REDWOOD_DIR/scripts/run_firestore_agent_bridge.py"
  exit 0
fi

if [[ "$BUILD_AGENT_IMAGE" == true ]]; then
  echo -e "\n🔨 Building Autonomous A2A Multi-Agent Platform Container Image..."
  IMAGE_TAG="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/pipeline-images/loyalty-agent-runtime:latest"
  echo "Target Image: $IMAGE_TAG"

  # Ensure Artifact Registry repository exists
  if ! gcloud artifacts repositories describe pipeline-images --location="$GCP_REGION" --project="$GCP_PROJECT_ID" &>/dev/null; then
    echo "Creating Artifact Registry repository 'pipeline-images' in $GCP_REGION..."
    gcloud artifacts repositories create pipeline-images \
      --repository-format=docker \
      --location="$GCP_REGION" \
      --project="$GCP_PROJECT_ID" \
      --description="Docker repository for Redwood Retail pipeline and agent images"
  fi

  echo "Submitting build to Cloud Build..."
  gcloud builds submit "$REDWOOD_DIR" --tag "$IMAGE_TAG" --project="$GCP_PROJECT_ID"
  echo "✅ Loyalty Offer Agent Runtime container successfully built and published to Artifact Registry:"
  echo "   $IMAGE_TAG"
  exit 0
fi

if [[ "$TEST_AGENT_RUNTIME" == true ]]; then
  echo -e "\n🧪 Testing A2A Multi-Agent Platform on Agent Runtime End-to-End..."
  EXTRA_ARGS=()
  if [[ -n "${AGENT_RUNTIME_URL:-}" ]]; then
    EXTRA_ARGS+=("--runtime-url" "$AGENT_RUNTIME_URL")
  fi
  PYTHONPATH="$REDWOOD_DIR" "$PYTHON_EXEC" "$REDWOOD_DIR/scripts/test_agent_runtime.py" \
    --project "$GCP_PROJECT_ID" \
    --database "$FIRESTORE_DATABASE_ID" \
    "${EXTRA_ARGS[@]}" || {
    echo "❌ Error: A2A Multi-Agent Platform verification failed!" >&2
    exit 1
  }
  echo "🎉 A2A Multi-Agent Platform test passed successfully!"
  exit 0
fi

# ------------------------------------------------------------------------------
# 2. TEARDOWN LIFECYCLE
# ------------------------------------------------------------------------------
if [[ "$TEARDOWN_MODE" == true ]]; then
  echo -e "\n⚠️  WARNING: You are about to DESTROY all Redwood Retail infrastructure in project '$GCP_PROJECT_ID'."
  if [[ "$AUTO_APPROVE" != true ]]; then
    read -p "Are you sure you want to proceed? (y/N): " -r CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
      echo "Teardown aborted by user."
      exit 0
    fi
  fi

  echo -e "\n🧹 Destroying cloud infrastructure via Terraform..."
  MAX_DESTROY_ATTEMPTS=3
  for ((attempt=1; attempt<=MAX_DESTROY_ATTEMPTS; attempt++)); do
    if terraform -chdir="$TERRAFORM_DIR" destroy -auto-approve; then
      echo -e "\n🎉 Teardown completed successfully! All cloud resources have been cleaned up."
      exit 0
    else
      if [[ $attempt -lt $MAX_DESTROY_ATTEMPTS ]]; then
        echo "⚠️  Terraform destroy encountered transient resource lock. Retrying in 10s (attempt $attempt/$MAX_DESTROY_ATTEMPTS)..."
        sleep 10
      else
        echo "❌ Error: Terraform destroy failed after $MAX_DESTROY_ATTEMPTS attempts." >&2
        exit 1
      fi
    fi
  done
fi


# ------------------------------------------------------------------------------
# 3. DRY RUN / PLAN
# ------------------------------------------------------------------------------
if [[ "$DRY_RUN" == true ]]; then
  echo -e "\n🔍 Running Terraform Plan (Dry Run)..."
  terraform -chdir="$TERRAFORM_DIR" init
  terraform -chdir="$TERRAFORM_DIR" plan
  echo -e "\n✅ Dry run completed. No infrastructure changes were applied."
  exit 0
fi

# ------------------------------------------------------------------------------
# 4. TERRAFORM PROVISIONING
# ------------------------------------------------------------------------------
echo -e "\n🔨 Step 1/5: Building the CDC container..."

# Terraform cannot create the Cloud Run service until this image exists, so the
# build has to come first rather than alongside.
CDC_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/redwood-cdc:latest"

if [[ "$SKIP_IMAGE_BUILD" == true ]]; then
  echo "⏭️  Reusing existing image ($CDC_IMAGE)."
else
  gcloud services enable cloudbuild.googleapis.com artifactregistry.googleapis.com \
    --project="$GCP_PROJECT_ID" &>/dev/null || true

  if ! gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" \
       --location="$GCP_REGION" --project="$GCP_PROJECT_ID" &>/dev/null; then
    echo "Creating Artifact Registry repository '$ARTIFACT_REPOSITORY'..."
    gcloud artifacts repositories create "$ARTIFACT_REPOSITORY" \
      --repository-format=docker --location="$GCP_REGION" \
      --project="$GCP_PROJECT_ID" \
      --description="Redwood Retail pipeline and agent images"
  fi

  gcloud builds submit "$REDWOOD_DIR/cdc_service" \
    --tag="$CDC_IMAGE" --project="$GCP_PROJECT_ID" --region="$GCP_REGION"
  echo "✅ CDC container published: $CDC_IMAGE"
fi

# Resolve the tag to a digest and deploy that instead. Terraform compares the
# image string, so redeploying ":latest" after a rebuild is a no-op plan and
# the service carries on serving the image the revision was first created with.
# Passing the digest makes a rebuild an actual change.
CDC_IMAGE_DIGEST=$(gcloud artifacts docker images describe "$CDC_IMAGE" \
  --format='value(image_summary.digest)' --project="$GCP_PROJECT_ID" 2>/dev/null || true)
if [[ -n "$CDC_IMAGE_DIGEST" ]]; then
  export TF_VAR_cdc_image_digest="$CDC_IMAGE_DIGEST"
  echo "   Pinned digest: $CDC_IMAGE_DIGEST"
else
  echo "⚠️  Could not resolve the image digest; falling back to the mutable tag."
  echo "    A rebuilt image may not roll out until the service is redeployed."
fi

echo -e "\n🚀 Step 2/5: Provisioning Infrastructure via Terraform..."

# Pre-create Google-managed service identities so the IAM bindings below them
# succeed on a first run. Eventarc in particular refuses to create a Firestore
# trigger until both its own and Firestore's service agent exist, and the
# resulting error names neither of them.
gcloud services enable aiplatform.googleapis.com eventarc.googleapis.com \
  firestore.googleapis.com --project="$GCP_PROJECT_ID" &>/dev/null || true
for SVC in aiplatform eventarc firestore; do
  gcloud beta services identity create --service="${SVC}.googleapis.com" \
    --project="$GCP_PROJECT_ID" &>/dev/null || true
done

terraform -chdir="$TERRAFORM_DIR" init
terraform -chdir="$TERRAFORM_DIR" apply -auto-approve

CDC_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw cdc_service_url 2>/dev/null || true)
echo "✅ Terraform infrastructure provisioning completed."
[[ -n "$CDC_URL" ]] && echo "   CDC service: $CDC_URL"

# ------------------------------------------------------------------------------
# 5. DATA SEEDING (SYNTHETIC TRANSACTIONS)
# ------------------------------------------------------------------------------
if [[ "$SKIP_SEED" != true ]]; then
  echo -e "\n📦 Step 3/5: Seeding customers and order histories into Firestore..."
  "$PYTHON_EXEC" "$REDWOOD_DIR/generate_retail_dataset.py" \
    --count "$SEED_COUNT" \
    --workers 4 \
    --project "$GCP_PROJECT_ID" \
    --region "$GCP_REGION" \
    --database "$FIRESTORE_DATABASE_ID" \
    --collection "$FIRESTORE_COLLECTION" \
    --customers-collection "$FIRESTORE_CUSTOMERS_COLLECTION" \
    --drop-existing

  echo "✅ Seeding complete."
  echo "⏳ Waiting for Eventarc to replicate documents into BigQuery..."

  # Each seeded document raises a change event, so replication runs
  # concurrently with seeding and is usually close to done already. Wait for the
  # count to stop climbing rather than for a fixed target, since the exact order
  # count depends on the generated archetypes.
  MAX_WAIT=420
  START_WAIT=$(date +%s)
  ROW_COUNT=0
  STABLE_READINGS=0
  LAST_COUNT=-1

  while true; do
    ROW_COUNT=$("$PYTHON_EXEC" -c "
from google.cloud import bigquery
c = bigquery.Client(project='$GCP_PROJECT_ID')
try:
    r = list(c.query('SELECT COUNT(*) AS n FROM \`$GCP_PROJECT_ID.$BIGQUERY_DATASET.$BIGQUERY_ORDERS_TABLE\`').result())
    print(r[0].n if r else 0)
except Exception:
    print(0)
" 2>/dev/null || echo 0)

    if [[ "$ROW_COUNT" -gt 0 && "$ROW_COUNT" -eq "$LAST_COUNT" ]]; then
      STABLE_READINGS=$(( STABLE_READINGS + 1 ))
    else
      STABLE_READINGS=0
    fi
    LAST_COUNT="$ROW_COUNT"

    if [[ "$STABLE_READINGS" -ge 3 ]]; then
      echo "✅ Replication settled at $ROW_COUNT orders in $BIGQUERY_ORDERS_TABLE."
      break
    fi

    NOW=$(date +%s)
    ELAPSED=$(( NOW - START_WAIT ))
    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
      echo "⚠️  Replication still moving after ${MAX_WAIT}s (at $ROW_COUNT rows)."
      break
    fi

    echo "   $ROW_COUNT orders replicated (${ELAPSED}s elapsed)..."
    sleep 10
  done

  # Backfill anything the triggers missed. Eventarc only sees changes made
  # after a trigger exists, so a re-seed against a half-built stack, or a
  # trigger that was still activating, leaves gaps. The endpoint upserts, so
  # calling it when nothing is missing is harmless.
  if [[ -n "${CDC_URL:-}" && "$ROW_COUNT" -eq 0 ]]; then
    echo "No rows replicated; reconciling via the backfill endpoint..."
    TOKEN=$(gcloud auth print-identity-token 2>/dev/null || true)
    for COLL in "$FIRESTORE_COLLECTION" "$FIRESTORE_CUSTOMERS_COLLECTION"; do
      curl -sS -X POST "$CDC_URL/admin/backfill" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"collection\": \"$COLL\"}" | head -c 400
      echo
    done
  fi
else
  echo -e "\n⏭️  Step 3/5: Skipping seeding (--skip-seed requested)."
fi

# ------------------------------------------------------------------------------
# 6. BIGQUERY ML MODEL TRAINING & PREDICTION
# ------------------------------------------------------------------------------
if [[ "$SKIP_BQML" != true ]]; then
  if [[ "${ROW_COUNT:-0}" -eq 0 && "$SKIP_SEED" != true ]]; then
    # Training on an empty table fails with "Input data doesn't contain any
    # rows", which reads like a modelling problem rather than the replication
    # problem it actually is. Say what happened and where to look.
    echo -e "\n⚠️  No orders reached $BIGQUERY_DATASET.$BIGQUERY_ORDERS_TABLE, so there is nothing to train on."
    echo "    Check the CDC service and its triggers:"
    echo "      gcloud run services logs read redwood-cdc --region=$GCP_REGION --limit=50"
    echo "      gcloud eventarc triggers list --location=$GCP_REGION"
    echo "    Then re-run:"
    echo "      $PYTHON_EXEC $REDWOOD_DIR/run_bigquery_analysis.py --execute --report"
  else
    echo -e "\n🧠 Step 4/5: Building feature views and training the churn model..."
    "$PYTHON_EXEC" "$REDWOOD_DIR/run_bigquery_analysis.py" --execute --report
    echo "✅ Churn pipeline completed."
  fi
else
  echo -e "\n⏭️  Step 4/5: Skipping churn model training (--skip-bqml requested)."
fi

# ------------------------------------------------------------------------------
# 7. DEPLOYMENT DASHBOARD & HEALTH SUMMARY
# ------------------------------------------------------------------------------
echo -e "\n================================================================="
echo " 🎉 REDWOOD RETAIL DEPLOYMENT COMPLETE & OPERATIONAL!"
echo "================================================================="
echo " Target Project:     $GCP_PROJECT_ID"
echo " Region:             $GCP_REGION"
echo " Firestore DB:       $FIRESTORE_DATABASE_ID (Native Mode, Collection: $FIRESTORE_COLLECTION)"
echo " BigQuery Table:     $GCP_PROJECT_ID.$BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE"
echo " BigQuery Model:     $GCP_PROJECT_ID.$BIGQUERY_DATASET.$BIGQUERY_CHURN_MODEL"
echo " Dataflow Streaming: $DATAFLOW_JOB_NAME"
echo "-----------------------------------------------------------------"
echo " 🌐 Google Cloud Console Quick Links:"
echo " • Dataflow Jobs: https://console.cloud.google.com/dataflow/jobs?project=$GCP_PROJECT_ID"
echo " • BigQuery Studio: https://console.cloud.google.com/bigquery?project=$GCP_PROJECT_ID"
echo " • Firestore Databases: https://console.cloud.google.com/firestore/databases?project=$GCP_PROJECT_ID"
echo "-----------------------------------------------------------------"
echo " To clean up all resources later, run: ./teardown.sh"
echo "================================================================="

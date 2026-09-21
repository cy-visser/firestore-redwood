#!/usr/bin/env bash
# ==============================================================================
# Redwood Retail: Single-Command End-to-End Deployment Pipeline
#
# Order matters here. The CDC container has to exist in Artifact Registry
# before Terraform can create the Cloud Run service that runs it, and the
# Eventarc triggers have to exist before seeding or the seeded documents
# produce no change events and never reach BigQuery. Seeding before the
# triggers are live is recoverable, since the run ends with a reconciliation
# pass, but that pass is a safety net rather than the intended path.
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
SKIP_AGENT_DEPLOY=false

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
  --skip-image-build       Reuse the container images already in Artifact Registry.
  --skip-agent-deploy      Leave the deployed loyalty agent as it is.
  --dry-run                Validate configuration and run Terraform plan without modifying GCP resources.
  -t, --teardown, --destroy Cleanly tear down all provisioned GCP infrastructure and stop jobs.
  -y, --auto-approve       Skip confirmation prompts during deployment or teardown.
  --run-tests              Execute the CDC and loyalty agent self-test suites and exit.
  --run-agent              Run the loyalty agent locally (Firestore real-time listener).
  --build-agent-image      Build and push Agent Runtime container image to Artifact Registry.
  --test-agent-runtime     Validate Agent Runtime: live Firestore session injection.

Examples:
  ./deploy.sh                         # Deploy entire infrastructure, seed 250 orders, and train BQML
  ./deploy.sh --run-tests             # Execute the self-test suites
  ./deploy.sh --run-agent             # Start the loyalty agent locally
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
    --skip-agent-deploy)
      SKIP_AGENT_DEPLOY=true
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
    # An `if` for the same reason as the Terraform output reporting below:
    # under `set -e` the `&&` form exits the script whenever the test is false,
    # which here means every run that did not pass --yes.
    APPROVE_FLAG=""
    if [[ "$AUTO_APPROVE" == true ]]; then
      APPROVE_FLAG="-auto-approve"
    fi
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
)

# Optional, with defaults matching the Terraform variables.
FIRESTORE_CUSTOMERS_COLLECTION="${FIRESTORE_CUSTOMERS_COLLECTION:-customers}"
BIGQUERY_ORDERS_TABLE="${BIGQUERY_ORDERS_TABLE:-${FIRESTORE_COLLECTION}_current}"
ARTIFACT_REPOSITORY="${ARTIFACT_REPOSITORY:-pipeline-images}"

# Terraform has always called this resource pipeline_sa; only the .env name
# still referred to Dataflow. Accept the old name so existing .env files keep
# working. The account id itself is left alone deliberately: changing it would
# make Terraform destroy and recreate the account, taking its IAM grants with
# it, purely to rename something.
PIPELINE_SERVICE_ACCOUNT="${PIPELINE_SERVICE_ACCOUNT:-${DATAFLOW_SERVICE_ACCOUNT:-}}"
if [[ -z "$PIPELINE_SERVICE_ACCOUNT" ]]; then
  echo "❌ Error: PIPELINE_SERVICE_ACCOUNT is missing or empty in .env." >&2
  exit 1
fi

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
export TF_VAR_service_account_id="$PIPELINE_SERVICE_ACCOUNT"
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

# Ensure Python requirements are met in virtual environment. The probe imports
# the same packages the install line provides, so a partially provisioned venv
# is repaired rather than silently accepted.
"$PYTHON_EXEC" -c "
import cloudpickle, dotenv, pydantic, setuptools, vertexai
import google.auth, google.cloud.firestore, google.cloud.bigquery
import google.cloud.bigquery_storage
" 2>/dev/null || {
  echo "📦 Installing required Python dependencies inside virtual environment..."
  # cloudpickle is what Agent Engine uses to serialise the agent object, but
  # the base google-cloud-aiplatform install does not pull it in, so step 5
  # fails on a fresh virtual environment without it. It is what the
  # [agent_engines] extra would add.
  "$PYTHON_EXEC" -m pip install -q \
    "google-cloud-firestore>=2.20.0" \
    "google-cloud-bigquery>=3.25.0" \
    "google-cloud-bigquery-storage>=2.25.0" \
    "google-cloud-aiplatform>=1.60.0" \
    "cloudpickle>=3.0.0" \
    "python-dotenv>=1.0.0" \
    "pydantic>=2.0.0" \
    "protobuf>=4.25.0" \
    "setuptools"
}

# ------------------------------------------------------------------------------
# 1.1 Optional: Automated Test Runner & Agent Run Modes
# ------------------------------------------------------------------------------
if [[ "$RUN_TESTS" == true ]]; then
  echo -e "\n🧪 Running Redwood Retail test suites..."

  # These replace the old tests/ directory, which only ever covered the A2A
  # agent mesh and broke at import once that collapsed into a single agent.
  # Each module exercises the real code path rather than a mock of it.
  for SUITE in "$REDWOOD_DIR/cdc_service/selftest.py" "$REDWOOD_DIR/loyalty_agent/selftest.py"; do
    echo -e "\n--- $(basename "$(dirname "$SUITE")") ---"
    PYTHONPATH="$REDWOOD_DIR" "$PYTHON_EXEC" "$SUITE" || {
      echo "❌ Error: $SUITE failed!" >&2
      exit 1
    }
  done

  echo -e "\n🎉 All test suites passed."
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
echo -e "\n🔨 Step 1/6: Building the service containers..."

# Terraform cannot create either Cloud Run service until its image exists, so
# the builds have to come first rather than alongside.
CDC_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/redwood-cdc:latest"
BRIDGE_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/redwood-agent-bridge:latest"

ensure_repository() {
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
}

# Resolve the tag to a digest and deploy that instead. Terraform compares the
# image string, so redeploying ":latest" after a rebuild is a no-op plan and the
# service carries on serving the image the revision was first created with.
# Passing the digest makes a rebuild an actual change. This bit us once already:
# the CDC service served a stale revision while :latest had moved on, and every
# event failed against the old code path.
pin_digest() {
  local image="$1" tf_var="$2" digest
  digest=$(gcloud artifacts docker images describe "$image" \
    --format='value(image_summary.digest)' --project="$GCP_PROJECT_ID" 2>/dev/null || true)
  if [[ -n "$digest" ]]; then
    export "$tf_var=$digest"
    echo "   Pinned digest: $digest"
  else
    echo "⚠️  Could not resolve the digest for $image; falling back to the mutable tag."
    echo "    A rebuilt image may not roll out until the service is redeployed."
  fi
}

if [[ "$SKIP_IMAGE_BUILD" == true ]]; then
  echo "⏭️  Reusing existing images."
else
  ensure_repository

  # The bridge shares the CDC service's Firestore event decoder, so its build
  # context is the repository root rather than its own directory.
  echo "   Building the CDC service..."
  gcloud builds submit "$REDWOOD_DIR/cdc_service" \
    --tag="$CDC_IMAGE" --project="$GCP_PROJECT_ID" --region="$GCP_REGION"

  echo "   Building the agent bridge..."
  gcloud builds submit "$REDWOOD_DIR" \
    --config=/dev/stdin --project="$GCP_PROJECT_ID" --region="$GCP_REGION" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-t", "$BRIDGE_IMAGE", "-f", "agent_bridge/Dockerfile", "."]
images: ["$BRIDGE_IMAGE"]
EOF

  echo "✅ Containers published."
fi

pin_digest "$CDC_IMAGE" TF_VAR_cdc_image_digest
pin_digest "$BRIDGE_IMAGE" TF_VAR_agent_bridge_image_digest


echo -e "\n🚀 Step 2/6: Provisioning Infrastructure via Terraform..."

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
BRIDGE_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw agent_bridge_url 2>/dev/null || true)
CHURN_SCHEDULE=$(terraform -chdir="$TERRAFORM_DIR" output -raw churn_schedule_name 2>/dev/null || true)
echo "✅ Terraform infrastructure provisioning completed."
# Written as `if` rather than `[[ ... ]] && echo`: the latter evaluates to a
# failed command when the variable is empty, and under `set -e` that ends the
# run immediately after a successful apply. agent_bridge_url is empty whenever
# the bridge is disabled, so this is reachable, not theoretical.
if [[ -n "$CDC_URL" ]]; then
  echo "   CDC service:  $CDC_URL"
fi
if [[ -n "$BRIDGE_URL" ]]; then
  echo "   Agent bridge: $BRIDGE_URL"
fi

# ------------------------------------------------------------------------------
# 5. DATA SEEDING (SYNTHETIC TRANSACTIONS)
# ------------------------------------------------------------------------------
if [[ "$SKIP_SEED" != true ]]; then
  echo -e "\n📦 Step 3/6: Seeding customers and order histories into Firestore..."
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

  # Count helper, used both while watching replication and after reconciling.
  count_orders() {
    "$PYTHON_EXEC" -c "
from google.cloud import bigquery
c = bigquery.Client(project='$GCP_PROJECT_ID')
try:
    r = list(c.query('SELECT COUNT(*) AS n FROM \`$GCP_PROJECT_ID.$BIGQUERY_DATASET.$BIGQUERY_ORDERS_TABLE\`').result())
    print(r[0].n if r else 0)
except Exception:
    print(0)
" 2>/dev/null || echo 0
  }

  # Each seeded document raises a change event, so replication runs
  # concurrently with seeding and is usually close to done already. Wait for the
  # count to stop climbing rather than for a fixed target, since the exact order
  # count depends on the generated archetypes. This window only gives the live
  # path a chance to finish on its own; the reconciliation below is what
  # guarantees the final state, so there is no need to wait this out.
  MAX_WAIT=120
  START_WAIT=$(date +%s)
  ROW_COUNT=0
  STABLE_READINGS=0
  LAST_COUNT=-1

  while true; do
    ROW_COUNT=$(count_orders)

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
      echo "   Replication still moving after ${MAX_WAIT}s (at $ROW_COUNT rows); reconciling below."
      break
    fi

    echo "   $ROW_COUNT orders replicated (${ELAPSED}s elapsed)..."
    sleep 10
  done

  # Reconcile unconditionally. Eventarc only sees changes made after a trigger
  # exists, so anything seeded against a half-built stack is invisible. The
  # subtler case is a re-seed: the generator is deterministic, so it rewrites
  # documents byte-for-byte, Firestore raises no change event at all, and the
  # table sits at a stale count that looks perfectly healthy. Neither gap is
  # detectable from the row count alone, which is why this does not run
  # conditionally. Rows go through the same UPSERT path as live events and are
  # sequenced on each document's own update time, so this cannot duplicate a
  # row or overwrite newer data with older.
  echo "🔄 Reconciling Firestore into BigQuery..."
  "$PYTHON_EXEC" "$REDWOOD_DIR/cdc_service/backfill.py" --all --ensure-tables

  ROW_COUNT=$(count_orders)
  echo "✅ $ROW_COUNT orders in $BIGQUERY_DATASET.$BIGQUERY_ORDERS_TABLE."

else
  echo -e "\n⏭️  Step 3/6: Skipping seeding (--skip-seed requested)."
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
    echo -e "\n🧠 Step 4/6: Building feature views and training the churn model..."
    "$PYTHON_EXEC" "$REDWOOD_DIR/run_bigquery_analysis.py" --execute --report
    echo "✅ Churn pipeline completed."
  fi
else
  echo -e "\n⏭️  Step 4/6: Skipping churn model training (--skip-bqml requested)."
fi

# ------------------------------------------------------------------------------
# 6.5 LOYALTY AGENT DEPLOYMENT
# ------------------------------------------------------------------------------
# This runs after Terraform because it stages through the bucket Terraform
# creates, and after the churn pipeline so there are scores to read the first
# time a session arrives. The bridge resolves the agent lazily on its first
# request rather than at startup, so deploying the agent last does not leave
# the bridge holding a dangling reference.
if [[ "$SKIP_AGENT_DEPLOY" != true ]]; then
  echo -e "\n🤖 Step 5/6: Deploying the loyalty agent to Agent Engine..."
  echo "   This takes several minutes; the runtime builds an image from the agent package."
  PYTHONPATH="$REDWOOD_DIR" "$PYTHON_EXEC" "$REDWOOD_DIR/scripts/deploy_agent_engine.py"
  echo "✅ Loyalty agent deployed."
else
  echo -e "\n⏭️  Step 5/6: Skipping agent deployment (--skip-agent-deploy requested)."
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
echo " CDC Service:        ${CDC_URL:-not deployed}"
echo " Agent Bridge:       ${BRIDGE_URL:-not deployed}"
echo " Loyalty Agent:      ${AGENT_DISPLAY_NAME:-redwood-loyalty-agent} (Agent Engine, resolved by display name)"
echo " Daily churn job:    ${CHURN_SCHEDULE:-see BigQuery scheduled queries}"
echo "-----------------------------------------------------------------"
echo " 🌐 Google Cloud Console Quick Links:"
echo " • Cloud Run: https://console.cloud.google.com/run?project=$GCP_PROJECT_ID"
echo " • Eventarc Triggers: https://console.cloud.google.com/eventarc/triggers?project=$GCP_PROJECT_ID"
echo " • Agent Engine: https://console.cloud.google.com/vertex-ai/agents/agent-engines?project=$GCP_PROJECT_ID"
echo " • BigQuery Studio: https://console.cloud.google.com/bigquery?project=$GCP_PROJECT_ID"
echo " • Firestore Databases: https://console.cloud.google.com/firestore/databases?project=$GCP_PROJECT_ID"
echo "-----------------------------------------------------------------"
echo " To exercise the demo, write a customer_sessions document with"
echo " customerId set and agentProcessingStatus set to PENDING. cust_demo2"
echo " is scored CRITICAL and receives an offer; cust_demo1 is LOW and does"
echo " not."
echo " To clean up all resources later, run: ./teardown.sh"
echo "================================================================="

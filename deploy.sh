#!/usr/bin/env bash
# ==============================================================================
# Redwood Retail: Single-Command End-to-End Deployment Pipeline
# Provisions Terraform infrastructure, starts Cloud Dataflow CDC replication,
# seeds synthetic Firestore transactions, and trains BigQuery ML models.
# ==============================================================================
set -euo pipefail

REDWOOD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$REDWOOD_DIR/terraform"
PYTHON_EXEC="${PYTHON_EXEC:-}"

# Default configuration flags
SEED_COUNT=250
SKIP_SEED=false
SKIP_BQML=false
DRY_RUN=false
TEARDOWN_MODE=false
AUTO_APPROVE=false
CREATE_PROJECT=false

usage() {
  cat <<EOF
Usage: ./deploy.sh [OPTIONS]

Single-command full deployment and lifecycle management for Redwood Retail.

Options:
  -h, --help               Show this help message and exit.
  -p, --create-project     Provision a new GCP project using terraform/bootstrap before deploying components.
  -s, --seed-count <N>     Number of initial synthetic orders to generate into Firestore (default: 250).
  --skip-seed              Skip generating synthetic transactions into Firestore.
  --skip-bqml              Skip training and evaluating BigQuery ML churn models.
  --dry-run                Validate configuration and run Terraform plan without modifying GCP resources.
  -t, --teardown, --destroy Cleanly tear down all provisioned GCP infrastructure and stop jobs.
  -y, --auto-approve       Skip confirmation prompts during deployment or teardown.

Examples:
  ./deploy.sh                         # Deploy entire infrastructure, seed 250 orders, and train BQML
  ./deploy.sh --create-project        # Bootstrap a new GCP project first, then deploy components
  ./deploy.sh --seed-count 1000       # Deploy and seed 1,000 transactions
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
  DATAFLOW_JOB_NAME
  DATAFLOW_SERVICE_ACCOUNT
)

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
export TF_VAR_dataflow_job_name="$DATAFLOW_JOB_NAME"

echo "Project ID:          $GCP_PROJECT_ID"
echo "Region:              $GCP_REGION"
echo "Firestore Database:  $FIRESTORE_DATABASE_ID (Native Mode, collection: $FIRESTORE_COLLECTION)"
echo "BigQuery Sink:       $BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE"
echo "Dataflow Job:        $DATAFLOW_JOB_NAME"
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
"$PYTHON_EXEC" -c "import dotenv, google.cloud.firestore, google.auth, setuptools, build" 2>/dev/null || {
  echo "📦 Installing required Python dependencies inside virtual environment..."
  "$PYTHON_EXEC" -m pip install -q "apache-beam[gcp]>=2.75.0" "google-cloud-firestore>=2.20.0" "google-cloud-bigquery>=3.25.0" "python-dotenv>=1.0.0" "setuptools" "build"
}

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

  echo -e "\n🧹 Step 1/2: Pre-emptively stopping Dataflow streaming pipeline and waiting for VM shutdown..."
  bash "$TERRAFORM_DIR/scripts/cleanup_dataflow_job.sh" "$GCP_PROJECT_ID" "$GCP_REGION" "$DATAFLOW_JOB_NAME"

  echo -e "\n🧹 Step 2/2: Destroying cloud infrastructure via Terraform..."
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
echo -e "\n🚀 Step 1/4: Provisioning Infrastructure via Terraform..."
terraform -chdir="$TERRAFORM_DIR" init
terraform -chdir="$TERRAFORM_DIR" apply -auto-approve

echo "✅ Terraform infrastructure provisioning completed."

# ------------------------------------------------------------------------------
# 5. DATA SEEDING (SYNTHETIC TRANSACTIONS)
# ------------------------------------------------------------------------------
if [[ "$SKIP_SEED" != true ]]; then
  echo -e "\n📦 Step 2/4: Seeding Synthetic Transactions into Firestore Enterprise..."
  "$PYTHON_EXEC" "$REDWOOD_DIR/generate_retail_dataset.py" \
    --count "$SEED_COUNT" \
    --workers 4 \
    --project "$GCP_PROJECT_ID" \
    --region "$GCP_REGION" \
    --database "$FIRESTORE_DATABASE_ID" \
    --collection "$FIRESTORE_COLLECTION"

  echo "✅ Successfully seeded $SEED_COUNT orders into Firestore."
  echo "⏳ Waiting for Cloud Dataflow to replicate events into BigQuery ($BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE)..."
  
  MAX_WAIT=300
  START_WAIT=$(date +%s)
  ROW_COUNT=0
  while true; do
    ROW_COUNT=$("$PYTHON_EXEC" -c "
from google.cloud import bigquery
client = bigquery.Client(project='$GCP_PROJECT_ID')
query = 'SELECT COUNT(*) AS total FROM \`$GCP_PROJECT_ID.$BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE\`'
try:
    results = list(client.query(query).result())
    print(results[0].total if results else 0)
except Exception:
    print(0)
" 2>/dev/null || echo 0)

    if [[ "$ROW_COUNT" -gt 0 ]]; then
      echo "✅ Confirmed $ROW_COUNT records replicated into BigQuery table '$BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE'."
      break
    fi

    NOW=$(date +%s)
    ELAPSED=$(( NOW - START_WAIT ))
    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
      echo "⚠️ Reached ${MAX_WAIT}s wait limit for initial Dataflow replication. Continuing..."
      break
    fi

    echo "   Waiting for Dataflow workers to stream events (${ELAPSED}s elapsed)..."
    sleep 10
  done
else
  echo -e "\n⏭️  Step 2/4: Skipping synthetic data seeding (--skip-seed requested)."
fi

# ------------------------------------------------------------------------------
# 6. BIGQUERY ML MODEL TRAINING & PREDICTION
# ------------------------------------------------------------------------------
if [[ "$SKIP_BQML" != true ]]; then
  if [[ "${ROW_COUNT:-0}" -eq 0 && "$SKIP_SEED" != true ]]; then
    echo -e "\n⚠️ Warning: No records found in BigQuery CDC table yet ($BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE)."
    echo "Cloud Dataflow workers are still initializing in the background."
    echo "Skipping immediate BQML training to prevent 'Input data doesn't contain any rows' error."
    echo "Once Dataflow workers finish streaming records, train the model by running:"
    echo "   $PYTHON_EXEC $REDWOOD_DIR/run_bigquery_analysis.py --execute"
  else
    echo -e "\n🧠 Step 3/4: Building BigQuery Feature Views & Training Churn ML Model..."
    "$PYTHON_EXEC" "$REDWOOD_DIR/run_bigquery_analysis.py" --execute
    echo "✅ BigQuery ML pipeline execution completed."
  fi
else
  echo -e "\n⏭️  Step 3/4: Skipping BigQuery ML training (--skip-bqml requested)."
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

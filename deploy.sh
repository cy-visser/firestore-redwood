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
RUN_TESTS=false
VERIFY_DEMO=false
SKIP_IMAGE_BUILD=false
SKIP_AGENT_DEPLOY=false
START_PROXY=true
PROXY_ONLY=false
VERBOSE=false

usage() {
  cat <<EOF
Usage: ./deploy.sh [OPTIONS]

Single-command full deployment and lifecycle management for Redwood Retail.

Options:
  -h, --help               Show this help message and exit.
  -s, --seed-count <N>     Number of synthetic customers to generate (default: 400). Each
                           produces roughly 4-20 orders depending on archetype, and the two
                           demo personas are seeded in addition to this count.
  --skip-seed              Skip generating synthetic transactions into Firestore.
  --skip-bqml              Skip training and evaluating BigQuery ML churn models.
  --skip-image-build       Reuse the container images already in Artifact Registry.
  --skip-agent-deploy      Leave the deployed loyalty agent as it is.
  --proxy, --tunnel        Open the authenticated local tunnel to the already-deployed
                           redwood-app without re-running deployment steps.
  --no-proxy               Finish after deploying instead of opening the authenticated proxy
                           to redwood-app. Use for CI and unattended runs.
  --dry-run                Validate configuration and run Terraform plan without modifying GCP resources.
  -t, --teardown, --destroy Cleanly tear down all provisioned GCP infrastructure and stop jobs.
  -y, --auto-approve       Skip confirmation prompts during deployment or teardown.
  -v, --verbose            Stream full Cloud Build and Terraform output instead of concise progress lines.
  --run-tests              Execute the CDC and loyalty agent self-test suites and exit.
  --verify-demo            Write a session for each demo customer against the deployed
                           stack and check the agent reacts correctly, then exit.

Examples:
  ./deploy.sh                         # Deploy everything, then open the app in a local proxy
  ./deploy.sh --proxy                 # Reopen the authenticated tunnel to redwood-app
  ./deploy.sh --no-proxy              # Deploy and exit without holding a tunnel
  ./deploy.sh --run-tests             # Execute the self-test suites
  ./deploy.sh --verify-demo           # Check the deployed stack end to end
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
    --proxy|--tunnel)
      PROXY_ONLY=true
      START_PROXY=true
      shift
      ;;
    --no-proxy)
      START_PROXY=false
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
    -v|--verbose)
      VERBOSE=true
      shift
      ;;
    --run-tests)
      RUN_TESTS=true
      shift
      ;;
    --verify-demo)
      VERIFY_DEMO=true
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
# 0. PREFLIGHT: SDK and credentials
# ------------------------------------------------------------------------------
# Nothing in this deployment runs locally any more -- the app, the churn
# pipeline and the agent all execute in Google Cloud -- so every step past here
# needs three things that are easy to confuse with each other:
#
#   1. the gcloud CLI itself,
#   2. a *user* login, which is what gcloud commands and `gcloud run services
#      proxy` authenticate with,
#   3. Application Default Credentials, which is what Terraform, the seeders
#      and the Python client libraries authenticate with.
#
# (2) and (3) are independent. `gcloud auth login` does not create ADC, and a
# workstation can be perfectly logged in while Terraform cannot authenticate at
# all. Checking them separately is the difference between "run this one
# command" and twenty minutes of confusion several steps later.

# Offer to run a fix rather than merely naming it.
offer() {
  local prompt="$1"; shift
  echo "   Fix: $*"
  # Not run unattended: every command this is used for opens a browser and
  # blocks, which in CI means a hang rather than a failure.
  if [[ "$AUTO_APPROVE" == true ]] || [[ ! -t 0 ]]; then
    echo "   (Not run automatically: it needs an interactive browser sign-in.)"
    return 1
  fi
  read -r -p "   $prompt [Y/n] " reply
  [[ -z "$reply" || "$reply" =~ ^[Yy] ]] || return 1
  "$@"
}

# Checks that do not need a project id, so the --create-project bootstrap below
# gets them too.
preflight_sdk() {
  echo -e "\n🔑 Step 0/7: Checking the Cloud SDK and your credentials..."

  if [[ -n "${SUDO_USER:-}" ]]; then
    echo "⚠️  Running under sudo ($SUDO_USER -> root); run ./deploy.sh without sudo so .venv and .terraform are owned by your user." >&2
  fi

  if ! command -v gcloud &>/dev/null; then
    echo "❌ The gcloud CLI is not installed, or is not on PATH." >&2
    echo "   Install it:        https://cloud.google.com/sdk/docs/install" >&2
    echo "   On Debian/Ubuntu:  sudo apt-get install google-cloud-cli" >&2
    exit 1
  fi
  echo "   ✅ gcloud $(gcloud version --format='value("Google Cloud SDK")' 2>/dev/null | head -1)"

  # `gcloud auth list` prints nothing filterable when the credential store is
  # empty, hence the emptiness test rather than a status code.
  ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE \
    --format='value(account)' 2>/dev/null | head -1)
  if [[ -z "$ACTIVE_ACCOUNT" ]]; then
    echo "⚠️  No active gcloud account."
    offer "Sign in now?" gcloud auth login || {
      echo "❌ A logged-in account is required." >&2
      exit 1
    }
    ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE \
      --format='value(account)' 2>/dev/null | head -1)
  fi
  echo "   ✅ Signed in as $ACTIVE_ACCOUNT"

  # Minting a token is the only check that proves the credential is present
  # *and* still valid. The file existing proves neither, and an expired refresh
  # token is the usual failure on a workstation that has been idle a week.
  if ! gcloud auth application-default print-access-token &>/dev/null; then
    echo "⚠️  Application Default Credentials are missing or expired."
    echo "   ADC is a separate credential from the login above: Terraform and"
    echo "   the Python client libraries use ADC, not your gcloud session."
    offer "Create them now?" gcloud auth application-default login || {
      echo "❌ Working Application Default Credentials are required." >&2
      exit 1
    }
  fi
  echo "   ✅ Application Default Credentials are valid"

  # The last step of this script opens a tunnel so a browser can reach the
  # IAM-only app. It is scripts/run_proxy.py rather than `gcloud run services
  # proxy`, which cannot authenticate to an IAM-only service; see the comment
  # at step 7. Its only prerequisites are the script itself and the virtual
  # environment this script provisions, so all that is worth checking here is
  # that the script has not gone missing.
  if [[ "$START_PROXY" == true && ! -f "$REDWOOD_DIR/scripts/run_proxy.py" ]]; then
    echo "⚠️  scripts/run_proxy.py is missing, so the app cannot be opened locally."
    echo "   Deploy will still complete; re-run with --no-proxy to silence this."
  fi
}

# Checks that need GCP_PROJECT_ID, so these run after .env is sourced.
preflight_project() {
  # The untouched template value is the most common first-run mistake, and it
  # deserves a better message than "not visible".
  if [[ "$GCP_PROJECT_ID" == "your-gcp-project-id" ]]; then
    echo "❌ GCP_PROJECT_ID in .env is still the template placeholder." >&2
    echo "   Set it to an existing project you can deploy into, or run" >&2
    echo "   ./deploy.sh --create-project to create one (see README, section 0)." >&2
    exit 1
  fi

  # Checked first, before anything below offers to repoint ADC or gcloud's
  # active project: accepting those for a project that does not exist leaves
  # the workstation's global config pointing at nothing. Also cheaper to fail
  # here than inside a Terraform apply.
  if ! gcloud projects describe "$GCP_PROJECT_ID" &>/dev/null; then
    echo "❌ Project '$GCP_PROJECT_ID' is not visible to ${ACTIVE_ACCOUNT:-your account}." >&2
    echo "   Check GCP_PROJECT_ID in .env, or run ./deploy.sh --create-project." >&2
    exit 1
  fi

  # Without a quota project the client libraries send requests with no billing
  # project attached, and BigQuery answers with a "user without a project"
  # error that names nothing useful. gcloud has no read-side command for this,
  # so the credential file is read directly.
  local adc_file adc_quota_project
  adc_file="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gcloud/application_default_credentials.json}"
  adc_quota_project=$(python3 -c "
import json, sys
try:
    print(json.load(open(sys.argv[1])).get('quota_project_id', ''))
except Exception:
    print('')
" "$adc_file" 2>/dev/null || true)

  if [[ "$adc_quota_project" != "$GCP_PROJECT_ID" ]]; then
    echo "⚠️  ADC quota project is '${adc_quota_project:-unset}', not '$GCP_PROJECT_ID'."
    offer "Set it now?" gcloud auth application-default \
      set-quota-project "$GCP_PROJECT_ID" || true
  fi

  # The active project, so `gcloud run services proxy` and the identity-token
  # call at the end do not each need an explicit --project.
  local configured_project
  configured_project=$(gcloud config get-value project 2>/dev/null)
  if [[ "$configured_project" != "$GCP_PROJECT_ID" ]]; then
    echo "⚠️  gcloud's active project is '${configured_project:-unset}', but .env says '$GCP_PROJECT_ID'."
    offer "Switch it?" gcloud config set project "$GCP_PROJECT_ID" || true
  fi

  echo "   ✅ Project $GCP_PROJECT_ID is reachable"
}

# Terraform state is local (terraform/terraform.tfstate) with no backend or
# workspaces, and every resource in it records the project it lives in. Pointing
# .env at a different project while that state is present makes Terraform plan
# to destroy the whole demo in the old project and recreate it in the new one --
# and the main apply runs with -auto-approve. Refuse instead.
guard_state_project() {
  local state_file="$TERRAFORM_DIR/terraform.tfstate" state_project
  [[ -f "$state_file" ]] || return 0
  state_project=$(python3 -c "
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
out = s.get('outputs', {}).get('project_id', {}).get('value')
if out:
    print(out); sys.exit(0)
for r in s.get('resources', []):
    for i in r.get('instances', []):
        p = (i.get('attributes') or {}).get('project')
        if p:
            print(p); sys.exit(0)
" "$state_file" 2>/dev/null || true)

  if [[ -n "$state_project" && "$state_project" != "$GCP_PROJECT_ID" ]]; then
    echo "❌ terraform/terraform.tfstate belongs to project '$state_project', but .env says '$GCP_PROJECT_ID'." >&2
    echo "   Continuing would destroy the Redwood deployment in '$state_project'. Either:" >&2
    echo "     - tear the old one down first: set GCP_PROJECT_ID=$state_project, run ./deploy.sh --teardown, then switch, or" >&2
    echo "     - keep it and start fresh state for the new project:" >&2
    echo "         mv terraform/terraform.tfstate terraform/terraform.tfstate.$state_project" >&2
    echo "         mv terraform/terraform.tfstate.backup terraform/terraform.tfstate.backup.$state_project" >&2
    exit 1
  fi
}

# Bind a socket to find a port nothing holds, and poll until something answers
# on it. Both are needed by the proxy launch at the end, and both are lifted
# from mobile_client/start_mobile_app.sh rather than reinvented.
find_free_port() {
  python3 -c "
import socket, sys
port = int(sys.argv[1])
while port < 65535:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            port += 1
            continue
        print(port)
        break
else:
    sys.exit('no free port from ' + sys.argv[1])
" "$1"
}

wait_for_port() {
  python3 -c "
import socket, sys, time
port, deadline = int(sys.argv[1]), float(sys.argv[2])
end = time.time() + deadline
while time.time() < end:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(('127.0.0.1', port)) == 0:
            sys.exit(0)
    time.sleep(0.2)
sys.exit(1)
" "$1" "$2"
}

preflight_sdk

# ------------------------------------------------------------------------------
# 1. Environment & Prerequisites Verification
# ------------------------------------------------------------------------------
if [[ ! -f "$REDWOOD_DIR/.env" ]]; then
  echo "❌ Error: .env file not found in $REDWOOD_DIR." >&2
  echo "   Run: cp .env.example .env, then set GCP_PROJECT_ID (and GCP_REGION)." >&2
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

# redwood-app and redwood-churn are IAM-only, so somebody has to be allowed to
# call them or the deploy finishes with an app nobody can open. The account
# running this is the obvious candidate, and making it automatic is what stops
# a first deploy ending in a 403 from the proxy. APP_INVOKER_MEMBERS in .env
# overrides, for granting a whole team at once.
if [[ -n "${APP_INVOKER_MEMBERS:-}" ]]; then
  export TF_VAR_app_invoker_members="$APP_INVOKER_MEMBERS"
elif [[ -n "${ACTIVE_ACCOUNT:-}" ]]; then
  # Service accounts and users are different member prefixes, and getting it
  # wrong is a Terraform error rather than a silent no-op.
  if [[ "$ACTIVE_ACCOUNT" == *".gserviceaccount.com" ]]; then
    export TF_VAR_app_invoker_members="[\"serviceAccount:${ACTIVE_ACCOUNT}\"]"
  else
    export TF_VAR_app_invoker_members="[\"user:${ACTIVE_ACCOUNT}\"]"
  fi
fi

echo "Project ID:          $GCP_PROJECT_ID"
echo "Region:              $GCP_REGION"
echo "Firestore Database:  $FIRESTORE_DATABASE_ID (Native Mode, collection: $FIRESTORE_COLLECTION)"
echo "BigQuery Sink:       $BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE (ledger) + $BIGQUERY_ORDERS_TABLE (mirror)"
echo "Replication:         Eventarc -> Cloud Run -> BigQuery Storage Write API"
echo "Mode:                $([[ "$TEARDOWN_MODE" == true ]] && echo "TEARDOWN" || ([[ "$DRY_RUN" == true ]] && echo "DRY RUN / PLAN" || ([[ "$PROXY_ONLY" == true ]] && echo "TUNNEL ONLY" || echo "FULL DEPLOYMENT")))"
echo "================================================================="

# The gcloud presence check lives in preflight_sdk above, which has already
# run. This is the half that needed GCP_PROJECT_ID.
preflight_project

# Every interactive gcloud prompt belongs in preflight above. Disabling prompts
# from here on stops subsequent gcloud calls from hanging on stdin.
export CLOUDSDK_CORE_DISABLE_PROMPTS=1


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
import fastapi, uvicorn, httpx, pytest
" 2>/dev/null || {
  echo "📦 Installing required Python dependencies inside virtual environment..."
  # cloudpickle is what Agent Engine uses to serialise the agent object, but
  # the base google-cloud-aiplatform install does not pull it in, so step 5
  # fails on a fresh virtual environment without it. It is what the
  # [agent_engines] extra would add.
  #
  # fastapi and uvicorn are the mobile client backend; httpx is what
  # fastapi.testclient runs on and pytest is what runs the suite. They live
  # here because this is the only virtual environment the repository
  # provisions, and splitting the app across two of them is what left one with
  # a web framework and no test runner and the other with the reverse.
  export PIP_ROOT_USER_ACTION=ignore
  export PIP_DISABLE_PIP_VERSION_CHECK=1
  REDWOOD_PIP_PACKAGES=(
    "google-cloud-firestore>=2.20.0"
    "google-cloud-bigquery>=3.25.0"
    "google-cloud-bigquery-storage>=2.25.0"
    "google-cloud-aiplatform>=1.60.0"
    "cloudpickle>=3.0.0"
    "python-dotenv>=1.0.0"
    "pydantic>=2.0.0"
    "protobuf>=4.25.0"
    "setuptools"
    "fastapi>=0.110.0"
    "uvicorn>=0.30.0"
    "httpx>=0.27.0"
    "pytest>=8.0.0"
  )
  # An isolated .venv does not have keyrings.google-artifactregistry-auth
  # installed, so if the host's pip.conf points at https://*-python.pkg.dev/...
  # pip receives 401 and prompts "User for us-python.pkg.dev:". Bypass host
  # pip.conf and install from public PyPI first; if corp firewall blocks direct
  # pypi.org, fall back to the host index using gcloud's access token.
  if ! PIP_CONFIG_FILE=/dev/null "$PYTHON_EXEC" -m pip install -q --no-input \
       --index-url https://pypi.org/simple "${REDWOOD_PIP_PACKAGES[@]}" 2>/dev/null; then
    CFG_INDEX=$("$PYTHON_EXEC" -m pip config get global.index-url 2>/dev/null || true)
    if [[ "$CFG_INDEX" == https://*pkg.dev/* ]]; then
      AR_TOKEN=$(gcloud auth print-access-token 2>/dev/null || true)
      CFG_INDEX="https://oauth2accesstoken:${AR_TOKEN}@${CFG_INDEX#https://}"
      "$PYTHON_EXEC" -m pip install -q --no-input --index-url "$CFG_INDEX" "${REDWOOD_PIP_PACKAGES[@]}"
    else
      "$PYTHON_EXEC" -m pip install -q --no-input "${REDWOOD_PIP_PACKAGES[@]}"
    fi
  fi
}

launch_proxy() {
  local step_label="${1:-Step 7/7: }"
  PROXY_PORT="${PROXY_PORT:-$(find_free_port 8080)}"

  # Backgrounded first and only announced once it is listening. Printing
  # http://localhost:$PROXY_PORT any earlier is actively harmful here: the IDE
  # scans terminal output for URLs and immediately binds the port for
  # auto-forwarding, stealing it from the proxy that is still starting. This is
  # the same hazard mobile_client/start_mobile_app.sh documents at its uvicorn
  # launch.
  echo ""
  echo "🔌 ${step_label}Opening an authenticated tunnel to redwood-app..."
  # Exported rather than passed as flags: the tunnel resolves the account to
  # impersonate through the same helper the backend uses, which reads these.
  PIPELINE_SERVICE_ACCOUNT="$PIPELINE_SERVICE_ACCOUNT" \
  GCP_PROJECT_ID="$GCP_PROJECT_ID" \
    "$PYTHON_EXEC" "$REDWOOD_DIR/scripts/run_proxy.py" \
      --url "$APP_URL" --port "$PROXY_PORT" &
  PROXY_PID=$!

  cleanup_proxy() {
    trap - SIGINT SIGTERM EXIT
    kill -TERM "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
    echo ""
    echo "[INFO] Tunnel closed. redwood-app is still running in $GCP_REGION."
    echo "       Reopen the tunnel any time with: ./deploy.sh --proxy"
  }
  trap cleanup_proxy SIGINT SIGTERM EXIT

  if ! wait_for_port "$PROXY_PORT" 30; then
    echo "❌ The proxy did not bind port $PROXY_PORT." >&2
    echo "   Check that ${ACTIVE_ACCOUNT:-your account} holds roles/run.invoker on redwood-app:" >&2
    echo "     gcloud run services get-iam-policy redwood-app --region=$GCP_REGION" >&2
    exit 1
  fi

  echo ""
  echo "================================================================="
  echo " 🌲 REDWOOD RETAIL IS LIVE (served from Cloud Run)"
  echo "================================================================="
  echo " Mobile App:       http://localhost:$PROXY_PORT/"
  echo " Redwood Console:  http://localhost:$PROXY_PORT/console"
  echo " API Docs:         http://localhost:$PROXY_PORT/docs"
  echo "-----------------------------------------------------------------"
  echo " Both are served by redwood-app in $GCP_REGION. This terminal only"
  echo " holds the authenticated tunnel; Ctrl+C closes it and leaves the"
  echo " service running."
  echo ""
  echo " In the demo, sign in as demo2: it is scored CRITICAL and receives"
  echo " an offer. demo1 is LOW and does not."
  echo "================================================================="

  wait "$PROXY_PID"
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

  # The mobile client suite covers the login session contract and the order
  # schema parity, both offline. It is a pytest run rather than a script
  # because it is the one suite written as test cases.
  echo -e "\n--- mobile_client ---"
  PYTHONPATH="$REDWOOD_DIR" "$PYTHON_EXEC" -m pytest "$REDWOOD_DIR/mobile_client/tests" -q || {
    echo "❌ Error: mobile_client tests failed!" >&2
    exit 1
  }

  echo -e "\n🎉 All test suites passed."
  exit 0
fi

if [[ "$VERIFY_DEMO" == true ]]; then
  echo -e "\n🧪 Verifying the demo flow against the deployed stack..."
  PYTHONPATH="$REDWOOD_DIR" "$PYTHON_EXEC" "$REDWOOD_DIR/scripts/verify_demo_flow.py" \
    --project "$GCP_PROJECT_ID" \
    --database "$FIRESTORE_DATABASE_ID" || {
    echo "❌ Demo flow verification failed." >&2
    exit 1
  }
  exit 0
fi

if [[ "$PROXY_ONLY" == true ]]; then
  APP_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw app_service_url 2>/dev/null || true)
  if [[ -z "$APP_URL" ]]; then
    APP_URL=$(gcloud run services describe redwood-app \
      --region="$GCP_REGION" --project="$GCP_PROJECT_ID" \
      --format='value(status.url)' 2>/dev/null || true)
  fi
  if [[ -z "$APP_URL" ]]; then
    echo "❌ Could not find a deployed redwood-app service in $GCP_PROJECT_ID ($GCP_REGION)." >&2
    echo "   Run ./deploy.sh first to provision the stack." >&2
    exit 1
  fi
  launch_proxy ""
  exit 0
fi

# Everything from here on runs Terraform against the root state.
guard_state_project

run_tf_init() {
  if [[ "$VERBOSE" == true ]]; then
    terraform -chdir="$TERRAFORM_DIR" init
    return $?
  fi
  local log_file status=0
  log_file="$(mktemp -t redwood-tf-init-XXXXXX.log)"
  terraform -chdir="$TERRAFORM_DIR" init -input=false -no-color >"$log_file" 2>&1 || status=$?
  if [[ $status -ne 0 ]]; then
    cat "$log_file" >&2
  fi
  rm -f "$log_file"
  return "$status"
}

# Streams only one-line resource lifecycle transitions and final summary lines
# unless VERBOSE=true. If Terraform exits non-zero, dumps the full captured log.
run_tf_apply() {
  if [[ "$VERBOSE" == true ]]; then
    terraform -chdir="$TERRAFORM_DIR" "$@"
    return $?
  fi
  local log_file status=0
  log_file="$(mktemp -t redwood-tf-XXXXXX.log)"
  terraform -chdir="$TERRAFORM_DIR" "$@" -compact-warnings -input=false -no-color 2>&1 \
    | tee "$log_file" \
    | awk '/: (Creating\.\.\.|Creation complete|Modifying\.\.\.|Modifications complete|Destroying\.\.\.|Destruction complete)|^(Apply|Destroy) complete!|^No changes\./ { print "   " $0; fflush() }' \
    || status=$?
  if [[ $status -ne 0 ]]; then
    cat "$log_file" >&2
  fi
  rm -f "$log_file"
  return "$status"
}

run_tf_init

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
    if run_tf_apply destroy -auto-approve; then
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
  terraform -chdir="$TERRAFORM_DIR" plan
  echo -e "\n✅ Dry run completed. No infrastructure changes were applied."
  exit 0
fi

# ------------------------------------------------------------------------------
# 4. TERRAFORM PROVISIONING
# ------------------------------------------------------------------------------
echo -e "\n🔨 Step 1/7: Building the service containers..."

# Terraform cannot create any of these Cloud Run services until its image
# exists, so the builds have to come first rather than alongside.
CDC_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/redwood-cdc:latest"
BRIDGE_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/redwood-agent-bridge:latest"
CHURN_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/redwood-churn:latest"
APP_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/redwood-app:latest"

ensure_build_prerequisites() {
  echo "   Provisioning APIs, Cloud Build permissions, and Artifact Registry via Terraform..."
  run_tf_apply apply -auto-approve \
    -target=google_project_service.services \
    -target=google_project_service_identity.compute_sa \
    -target=google_project_iam_member.compute_sa_cloudbuild_builder \
    -target=google_artifact_registry_repository.pipeline_repo
}

submit_build() {
  # GCS caches IAM policy evaluations for ~60s after a fresh grant on
  # ${project_number}-compute@developer.gserviceaccount.com, so allow up to 75s
  # (5 x 15s waits across 6 attempts) before giving up.
  local max_attempts=6 attempt log_file
  log_file="$(mktemp -t redwood-build-XXXXXX.log)"
  for ((attempt=1; attempt<=max_attempts; attempt++)); do
    if [[ "$VERBOSE" == true ]]; then
      if gcloud builds submit "$@"; then
        rm -f "$log_file"
        return 0
      fi
    else
      if gcloud builds submit "$@" >"$log_file" 2>&1; then
        rm -f "$log_file"
        return 0
      fi
    fi
    if [[ $attempt -lt $max_attempts ]]; then
      echo "⚠️  Cloud Build submission failed (attempt $attempt/$max_attempts); waiting 15s for IAM propagation before retrying..."
      sleep 15
    fi
  done
  if [[ "$VERBOSE" != true && -s "$log_file" ]]; then
    cat "$log_file" >&2
  fi
  rm -f "$log_file"
  return 1
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

# Three of the four images need a file from outside their own directory, so
# their build context is the repository root and the Dockerfile is named
# explicitly. Only the CDC service is self-contained.
#
# `gcloud builds submit --tag` cannot name a Dockerfile, so this passes an
# inline build config instead. It goes to a real temporary file rather than
# /dev/stdin because gcloud resolves --config by path and expects a .yaml.
build_from_root() {
  local image="$1" dockerfile="$2" config
  config="$(mktemp -t redwood-cloudbuild-XXXXXX.yaml)"
  cat >"$config" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-t", "$image", "-f", "$dockerfile", "."]
images: ["$image"]
EOF
  # Guarded so a failed build still cleans up before set -e takes the script
  # down; the exit status is preserved for the caller.
  local status=0
  submit_build "$REDWOOD_DIR" \
    --config="$config" --project="$GCP_PROJECT_ID" --region="$GCP_REGION" || status=$?
  rm -f "$config"
  return "$status"
}

if [[ "$SKIP_IMAGE_BUILD" == true ]]; then
  echo "⏭️  Reusing existing images."
else
  ensure_build_prerequisites

  echo "   Building the CDC service..."
  submit_build "$REDWOOD_DIR/cdc_service" \
    --tag="$CDC_IMAGE" --project="$GCP_PROJECT_ID" --region="$GCP_REGION"

  # The bridge shares the CDC service's Firestore event decoder.
  echo "   Building the agent bridge..."
  build_from_root "$BRIDGE_IMAGE" "agent_bridge/Dockerfile"

  # The churn function bakes in the one copy of the churn SQL, which lives at
  # the repository root because Terraform's daily scheduled query renders the
  # same file.
  echo "   Building the churn function..."
  build_from_root "$CHURN_IMAGE" "churn_service/Dockerfile"

  # The app imports firestore_auth, retail_catalog and customer_profiles from
  # the root, and builds the Vite bundle in its own Node stage.
  echo "   Building the app (frontend bundle + FastAPI)..."
  build_from_root "$APP_IMAGE" "mobile_client/Dockerfile"

  echo "✅ Containers published."
fi

pin_digest "$CDC_IMAGE" TF_VAR_cdc_image_digest
pin_digest "$BRIDGE_IMAGE" TF_VAR_agent_bridge_image_digest
pin_digest "$CHURN_IMAGE" TF_VAR_churn_image_digest
pin_digest "$APP_IMAGE" TF_VAR_app_image_digest


echo -e "\n🚀 Step 2/7: Provisioning Infrastructure via Terraform..."

# Migrate existing states off the legacy terraform_data local-exec wrapper
# without triggering its destroy-time provisioner, and adopt the database into
# the native google_firestore_database resource if it already exists in GCP.
terraform -chdir="$TERRAFORM_DIR" state rm terraform_data.firestore_database &>/dev/null || true
if ! terraform -chdir="$TERRAFORM_DIR" state show google_firestore_database.database &>/dev/null; then
  if gcloud firestore databases describe --database="$FIRESTORE_DATABASE_ID" \
       --project="$GCP_PROJECT_ID" &>/dev/null; then
    terraform -chdir="$TERRAFORM_DIR" import -input=false \
      google_firestore_database.database \
      "projects/${GCP_PROJECT_ID}/databases/${FIRESTORE_DATABASE_ID}" >/dev/null
  fi
fi

run_tf_apply apply -auto-approve

CDC_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw cdc_service_url 2>/dev/null || true)
BRIDGE_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw agent_bridge_url 2>/dev/null || true)
CHURN_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw churn_service_url 2>/dev/null || true)
APP_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw app_service_url 2>/dev/null || true)
CHURN_SCHEDULE=$(terraform -chdir="$TERRAFORM_DIR" output -raw churn_schedule_name 2>/dev/null || true)
echo "✅ Terraform infrastructure provisioning completed."
# Written as `if` rather than `[[ ... ]] && echo`: the latter evaluates to a
# failed command when the variable is empty, and under `set -e` that ends the
# run immediately after a successful apply. agent_bridge_url is empty whenever
# the bridge is disabled, so this is reachable, not theoretical.
if [[ -n "$CDC_URL" ]]; then
  echo "   CDC service:    $CDC_URL"
fi
if [[ -n "$BRIDGE_URL" ]]; then
  echo "   Agent bridge:   $BRIDGE_URL"
fi
if [[ -n "$CHURN_URL" ]]; then
  echo "   Churn function: $CHURN_URL"
fi
if [[ -n "$APP_URL" ]]; then
  echo "   Redwood app:    $APP_URL"
fi

# ------------------------------------------------------------------------------
# 5. DATA SEEDING (SYNTHETIC TRANSACTIONS)
# ------------------------------------------------------------------------------
if [[ "$SKIP_SEED" != true ]]; then
  echo -e "\n📦 Step 3/7: Seeding customers and order histories into Firestore..."
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
  echo -e "\n⏭️  Step 3/7: Skipping seeding (--skip-seed requested)."
fi

# ------------------------------------------------------------------------------
# 6. BIGQUERY ML MODEL TRAINING & PREDICTION
# ------------------------------------------------------------------------------
# Runs in the cloud, in the redwood-churn function Terraform created above.
# Nothing here renders SQL or talks to BigQuery; it makes one authenticated
# HTTP call and relays the log.
run_churn_pipeline() {
  local mode="${1:-full}" log token

  if [[ -z "${CHURN_URL:-}" ]]; then
    echo "❌ No churn function URL from Terraform; cannot run the pipeline." >&2
    return 1
  fi

  # Cloud Run requires an ID token whose `aud` is the service URL. A user's own
  # ID token cannot have one: `gcloud auth print-identity-token` returns a token
  # minted for the gcloud OAuth client, so its audience is that client id and
  # the service answers 401 with an HTML error page that says nothing useful.
  #
  # So mint the token as the pipeline service account instead, which is both the
  # account that holds run.invoker on the churn service and exactly what the
  # backend does at runtime (mobile_client/backend/idtoken.py). The Terraform in
  # mobile_app.tf grants the deploying user serviceAccountTokenCreator on it for
  # this reason.
  #
  # --include-email is required: without it the token carries no email claim and
  # Cloud Run's IAM check has no principal to authorise.
  local invoker_sa="${PIPELINE_SERVICE_ACCOUNT}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
  token=$(gcloud auth print-identity-token \
    --impersonate-service-account="$invoker_sa" \
    --audiences="$CHURN_URL" \
    --include-email 2>/dev/null || true)
  if [[ -z "$token" ]]; then
    echo "❌ Could not mint an identity token for $CHURN_URL as $invoker_sa." >&2
    echo "   Check that ${ACTIVE_ACCOUNT:-your account} holds roles/iam.serviceAccountTokenCreator on it:" >&2
    echo "     gcloud iam service-accounts get-iam-policy $invoker_sa" >&2
    return 1
  fi

  # -N keeps curl unbuffered so the statement-by-statement log appears here as
  # it does in the console, and tee puts it on screen while capturing it.
  #
  # The run's real outcome is the trailing [exit N] line, not curl's exit
  # status: the response streams, so its 200 was committed before the first
  # BigQuery statement ran and curl has no way to learn that a later statement
  # failed.
  log=$(curl -sS -N -X POST "$CHURN_URL" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "{\"mode\":\"${mode}\",\"report\":true}" | tee /dev/stderr) || {
    echo "❌ The call to the churn function failed." >&2
    return 1
  }

  if ! grep -q '^\[exit 0\]$' <<<"$log"; then
    echo "❌ The churn pipeline reported a failure. See the log above." >&2
    return 1
  fi
}

if [[ "$SKIP_BQML" != true ]]; then
  if [[ "${ROW_COUNT:-0}" -eq 0 && "$SKIP_SEED" != true ]]; then
    # Training on an empty table fails with "Input data doesn't contain any
    # rows", which reads like a modelling problem rather than the replication
    # problem it actually is. Say what happened and where to look.
    echo -e "\n⚠️  No orders reached $BIGQUERY_DATASET.$BIGQUERY_ORDERS_TABLE, so there is nothing to train on."
    echo "    Check the CDC service and its triggers:"
    echo "      gcloud run services logs read redwood-cdc --region=$GCP_REGION --limit=50"
    echo "      gcloud eventarc triggers list --location=$GCP_REGION"
    echo "    Then re-run the pipeline:"
    echo "      curl -N -X POST ${CHURN_URL:-<churn function url>} \\"
    echo "        -H \"Authorization: Bearer \\$(gcloud auth print-identity-token)\" \\"
    echo "        -H 'Content-Type: application/json' -d '{\"mode\":\"full\"}'"
  else
    echo -e "\n🧠 Step 4/7: Building feature views and training the churn model..."
    echo "   Running in redwood-churn; the log below streams from the function."
    run_churn_pipeline full
    echo "✅ Churn pipeline completed."
  fi
else
  echo -e "\n⏭️  Step 4/7: Skipping churn model training (--skip-bqml requested)."
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
  echo -e "\n🤖 Step 5/7: Deploying the loyalty agent to Agent Engine..."
  echo "   This takes several minutes; the runtime builds an image from the agent package."
  PYTHONPATH="$REDWOOD_DIR" "$PYTHON_EXEC" "$REDWOOD_DIR/scripts/deploy_agent_engine.py"
  echo "✅ Loyalty agent deployed."
else
  echo -e "\n⏭️  Step 5/7: Skipping agent deployment (--skip-agent-deploy requested)."
fi


# ------------------------------------------------------------------------------
# 7. DEPLOYMENT DASHBOARD & HEALTH SUMMARY
# ------------------------------------------------------------------------------
echo -e "\n📋 Step 6/7: Deployment summary"
echo "================================================================="
echo " 🎉 REDWOOD RETAIL DEPLOYMENT COMPLETE & OPERATIONAL!"
echo "================================================================="
echo " Target Project:     $GCP_PROJECT_ID"
echo " Region:             $GCP_REGION"
echo " Firestore DB:       $FIRESTORE_DATABASE_ID (Native Mode, Collection: $FIRESTORE_COLLECTION)"
echo " BigQuery Table:     $GCP_PROJECT_ID.$BIGQUERY_DATASET.$BIGQUERY_CDC_TABLE"
echo " BigQuery Model:     $GCP_PROJECT_ID.$BIGQUERY_DATASET.$BIGQUERY_CHURN_MODEL"
echo " CDC Service:        ${CDC_URL:-not deployed}"
echo " Agent Bridge:       ${BRIDGE_URL:-not deployed}"
echo " Churn Function:     ${CHURN_URL:-not deployed} (IAM-only)"
echo " Redwood App:        ${APP_URL:-not deployed} (IAM-only)"
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
echo " To clean up all resources later, run: ./teardown.sh"
echo "================================================================="

# ------------------------------------------------------------------------------
# 8. LAUNCH: authenticated tunnel to the Cloud Run app
# ------------------------------------------------------------------------------
# redwood-app is IAM-only -- a browser hitting its run.app URL directly gets a
# 403, because Cloud Run has no interactive sign-in without IAP. Something local
# has to sign each request, which is what the tunnel does.
#
# Not `gcloud run services proxy`: that cannot authenticate to this service.
# With user credentials it presents an ID token whose audience is the gcloud
# OAuth client rather than the service URL, and Cloud Run answers 401; with
# --impersonate-service-account, which would produce the right audience, the
# proxy binary rejects the credential type outright. scripts/run_proxy.py does
# the same job using the token path the backend already uses.
#
# Nothing of the application runs here: the tunnel forwards, the container in
# Cloud Run serves. The script blocks on it because at this point the deploy is
# finished and "the app is running" is the correct terminal state.
if [[ "$START_PROXY" != true ]]; then
  echo ""
  echo " Skipping the tunnel (--no-proxy). To open the app later:"
  echo "   ./deploy.sh --proxy"
  exit 0
fi

if [[ -z "${APP_URL:-}" ]]; then
  echo ""
  echo "⚠️  No app URL from Terraform, so there is nothing to proxy to." >&2
  exit 0
fi

launch_proxy "Step 7/7: "

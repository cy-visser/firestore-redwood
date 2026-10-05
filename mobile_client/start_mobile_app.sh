#!/usr/bin/env bash
# ==============================================================================
# Redwood Retail: Mobile Client & Redwood Console Launcher
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REDWOOD_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# The repository's own virtual environment, which is the one deploy.sh
# provisions and the one that holds fastapi. This used to point at an absolute
# path outside the repository that had no fastapi in it at all, so the backend
# could not start.
VENV_DIR="${VENV_DIR:-${REDWOOD_DIR}/.venv}"
VENV_PYTHON="${VENV_DIR}/bin/python3"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[ERROR] Python virtual environment not found at: ${VENV_PYTHON}"
  echo "        Run ./deploy.sh once to provision it, or set VENV_DIR."
  exit 1
fi

if ! "${VENV_PYTHON}" -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "[ERROR] ${VENV_PYTHON} has no fastapi/uvicorn."
  echo "        Run ./deploy.sh --run-tests once; it installs them."
  exit 1
fi

# The console's Recalculate Churn and Reset Demo buttons call the deployed
# churn function and delete documents respectively. They default on because
# Reset Demo is the one thing the presenter has to press before a run, and a
# button that 403s out of the box is discovered on stage. The variable is still
# honoured, so ENABLE_DEMO_CONTROLS=0 turns them off for anything less private
# than a localhost demo.
ENABLE_DEMO_CONTROLS="${ENABLE_DEMO_CONTROLS:-1}"
export ENABLE_DEMO_CONTROLS

# Where the churn pipeline runs. It is a Cloud Run service now, not a script on
# this machine, so even a local backend has to be told where to find it --
# otherwise the churn controls disable themselves and say why.
#
# Read from Terraform state rather than hardcoded: the URL contains a
# project-specific hash, so there is nothing to hardcode. A missing or
# un-applied state is not an error here; the rest of the app works fine
# without churn.
if [[ -z "${CHURN_FUNCTION_URL:-}" ]] && command -v terraform >/dev/null 2>&1; then
  CHURN_FUNCTION_URL="$(terraform -chdir="${REDWOOD_DIR}/terraform" \
    output -raw churn_service_url 2>/dev/null || true)"
fi
export CHURN_FUNCTION_URL="${CHURN_FUNCTION_URL:-}"

# Calling the function needs an ID token for its own URL, which user ADC cannot
# mint directly; the backend impersonates the pipeline service account to get
# one. Tell it which account, using the same name deploy.sh uses.
export PIPELINE_SERVICE_ACCOUNT="${PIPELINE_SERVICE_ACCOUNT:-${DATAFLOW_SERVICE_ACCOUNT:-dataflow-redwood-sa}}"

echo "=============================================================================="
echo " Starting Redwood Retail Mobile App Client & Firestore Bridge"
echo "=============================================================================="
echo " Project:       ${GCP_PROJECT_ID:-elevate-cyvisser}"
echo " Database:      redwood (Firestore Enterprise Native)"
echo " Collection:    retail"
echo " Demo Users:    demo1 (low churn risk) | demo2 (high churn risk)"
echo " Discounts:     none by tier; only the loyalty agent's offer discounts an order"
echo " Demo controls: ${ENABLE_DEMO_CONTROLS} (ENABLE_DEMO_CONTROLS=0 to disable)"
if [[ -n "${CHURN_FUNCTION_URL}" ]]; then
  echo " Churn function: ${CHURN_FUNCTION_URL}"
else
  echo " Churn function: not found in Terraform state - churn controls disabled"
  echo "                 (run ./deploy.sh, or export CHURN_FUNCTION_URL)"
fi
echo "=============================================================================="

# Always rebuild. The previous version built only when dist/ was absent, which
# meant the first build was served forever and every later source change was
# invisible in production mode.
echo "[INFO] Building frontend production bundle (mobile + console)..."
(cd "${SCRIPT_DIR}/frontend" && npm run build)

# Find free ports dynamically (Jetski's terminal output auto-port-forwarder
# grabs any port printed as http://localhost:<port> before the process binds it,
# and keeps holding 5173, 8084, 8085, 8086 on 127.0.0.1).
find_free_port() {
  local start_port="$1"
  # A socket can only be bound once, so probe each candidate with a fresh
  # socket. Binding 0.0.0.0 also fails when something already holds
  # 127.0.0.1:<port>, which is exactly the case this needs to detect.
  "${VENV_PYTHON}" -c "
import socket, sys
port = int(sys.argv[1])
while port < 65535:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
        except OSError:
            port += 1
            continue
        print(port)
        break
else:
    sys.exit('no free port from ' + sys.argv[1])
" "${start_port}"
}

BACKEND_PORT="${BACKEND_PORT:-$(find_free_port 8087)}"
FRONTEND_PORT="${FRONTEND_PORT:-$(find_free_port 5174)}"
export VITE_BACKEND_PORT="${BACKEND_PORT}"
export VITE_PORT="${FRONTEND_PORT}"

# IMPORTANT: Do NOT echo http://localhost:<port> before uvicorn binds the socket!
# Jetski IDE scans terminal stdout for URLs and immediately binds 127.0.0.1:<port>
# for auto-port-forwarding, which steals the port while Python is still importing.
echo "[INFO] Starting FastAPI Backend..."
cd "${REDWOOD_DIR}"
"${VENV_DIR}/bin/uvicorn" mobile_client.backend.server:app --host 0.0.0.0 --port "${BACKEND_PORT}" --timeout-graceful-shutdown 2 &
BACKEND_PID=$!

# Wait until uvicorn has actually bound the port before printing any URLs or starting Vite
"${VENV_PYTHON}" -c "
import socket, sys, time
port = int(sys.argv[1])
for _ in range(50):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(('127.0.0.1', port)) == 0:
            sys.exit(0)
    time.sleep(0.1)
sys.exit(1)
" "${BACKEND_PORT}" || {
  echo "[ERROR] FastAPI Backend failed to bind port ${BACKEND_PORT}."
  exit 1
}

echo "[INFO] Starting Vite Frontend..."
cd "${SCRIPT_DIR}/frontend"
npm run dev -- --host 0.0.0.0 --port "${FRONTEND_PORT}" &
FRONTEND_PID=$!

cleanup() {
  trap - SIGINT SIGTERM EXIT
  echo ""
  echo "[INFO] Shutting down Mobile Client services..."
  kill -TERM "${BACKEND_PID}" "${FRONTEND_PID}" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "${BACKEND_PID}" 2>/dev/null && ! kill -0 "${FRONTEND_PID}" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  kill -9 "${BACKEND_PID}" "${FRONTEND_PID}" 2>/dev/null || true
  wait "${BACKEND_PID}" 2>/dev/null || true
  wait "${FRONTEND_PID}" 2>/dev/null || true
  echo "[INFO] All services stopped."
  exit 0
}

trap cleanup SIGINT SIGTERM EXIT

echo ""
echo "=============================================================================="
echo " Redwood Retail Mobile Client is LIVE!"
echo "=============================================================================="
echo " Mobile App (Vite Dev Server):  http://localhost:${FRONTEND_PORT}"
echo " Redwood Console:               http://localhost:${FRONTEND_PORT}/console.html"
echo " Production Unified SPA & API:  http://localhost:${BACKEND_PORT}"
echo " Interactive API Docs:          http://localhost:${BACKEND_PORT}/docs"
echo "=============================================================================="
echo "Press Ctrl+C to terminate."

wait

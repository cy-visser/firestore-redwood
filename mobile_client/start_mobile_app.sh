#!/usr/bin/env bash
# ==============================================================================
# Redwood Retail: Mobile Client & Firestore API Launcher
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REDWOOD_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="/usr/local/google/home/cyvisser/source/firestore/venv/bin/python3"
VENV_UVICORN="/usr/local/google/home/cyvisser/source/firestore/venv/bin/uvicorn"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[ERROR] Python virtual environment not found at: ${VENV_PYTHON}"
  exit 1
fi

echo "=============================================================================="
echo " Starting Redwood Retail Mobile App Client & Firestore Bridge"
echo "=============================================================================="
echo " Project:       elevate-cyvisser"
echo " Database:      redwood (Firestore Enterprise Native)"
echo " Collection:    retail"
echo " Demo Users:    demo1 (VIP 25% OFF) | demo2 (Standard 10% OFF)"
echo "=============================================================================="

# Check if build is present
if [[ ! -d "${SCRIPT_DIR}/frontend/dist" ]]; then
  echo "[INFO] Building frontend production bundle..."
  (cd "${SCRIPT_DIR}/frontend" && npm run build)
fi

echo "[INFO] Starting FastAPI Backend on http://0.0.0.0:8085..."
cd "${REDWOOD_DIR}"
"${VENV_UVICORN}" mobile_client.backend.server:app --host 0.0.0.0 --port 8085 &
BACKEND_PID=$!

echo "[INFO] Starting Vite Frontend on http://0.0.0.0:5173..."
cd "${SCRIPT_DIR}/frontend"
npm run dev &
FRONTEND_PID=$!

cleanup() {
  echo ""
  echo "[INFO] Shutting down Mobile Client services..."
  kill -TERM "${BACKEND_PID}" 2>/dev/null || true
  kill -TERM "${FRONTEND_PID}" 2>/dev/null || true
  wait "${BACKEND_PID}" 2>/dev/null || true
  wait "${FRONTEND_PID}" 2>/dev/null || true
  echo "[INFO] All services stopped."
}

trap cleanup SIGINT SIGTERM EXIT

echo ""
echo "=============================================================================="
echo " Redwood Retail Mobile Client is LIVE!"
echo "=============================================================================="
echo " Mobile App (Vite Dev Server):  http://localhost:5173"
echo " Production Unified SPA & API:  http://localhost:8000"
echo " Interactive API Docs:          http://localhost:8000/docs"
echo "=============================================================================="
echo "Press Ctrl+C to terminate."

wait

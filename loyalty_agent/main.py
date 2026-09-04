"""
Entrypoint CLI runner for Redwood Retail Loyalty Offer Agent.
Can run as a persistent daemon or perform health and status checks.
"""

import sys
import os
import time
import signal
import logging
import threading
from typing import Optional
from http.server import HTTPServer, BaseHTTPRequestHandler

from loyalty_agent.config import config
from loyalty_agent.agent import AutonomousLoyaltyAgent
from loyalty_agent.listener import SessionEventListener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("loyalty_agent")


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Lightweight HTTP handler satisfying Cloud Run container PORT startup and liveness probes."""

    def do_GET(self):
        if self.path in ("/", "/healthz", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "HEALTHY", "service": "loyalty-agent-daemon", "mode": "event-listener"}\n')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress routine health check log spam
        pass


def start_healthcheck_server(port: int = 8080) -> HTTPServer:
    """Starts background HTTP server for Cloud Run health probes."""
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("🩺 Cloud Run health probe listening on 0.0.0.0:%d (/healthz)", port)
    return server


def build_clients():
    """Initializes Google Cloud clients for Firestore, BigQuery, and Vertex AI."""
    from google.cloud import firestore
    from google.cloud import bigquery
    from google import genai

    logger.info(f"Connecting to Firestore database '{config.firestore_database}' in '{config.region}'...")
    fs_client = firestore.Client(project=config.project_id, database=config.firestore_database)

    logger.info(f"Connecting to BigQuery dataset '{config.bigquery_dataset}'...")
    bq_client = bigquery.Client(project=config.project_id, location=config.region)

    logger.info(f"Initializing Gemini Client with model '{config.reasoning_model}'...")
    try:
        genai_client = genai.Client()
    except Exception as e:
        logger.warning(f"Vertex AI Client init warning (will use deterministic fallback): {e}")
        genai_client = None

    return fs_client, bq_client, genai_client


def run_daemon():
    """Runs the loyalty agent as a persistent background daemon."""
    logger.info("🌲 Redwood Retail Autonomous Loyalty Agent Starting...")
    logger.info(f"Target GCP Project: {config.project_id}")
    logger.info(f"Reasoning Model:    {config.reasoning_model}")

    # Start Cloud Run container port health probe
    port = int(os.getenv("PORT", "8080"))
    health_server = start_healthcheck_server(port)

    fs_client, bq_client, genai_client = build_clients()

    agent = AutonomousLoyaltyAgent(
        firestore_client=fs_client,
        bigquery_client=bq_client,
        gemini_model=genai_client,
        model_name=config.reasoning_model
    )

    listener = SessionEventListener(
        firestore_client=fs_client,
        session_processor=agent.process_session
    )

    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received. Exiting gracefully...")
        listener.stop()
        health_server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    listener.start()
    logger.info("⚡ Autonomous Agent daemon is live and listening for customer sessions.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_handler(None, None)


if __name__ == "__main__":
    if "--daemon" in sys.argv or len(sys.argv) == 1:
        run_daemon()

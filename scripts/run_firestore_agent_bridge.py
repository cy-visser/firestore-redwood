#!/usr/bin/env python3
"""
Firestore Event Bridge Daemon.

Maintains a persistent HTTP/2 gRPC Listen stream on Firestore
'/customer_sessions' and runs the loyalty agent against every incoming PENDING
login event. Also runs an embedded lightweight HTTP healthcheck listener on
$PORT (default: 8080) for Cloud Run startup and liveness probes.

The agent runs in this process. It used to be a remote Reasoning Engine
reached by RPC, which was the only reason the six-agent mesh needed to be
deployed as separate services; with one agent the extra hop bought nothing.
"""

import logging
import os
import signal
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import firestore

from loyalty_agent.config import config
from loyalty_agent.listener import SessionEventListener
from loyalty_agent.main import LoyaltyAgentEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("firestore_agent_bridge")


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Responds to Cloud Run startup and liveness probes."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"healthy","service":"firestore_agent_bridge"}\n')

    def log_message(self, format, *args):
        # Silence routine probe access logs
        pass


def start_health_server(port: int = 8080) -> HTTPServer:
    """Spawns an embedded HTTP health server on a background daemon thread."""
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health probe listener active on 0.0.0.0:%d (/healthz, /)", port)
    return server


class FirestoreAgentBridge:
    """Connects Firestore session events to the loyalty agent."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        database_id: Optional[str] = None
    ):
        self.project_id = project_id or config.project_id
        self.region = region or config.region
        self.database_id = database_id or config.firestore_database

        self.fs = firestore.Client(project=self.project_id, database=self.database_id)

        self.engine = LoyaltyAgentEngine(
            project_id=self.project_id,
            region=self.region,
            firestore_database=self.database_id,
        )
        # Share the client the listener watches on, so the agent and the watch
        # stream cannot disagree about which database they are talking to.
        self.engine.fs_client = self.fs
        self.agent = None

        self.listener: Optional[SessionEventListener] = None

    def _claim_session(self, session_id: str) -> bool:
        """Move PENDING -> PROCESSING, and report whether this worker won.

        The transaction is what makes the claim exclusive. A bare update would
        overwrite whatever another worker had already written, so two workers
        seeing the same login would both go on to issue an offer.
        """
        sess_ref = self.fs.collection("customer_sessions").document(session_id)

        @firestore.transactional
        def _claim(transaction) -> bool:
            snapshot = sess_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            if (snapshot.to_dict() or {}).get("agentProcessingStatus") != "PENDING":
                return False
            transaction.update(sess_ref, {"agentProcessingStatus": "PROCESSING"})
            return True

        try:
            return _claim(self.fs.transaction())
        except Exception as exc:
            logger.warning("Could not claim session %s: %s", session_id, exc)
            return False

    def process_pending_session(self, session_id: str):
        """Evaluate one session, leaving it in a terminal state either way."""
        logger.info("[EVENT RECEIVED] Processing PENDING session: %s", session_id)

        if not self._claim_session(session_id):
            logger.info("Session %s already claimed or no longer pending, skipping.", session_id)
            return

        if self.agent is None:
            self.agent = self.engine.set_up()

        t0 = time.time()
        try:
            offer = self.agent.process_session(session_id)
            logger.info(
                "Session %s evaluated in %.2fs. Action: %s",
                session_id,
                time.time() - t0,
                "OFFER_ISSUED" if offer else "NO_OFFER_ISSUED"
            )
        except Exception as exc:
            logger.error("Agent failed on session %s: %s", session_id, exc, exc_info=True)
            self.agent.mark_session_error(session_id, str(exc))

    def run(self, port: int = 8080):
        """Starts the health check server and persistent gRPC watch stream."""
        logger.info("=" * 65)
        logger.info("Starting Redwood Retail Firestore -> Loyalty Agent Bridge")
        logger.info("• Project:      %s", self.project_id)
        logger.info("• Region:       %s", self.region)
        logger.info("• Database:     %s", self.database_id)
        logger.info("• Probe Port:   %d", port)
        logger.info("=" * 65)

        # Start background health server for Cloud Run
        start_health_server(port=port)

        # Build the agent before accepting events so a misconfiguration fails
        # at startup rather than on the first customer login.
        self.agent = self.engine.set_up()

        self.listener = SessionEventListener(
            firestore_client=self.fs,
            session_processor=self.process_pending_session,
            max_workers=8
        )
        self.listener.start()
        logger.info("gRPC Listen stream active on /customer_sessions. Awaiting customer logins...")

        stop_signal = [False]

        def _handle_exit(sig, frame):
            logger.info("Received signal %s, initiating graceful shutdown...", sig)
            stop_signal[0] = True

        signal.signal(signal.SIGINT, _handle_exit)
        signal.signal(signal.SIGTERM, _handle_exit)

        try:
            while not stop_signal[0]:
                time.sleep(0.5)
        finally:
            if self.listener:
                self.listener.stop()
            logger.info("Bridge daemon shutdown complete.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Firestore to Loyalty Agent Event Bridge Daemon")
    parser.add_argument("--project", default=None, help="Google Cloud Project ID")
    parser.add_argument("--region", default=None, help="Google Cloud Region")
    parser.add_argument("--database", default=None, help="Firestore Database ID")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")), help="HTTP probe port")
    args = parser.parse_args()

    bridge = FirestoreAgentBridge(
        project_id=args.project,
        region=args.region,
        database_id=args.database
    )
    bridge.run(port=args.port)


if __name__ == "__main__":
    main()

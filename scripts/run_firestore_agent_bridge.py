#!/usr/bin/env python3
"""
Firestore-to-Agent-Runtime Event Bridge Daemon.

Maintains a persistent HTTP/2 gRPC Listen stream on Firestore '/customer_sessions'
and dispatches incoming PENDING login events via QueryReasoningEngine RPC to the
Retention Orchestrator Reasoning Engine on Google Cloud Agent Runtime.
"""

import os
import sys
import json
import time
import signal
import logging
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vertexai
from vertexai.preview import reasoning_engines
from google.cloud import firestore
from loyalty_agent.config import config
from loyalty_agent.listener import SessionEventListener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("firestore_agent_bridge")

REGISTRY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deployed_native_agents.json")


class FirestoreAgentRuntimeBridge:
    """
    Real-time bridge connecting Firestore change events to Agent Runtime Reasoning Engines.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        database_id: Optional[str] = None,
        orchestrator_resource_name: Optional[str] = None
    ):
        self.project_id = project_id or config.project_id
        self.region = region or config.region
        self.database_id = database_id or config.firestore_database

        if not orchestrator_resource_name and os.path.exists(REGISTRY_FILE):
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                reg = json.load(f)
                orchestrator_resource_name = reg.get("agents", {}).get("orchestrator", {}).get("resource_name")

        if not orchestrator_resource_name:
            raise ValueError(
                "Orchestrator resource name not found. Provide --orchestrator or deploy native agents first."
            )

        self.orchestrator_resource_name = orchestrator_resource_name
        self.fs = firestore.Client(project=self.project_id, database=self.database_id)

        logger.info("Connecting to Vertex AI Agent Runtime in %s...", self.region)
        vertexai.init(project=self.project_id, location=self.region)
        self.orch_engine = reasoning_engines.ReasoningEngine(self.orchestrator_resource_name)
        logger.info("✅ Attached to Retention Orchestrator: %s", self.orchestrator_resource_name)

        self.listener: Optional[SessionEventListener] = None

    def process_pending_session(self, session_id: str):
        """
        Callback executed upon detecting a document with agentProcessingStatus == 'PENDING'.
        Executes QueryReasoningEngine RPC against Agent Runtime.
        """
        logger.info("⚡ [EVENT RECEIVED] Processing PENDING session: %s", session_id)
        sess_ref = self.fs.collection("customer_sessions").document(session_id)

        # Mark as PROCESSING to prevent duplicate pickup
        try:
            sess_ref.update({"agentProcessingStatus": "PROCESSING"})
        except Exception as e:
            logger.warning("Could not set PROCESSING status on %s: %s", session_id, e)

        # Dispatch QueryReasoningEngine RPC to Agent Runtime
        t0 = time.time()
        try:
            logger.info("📡 Dispatching QueryReasoningEngine RPC to Agent Runtime for %s...", session_id)
            result = self.orch_engine.query(session_id=session_id)
            elapsed = time.time() - t0
            action = result.get("action", "UNKNOWN")
            logger.info(
                "✅ [RPC SUCCESS] Retention Orchestrator completed in %.2fs. Action: %s",
                elapsed,
                action
            )
        except Exception as e:
            logger.error("❌ [RPC FAILED] Error invoking Agent Runtime for session %s: %s", session_id, e, exc_info=True)
            sess_ref.update({"agentProcessingStatus": "ERROR", "errorMessage": str(e)})

    def run(self):
        """Starts the persistent gRPC watch stream and blocks until interrupted."""
        logger.info("=" * 65)
        logger.info("🌲 Starting Redwood Retail Firestore -> Agent Runtime Bridge")
        logger.info("• Project:      %s", self.project_id)
        logger.info("• Region:       %s", self.region)
        logger.info("• Database:     %s", self.database_id)
        logger.info("• Orchestrator: %s", self.orchestrator_resource_name)
        logger.info("=" * 65)

        self.listener = SessionEventListener(
            firestore_client=self.fs,
            session_processor=self.process_pending_session,
            max_workers=8
        )
        self.listener.start()
        logger.info("🚀 gRPC Listen stream active on /customer_sessions. Awaiting customer logins...")

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
    parser = argparse.ArgumentParser(description="Firestore to Agent Runtime Event Bridge Daemon")
    parser.add_argument("--project", default=config.project_id, help="Google Cloud Project ID")
    parser.add_argument("--region", default=config.region, help="Google Cloud Region")
    parser.add_argument("--database", default=config.firestore_database, help="Firestore Database ID")
    parser.add_argument("--orchestrator", default=None, help="Retention Orchestrator Reasoning Engine resource name")
    args = parser.parse_args()

    bridge = FirestoreAgentRuntimeBridge(
        project_id=args.project,
        region=args.region,
        database_id=args.database,
        orchestrator_resource_name=args.orchestrator
    )
    bridge.run()


if __name__ == "__main__":
    main()

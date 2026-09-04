"""
Firestore Real-Time Event Listener for Customer Login Sessions.
Attaches HTTP/2 bidirectional gRPC watch streams to detect logins with <50ms latency.
"""

import time
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

logger = logging.getLogger("loyalty_agent.listener")


class SessionEventListener:
    """
    Manages Firestore on_snapshot watch stream on `/customer_sessions`.
    """

    def __init__(
        self,
        firestore_client: Any,
        session_processor: Callable[[str], Any],
        max_workers: int = 8
    ):
        self.fs = firestore_client
        self.processor = session_processor
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent-worker")
        self.watch = None
        self._is_running = False

    def on_snapshot_callback(self, doc_snapshot, changes, read_time):
        """Callback triggered by Firestore gRPC watch stream."""
        for change in changes:
            # Detect new sessions or status transitions
            doc = change.document
            data = doc.to_dict() or {}
            status = data.get("agentProcessingStatus", data.get("status"))

            if status == "PENDING":
                session_id = doc.id
                logger.info(f"⚡ Received PENDING session event: {session_id}")
                self.executor.submit(self.processor, session_id)

    def start(self):
        """Attaches Firestore real-time listener."""
        logger.info("Starting Firestore real-time session listener...")
        query = self.fs.collection("customer_sessions").where("agentProcessingStatus", "==", "PENDING")
        self.watch = query.on_snapshot(self.on_snapshot_callback)
        self._is_running = True
        logger.info("✅ Firestore real-time session listener active.")

    def stop(self):
        """Stops the real-time listener and worker pool."""
        logger.info("Stopping Firestore listener...")
        if self.watch:
            self.watch.unsubscribe()
        self.executor.shutdown(wait=True)
        self._is_running = False
        logger.info("Firestore listener stopped.")

    @property
    def is_running(self) -> bool:
        return self._is_running

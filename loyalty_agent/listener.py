"""
Firestore Real-Time Event Listener for Customer Login Sessions.
Attaches HTTP/2 bidirectional gRPC watch streams to detect logins with <50ms latency.
"""

import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger("loyalty_agent.listener")

# Change kinds worth acting on. A REMOVED change means the document stopped
# matching the query, which is what happens when the agent itself moves a
# session out of PENDING.
ACTIONABLE_CHANGE_TYPES = {"ADDED", "MODIFIED"}

# How many recently dispatched session ids to remember. Enough to cover a
# reconnect replay of a realistic backlog without growing without bound.
DISPATCH_MEMORY = 4096


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
        self._dispatched: "OrderedDict[str, None]" = OrderedDict()
        self._dispatch_lock = threading.Lock()

    def _claim(self, session_id: str) -> bool:
        """Return True the first time a session id is seen, False afterwards.

        The watch stream re-sends every matching document as an ADDED change
        when it reconnects, so without this the whole PENDING backlog is
        reprocessed on each reconnect. This only covers the lifetime of one
        process; the durable guard is the status transition the worker makes.
        """
        with self._dispatch_lock:
            if session_id in self._dispatched:
                return False
            self._dispatched[session_id] = None
            while len(self._dispatched) > DISPATCH_MEMORY:
                self._dispatched.popitem(last=False)
            return True

    def on_snapshot_callback(self, doc_snapshot, changes, read_time):
        """Callback triggered by Firestore gRPC watch stream."""
        for change in changes:
            # ChangeType is an enum in the client and a plain string in tests;
            # comparing by name covers both without importing the internal type.
            change_type = getattr(change.type, "name", str(change.type)).upper()
            if change_type not in ACTIONABLE_CHANGE_TYPES:
                continue

            doc = change.document
            data = doc.to_dict() or {}
            status = data.get("agentProcessingStatus", data.get("status"))

            # A session already claimed or finished by another worker, or by
            # this process before a reconnect, must not be picked up again.
            if status != "PENDING":
                continue

            session_id = doc.id
            if not self._claim(session_id):
                logger.debug("Session %s already dispatched, ignoring replay.", session_id)
                continue

            logger.info("Received PENDING session event: %s", session_id)
            self.executor.submit(self.processor, session_id)

    def start(self):
        """Attaches Firestore real-time listener."""
        from google.cloud.firestore_v1.base_query import FieldFilter

        logger.info("Starting Firestore real-time session listener...")
        query = (
            self.fs.collection("customer_sessions")
            .where(filter=FieldFilter("agentProcessingStatus", "==", "PENDING"))
        )
        self.watch = query.on_snapshot(self.on_snapshot_callback)
        self._is_running = True
        logger.info("Firestore real-time session listener active.")

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

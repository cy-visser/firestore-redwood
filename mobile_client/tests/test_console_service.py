"""
Offline tests for the console's Reset Demo control.

Reset is the single button the presenter presses before a run, so what it
deletes and what it leaves alone is the contract that matters here.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fakes import FakeFirestore, make_offer
from mobile_client.backend import console_service
from mobile_client.backend.order_engine import MOBILE_ORDER_ID_PREFIX


class TestResetDemo(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()

        sessions = self.db.collection("customer_sessions")
        sessions.docs["sess_1"] = {"sessionId": "sess_1", "customerId": "cust_demo1"}
        sessions.docs["sess_2"] = {"sessionId": "sess_2", "customerId": "cust_demo2"}
        sessions.docs["sess_other"] = {"sessionId": "sess_other", "customerId": "cust_00042"}

        self.db.collection("loyalty_offers").docs["off_1"] = make_offer()

        traces = self.db.collection("pipeline_traces")
        traces.docs["tr_1"] = {"sessionId": "sess_1", "customerId": "cust_demo1"}
        traces.docs["tr_2"] = {"sessionId": "sess_2", "customerId": "cust_demo2"}
        traces.docs["tr_other"] = {"sessionId": "sess_other", "customerId": "cust_00042"}

        orders = self.db.collection("retail")
        orders.docs[f"{MOBILE_ORDER_ID_PREFIX}111111A22-IDX0000001"] = {
            "orderId": f"{MOBILE_ORDER_ID_PREFIX}111111A22-IDX0000001",
            "customerId": "cust_demo2",
        }
        orders.docs[f"{MOBILE_ORDER_ID_PREFIX}222222B33-IDX0000002"] = {
            "orderId": f"{MOBILE_ORDER_ID_PREFIX}222222B33-IDX0000002",
            "customerId": "cust_demo1",
        }
        orders.docs["ORD-2601-DEMO2-0007"] = {
            "orderId": "ORD-2601-DEMO2-0007",
            "customerId": "cust_demo2",
        }
        orders.docs["ORD-2601-LEGACY-0001"] = {
            "orderId": "ORD-2601-LEGACY-0001",
            "customerId": "demo2",
        }

    def reset(self):
        return console_service.reset_demo(self.db, warm_up=False)

    def test_sessions_and_offers_for_the_demo_customers_go(self):
        result = self.reset()
        self.assertEqual(result["deleted"]["sessions"], 2)
        self.assertEqual(result["deleted"]["offers"], 1)
        self.assertIn("sess_other", self.db.collection("customer_sessions").docs)

    def test_orders_the_app_wrote_are_swept(self):
        """A demo order sets daysSinceLastPurchase to 0 for cust_demo2, and the
        next churn run would then call the high-risk persona healthy."""
        result = self.reset()
        self.assertEqual(result["deleted"]["mobileOrders"], 2)
        remaining = set(self.db.collection("retail").docs)
        self.assertNotIn(f"{MOBILE_ORDER_ID_PREFIX}111111A22-IDX0000001", remaining)
        self.assertNotIn(f"{MOBILE_ORDER_ID_PREFIX}222222B33-IDX0000002", remaining)

    def test_the_seeded_dataset_survives(self):
        self.reset()
        self.assertIn("ORD-2601-DEMO2-0007", self.db.collection("retail").docs)

    def test_the_legacy_customer_id_sweep_is_kept(self):
        result = self.reset()
        self.assertEqual(result["deleted"]["legacyOrders"], 1)
        self.assertNotIn("ORD-2601-LEGACY-0001", self.db.collection("retail").docs)

    def test_traces_for_demo_customers_are_swept(self):
        result = self.reset()
        self.assertEqual(result["deleted"]["traces"], 2)
        self.assertIn("tr_other", self.db.collection("pipeline_traces").docs)
        self.assertNotIn("tr_1", self.db.collection("pipeline_traces").docs)
        self.assertNotIn("tr_2", self.db.collection("pipeline_traces").docs)

    def test_order_traces_are_swept_with_the_session_traces(self):
        """cdc_service records its BigQuery timing in the same collection under
        an ``order_`` key. It carries customerId, so the same sweep catches it
        and the console does not show last run's replication latency."""
        traces = self.db.collection("pipeline_traces")
        traces.docs["order_ORD-26-MOB-0042"] = {
            "kind": "ORDER",
            "orderId": "ORD-26-MOB-0042",
            "customerId": "cust_demo2",
        }

        result = self.reset()

        self.assertEqual(result["deleted"]["traces"], 3)
        self.assertNotIn("order_ORD-26-MOB-0042", traces.docs)

    def test_reset_clears_the_browser_clock_timings(self):
        """Login and order-write latencies live in a process-local dict, not in
        Firestore. Deleting documents alone would leave last run's numbers on
        the panel with nothing to correlate them to."""
        console_service.record_telemetry("sess_1", {"loginWriteMs": 42.0})
        self.addCleanup(console_service._TELEMETRY.clear)
        self.assertEqual(console_service.get_telemetry("sess_1")["loginWriteMs"], 42.0)

        self.reset()

        self.assertEqual(console_service.all_telemetry(), {})

    def test_recent_mobile_orders_query(self):
        query = console_service.recent_mobile_orders_query(self.db, limit=10)
        results = [snap.id for snap in query.stream()]
        self.assertIn(f"{MOBILE_ORDER_ID_PREFIX}111111A22-IDX0000001", results)
        self.assertIn(f"{MOBILE_ORDER_ID_PREFIX}222222B33-IDX0000002", results)
        self.assertNotIn("ORD-2601-DEMO2-0007", results)
        self.assertNotIn("ORD-2601-LEGACY-0001", results)

    def test_the_result_is_streamed_to_the_console(self):
        queue = console_service.broadcaster.subscribe()
        self.addCleanup(console_service.broadcaster.unsubscribe, queue)

        result = self.reset()

        event, payload = queue.get_nowait()
        self.assertEqual(event, "control")
        self.assertEqual(payload["action"], "reset")
        self.assertEqual(payload["counts"], result["deleted"])


class TestWarmUp(unittest.TestCase):
    """The first login of a demo should not be the one paying for a cold start."""

    def setUp(self):
        self.db = FakeFirestore()
        self.sessions = self.db.collection("customer_sessions")

    def agent_answers(self, status="SKIPPED", after_polls=1):
        """A sleep() stand-in that moves the session on like the agent would."""
        state = {"polls": 0}

        def fake_sleep(_seconds):
            state["polls"] += 1
            if state["polls"] >= after_polls:
                for doc in self.sessions.docs.values():
                    doc["agentProcessingStatus"] = status

        return fake_sleep

    def test_a_warm_up_waits_for_a_terminal_status_and_cleans_up(self):
        result = console_service.warm_up_agent(self.db, sleep=self.agent_answers())

        self.assertTrue(result["succeeded"])
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(self.sessions.docs, {})

    def test_the_warm_up_uses_the_low_risk_persona(self):
        """demo2's offer is the demo. Warming up with it would burn it."""
        seen = {}

        def capture(_seconds):
            seen.update(next(iter(self.sessions.docs.values())))
            for doc in self.sessions.docs.values():
                doc["agentProcessingStatus"] = "SKIPPED"

        console_service.warm_up_agent(self.db, sleep=capture)
        self.assertEqual(seen["customerId"], "cust_demo1")
        self.assertEqual(seen["channel"], "CONSOLE_WARMUP")

    def test_a_timeout_still_removes_the_session(self):
        """A leftover PENDING session is what the login reuse would latch on to."""
        result = console_service.warm_up_agent(
            self.db, timeout_seconds=0.05, poll_seconds=0.01, sleep=lambda _s: None
        )
        self.assertFalse(result["succeeded"])
        self.assertEqual(self.sessions.docs, {})

    def test_a_failed_warm_up_does_not_fail_the_reset(self):
        class ExplodingDb:
            def collection(self, _name):
                raise RuntimeError("firestore is down")

        result = console_service.warm_up_agent(ExplodingDb(), sleep=lambda _s: None)
        self.assertFalse(result["succeeded"])
        self.assertIn("error", result)


class TestResetDemoLines(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = FakeFirestore()
        self.bq_client = MagicMock()

    @patch("mobile_client.backend.console_service.warm_up_agent")
    @patch("mobile_client.backend.console_service.read_churn_scores")
    @patch("mobile_client.backend.console_service._stream_churn_function")
    @patch("mobile_client.backend.console_service._mobile_orders_remaining")
    async def test_reset_demo_lines_success(
        self, mock_drain, mock_stream, mock_read_scores, mock_warmup
    ):
        mock_drain.return_value = 0

        async def fake_stream(*args, **kwargs):
            yield "[1/1] Merge customer_churn_risk"
            yield "    done"
            yield "[exit 0]"

        mock_stream.side_effect = fake_stream
        mock_read_scores.return_value = [
            {"customerId": "cust_demo1", "score": 0.1, "tier": "LOW"},
            {"customerId": "cust_demo2", "score": 0.85, "tier": "CRITICAL"},
        ]
        mock_warmup.return_value = {"attempted": True, "succeeded": True, "status": "SKIPPED", "elapsedSeconds": 0.5}

        lines = []
        async for line in console_service.reset_demo_lines(self.db, self.bq_client, "test-proj"):
            lines.append(line)

        self.assertTrue(any(line.startswith("[1/4] Deleting demo") for line in lines))
        self.assertTrue(any(line.startswith("[2/4] Waiting for Firestore") for line in lines))
        self.assertTrue(any(line.startswith("[3/4] Re-scoring churn") for line in lines))
        self.assertTrue(any(line.startswith("[4/4] Warming up the agent") for line in lines))

        summary_line = next(line for line in lines if line.startswith("[SUMMARY] "))
        summary_data = json.loads(summary_line[len("[SUMMARY] "):])
        self.assertTrue(summary_data["succeeded"])
        self.assertEqual(len(summary_data["scores"]), 2)
        self.assertIn("deleted", summary_data)

    @patch("mobile_client.backend.console_service.warm_up_agent")
    @patch("mobile_client.backend.console_service.read_churn_scores")
    @patch("mobile_client.backend.console_service._stream_churn_function")
    @patch("mobile_client.backend.console_service._mobile_orders_remaining")
    async def test_reset_demo_lines_cdc_drain_timeout(
        self, mock_drain, mock_stream, mock_read_scores, mock_warmup
    ):
        mock_drain.return_value = 2  # never drains

        async def fake_stream(*args, **kwargs):
            yield "[exit 0]"

        mock_stream.side_effect = fake_stream
        mock_read_scores.return_value = []
        mock_warmup.return_value = {"attempted": True, "succeeded": True, "elapsedSeconds": 0.5}

        with patch("mobile_client.backend.console_service.CDC_DRAIN_TIMEOUT_SECONDS", 0.05), \
             patch("mobile_client.backend.console_service.CDC_DRAIN_POLL_SECONDS", 0.01):
            lines = []
            async for line in console_service.reset_demo_lines(self.db, self.bq_client, "test-proj"):
                lines.append(line)

            self.assertTrue(any("Still 2 order(s) after" in line for line in lines))

    @patch("mobile_client.backend.console_service._reset_collections")
    async def test_reset_demo_lines_deletion_failure(self, mock_reset):
        mock_reset.side_effect = RuntimeError("Firestore unavailable")

        lines = []
        async for line in console_service.reset_demo_lines(self.db, self.bq_client, "test-proj"):
            lines.append(line)

        self.assertTrue(any("[ERROR] Deletion failed: Firestore unavailable" in line for line in lines))
        summary_line = next(line for line in lines if line.startswith("[SUMMARY] "))
        summary_data = json.loads(summary_line[len("[SUMMARY] "):])
        self.assertFalse(summary_data["succeeded"])
        self.assertEqual(summary_data["error"], "Firestore unavailable")
        self.assertIn("[exit 1]", lines)


if __name__ == "__main__":
    unittest.main()

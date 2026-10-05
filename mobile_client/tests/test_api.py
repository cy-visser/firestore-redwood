"""
API Endpoint tests for Redwood Retail Mobile Client FastAPI service.

Every test that reaches Firestore patches ``server.get_db`` with a fake. The
suite has to pass on a machine with no credentials, and one that silently
picked up real ones would be quietly talking to the live demo database.
"""

import os
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fakes import FakeFirestore, make_offer
from mobile_client.backend import console_service
from mobile_client.backend.server import app


class TestMobileAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db = FakeFirestore()
        patcher = patch(
            "mobile_client.backend.server.get_db", return_value=self.db
        )
        self.get_db = patcher.start()
        self.addCleanup(patcher.stop)

    def test_health_endpoint(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["databaseId"], "redwood")
        self.assertEqual(data["collection"], "retail")

    def test_principals_endpoint(self):
        res = self.client.get("/api/principals")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("demo1", data["profiles"])
        self.assertIn("demo2", data["profiles"])
        self.assertEqual(data["profiles"]["demo1"]["customerSegment"], "ENTERPRISE_VIP")
        self.assertEqual(data["profiles"]["demo2"]["customerSegment"], "STANDARD_LOYALTY")

    def test_catalog_endpoint(self):
        res = self.client.get("/api/catalog")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreater(len(data["items"]), 0)
        self.assertIn("Sensors", data["categories"])
        self.assertIn("WH-ROTTERDAM-1", data["warehouses"])
        self.assertIn("DHL_EXPRESS", data["carriers"])

    def test_order_preview_without_an_offer_is_full_price(self):
        payload = {
            "principalId": "demo1",
            "items": [
                {"sku": "SKU-OPT-9901", "quantity": 3}
            ],
            "paymentMethod": "INVOICE_NET30",
            "serviceLevel": "NEXT_DAY_AIR",
            "feedbackRating": 5
        }
        res = self.client.post("/api/orders/preview", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        order = data["order"]
        # The order records the customer, not the principal that placed it.
        # This used to assert "demo1", which is the identity nothing else in
        # the system uses: BigQuery and the agent both key on cust_demo1.
        self.assertEqual(order["customerId"], "cust_demo1")
        self.assertEqual(order["financials"]["subtotal"], 3600.00)
        # No agent offer, so no discount. This used to expect 900.00 from a
        # hardcoded 25% VIP tier rate, which is precisely the invention the
        # offer-driven pricing removed.
        self.assertEqual(order["financials"]["discountTotal"], 0.0)
        self.assertEqual(order["financials"]["shippingFee"], 45.00)
        self.assertFalse(data["loyaltyOffer"]["offerApplied"])
        self.assertEqual(data["loyaltyOffer"]["discountPercent"], 0)
        self.assertEqual(order["metadata"]["sourcePlatform"], "CUSTOM_MOBILE_APP")

    def test_order_preview_prices_from_the_agents_offer(self):
        self.db.collection("loyalty_offers").docs["off_sess_1_retention"] = make_offer(
            discount_percent=20, free_express_shipping=True
        )

        res = self.client.post("/api/orders/preview", json={
            "principalId": "demo2",
            "items": [{"sku": "SKU-OPT-9901", "quantity": 3}],
            "offerId": "off_sess_1_retention",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["order"]["financials"]["subtotal"], 3600.00)
        self.assertEqual(data["order"]["financials"]["discountTotal"], 720.00)
        # freeExpressShipping on the offer, so the fee is waived by the offer
        # rather than by a per-principal special case.
        self.assertEqual(data["order"]["financials"]["shippingFee"], 0.0)

        provenance = data["order"]["loyaltyOffer"]
        self.assertTrue(provenance["offerApplied"])
        self.assertEqual(provenance["offerId"], "off_sess_1_retention")
        self.assertEqual(provenance["discountPercent"], 20)
        self.assertEqual(provenance["promoCode"], "RETENTION-20")

    def test_preview_finds_the_offer_even_when_the_client_names_none(self):
        self.db.collection("loyalty_offers").docs["off_sess_1_retention"] = make_offer(
            discount_percent=15
        )
        res = self.client.post("/api/orders/preview", json={
            "principalId": "demo2",
            "items": [{"sku": "SKU-OPT-9901", "quantity": 3}],
        })
        self.assertEqual(res.json()["order"]["financials"]["discountTotal"], 540.00)

    def test_a_stale_offer_id_does_not_break_checkout(self):
        """An expired offer in a browser tab is no discount, not a failure."""
        self.db.collection("loyalty_offers").docs["off_old"] = make_offer(
            offer_id="off_old", valid_until="2020-01-01T00:00:00+00:00"
        )
        res = self.client.post("/api/orders/preview", json={
            "principalId": "demo2",
            "items": [{"sku": "SKU-OPT-9901", "quantity": 3}],
            "offerId": "off_old",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["order"]["financials"]["discountTotal"], 0.0)
        self.assertFalse(res.json()["loyaltyOffer"]["offerApplied"])

    def test_an_offer_belonging_to_someone_else_buys_nothing(self):
        self.db.collection("loyalty_offers").docs["off_sess_1_retention"] = make_offer(
            customer_id="cust_demo2", discount_percent=30
        )
        res = self.client.post("/api/orders/preview", json={
            "principalId": "demo1",
            "items": [{"sku": "SKU-OPT-9901", "quantity": 3}],
            "offerId": "off_sess_1_retention",
        })
        self.assertEqual(res.json()["order"]["financials"]["discountTotal"], 0.0)

    def test_order_submit_dry_run(self):
        payload = {
            "principalId": "demo2",
            "items": [
                {"sku": "SKU-NET-4420", "quantity": 1}
            ],
            "feedbackRating": 2,
            "complaintReason": "DEFECTIVE_COMPONENT",
            "dryRun": True
        }
        res = self.client.post("/api/orders/submit", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "dry_run_success")
        order = data["order"]
        self.assertEqual(order["customerId"], "cust_demo2")
        self.assertTrue(order["supportMetrics"]["hasActiveComplaint"])
        self.assertEqual(order["supportMetrics"]["primaryComplaintReason"], "DEFECTIVE_COMPONENT")

    def test_submit_claims_the_offer_and_links_it_to_the_order(self):
        self.db.collection("loyalty_offers").docs["off_sess_1_retention"] = make_offer(
            discount_percent=20
        )

        res = self.client.post("/api/orders/submit", json={
            "principalId": "demo2",
            "items": [{"sku": "SKU-OPT-9901", "quantity": 3}],
            "offerId": "off_sess_1_retention",
        })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["loyaltyOffer"]["claimed"])

        offer = self.db.collection("loyalty_offers").docs["off_sess_1_retention"]
        self.assertEqual(offer["status"], "REDEEMED")
        self.assertEqual(offer["orderId"], body["orderId"])

        order = self.db.collection("retail").docs[body["orderId"]]
        self.assertEqual(order["loyaltyOffer"]["offerId"], "off_sess_1_retention")
        self.assertEqual(order["financials"]["discountTotal"], 720.00)

    def test_an_offer_cannot_discount_two_orders(self):
        self.db.collection("loyalty_offers").docs["off_sess_1_retention"] = make_offer(
            discount_percent=20
        )
        payload = {
            "principalId": "demo2",
            "items": [{"sku": "SKU-OPT-9901", "quantity": 3}],
            "offerId": "off_sess_1_retention",
        }
        first = self.client.post("/api/orders/submit", json=payload).json()
        second = self.client.post("/api/orders/submit", json=payload).json()

        self.assertEqual(first["order"]["financials"]["discountTotal"], 720.00)
        self.assertEqual(second["order"]["financials"]["discountTotal"], 0.0)

    def test_principals_expose_the_resolved_customer_id(self):
        """The frontend filters orders by customer id, so it needs the mapping."""
        res = self.client.get("/api/principals")
        data = res.json()
        self.assertEqual(data["profiles"]["demo1"]["customerId"], "cust_demo1")
        self.assertEqual(data["profiles"]["demo2"]["customerId"], "cust_demo2")

    def test_no_principal_advertises_a_tier_discount(self):
        """The only discount in the demo is the one the agent decided on."""
        profiles = self.client.get("/api/principals").json()["profiles"]
        for principal_id, profile in profiles.items():
            self.assertEqual(profile["discountRate"], 0.0, principal_id)

    def test_login_rejects_an_unknown_principal(self):
        """Reaches the route without touching Firestore: the principal is
        validated before any client is built."""
        res = self.client.post("/api/session/login", json={"principalId": "nobody"})
        self.assertEqual(res.status_code, 400)

    def test_login_publishes_write_event(self):
        queue = console_service.broadcaster.subscribe()
        self.addCleanup(console_service.broadcaster.unsubscribe, queue)

        res = self.client.post("/api/session/login", json={"principalId": "demo1"})
        self.assertEqual(res.status_code, 200)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        write_events = [payload for event, payload in events if event == "write"]
        self.assertTrue(any(p["kind"] == "login" and p["durationMs"] >= 0 for p in write_events))

    def test_order_submit_publishes_write_event(self):
        queue = console_service.broadcaster.subscribe()
        self.addCleanup(console_service.broadcaster.unsubscribe, queue)

        res = self.client.post("/api/orders/submit", json={
            "principalId": "demo1",
            "items": [{"sku": "SKU-OPT-9901", "quantity": 1}],
        })
        self.assertEqual(res.status_code, 200)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        write_events = [payload for event, payload in events if event == "write"]
        self.assertTrue(any(p["kind"] == "order" and p["durationMs"] >= 0 for p in write_events))

    def test_demo_controls_are_off_by_default(self):
        """The routes that execute or delete must refuse unless asked.

        The launcher now exports ENABLE_DEMO_CONTROLS=1, but the code's own
        default stays off: anything that deletes documents should need a
        deliberate opt-in from whatever starts it.
        """
        self.assertFalse(console_service.demo_controls_enabled())
        res = self.client.post("/api/console/reset")
        self.assertEqual(res.status_code, 403)


class TestListOrders(unittest.TestCase):
    """The orders feed has to show the order that was just placed."""

    def setUp(self):
        self.client = TestClient(app)
        self.db = FakeFirestore()
        patcher = patch(
            "mobile_client.backend.server.get_db", return_value=self.db
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        orders = self.db.collection("retail")
        orders.docs["ORD-SEEDED-1"] = {
            "orderId": "ORD-SEEDED-1",
            "customerId": "cust_demo2",
            "createdAt": "2026-01-05T09:00:00+00:00",
        }
        orders.docs["ORD-26-MOB-NEW"] = {
            "orderId": "ORD-26-MOB-NEW",
            "customerId": "cust_demo2",
            "createdAt": "2026-09-22T09:00:00+00:00",
        }
        orders.docs["ORD-OTHER-1"] = {
            "orderId": "ORD-OTHER-1",
            "customerId": "cust_demo1",
            "createdAt": "2026-09-22T09:30:00+00:00",
        }

    def test_orders_are_filtered_by_customer_and_newest_first(self):
        res = self.client.get("/api/orders?principalId=demo2")
        self.assertEqual(res.status_code, 200)
        body = res.json()

        self.assertEqual(body["customerId"], "cust_demo2")
        self.assertEqual(
            [order["orderId"] for order in body["orders"]],
            ["ORD-26-MOB-NEW", "ORD-SEEDED-1"],
        )
        self.assertTrue(body["orderedByFirestore"])

    def test_a_customer_id_is_accepted_directly(self):
        res = self.client.get("/api/orders?customerId=cust_demo1")
        self.assertEqual(
            [order["orderId"] for order in res.json()["orders"]], ["ORD-OTHER-1"]
        )

    def test_an_unfiltered_request_is_refused(self):
        """Unfiltered, this returned arbitrary documents from the whole
        seeded collection and the client had to filter them afterwards."""
        self.assertEqual(self.client.get("/api/orders").status_code, 400)

    def test_a_missing_index_falls_back_to_sorting_in_the_process(self):
        self.db.collection("retail").fail_ordered_queries = True

        res = self.client.get("/api/orders?principalId=demo2")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["orderedByFirestore"])
        self.assertEqual(
            [order["orderId"] for order in body["orders"]],
            ["ORD-26-MOB-NEW", "ORD-SEEDED-1"],
        )


class TestDemo2Recovery(unittest.TestCase):
    """demo2's engagement snapshot improves once she has come back.

    The churn features read engagement and support from the latest order, so
    this snapshot is what lets cust_demo2 fall from HIGH to LOW in the demo.
    """

    ITEMS = [{"sku": "SKU-OPT-9901", "quantity": 1}]

    def setUp(self):
        self.client = TestClient(app)
        self.db = FakeFirestore()
        patcher = patch(
            "mobile_client.backend.server.get_db", return_value=self.db
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        orders = self.db.collection("retail")
        # Seeded history must not count towards recovery; only app orders do.
        orders.docs["ORD-SEEDED-1"] = {"customerId": "cust_demo2"}
        orders.docs["ORD-SEEDED-2"] = {"customerId": "cust_demo2"}
        orders.docs["ORD-26-MOB-DEMO1"] = {"customerId": "cust_demo1"}

    def _add_app_orders(self, customer_id, count):
        for i in range(count):
            self.db.collection("retail").docs[f"ORD-26-MOB-{customer_id}-{i}"] = {
                "customerId": customer_id
            }

    def _preview(self, principal="demo2", rating=5):
        res = self.client.post("/api/orders/preview", json={
            "principalId": principal, "items": self.ITEMS, "feedbackRating": rating,
        })
        self.assertEqual(res.status_code, 200)
        return res.json()["order"]

    def test_first_two_orders_carry_the_struggling_snapshot(self):
        for prior in (0, 1):
            with self.subTest(prior=prior):
                self.db.collection("retail").docs = {
                    k: v for k, v in self.db.collection("retail").docs.items()
                    if not k.startswith("ORD-26-MOB-cust_demo2")
                }
                self._add_app_orders("cust_demo2", prior)
                order = self._preview()
                self.assertEqual(order["engagement"]["loginFrequencyMonthly"], 6)
                self.assertEqual(order["supportMetrics"]["returnRatePercent"], 15.0)
                self.assertEqual(order["supportMetrics"]["supportTicketsCount"], 4)

    def test_third_well_rated_order_carries_the_recovered_snapshot(self):
        self._add_app_orders("cust_demo2", 2)
        order = self._preview(rating=5)
        self.assertEqual(order["engagement"]["loginFrequencyMonthly"], 14)
        self.assertEqual(order["engagement"]["appEngagementScore"], 0.68)
        self.assertEqual(order["supportMetrics"]["returnRatePercent"], 4.0)
        self.assertEqual(order["supportMetrics"]["openSupportTicketsCount"], 0)
        self.assertFalse(order["supportMetrics"]["hasActiveComplaint"])

    def test_a_poor_rating_is_not_recovery(self):
        self._add_app_orders("cust_demo2", 3)
        order = self._preview(rating=1)
        self.assertEqual(order["engagement"]["loginFrequencyMonthly"], 6)
        # The complaint still adds its ticket on top of the struggling snapshot.
        self.assertEqual(order["supportMetrics"]["supportTicketsCount"], 5)
        self.assertTrue(order["supportMetrics"]["hasActiveComplaint"])

    def test_demo1_is_untouched(self):
        self._add_app_orders("cust_demo1", 5)
        order = self._preview(principal="demo1")
        self.assertEqual(order["engagement"]["loginFrequencyMonthly"], 24)
        self.assertEqual(order["supportMetrics"]["returnRatePercent"], 1.5)


if __name__ == "__main__":
    unittest.main()

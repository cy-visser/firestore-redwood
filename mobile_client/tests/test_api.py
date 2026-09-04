"""
API Endpoint tests for Redwood Retail Mobile Client FastAPI service.
"""

import os
import sys
import unittest
from fastapi.testclient import TestClient

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mobile_client.backend.server import app


class TestMobileAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

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

    def test_order_preview(self):
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
        self.assertEqual(order["customerId"], "demo1")
        self.assertEqual(order["financials"]["subtotal"], 3600.00)
        # 25% discount for demo1 VIP
        self.assertEqual(order["financials"]["discountTotal"], 900.00)
        self.assertEqual(order["metadata"]["sourcePlatform"], "CUSTOM_MOBILE_APP")

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
        self.assertEqual(order["customerId"], "demo2")
        self.assertTrue(order["supportMetrics"]["hasActiveComplaint"])
        self.assertEqual(order["supportMetrics"]["primaryComplaintReason"], "DEFECTIVE_COMPONENT")


if __name__ == "__main__":
    unittest.main()

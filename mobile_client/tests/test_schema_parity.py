"""
Unit tests to assert 100% schema and type parity between
mobile_client.backend.order_engine and generate_retail_dataset.py.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

# Add parent directory
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from generate_retail_dataset import generate_single_order
from mobile_client.backend.order_engine import (
    create_order_from_cart, DEMO_PRINCIPALS, CATALOG_ITEMS
)


class TestSchemaParity(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)
        self.sample_synthetic = generate_single_order(0, 100, self.base_time)
        
        # Create cart items from catalog
        self.cart_items = [
            {"sku": CATALOG_ITEMS[0]["sku"], "quantity": 2},
            {"sku": CATALOG_ITEMS[1]["sku"], "quantity": 1}
        ]
        self.sample_mobile_demo1 = create_order_from_cart(
            cart_items=self.cart_items,
            principal_id="demo1",
            now=self.base_time
        )
        self.sample_mobile_demo2 = create_order_from_cart(
            cart_items=self.cart_items,
            principal_id="demo2",
            feedback_rating=2,
            complaint_reason="LATE_DELIVERY",
            now=self.base_time
        )

    def test_top_level_keys_parity(self):
        synthetic_keys = set(self.sample_synthetic.keys())
        mobile_keys_demo1 = set(self.sample_mobile_demo1.keys())
        mobile_keys_demo2 = set(self.sample_mobile_demo2.keys())
        
        self.assertEqual(synthetic_keys, mobile_keys_demo1, "demo1 top-level keys must match synthetic order keys")
        self.assertEqual(synthetic_keys, mobile_keys_demo2, "demo2 top-level keys must match synthetic order keys")

    def test_nested_subsections_parity(self):
        subsections = [
            "financials",
            "transactionalMetrics",
            "engagement",
            "supportMetrics",
            "accountState",
            "logistics",
            "shippingAddress",
            "customerFeedback",
            "metadata"
        ]
        for sub in subsections:
            synth_sub = set(self.sample_synthetic[sub].keys())
            mob_sub_1 = set(self.sample_mobile_demo1[sub].keys())
            mob_sub_2 = set(self.sample_mobile_demo2[sub].keys())
            self.assertEqual(synth_sub, mob_sub_1, f"Subsection {sub} keys mismatch in demo1")
            self.assertEqual(synth_sub, mob_sub_2, f"Subsection {sub} keys mismatch in demo2")

    def test_line_items_structure(self):
        synth_item_keys = set(self.sample_synthetic["lineItems"][0].keys())
        mob_item_keys_1 = set(self.sample_mobile_demo1["lineItems"][0].keys())
        mob_item_keys_2 = set(self.sample_mobile_demo2["lineItems"][0].keys())
        self.assertEqual(synth_item_keys, mob_item_keys_1, "lineItems item keys mismatch in demo1")
        self.assertEqual(synth_item_keys, mob_item_keys_2, "lineItems item keys mismatch in demo2")

    def test_data_types_parity(self):
        """Verify value types across all nested fields match."""
        def assert_types(synth_dict, mob_dict, path=""):
            for k, synth_val in synth_dict.items():
                current_path = f"{path}.{k}" if path else k
                self.assertIn(k, mob_dict, f"Missing key {current_path} in mobile dict")
                mob_val = mob_dict[k]
                if synth_val is None or mob_val is None:
                    continue  # Nullable fields like primaryComplaintReason
                if isinstance(synth_val, dict):
                    self.assertIsInstance(mob_val, dict, f"Type mismatch at {current_path}: expected dict, got {type(mob_val)}")
                    assert_types(synth_val, mob_val, current_path)
                elif isinstance(synth_val, list):
                    self.assertIsInstance(mob_val, list, f"Type mismatch at {current_path}: expected list, got {type(mob_val)}")
                elif isinstance(synth_val, (int, float)):
                    self.assertIsInstance(mob_val, (int, float), f"Numeric type mismatch at {current_path}: {type(synth_val)} vs {type(mob_val)}")
                elif isinstance(synth_val, bool):
                    self.assertIsInstance(mob_val, bool, f"Boolean mismatch at {current_path}: {type(synth_val)} vs {type(mob_val)}")
                elif isinstance(synth_val, str):
                    self.assertIsInstance(mob_val, str, f"String mismatch at {current_path}: {type(synth_val)} vs {type(mob_val)}")

        assert_types(self.sample_synthetic, self.sample_mobile_demo1)
        assert_types(self.sample_synthetic, self.sample_mobile_demo2)

    def test_financial_consistency(self):
        fin = self.sample_mobile_demo1["financials"]
        expected_grand = round(fin["subtotal"] - fin["discountTotal"] + fin["taxAmount"] + fin["shippingFee"], 2)
        self.assertAlmostEqual(fin["grandTotal"], expected_grand, places=2)

    def test_complaint_logic_for_demo2(self):
        sup = self.sample_mobile_demo2["supportMetrics"]
        fb = self.sample_mobile_demo2["customerFeedback"]
        self.assertTrue(sup["hasActiveComplaint"])
        self.assertTrue(fb["hasActiveComplaint"])
        self.assertEqual(sup["primaryComplaintReason"], "LATE_DELIVERY")
        self.assertEqual(fb["primaryComplaintReason"], "LATE_DELIVERY")
        self.assertLess(sup["sentimentScore"], 0.0)


if __name__ == "__main__":
    unittest.main()

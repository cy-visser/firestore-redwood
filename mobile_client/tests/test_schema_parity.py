"""
Unit tests to assert 100% schema and type parity between
mobile_client.backend.order_engine and the seeder's order_factory.

The reference order comes from ``order_factory.build_order``, which is what
``generate_retail_dataset.py`` calls. This module used to import
``generate_single_order`` from the seeder directly; the seeder rewrite removed
that function, and the test had not imported since.
"""

import os
import random
import sys
import unittest
from datetime import datetime, timezone

# Add parent directory
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from customer_profiles import DEMO_PERSONAS, build_customer, generate_order_dates
from order_factory import build_order
from mobile_client.backend.order_engine import (
    create_order_from_cart, DEMO_PRINCIPALS, CATALOG_ITEMS, MOBILE_ORDER_ID_PREFIX
)


def build_reference_order(now: datetime) -> dict:
    """Produce one seeded order to compare the mobile client's output against.

    Seeded deterministically so a parity failure is a real difference rather
    than a different roll of the dice.
    """
    rng = random.Random(20260801)
    persona = DEMO_PERSONAS[0]
    customer = build_customer(0, rng, persona=persona, project_id="test-project")
    generate_order_dates(customer, rng, now, persona=persona)
    return build_order(
        customer=customer,
        order_date=now,
        sequence=1,
        rng=rng,
        prior_orders=[],
    )


class TestSchemaParity(unittest.TestCase):
    # The mobile client's order is the seeded order plus exactly this. The
    # seeder has no retention agent, so it has nothing to record here; adding
    # it is a deliberate, single-field extension rather than a drift, and this
    # set is what keeps it to one field. cdc_service maps named paths onto
    # BigQuery columns and carries everything else in document_data, so an
    # extra top-level object changes no table.
    MOBILE_ONLY_KEYS = {"loyaltyOffer"}

    def setUp(self):
        self.base_time = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)
        self.sample_synthetic = build_reference_order(self.base_time)

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

        self.assertEqual(
            synthetic_keys | self.MOBILE_ONLY_KEYS, mobile_keys_demo1,
            "demo1 keys must be the synthetic order's keys plus the offer block"
        )
        self.assertEqual(
            synthetic_keys | self.MOBILE_ONLY_KEYS, mobile_keys_demo2,
            "demo2 keys must be the synthetic order's keys plus the offer block"
        )

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


class TestOfferDrivenPricing(unittest.TestCase):
    """The discount is the agent's offer, and nothing else is."""

    def setUp(self):
        self.base_time = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)
        self.cart_items = [{"sku": CATALOG_ITEMS[0]["sku"], "quantity": 2}]

    def build(self, principal_id="demo2", offer=None):
        return create_order_from_cart(
            cart_items=self.cart_items,
            principal_id=principal_id,
            offer=offer,
            now=self.base_time,
        )

    def test_no_offer_means_no_discount_for_either_principal(self):
        for principal_id in ("demo1", "demo2"):
            order = self.build(principal_id=principal_id)
            self.assertEqual(order["financials"]["discountTotal"], 0.0, principal_id)
            self.assertEqual(order["financials"]["shippingFee"], 45.00, principal_id)
            self.assertFalse(order["loyaltyOffer"]["offerApplied"], principal_id)
            self.assertIsNone(order["loyaltyOffer"]["offerId"], principal_id)

    def test_the_offers_percentage_is_the_discount(self):
        order = self.build(offer={
            "offerId": "off_x",
            "promoCode": "STAY20",
            "discountPercent": 20,
        })
        subtotal = order["financials"]["subtotal"]
        self.assertEqual(
            order["financials"]["discountTotal"], round(subtotal * 0.20, 2)
        )
        self.assertEqual(order["loyaltyOffer"], {
            "offerApplied": True,
            "offerId": "off_x",
            "promoCode": "STAY20",
            "discountPercent": 20,
            "freeExpressShipping": False,
        })

    def test_free_shipping_comes_from_the_offer(self):
        order = self.build(offer={
            "offerId": "off_x",
            "discountPercent": 10,
            "freeExpressShipping": True,
        })
        self.assertEqual(order["financials"]["shippingFee"], 0.0)

    def test_a_demo1_order_over_a_thousand_euro_still_pays_shipping(self):
        """The demo1-only free shipping rule had nothing behind it."""
        order = create_order_from_cart(
            cart_items=[{"sku": CATALOG_ITEMS[0]["sku"], "quantity": 20}],
            principal_id="demo1",
            now=self.base_time,
        )
        self.assertGreater(order["financials"]["subtotal"], 1000.0)
        self.assertEqual(order["financials"]["shippingFee"], 45.00)

    def test_an_absurd_percentage_cannot_pay_the_customer(self):
        order = self.build(offer={"offerId": "off_x", "discountPercent": 900})
        self.assertEqual(order["loyaltyOffer"]["discountPercent"], 100)
        self.assertEqual(
            order["financials"]["discountTotal"], order["financials"]["subtotal"]
        )

    def test_the_retired_mirror_field_is_no_longer_honoured(self):
        """discountPercentage was one of eleven aliases the agent mirrored.

        Nothing read them and two spellings of one number is how they drift,
        so the agent now writes discountPercent alone. An offer carrying only
        the retired name is not a discount.
        """
        order = self.build(offer={"offerId": "off_x", "discountPercentage": 12})
        self.assertEqual(order["loyaltyOffer"]["discountPercent"], 0)

    def test_the_mobile_order_id_prefix_is_what_reset_sweeps(self):
        order = self.build()
        self.assertTrue(order["orderId"].startswith(MOBILE_ORDER_ID_PREFIX))


if __name__ == "__main__":
    unittest.main()


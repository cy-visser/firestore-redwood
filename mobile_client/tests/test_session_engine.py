"""
Offline tests for the login session contract and offer resolution.

The Firestore doubles live in fakes.py; see the note there on why these are
fakes and not mocks.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fakes import FakeFirestore, make_offer
from mobile_client.backend import session_engine


class TestLoginSession(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        self.now = datetime(2026, 9, 22, 8, 0, 0, tzinfo=timezone.utc)

    def test_login_resolves_the_seeded_customer_id(self):
        """demo1 is the principal; cust_demo1 is who the churn model knows."""
        session, reused = session_engine.start_session(self.db, "demo1", now=self.now)
        self.assertEqual(session["customerId"], "cust_demo1")
        self.assertEqual(session["principalId"], "demo1")
        self.assertFalse(reused)

        session2, _ = session_engine.start_session(self.db, "demo2", now=self.now)
        self.assertEqual(session2["customerId"], "cust_demo2")

    def test_login_writes_a_pending_session(self):
        session, _ = session_engine.start_session(self.db, "demo2", now=self.now)

        stored = self.db.collection("customer_sessions").docs[session["sessionId"]]
        self.assertEqual(stored["agentProcessingStatus"], "PENDING")
        self.assertEqual(stored["status"], "PENDING")
        # The bridge only forwards PENDING sessions and the composite indexes
        # order on loginTimestamp, so both are part of the contract.
        self.assertEqual(stored["loginTimestamp"], self.now)
        self.assertGreater(stored["expireAt"], self.now)
        self.assertEqual(stored["channel"], "MOBILE_APP")

    def test_second_login_reuses_the_pending_session(self):
        """A double tap must not produce two sessions for one customer.

        Two sessions would both be claimable and, because neither offer exists
        yet, both would pass the agent's cooldown check and issue an offer.
        """
        first, reused_first = session_engine.start_session(self.db, "demo2", now=self.now)
        second, reused_second = session_engine.start_session(self.db, "demo2", now=self.now)

        self.assertFalse(reused_first)
        self.assertTrue(reused_second)
        self.assertEqual(first["sessionId"], second["sessionId"])
        self.assertEqual(len(self.db.collection("customer_sessions").docs), 1)

    def test_a_session_the_agent_is_working_on_is_also_reused(self):
        first, _ = session_engine.start_session(self.db, "demo2", now=self.now)
        self.db.collection("customer_sessions").docs[first["sessionId"]][
            "agentProcessingStatus"
        ] = "PROCESSING"

        second, reused = session_engine.start_session(self.db, "demo2", now=self.now)
        self.assertTrue(reused)
        self.assertEqual(first["sessionId"], second["sessionId"])

    def test_a_finished_session_does_not_block_the_next_login(self):
        first, _ = session_engine.start_session(self.db, "demo2", now=self.now)
        self.db.collection("customer_sessions").docs[first["sessionId"]][
            "agentProcessingStatus"
        ] = "PROCESSED"

        second, reused = session_engine.start_session(self.db, "demo2", now=self.now)
        self.assertFalse(reused)
        self.assertNotEqual(first["sessionId"], second["sessionId"])

    def test_unknown_principal_is_rejected(self):
        with self.assertRaises(session_engine.UnknownPrincipalError):
            session_engine.start_session(self.db, "nobody", now=self.now)


class TestInFlightSessionAgeBound(unittest.TestCase):
    """One outage must not wedge every later login on a dead session."""

    def setUp(self):
        self.db = FakeFirestore()
        self.now = datetime(2026, 9, 22, 8, 0, 0, tzinfo=timezone.utc)

    def test_a_stale_pending_session_is_not_reused(self):
        stale, _ = session_engine.start_session(self.db, "demo2", now=self.now)

        later = self.now + timedelta(
            seconds=session_engine.session_reuse_max_age_seconds() + 1
        )
        fresh, reused = session_engine.start_session(self.db, "demo2", now=later)

        self.assertFalse(reused)
        self.assertNotEqual(stale["sessionId"], fresh["sessionId"])
        self.assertEqual(len(self.db.collection("customer_sessions").docs), 2)

    def test_a_session_inside_the_window_is_still_reused(self):
        first, _ = session_engine.start_session(self.db, "demo2", now=self.now)

        later = self.now + timedelta(
            seconds=session_engine.session_reuse_max_age_seconds() - 1
        )
        second, reused = session_engine.start_session(self.db, "demo2", now=later)

        self.assertTrue(reused)
        self.assertEqual(first["sessionId"], second["sessionId"])

    def test_the_window_is_configurable(self):
        os.environ["SESSION_REUSE_MAX_AGE_SECONDS"] = "5"
        self.addCleanup(os.environ.pop, "SESSION_REUSE_MAX_AGE_SECONDS", None)

        self.assertEqual(session_engine.session_reuse_max_age_seconds(), 5.0)
        first, _ = session_engine.start_session(self.db, "demo2", now=self.now)
        _, reused = session_engine.start_session(
            self.db, "demo2", now=self.now + timedelta(seconds=6)
        )
        self.assertFalse(reused)

    def test_a_nonsense_window_falls_back_to_the_default(self):
        os.environ["SESSION_REUSE_MAX_AGE_SECONDS"] = "soon"
        self.addCleanup(os.environ.pop, "SESSION_REUSE_MAX_AGE_SECONDS", None)
        self.assertEqual(session_engine.session_reuse_max_age_seconds(), 120.0)

    def test_the_freshest_in_flight_session_wins(self):
        """With a pile of stale sessions, reuse must still find the live one."""
        sessions = self.db.collection("customer_sessions")
        for index in range(3):
            stale = session_engine.build_session_doc(
                "demo2", now=self.now - timedelta(hours=index + 1)
            )
            sessions.docs[stale["sessionId"]] = stale

        live, _ = session_engine.start_session(self.db, "demo2", now=self.now)
        again, reused = session_engine.start_session(self.db, "demo2", now=self.now)

        self.assertTrue(reused)
        self.assertEqual(live["sessionId"], again["sessionId"])


class TestOfferResolution(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        self.now = datetime(2026, 9, 22, 8, 5, 0, tzinfo=timezone.utc)
        self.offers = self.db.collection("loyalty_offers")
        self.offers.docs["off_sess_1_retention"] = make_offer()

    def test_an_active_offer_is_found_without_being_named(self):
        offer = session_engine.resolve_offer_for_order(
            self.db, "cust_demo2", now=self.now
        )
        self.assertIsNotNone(offer)
        self.assertEqual(offer["offerId"], "off_sess_1_retention")
        self.assertEqual(offer["discountPercent"], 20)

    def test_a_customer_with_no_offer_gets_none(self):
        self.assertIsNone(
            session_engine.resolve_offer_for_order(self.db, "cust_demo1", now=self.now)
        )

    def test_the_newest_offer_wins(self):
        self.offers.docs["off_sess_2_retention"] = make_offer(
            offer_id="off_sess_2_retention",
            session_id="sess_2",
            discount_percent=25,
            created_at="2026-09-22T09:00:00+00:00",
        )
        offer = session_engine.resolve_offer_for_order(
            self.db, "cust_demo2", now=self.now
        )
        self.assertEqual(offer["offerId"], "off_sess_2_retention")

    def test_another_customers_offer_is_refused(self):
        """The id comes from the browser, so ownership is checked server side."""
        self.assertIsNone(
            session_engine.resolve_offer_for_order(
                self.db, "cust_demo1", offer_id="off_sess_1_retention", now=self.now
            )
        )

    def test_an_expired_offer_is_refused(self):
        self.offers.docs["off_sess_1_retention"] = make_offer(
            valid_until="2026-09-01T00:00:00+00:00"
        )
        self.assertIsNone(
            session_engine.resolve_offer_for_order(
                self.db, "cust_demo2", offer_id="off_sess_1_retention", now=self.now
            )
        )
        self.assertIsNone(
            session_engine.resolve_offer_for_order(self.db, "cust_demo2", now=self.now)
        )

    def test_an_unknown_offer_id_is_no_discount_rather_than_an_error(self):
        self.assertIsNone(
            session_engine.resolve_offer_for_order(
                self.db, "cust_demo2", offer_id="off_nope", now=self.now
            )
        )

    def test_an_offer_already_spent_on_an_order_is_refused(self):
        self.offers.docs["off_sess_1_retention"] = make_offer(
            status="REDEEMED", order_id="ORD-26-MOB-1"
        )
        self.assertIsNone(
            session_engine.resolve_offer_for_order(
                self.db, "cust_demo2", offer_id="off_sess_1_retention", now=self.now
            )
        )

    def test_an_offer_claimed_in_the_sheet_still_prices_the_order(self):
        """Claim then check out is the demo's own path; it must not be list price."""
        self.offers.docs["off_sess_1_retention"] = make_offer(status="REDEEMED")
        offer = session_engine.resolve_offer_for_order(
            self.db, "cust_demo2", offer_id="off_sess_1_retention", now=self.now
        )
        self.assertIsNotNone(offer)
        self.assertEqual(offer["discountPercent"], 20)

    def test_an_expired_status_is_refused(self):
        self.offers.docs["off_sess_1_retention"] = make_offer(status="EXPIRED")
        self.assertIsNone(
            session_engine.resolve_offer_for_order(
                self.db, "cust_demo2", offer_id="off_sess_1_retention", now=self.now
            )
        )


class TestClaimOffer(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        self.now = datetime(2026, 9, 22, 8, 5, 0, tzinfo=timezone.utc)
        self.db.collection("loyalty_offers").docs["off_sess_1_retention"] = make_offer()

    def test_claim_flips_active_to_redeemed(self):
        offer = session_engine.claim_offer(
            self.db, "off_sess_1_retention", now=self.now
        )
        self.assertEqual(offer["status"], "REDEEMED")
        self.assertEqual(offer["claimedAt"], self.now.isoformat())

        stored = self.db.collection("loyalty_offers").docs["off_sess_1_retention"]
        self.assertEqual(stored["status"], "REDEEMED")

    def test_claim_only_touches_the_fields_the_rules_allow(self):
        """firestore.rules permits status, claimedAt and orderId, nothing else."""
        before = dict(self.db.collection("loyalty_offers").docs["off_sess_1_retention"])
        session_engine.claim_offer(
            self.db, "off_sess_1_retention", order_id="ORD-1", now=self.now
        )
        after = self.db.collection("loyalty_offers").docs["off_sess_1_retention"]

        changed = {k for k in after if before.get(k) != after[k]}
        self.assertTrue(changed <= {"status", "claimedAt", "orderId"}, changed)

    def test_claiming_twice_is_refused(self):
        session_engine.claim_offer(self.db, "off_sess_1_retention", now=self.now)
        with self.assertRaises(session_engine.OfferNotClaimableError):
            session_engine.claim_offer(self.db, "off_sess_1_retention", now=self.now)

    def test_an_order_can_be_attached_to_an_offer_claimed_in_the_sheet(self):
        session_engine.claim_offer(self.db, "off_sess_1_retention", now=self.now)
        offer = session_engine.claim_offer(
            self.db, "off_sess_1_retention", order_id="ORD-1", now=self.now
        )
        self.assertEqual(offer["orderId"], "ORD-1")

    def test_a_second_order_cannot_reuse_a_spent_offer(self):
        session_engine.claim_offer(
            self.db, "off_sess_1_retention", order_id="ORD-1", now=self.now
        )
        with self.assertRaises(session_engine.OfferNotClaimableError):
            session_engine.claim_offer(
                self.db, "off_sess_1_retention", order_id="ORD-2", now=self.now
            )

    def test_claiming_an_unknown_offer_is_refused(self):
        with self.assertRaises(session_engine.OfferNotFoundError):
            session_engine.claim_offer(self.db, "off_nope", now=self.now)


if __name__ == "__main__":
    unittest.main()

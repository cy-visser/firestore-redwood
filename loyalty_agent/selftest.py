"""
Offline checks for the loyalty offer agent.

These exercise the whole decision path -- churn lookup, friction boost,
cooldown, the risk gate, discount guardrails and offer persistence -- against
fake Firestore, BigQuery and Gemini clients, so a broken gate or a malformed
offer fails here in milliseconds rather than after a deploy and a live login.

No Google Cloud connection is made or required.

Run with:  .venv/bin/python loyalty_agent/selftest.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The agent refuses to guess a project id, so give it one before importing the
# config. Nothing here connects to it.
os.environ.setdefault("GCP_PROJECT_ID", "selftest-project")

from loyalty_agent.agent import (  # noqa: E402
    COOLDOWN_SCAN_LIMIT,
    LoyaltyAgent,
    apply_discount_guardrails,
    discount_ceiling_for_segment,
    evaluate_churn_tier,
    flatten_profile,
)
from loyalty_agent.config import config  # noqa: E402
from loyalty_agent.schemas import LoyaltyOffer  # noqa: E402

FAILURES: List[str] = []

PROJECT = "selftest-project"
DATASET = "redwood_retail"
TABLE = "customer_churn_risk"
SESSION_ID = "sess_selftest_0001"
CUSTOMER_ID = "cust_selftest_4271"


def check(label: str, actual, expected) -> None:
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok    {label}")


def check_true(label: str, value) -> None:
    check(label, bool(value), True)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSnapshot:
    def __init__(self, doc_id: str, data: Optional[Dict[str, Any]]):
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> Optional[Dict[str, Any]]:
        return dict(self._data) if self._data is not None else None


class FakeDocumentRef:
    def __init__(self, store: Dict[str, Dict[str, Any]], doc_id: str):
        self._store = store
        self.id = doc_id

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self.id, self._store.get(self.id))

    def set(self, data: Dict[str, Any], merge: bool = False) -> None:
        if merge and self.id in self._store:
            self._store[self.id].update(data)
        else:
            self._store[self.id] = dict(data)

    def update(self, data: Dict[str, Any]) -> None:
        self._store.setdefault(self.id, {}).update(data)


class FakeQuery:
    """Evaluates the subset of the query API the agent uses."""

    def __init__(self, collection: "FakeCollection"):
        self.collection = collection
        self.filters: List[Any] = []
        self.order_by_field: Optional[str] = None
        self.order_direction: Optional[str] = None
        self.limit_value: Optional[int] = None

    def where(self, filter=None, **kwargs):  # noqa: A002 - mirrors the real API
        if filter is None:
            raise AssertionError("positional where() is deprecated; pass filter=FieldFilter(...)")
        self.filters.append(filter)
        return self

    def order_by(self, field_path: str, direction: str = "ASCENDING"):
        self.order_by_field = field_path
        self.order_direction = direction
        return self

    def limit(self, count: int):
        self.limit_value = count
        return self

    def stream(self):
        self.collection.db.queries.append(self)
        rows = [
            (doc_id, data)
            for doc_id, data in self.collection.store.items()
            if all(_matches(data, f) for f in self.filters)
        ]
        if self.order_by_field:
            rows.sort(
                key=lambda item: item[1].get(self.order_by_field) or "",
                reverse=(self.order_direction == "DESCENDING"),
            )
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return [FakeSnapshot(doc_id, data) for doc_id, data in rows]


def _matches(data: Dict[str, Any], field_filter: Any) -> bool:
    field = field_filter.field_path
    op = field_filter.op_string
    value = field_filter.value
    actual = data.get(field)
    if op == "==":
        return actual == value
    if op == ">=":
        return actual is not None and actual >= value
    raise AssertionError(f"unsupported operator in fake: {op}")


class FakeCollection:
    def __init__(self, db: "FakeFirestore", name: str):
        self.db = db
        self.name = name
        self.store = db.data.setdefault(name, {})

    def document(self, doc_id: str) -> FakeDocumentRef:
        return FakeDocumentRef(self.store, doc_id)

    def where(self, filter=None, **kwargs):  # noqa: A002
        return FakeQuery(self).where(filter=filter)

    def order_by(self, field_path: str, direction: str = "ASCENDING"):
        return FakeQuery(self).order_by(field_path, direction)

    def limit(self, count: int):
        return FakeQuery(self).limit(count)


class FakeFirestore:
    def __init__(self, data: Optional[Dict[str, Dict[str, Any]]] = None):
        self.data: Dict[str, Dict[str, Any]] = data or {}
        self.queries: List[FakeQuery] = []

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self, name)

    # Convenience accessors used by the assertions below.
    def session(self, session_id: str = SESSION_ID) -> Dict[str, Any]:
        return self.data.get("customer_sessions", {}).get(session_id, {})

    def offers(self) -> Dict[str, Any]:
        return self.data.get("loyalty_offers", {})


class FakeQueryJob:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def result(self):
        return list(self._rows)


class FakeBigQuery:
    """Records the SQL it is handed; never contacts BigQuery."""

    def __init__(self, rows: Optional[List[Any]] = None, error: Optional[Exception] = None):
        self.rows = rows or []
        self.error = error
        self.sql: Optional[str] = None
        self.job_config: Any = None
        self.call_count = 0

    def query(self, sql: str, job_config: Any = None) -> FakeQueryJob:
        self.sql = sql
        self.job_config = job_config
        self.call_count += 1
        if self.error:
            raise self.error
        return FakeQueryJob(self.rows)


class FakeGeminiModels:
    def __init__(self, text: Optional[str], error: Optional[Exception]):
        self.text = text
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def generate_content(self, model=None, contents=None, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


class FakeGeminiClient:
    def __init__(self, text: Optional[str] = None, error: Optional[Exception] = None):
        self.models = FakeGeminiModels(text, error)


class LegacyGeminiClient:
    """A client shaped like the one the old synthesis agent called.

    google.genai.Client has no generate_content method; the old code called it
    anyway, so generation threw on every request and the fallback always ran.
    """

    def generate_content(self, prompt):  # pragma: no cover - must never be reached
        raise AssertionError("the agent must not call Client.generate_content")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def churn_row(
    probability: float,
    tier: Optional[str] = None,
    segment: Optional[str] = None,
    spend: float = 4200.0,
    sentiment: float = 0.4,
) -> SimpleNamespace:
    return SimpleNamespace(
        customer_id=CUSTOMER_ID,
        churn_probability=probability,
        churn_risk_tier=tier,
        customer_segment=segment,
        total_spend_90d=spend,
        sentiment_score=sentiment,
        calculation_timestamp="2026-09-21T02:00:00+00:00",
    )


def make_offer(
    offer_id: str,
    created_days_ago: float,
    status: str = "ACTIVE",
    cooldown_days_ahead: float = 5.0,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "offerId": offer_id,
        "customerId": CUSTOMER_ID,
        "status": status,
        "createdAt": (now - timedelta(days=created_days_ago)).isoformat(),
        "cooldownUntil": (now + timedelta(days=cooldown_days_ahead)).isoformat(),
    }


def build_agent(
    churn_probability: Optional[float] = 0.80,
    churn_tier: Optional[str] = None,
    profile: Optional[Dict[str, Any]] = None,
    offers: Optional[List[Dict[str, Any]]] = None,
    genai_client: Any = None,
    bigquery_error: Optional[Exception] = None,
    session: Optional[Dict[str, Any]] = None,
    segment: Optional[str] = None,
):
    """Assemble an agent over fakes, returning (agent, firestore, bigquery)."""
    session_doc = session if session is not None else {
        "sessionId": SESSION_ID,
        "customerId": CUSTOMER_ID,
        "loginTimestamp": datetime.now(timezone.utc).isoformat(),
        "status": "ACTIVE",
        "agentProcessingStatus": "PROCESSING",
    }

    fs = FakeFirestore({
        "customer_sessions": {SESSION_ID: session_doc},
        "customers": {CUSTOMER_ID: profile} if profile is not None else {},
        "loyalty_offers": {o["offerId"]: o for o in (offers or [])},
    })

    rows = [] if churn_probability is None else [
        churn_row(churn_probability, tier=churn_tier, segment=segment)
    ]
    bq = FakeBigQuery(rows=rows, error=bigquery_error)

    agent = LoyaltyAgent(
        firestore_client=fs,
        bigquery_client=bq,
        genai_client=genai_client,
        project_id=PROJECT,
        dataset_id=DATASET,
        table_id=TABLE,
    )
    return agent, fs, bq


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def test_churn_gate_boundaries() -> None:
    print("\n[churn gate]")
    threshold = config.churn_trigger_threshold
    check("configured threshold", threshold, 0.50)

    agent, fs, _ = build_agent(churn_probability=threshold - 0.01)
    check("below threshold issues no offer", agent.process_session(SESSION_ID), None)
    check("below threshold status", fs.session()["agentProcessingStatus"], "SKIPPED")
    check("below threshold reason", fs.session()["skipReason"], "LOW_CHURN_RISK")
    check("below threshold writes no offer", len(fs.offers()), 0)
    # SKIPPED is terminal for the agent but the session itself is done.
    check("below threshold session status", fs.session()["status"], "PROCESSED")

    agent, fs, _ = build_agent(churn_probability=threshold)
    offer = agent.process_session(SESSION_ID)
    check_true("at threshold issues an offer", offer is not None)
    check("at threshold status", fs.session()["agentProcessingStatus"], "PROCESSED")
    check("at threshold has no skip reason", fs.session()["skipReason"], None)
    check("at threshold links the offer", fs.session()["offerId"], offer["offerId"])

    agent, fs, _ = build_agent(churn_probability=0.95)
    offer = agent.process_session(SESSION_ID)
    check("critical tier recorded", offer["churnRiskTier"], "CRITICAL")
    check("tier classifier at 0.75", evaluate_churn_tier(0.75), "CRITICAL")
    check("tier classifier at 0.50", evaluate_churn_tier(0.50), "HIGH")
    check("tier classifier at 0.25", evaluate_churn_tier(0.25), "MODERATE")
    check("tier classifier below 0.25", evaluate_churn_tier(0.24), "LOW")


def test_friction_boost() -> None:
    print("\n[friction boost]")
    check("configured boost", config.acute_friction_boost, 0.25)

    # 0.40 is below the gate on its own; an acute grievance lifts it over.
    agent, fs, _ = build_agent(
        churn_probability=0.40,
        profile={"customerSegment": "ENTERPRISE_VIP", "primaryComplaintReason": "LATE_DELIVERY"},
    )
    offer = agent.process_session(SESSION_ID)
    check_true("acute friction lifts over the gate", offer is not None)
    check("boosted probability", offer["churnProbability"], 0.65)
    check("baseline preserved", offer["baselineChurnRisk"], 0.40)
    check("evaluation source", offer["evaluationSource"], "EVENT_AUGMENTED_HYBRID")
    check("tier recomputed from boosted score", offer["churnRiskTier"], "HIGH")

    # A complaint that is not in the acute set must not move the score.
    agent, fs, _ = build_agent(
        churn_probability=0.40,
        profile={"customerSegment": "ENTERPRISE_VIP", "primaryComplaintReason": "POOR_SUPPORT_RESPONSE"},
    )
    check("non-acute complaint does not boost", agent.process_session(SESSION_ID), None)
    check("non-acute complaint skips", fs.session()["skipReason"], "LOW_CHURN_RISK")

    agent, fs, _ = build_agent(
        churn_probability=0.90,
        profile={"customerSegment": "ENTERPRISE_VIP", "recentFrictionEvent": "BILLING_DISPUTE"},
    )
    offer = agent.process_session(SESSION_ID)
    check("boost saturates at 1.0", offer["churnProbability"], 1.0)


def test_churn_lookup() -> None:
    print("\n[churn lookup]")
    agent, fs, bq = build_agent(churn_probability=0.80)
    agent.process_session(SESSION_ID)
    sql = bq.sql or ""

    # Interpolating the id into the SQL text made this injectable; the id must
    # travel as a bound parameter instead.
    check_true("query is parameterised", "@customer_id" in sql)
    check("customer id absent from SQL text", CUSTOMER_ID in sql, False)
    check_true("table is fully qualified",
               f"`{PROJECT}.{DATASET}.{TABLE}`" in sql)
    check_true("query is bounded", "LIMIT 1" in sql)
    check_true("newest row first", "ORDER BY calculation_timestamp DESC" in sql)

    params = list(getattr(bq.job_config, "query_parameters", []))
    check("one bound parameter", len(params), 1)
    check("parameter name", params[0].name, "customer_id")
    check("parameter type", params[0].type_, "STRING")
    check("parameter value", params[0].value, CUSTOMER_ID)

    # The Firestore fast path was removed on purpose: the seeder omits
    # baselineChurnRisk so the agent has to read the model output, and a cached
    # value must not be able to shadow it.
    agent, fs, bq = build_agent(
        churn_probability=0.10,
        profile={"baselineChurnRisk": 0.99, "customerSegment": "RETAIL_PRO"},
    )
    check("cached baseline ignored", agent.process_session(SESSION_ID), None)
    check("cached baseline did not gate", fs.session()["skipReason"], "LOW_CHURN_RISK")
    check("bigquery was consulted", bq.call_count, 1)

    # BigQuery down: the heuristic must still produce a usable score.
    agent, fs, bq = build_agent(
        bigquery_error=RuntimeError("bigquery unavailable"),
        profile={
            "customerSegment": "RETAIL_PRO",
            "daysSinceLastPurchase": 95,
            "supportMetrics": {"complaintsCount": 2},
        },
    )
    offer = agent.process_session(SESSION_ID)
    check_true("heuristic covers a BigQuery outage", offer is not None)
    check("heuristic evaluation source", offer["evaluationSource"], "HEURISTIC_FALLBACK")
    check("heuristic score", offer["churnProbability"], 0.75)

    # No row for the customer is the same situation as no BigQuery.
    agent, fs, bq = build_agent(churn_probability=None, profile={"daysSinceLastPurchase": 95})
    offer = agent.process_session(SESSION_ID)
    check("missing row falls back", offer["evaluationSource"], "HEURISTIC_FALLBACK")


def test_profile_flattening() -> None:
    print("\n[profile shape]")
    # The seeder writes these figures inside nested maps; the scoring code reads
    # them flat, so without flattening every one of them scored as its default.
    flat = flatten_profile({
        "customerId": CUSTOMER_ID,
        "customerSegment": "CASUAL",
        "engagement": {"cartAbandonmentCount": 3},
        "supportMetrics": {"complaintsCount": 2, "supportTicketsCount": 1},
    })
    check("nested engagement lifted", flat["cartAbandonmentCount"], 3)
    check("nested support lifted", flat["complaintsCount"], 2)
    check("top level preserved", flat["customerSegment"], "CASUAL")

    # An explicit top-level value wins over the nested copy.
    flat = flatten_profile({"complaintsCount": 9, "supportMetrics": {"complaintsCount": 2}})
    check("top level wins", flat["complaintsCount"], 9)


def test_cooldown() -> None:
    print("\n[cooldown]")
    check("configured cooldown", config.cooldown_days, 7)

    # Offer inside the window, no longer ACTIVE, cooldown not yet expired.
    agent, fs, _ = build_agent(
        churn_probability=0.90,
        offers=[make_offer("off_prev", created_days_ago=2, status="REDEEMED")],
    )
    check("cooldown suppresses the offer", agent.process_session(SESSION_ID), None)
    check("cooldown status", fs.session()["agentProcessingStatus"], "SKIPPED")
    check("cooldown reason", fs.session()["skipReason"], "COOLDOWN_ACTIVE")
    check("cooldown writes no new offer", len(fs.offers()), 1)

    cooldown_query = fs.queries[-1]
    check("cooldown query is filtered on two fields", len(cooldown_query.filters), 2)
    check("cooldown filters on customer",
          cooldown_query.filters[0].field_path, "customerId")
    check("cooldown filters on creation time",
          cooldown_query.filters[1].field_path, "createdAt")
    check("cooldown range operator", cooldown_query.filters[1].op_string, ">=")
    check("cooldown query is bounded", cooldown_query.limit_value, COOLDOWN_SCAN_LIMIT)

    # An offer still ACTIVE is surfaced rather than replaced.
    agent, fs, _ = build_agent(
        churn_probability=0.90,
        offers=[make_offer("off_active", created_days_ago=1, status="ACTIVE")],
    )
    returned = agent.process_session(SESSION_ID)
    check("active offer is returned", returned["offerId"], "off_active")
    check("active offer status", fs.session()["agentProcessingStatus"], "PROCESSED")
    check("active offer reason", fs.session()["skipReason"], "ACTIVE_OFFER_ALREADY_EXISTS")
    check("active offer linked", fs.session()["activeOfferId"], "off_active")

    # Outside the window the customer is eligible again, even if an old offer
    # was never moved out of ACTIVE. The unbounded scan used to block these
    # customers permanently.
    agent, fs, _ = build_agent(
        churn_probability=0.90,
        offers=[make_offer("off_stale", created_days_ago=30, status="ACTIVE",
                           cooldown_days_ahead=-23)],
    )
    offer = agent.process_session(SESSION_ID)
    check_true("offer outside the window does not suppress", offer is not None)
    check("new offer written", offer["offerId"], f"off_{SESSION_ID}_retention")


def test_discount_guardrails() -> None:
    print("\n[discount guardrails]")
    check("enterprise ceiling", discount_ceiling_for_segment("ENTERPRISE_VIP"), 25)
    check("retail pro ceiling", discount_ceiling_for_segment("RETAIL_PRO"), 20)
    check("standard ceiling", discount_ceiling_for_segment("STANDARD_LOYALTY"), 15)
    check("casual ceiling", discount_ceiling_for_segment("CASUAL"), 12)
    check("unknown segment falls back to default", discount_ceiling_for_segment("MYSTERY"), 15)
    check("legacy alias resolves", discount_ceiling_for_segment("PLATINUM"), 20)
    check("missing segment falls back", discount_ceiling_for_segment(None), 15)

    check("request under the ceiling is kept", apply_discount_guardrails(10, "CASUAL"), 10)
    check("request over the ceiling is clamped", apply_discount_guardrails(40, "CASUAL"), 12)
    check("ceiling applies to enterprise too", apply_discount_guardrails(40, "ENTERPRISE_VIP"), 25)
    check("garbage discount becomes zero", apply_discount_guardrails("lots", "CASUAL"), 0)

    # A discount comes off gross margin, so with an 18 percent margin and a 10
    # percent floor only 8 points can be given away.
    check("margin floor", config.margin_floor_percent, 10.0)
    check("margin floor binds below the ceiling",
          apply_discount_guardrails(25, "ENTERPRISE_VIP", gross_margin_percent=18.0), 8)
    check("ceiling still binds on a fat margin",
          apply_discount_guardrails(25, "ENTERPRISE_VIP", gross_margin_percent=60.0), 25)
    check("margin at the floor permits nothing",
          apply_discount_guardrails(25, "ENTERPRISE_VIP", gross_margin_percent=10.0), 0)
    check("margin below the floor never goes negative",
          apply_discount_guardrails(25, "ENTERPRISE_VIP", gross_margin_percent=4.0), 0)

    # End to end: the deterministic offer asks for 25 for a critical customer,
    # which a CASUAL customer's ceiling must cut back.
    agent, fs, _ = build_agent(churn_probability=0.90, profile={"customerSegment": "CASUAL"})
    offer = agent.process_session(SESSION_ID)
    check("issued discount respects the tier ceiling", offer["discountPercent"], 12)
    check("mirrored discount field agrees", offer["discountPercentage"], 12)

    agent, fs, _ = build_agent(
        churn_probability=0.90,
        profile={"customerSegment": "ENTERPRISE_VIP", "profitMargin": 0.30},
    )
    offer = agent.process_session(SESSION_ID)
    check("issued discount respects the margin floor", offer["discountPercent"], 20)


def test_offer_id_and_schema() -> None:
    print("\n[offer document]")
    agent, fs, _ = build_agent(churn_probability=0.90)
    offer = agent.process_session(SESSION_ID)

    check("derived offer id", offer["offerId"], f"off_{SESSION_ID}_retention")
    check("offer stored under its id", list(fs.offers().keys()), [f"off_{SESSION_ID}_retention"])
    check("offer carries the session", offer["sessionId"], SESSION_ID)
    check("offer carries the customer", offer["customerId"], CUSTOMER_ID)
    check("new offer is active", offer["status"], "ACTIVE")

    # The mobile client reads these names; validation is what keeps them stable.
    validated = LoyaltyOffer.model_validate(offer)
    check("offer validates against the schema", validated.offerId, offer["offerId"])
    for field in ("promoCode", "voucherCode", "discountPercent", "validUntil",
                  "cooldownUntil", "ttlExpiryAt", "createdAt"):
        check_true(f"offer has {field}", offer.get(field) is not None)

    created = datetime.fromisoformat(offer["createdAt"])
    check("validity window", (datetime.fromisoformat(offer["validUntil"]) - created).days,
          config.offer_validity_days)
    check("cooldown window", (datetime.fromisoformat(offer["cooldownUntil"]) - created).days,
          config.cooldown_days)
    check("audit ttl", (datetime.fromisoformat(offer["ttlExpiryAt"]) - created).days,
          config.offer_audit_ttl_days)

    # An id supplied by the synthesis step is honoured rather than overwritten.
    agent, fs, _ = build_agent(churn_probability=0.90)
    persisted = agent.persist_offer(
        CUSTOMER_ID, SESSION_ID,
        {"offerId": "off_supplied", "title": "t", "description": "d",
         "promoCode": "P", "discountPercent": 10, "churnRiskTier": "HIGH"},
    )
    check("supplied offer id wins", persisted["offerId"], "off_supplied")


def test_offer_synthesis() -> None:
    print("\n[offer synthesis]")
    # No Gemini client at all.
    agent, fs, _ = build_agent(churn_probability=0.90, genai_client=None)
    offer = agent.process_session(SESSION_ID)
    check("without Gemini the offer is deterministic", offer["generationSource"], "DETERMINISTIC_RULES")
    check_true("deterministic offer has copy", bool(offer["title"] and offer["description"]))
    check_true("deterministic offer has a promo code", offer["promoCode"].startswith("RETENTION-DET-"))

    # Gemini present and healthy.
    gemini = FakeGeminiClient(text=(
        '{"title": "A parting gift", "description": "Fifteen percent off your next order.",'
        ' "promoCode": "STAY15", "discountPercent": 15, "freeExpressShipping": true,'
        ' "perks": ["FREE_EXPRESS_SHIPPING"], "personalizedApology": null}'
    ))
    agent, fs, _ = build_agent(churn_probability=0.90, genai_client=gemini,
                               profile={"customerSegment": "ENTERPRISE_VIP"})
    offer = agent.process_session(SESSION_ID)
    check("Gemini path is recorded", offer["generationSource"], "GEMINI_AI")
    check("Gemini copy is used", offer["title"], "A parting gift")
    check("Gemini discount is used", offer["discountPercent"], 15)
    check("model name is passed", gemini.models.calls[0]["model"], config.reasoning_model)
    check("json response requested",
          gemini.models.calls[0]["config"]["response_mime_type"], "application/json")

    # Gemini must not be able to exceed the tier ceiling.
    gemini = FakeGeminiClient(text='{"title": "t", "description": "d", "promoCode": "P", "discountPercent": 90}')
    agent, fs, _ = build_agent(churn_probability=0.90, genai_client=gemini,
                               profile={"customerSegment": "CASUAL"})
    offer = agent.process_session(SESSION_ID)
    check("Gemini discount is clamped", offer["discountPercent"], 12)

    # Gemini failing, returning nonsense, or being the old client shape all
    # have to land on the deterministic copy rather than on no offer.
    for label, client in (
        ("Gemini error", FakeGeminiClient(error=RuntimeError("429 quota exhausted"))),
        ("Gemini non-JSON", FakeGeminiClient(text="Sorry, I cannot help with that.")),
        ("Gemini JSON that is not an object", FakeGeminiClient(text="[1, 2, 3]")),
        ("old client shape", LegacyGeminiClient()),
    ):
        agent, fs, _ = build_agent(churn_probability=0.90, genai_client=client)
        offer = agent.process_session(SESSION_ID)
        check_true(f"{label} still yields an offer", offer is not None)
        check(f"{label} falls back", offer["generationSource"], "DETERMINISTIC_RULES")


def test_session_state_machine() -> None:
    print("\n[session state machine]")
    agent, fs, _ = build_agent(churn_probability=0.90)
    offer = agent.process_session(SESSION_ID)
    session = fs.session()
    check("processed status", session["agentProcessingStatus"], "PROCESSED")
    check("offer linked", session["offerId"], offer["offerId"])
    check("active offer linked", session["activeOfferId"], offer["offerId"])
    check_true("processed timestamp written", session.get("processedAt") is not None)

    agent, fs, _ = build_agent(churn_probability=0.10)
    agent.process_session(SESSION_ID)
    check("skipped clears the offer link", fs.session()["offerId"], None)

    # A failure must be visible on the session, not just in the logs.
    agent, fs, _ = build_agent(churn_probability=0.90)
    agent.mark_session_error(SESSION_ID, "boom")
    check("error status", fs.session()["agentProcessingStatus"], "ERROR")
    check("error message", fs.session()["errorMessage"], "boom")

    # A session that does not exist, or carries no customer, is not an error.
    agent, fs, _ = build_agent(churn_probability=0.90)
    check("unknown session is ignored", agent.process_session("sess_missing"), None)

    agent, fs, _ = build_agent(churn_probability=0.90, session={"sessionId": SESSION_ID})
    check("session without a customer is ignored", agent.process_session(SESSION_ID), None)


def main() -> int:
    print("=" * 62)
    print(" Redwood loyalty agent self-test")
    print("=" * 62)

    test_churn_gate_boundaries()
    test_friction_boost()
    test_churn_lookup()
    test_profile_flattening()
    test_cooldown()
    test_discount_guardrails()
    test_offer_id_and_schema()
    test_offer_synthesis()
    test_session_state_machine()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f" {len(FAILURES)} FAILURE(S)")
        for failure in FAILURES:
            print(f"   - {failure}")
        return 1
    print(" All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

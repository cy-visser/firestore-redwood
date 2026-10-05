"""Offline checks for the loyalty offer agent."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The agent refuses to guess a project id, so give it one before importing the
# config. Nothing here connects to it.
os.environ.setdefault("GCP_PROJECT_ID", "selftest-project")

from loyalty_agent.agent import COOLDOWN_SCAN_LIMIT, LoyaltyAgent  # noqa: E402
from loyalty_agent.config import config  # noqa: E402
from loyalty_agent.policy import (  # noqa: E402
    GATE_JUDGE_UNAVAILABLE,
    GATE_JUDGED,
    GATE_NO_COMPLAINT,
    GATE_TIER_NOT_CANDIDATE,
    apply_discount_guardrails,
    discount_ceiling_for_segment,
    evaluate_churn_tier,
    flatten_profile,
    gate_note,
    latest_complaint,
)
from loyalty_agent.escalation import timing_line  # noqa: E402
from loyalty_agent.offers import persist_offer  # noqa: E402
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

    def trace(self, session_id: str = SESSION_ID) -> Dict[str, Any]:
        return self.data.get("pipeline_traces", {}).get(session_id, {})


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


class FakeEscalationJudge:
    """Stands in for the ADK judge. Records what it was asked."""

    def __init__(self, escalate: bool = False, reasoning: str = "because",
                 error: Optional[Exception] = None):
        self.escalate = escalate
        self.reasoning = reasoning
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def decide(self, churn, friction, context, spans=None):
        self.calls.append(
            {"churn": churn, "friction": friction, "context": context}
        )
        if spans is not None:
            spans["llmEscalationMs"] = 12.0
            spans["escalationOutcome"] = "FAILED" if self.error else "OK"
            spans["escalationVerdict"] = (
                "ESCALATE" if self.escalate and not self.error else "DECLINE"
            )
        if self.error:
            # Matches EscalationJudge: an unreachable judge returns no
            # sentence at all, so the caller cannot mistake an outage for a
            # verdict and print it as one.
            return {"escalate": False, "reasoning": None, "available": False}
        return {
            "escalate": self.escalate,
            "reasoning": self.reasoning,
            "available": True,
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SCORED_AT = "2026-09-21T02:00:00+00:00"


def churn_row(
    probability: float,
    tier: Optional[str] = None,
    segment: Optional[str] = None,
    spend: float = 4200.0,
    sentiment: float = 0.4,
    scored_at: str = SCORED_AT,
) -> SimpleNamespace:
    return SimpleNamespace(
        customer_id=CUSTOMER_ID,
        churn_probability=probability,
        churn_risk_tier=tier,
        customer_segment=segment,
        total_spend_90d=spend,
        sentiment_score=sentiment,
        calculation_timestamp=scored_at,
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


def make_order(
    order_id: str = "ORD-26-MOB-0001",
    rating: int = 1,
    reason: Optional[str] = "DAMAGED_SHIPMENT",
    feedback_at: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """An order document shaped like the one the mobile client writes."""
    # `is None` rather than `or`: passing "" has to survive, because an order
    # with no feedback timestamp is one of the cases under test.
    stamp = "2026-09-21T14:30:00+00:00" if feedback_at is None else feedback_at
    return {
        "orderId": order_id,
        "customerId": CUSTOMER_ID,
        "createdAt": created_at or "2026-09-21T14:30:00+00:00",

        "customerFeedback": {
            "rating": rating,
            "primaryComplaintReason": reason,
            "feedbackText": "The pallet arrived crushed.",
            "feedbackTimestamp": stamp,
        },
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
    orders: Optional[List[Dict[str, Any]]] = None,
    escalation_judge: Any = None,
    scored_at: str = SCORED_AT,
):
    """Assemble an agent over fakes, returning (agent, firestore, bigquery)."""
    # PENDING is what every producer writes, and what the bridge requires
    # before it will forward a session. Starting the fixture anywhere else
    # would test a state the agent never actually receives.
    session_doc = session if session is not None else {
        "sessionId": SESSION_ID,
        "customerId": CUSTOMER_ID,
        "loginTimestamp": datetime.now(timezone.utc).isoformat(),
        "status": "ACTIVE",
        "agentProcessingStatus": "PENDING",
    }

    fs = FakeFirestore({
        "customer_sessions": {SESSION_ID: session_doc},
        "customers": {CUSTOMER_ID: profile} if profile is not None else {},
        "loyalty_offers": {o["offerId"]: o for o in (offers or [])},
        "retail": {o["orderId"]: o for o in (orders or [])},
    })

    rows = [] if churn_probability is None else [
        churn_row(churn_probability, tier=churn_tier, segment=segment,
                  scored_at=scored_at)
    ]
    bq = FakeBigQuery(rows=rows, error=bigquery_error)

    agent = LoyaltyAgent(
        firestore_client=fs,
        bigquery_client=bq,
        genai_client=genai_client,
        project_id=PROJECT,
        dataset_id=DATASET,
        table_id=TABLE,
        # Never None in the tests: leaving it unset would let a failing case
        # reach the real ADK judge and try to call Vertex AI.
        escalation_judge=escalation_judge or FakeEscalationJudge(escalate=False),
    )
    return agent, fs, bq


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def test_churn_gate_boundaries() -> None:
    print("\n[churn gate]")
    threshold = config.churn_trigger_threshold
    check("configured threshold", threshold, 0.60)
    check("qualifying tiers", config.offer_tiers, ("HIGH", "CRITICAL"))

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

    # The boundaries are BigQuery's, from bigquery_churn_sentiment_analysis.sql.
    check("tier classifier at 0.80", evaluate_churn_tier(0.80), "CRITICAL")
    check("tier classifier at 0.60", evaluate_churn_tier(0.60), "HIGH")
    check("tier classifier at 0.40", evaluate_churn_tier(0.40), "MODERATE")
    check("tier classifier below 0.40", evaluate_churn_tier(0.39), "LOW")

    # BigQuery is the authority: where the scored row carries a tier, that tier
    # decides, even when the probability beside it would classify differently.
    # Anything else is the agent overruling the model on the model's own number.
    agent, fs, _ = build_agent(churn_probability=0.95, churn_tier="MODERATE")
    check("BigQuery tier overrides a high probability", agent.process_session(SESSION_ID), None)
    check("overridden session skips", fs.session()["skipReason"], "LOW_CHURN_RISK")

    agent, fs, _ = build_agent(churn_probability=0.10, churn_tier="CRITICAL")
    offer = agent.process_session(SESSION_ID)
    check_true("BigQuery tier overrides a low probability", offer is not None)
    check("overriding tier is recorded", offer["churnRiskTier"], "CRITICAL")


def test_complaint_detection() -> None:
    """What reaches the judge, and what the judge is told about its timing."""
    print("\n[complaint detection]")
    scored_at = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)

    fresh = latest_complaint(
        make_order(feedback_at="2026-09-21T14:30:00+00:00"), scored_at
    )
    check_true("a complaint after scoring is found", fresh is not None)
    check("the reason is carried through", fresh["reason"], "DAMAGED_SHIPMENT")
    check("the rating is carried through", fresh["rating"], 1)
    check("the model cannot have seen it", fresh["alreadyScored"], False)
    check("the gap is positive", round(fresh["gapHours"], 1), 12.5)

    # The inversion this change turns on. A complaint the model already
    # weighed used to be discarded here, silently. It is now carried to the
    # judge with the fact that makes declining the likely answer attached, so
    # the decision is made out loud instead of by omission.
    stale = latest_complaint(
        make_order(feedback_at="2026-09-20T09:00:00+00:00"), scored_at
    )
    check_true("a complaint the model saw is still found", stale is not None)
    check("it is marked as already scored", stale["alreadyScored"], True)
    check("the gap is negative", round(stale["gapHours"], 1), -17.0)

    happy = make_order(rating=5, reason=None,
                       feedback_at="2026-09-21T14:30:00+00:00")
    check("a good rating is not a complaint",
          latest_complaint(happy, scored_at), None)

    check("a three-star rating is not a complaint",
          latest_complaint(make_order(rating=3,
                                      feedback_at="2026-09-21T14:30:00+00:00"),
                           scored_at), None)

    check("no order at all", latest_complaint(None, scored_at), None)

    # A missing timestamp no longer suppresses the case; it produces a case
    # the judge is told it cannot date, which the instruction handles.
    undated = latest_complaint(make_order(feedback_at=""), scored_at)
    check_true("an undated complaint is still found", undated is not None)
    check("its timing is unknown", undated["alreadyScored"], None)
    unscored = latest_complaint(
        make_order(feedback_at="2026-09-21T14:30:00+00:00"), None
    )
    check("an unscored row leaves timing unknown",
          unscored["alreadyScored"], None)

    # The brief has to say which side of the score the complaint fell on. It
    # used to clamp the gap to zero and always print "after", which was only
    # ever true because the caller had filtered the other case out.
    check_true("the brief says AFTER when it is newer",
               "AFTER" in timing_line(12.5))
    check_true("the brief says BEFORE when it is older",
               "BEFORE" in timing_line(-17.0))
    check_true("the brief does not invent a direction",
               "unknown" in timing_line(None))

    # A re-score fired from the console lands seconds after the complaint, and
    # rounding that to "0.0 hours" hides the very thing the line exists to say.
    check_true("a gap under an hour is counted in minutes",
               "9 minutes BEFORE" in timing_line(-0.15))
    check_true("a gap over an hour stays in hours",
               "2.0 hours AFTER" in timing_line(2.0))

    # The sentences for the branches the judge never reaches.
    check_true("an excluded tier explains itself",
               "allow-list" in gate_note(GATE_TIER_NOT_CANDIDATE, "LOW"))
    check_true("an unreachable judge explains itself",
               "could not be reached" in gate_note(GATE_JUDGE_UNAVAILABLE))
    check("a customer who did not complain gets no sentence",
          gate_note(GATE_NO_COMPLAINT), None)


def test_escalation_judgement() -> None:
    print("\n[escalation judgement]")
    fresh_order = [make_order(feedback_at="2026-09-21T14:30:00+00:00")]
    profile = {"customerSegment": "ENTERPRISE_VIP"}

    # No complaint: the judge must not be consulted, the customer is skipped
    # for the ordinary reason, and nothing is narrated. A sentence on every
    # uneventful login is a sentence nobody reads on the login that matters.
    judge = FakeEscalationJudge(escalate=True)
    agent, fs, _ = build_agent(churn_probability=0.45, profile=profile,
                               escalation_judge=judge)
    check("no complaint issues no offer", agent.process_session(SESSION_ID), None)
    check("no complaint skips as low risk",
          fs.session()["skipReason"], "LOW_CHURN_RISK")
    check("no complaint does not call the judge", len(judge.calls), 0)
    check("the branch is named", fs.session()["escalationGate"],
          GATE_NO_COMPLAINT)
    check("and nothing is narrated", fs.session().get("skipDetail"), None)
    check("the trace carries the branch too",
          fs.trace()["escalationGate"], GATE_NO_COMPLAINT)

    # A complaint the model has already scored. This used to be dropped before
    # the judge saw it, which made it indistinguishable from the case above.
    # It now reaches the judge, carrying the timing, and the judge decides.
    judge = FakeEscalationJudge(
        escalate=False,
        reasoning="The complaint was already in front of the model when it "
                  "scored this customer LOW.",
    )
    agent, fs, _ = build_agent(
        churn_probability=0.45, profile=profile, escalation_judge=judge,
        orders=[make_order(feedback_at="2026-09-20T09:00:00+00:00")],
    )
    agent.process_session(SESSION_ID)
    check("a complaint the model saw still calls the judge", len(judge.calls), 1)
    check("the judge is told the model saw it",
          judge.calls[0]["friction"]["alreadyScored"], True)
    check("the outcome is a judgement, not a gate",
          fs.session()["skipReason"], "ESCALATION_DECLINED")
    check_true("and it is explained",
               "already in front of the model" in fs.session()["skipDetail"])

    # Fresh complaint, judge declines. This is the loyal-customer case: one bad
    # day should not buy a discount.
    judge = FakeEscalationJudge(
        escalate=False,
        reasoning="A single complaint from a customer with a three-year "
                  "spotless record does not indicate churn.",
    )
    agent, fs, _ = build_agent(churn_probability=0.20, profile=profile,
                               orders=fresh_order, escalation_judge=judge)
    check("a declined escalation issues no offer",
          agent.process_session(SESSION_ID), None)
    check("a declined escalation says so",
          fs.session()["skipReason"], "ESCALATION_DECLINED")
    check_true("the reasoning is recorded on the session",
               "spotless" in fs.session()["skipDetail"])
    check("the branch is named", fs.session()["escalationGate"], GATE_JUDGED)
    check("the judge was consulted once", len(judge.calls), 1)
    check("the judge is told the model could not have seen it",
          judge.calls[0]["friction"]["alreadyScored"], False)

    # Fresh complaint, judge escalates.
    judge = FakeEscalationJudge(
        escalate=True,
        reasoning="A damaged shipment on top of a falling order rate.",
    )
    agent, fs, _ = build_agent(churn_probability=0.45, profile=profile,
                               orders=fresh_order, escalation_judge=judge)
    offer = agent.process_session(SESSION_ID)
    check_true("an escalation issues an offer", offer is not None)

    # The load-bearing assertion of this whole change: the judge moved the
    # customer onto the offer path without editing the model's number.
    check("BigQuery's probability is untouched", offer["churnProbability"], 0.45)
    check("the model's tier is recorded as the model's",
          offer["churnRiskTier"], "MODERATE")
    check("the agent records what it acted on", offer["eligibilityTier"], "HIGH")
    check("the offer is flagged as escalated", offer["escalated"], True)
    check_true("the reasoning is on the offer",
               "damaged shipment" in offer["escalationReason"].lower())
    check("the evidence names the order",
          offer["escalationTrigger"]["orderId"], "ORD-26-MOB-0001")
    check("the evidence keeps both timestamps apart",
          offer["escalationTrigger"]["scoredAt"] <
          offer["escalationTrigger"]["submittedAt"], True)
    check("the evidence records what the judge was asked to weigh",
          offer["escalationTrigger"]["alreadyScored"], False)

    # An escalated customer is not a privileged one. Every guardrail still runs.
    check_true("the ceiling still applies", offer["discountPercent"] <= 25)

    # A judge that cannot be reached must not escalate, and must not be
    # reported as having declined: an outage is not a judgement.
    judge = FakeEscalationJudge(escalate=True, error=RuntimeError("no quota"))
    agent, fs, _ = build_agent(churn_probability=0.45, profile=profile,
                               orders=fresh_order, escalation_judge=judge)
    check("a broken judge issues no offer",
          agent.process_session(SESSION_ID), None)
    check("a broken judge is not a decline",
          fs.session()["skipReason"], "LOW_CHURN_RISK")
    check("the branch says the judge was unreachable",
          fs.session()["escalationGate"], GATE_JUDGE_UNAVAILABLE)
    check_true("and says so in words",
               "could not be reached" in fs.session()["skipDetail"])

    # Tiers outside the allow-list are never escalated, whatever the judge says.
    original = config.escalation_candidate_tiers
    try:
        object.__setattr__(config, "escalation_candidate_tiers", ("MODERATE",))
        judge = FakeEscalationJudge(escalate=True)
        agent, fs, _ = build_agent(churn_probability=0.10, profile=profile,
                                   orders=fresh_order, escalation_judge=judge)
        check("a LOW customer is not judged when the allow-list excludes it",
              agent.process_session(SESSION_ID), None)
        check("and the judge was never asked", len(judge.calls), 0)
        check("the branch names the allow-list",
              fs.session()["escalationGate"], GATE_TIER_NOT_CANDIDATE)
        check_true("and the exclusion is explained",
                   "allow-list" in fs.session()["skipDetail"])
    finally:
        object.__setattr__(config, "escalation_candidate_tiers", original)


def test_score_is_bigquerys_alone() -> None:
    """Regression guard: nothing may adjust the probability BigQuery produced.

    An earlier agent added 0.25 whenever the profile carried an acute
    complaint, which double counted a signal the model already trains on and
    saturated the demo customer to a certainty of 1.0.
    """
    print("\n[the score is BigQuery's]")
    for complaint in ("LATE_DELIVERY", "BILLING_DISPUTE", "REFUND_REQUESTED"):
        agent, _, _ = build_agent(
            churn_probability=0.8856,
            profile={
                "customerSegment": "ENTERPRISE_VIP",
                "primaryComplaintReason": complaint,
                "recentFrictionEvent": complaint,
            },
        )
        offer = agent.process_session(SESSION_ID)
        check(f"{complaint} does not move the score",
              offer["churnProbability"], 0.8856)
        check(f"{complaint} does not move the tier",
              offer["churnRiskTier"], "CRITICAL")
        check(f"{complaint} is not treated as an escalation",
              offer["escalated"], False)


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

    # BigQuery down. The agent stops rather than substituting a score: every
    # signal a local heuristic could use is already a feature of the model, so
    # a fallback would be a hand-weighted guess at the number it replaces --
    # and this one spends margin.
    agent, fs, bq = build_agent(
        bigquery_error=RuntimeError("bigquery unavailable"),
        profile={
            "customerSegment": "RETAIL_PRO",
            "daysSinceLastPurchase": 95,
            "supportMetrics": {"complaintsCount": 2},
        },
    )
    check("a BigQuery outage issues no offer",
          agent.process_session(SESSION_ID), None)
    check("the outage is named on the session",
          fs.session()["skipReason"], "CHURN_LOOKUP_FAILED")
    check("no offer is invented", len(fs.offers()), 0)
    check_true("the outage reaches the console",
               fs.trace()["agentOutcome"] == "CHURN_LOOKUP_FAILED")

    # An unscored customer is a different situation from an outage, and is
    # reported as one.
    agent, fs, bq = build_agent(churn_probability=None,
                                profile={"daysSinceLastPurchase": 95})
    check("an unscored customer issues no offer",
          agent.process_session(SESSION_ID), None)
    check("an unscored customer is named as such",
          fs.session()["skipReason"], "CUSTOMER_NOT_SCORED")


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

    # The case the cooldown still covers: an offer the customer never spent,
    # inside the window, whose cooldown has not expired. EXPIRED rather than
    # REDEEMED is the point -- we offered, they ignored it, so offering again
    # immediately would just be discounting at someone who is not listening.
    agent, fs, _ = build_agent(
        churn_probability=0.90,
        offers=[make_offer("off_prev", created_days_ago=2, status="EXPIRED")],
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


def test_followup_offers() -> None:
    """A customer who spends an offer and stays at risk gets one more.

    This is the demo's second beat. Before this, the redeemed offer's
    cooldownUntil suppressed every subsequent login with COOLDOWN_ACTIVE, so
    cust_demo2 could place a discounted order, remain HIGH on the recalculated
    score, and still never see another offer.
    """
    print("\n[follow-up offers]")
    check("configured cap", config.max_followup_offers, 1)
    check("configured step down", config.followup_step_down_percent, 5)

    # One redeemed offer, still at risk: a follow-up is issued, stepped down
    # from the 15% STANDARD_LOYALTY ceiling to 10%.
    agent, fs, _ = build_agent(
        churn_probability=0.90,
        offers=[make_offer("off_first", created_days_ago=1, status="REDEEMED")],
    )
    offer = agent.process_session(SESSION_ID)
    check_true("redeemed offer does not suppress the next", offer is not None)
    check("follow-up status", fs.session()["agentProcessingStatus"], "PROCESSED")
    check("follow-up clears the skip reason", fs.session()["skipReason"], None)
    check("follow-up steps the discount down", offer["discountPercent"], 10)
    check("follow-up ceiling recorded", offer["discountCeilingApplied"], 10)
    check("follow-up is second in sequence", offer["offerSequence"], 2)
    check("follow-up names what it supersedes", offer["supersedesOfferId"], "off_first")
    check("follow-up is written", len(fs.offers()), 2)

    # Two redeemed offers is past the cap, so the sequence stops. The customer
    # is still CRITICAL -- this is the guardrail, not the churn gate.
    agent, fs, _ = build_agent(
        churn_probability=0.90,
        offers=[
            make_offer("off_first", created_days_ago=3, status="REDEEMED"),
            make_offer("off_second", created_days_ago=1, status="REDEEMED"),
        ],
    )
    check("cap suppresses the third offer", agent.process_session(SESSION_ID), None)
    check("cap status", fs.session()["agentProcessingStatus"], "SKIPPED")
    check("cap reason", fs.session()["skipReason"], "FOLLOW_UP_LIMIT_REACHED")
    check("cap writes no new offer", len(fs.offers()), 2)

    # Past the cap *and* recovered: the demo's closing beat. Either gate would
    # skip, and the session says the customer recovered rather than that the
    # guardrail fired.
    agent, fs, _ = build_agent(
        churn_probability=0.30,
        offers=[
            make_offer("off_first", created_days_ago=3, status="REDEEMED"),
            make_offer("off_second", created_days_ago=1, status="REDEEMED"),
        ],
    )
    check("recovered and capped issues nothing", agent.process_session(SESSION_ID), None)
    check("recovered and capped reason", fs.session()["skipReason"], "LOW_CHURN_RISK")
    check("recovered and capped writes no offer", len(fs.offers()), 2)

    # A redeemed offer alongside an unspent ACTIVE one: the ACTIVE offer still
    # wins, because the customer has something to spend already.
    agent, fs, _ = build_agent(
        churn_probability=0.90,
        offers=[
            make_offer("off_first", created_days_ago=3, status="REDEEMED"),
            make_offer("off_active", created_days_ago=1, status="ACTIVE"),
        ],
    )
    returned = agent.process_session(SESSION_ID)
    check("unspent offer still takes precedence", returned["offerId"], "off_active")
    check("unspent offer reason", fs.session()["skipReason"],
          "ACTIVE_OFFER_ALREADY_EXISTS")

    # The step-down cannot walk a discount below the floor.
    check("step down respects the floor",
          apply_discount_guardrails(25, "ENTERPRISE_VIP", step_down_percent=40),
          config.min_followup_discount_percent)
    check("no step down leaves the ceiling alone",
          apply_discount_guardrails(25, "ENTERPRISE_VIP"), 25)


def test_llm_reasoning_span() -> None:
    """The Offer node's number is the model call, not the whole synthesis.

    offerGenerationMs covers the deterministic fallback, the JSON parse and
    the guardrail clamp as well, so it is not what "LLM reasoning" means.
    """
    print("\n[llm reasoning span]")

    agent, fs, _ = build_agent(
        churn_probability=0.90,
        genai_client=FakeGeminiClient(text=json.dumps({
            "title": "We would like you to stay",
            "description": "A thank you for your business.",
            "promoCode": "STAY15",
            "discountPercent": 15,
            "freeExpressShipping": True,
            "perks": ["FREE_EXPRESS_SHIPPING"],
            "personalizedApology": None,
        })),
    )
    offer = agent.process_session(SESSION_ID)
    check_true("offer issued", offer is not None)

    trace = fs.trace()
    check_true("reasoning span recorded", trace.get("llmReasoningMs") is not None)
    check("reasoning outcome", trace.get("llmOutcome"), "OK")
    check_true("reasoning model recorded", bool(trace.get("llmModel")))
    check_true(
        "reasoning is contained within the generation span",
        trace["llmReasoningMs"] <= trace["offerGenerationMs"] + 1.0,
    )

    # A model failure still produces a span, so the console can show that the
    # copy came from the rules rather than leaving the node blank.
    class ExplodingModels:
        def generate_content(self, **_kwargs):
            raise RuntimeError("quota exhausted")

    class ExplodingClient:
        models = ExplodingModels()

    agent, fs, _ = build_agent(
        churn_probability=0.90,
        genai_client=ExplodingClient(),
    )
    offer = agent.process_session(SESSION_ID)
    check("fallback still issues an offer", offer["discountPercent"], 15)
    trace = fs.trace()
    check("failed reasoning is recorded", trace.get("llmOutcome"), "FAILED")
    check_true("failed reasoning still timed",
               trace.get("llmReasoningMs") is not None)


def test_trace_document() -> None:
    """Traces carry a kind, because cdc_service shares the collection."""
    print("\n[trace document]")
    agent, fs, _ = build_agent(churn_probability=0.90)
    agent.process_session(SESSION_ID)

    trace = fs.trace()
    check("trace kind", trace.get("kind"), "SESSION")
    check("trace names the session", trace.get("sessionId"), SESSION_ID)
    check("trace names the customer", trace.get("customerId"), CUSTOMER_ID)
    check("trace records the outcome", trace.get("agentOutcome"), "OFFER_ISSUED")
    check_true("claim span recorded", trace.get("agentClaimMs") is not None)
    check_true("churn lookup span recorded", trace.get("churnLookupMs") is not None)
    check_true("offer write span recorded", trace.get("offerWriteMs") is not None)


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
    for field in ("promoCode", "discountPercent", "validUntil",
                  "cooldownUntil", "ttlExpiryAt", "createdAt"):
        check_true(f"offer has {field}", offer.get(field) is not None)

    # Eleven mirrored aliases used to be written beside these. Nothing read
    # them, and two spellings of one value is how they drift apart.
    for alias in ("discountPercentage", "voucherCode", "expiresAt", "headline",
                  "messageBody", "churnScore", "churnTier", "baselineChurnRisk"):
        check(f"{alias} is not written", alias in offer, False)

    # The model's tier and the tier the agent acted on are separate fields.
    # Without an escalation they agree, and that has to be the quiet default.
    check("model tier recorded", offer["churnRiskTier"], "CRITICAL")
    check("eligibility tier recorded", offer["eligibilityTier"], "CRITICAL")
    check("no escalation by default", offer["escalated"], False)
    check("no escalation reason by default", offer["escalationReason"], None)

    created = datetime.fromisoformat(offer["createdAt"])
    check("validity window", (datetime.fromisoformat(offer["validUntil"]) - created).days,
          config.offer_validity_days)
    check("cooldown window", (datetime.fromisoformat(offer["cooldownUntil"]) - created).days,
          config.cooldown_days)
    # Firestore only expires timestamp fields, so this one must survive
    # validation as a datetime and not be flattened back into a string.
    check_true("audit ttl is a timestamp", isinstance(offer["ttlExpiryAt"], datetime))
    check("audit ttl", (offer["ttlExpiryAt"] - created).days,
          config.offer_audit_ttl_days)

    # An id supplied by the synthesis step is honoured rather than overwritten.
    agent, fs, _ = build_agent(churn_probability=0.90)
    persisted = persist_offer(
        fs, CUSTOMER_ID, SESSION_ID,
        {"offerId": "off_supplied", "title": "t", "description": "d",
         "promoCode": "P", "discountPercent": 10, "churnRiskTier": "HIGH"},
    )
    check("supplied offer id wins", persisted["offerId"], "off_supplied")


def test_offer_synthesis() -> None:
    print("\n[offer synthesis]")
    # No Gemini client at all.
    #
    # generationSource used to be asserted throughout this function. It is no
    # longer on the document, so each path is identified by the copy it
    # produces -- which is the thing that actually differs, and the thing a
    # regression would break.
    agent, fs, _ = build_agent(churn_probability=0.90, genai_client=None)
    offer = agent.process_session(SESSION_ID)
    check("without Gemini the offer is the deterministic one",
          offer["title"], "Special Customer Loyalty Incentive")
    check_true("deterministic offer has copy", bool(offer["title"] and offer["description"]))
    check_true("deterministic offer has a promo code", offer["promoCode"].startswith("RETENTION-DET-"))

    # Neither provenance field may reach the document the phone reads.
    check("generationSource is not persisted", "generationSource" in offer, False)
    check("evaluationSource is not persisted", "evaluationSource" in offer, False)
    check("evaluationSource is not persisted in metadata",
          "evaluationSource" in offer.get("metadata", {}), False)

    # Gemini present and healthy.
    gemini = FakeGeminiClient(text=(
        '{"title": "A parting gift", "description": "Fifteen percent off your next order.",'
        ' "promoCode": "STAY15", "discountPercent": 15, "freeExpressShipping": true,'
        ' "perks": ["FREE_EXPRESS_SHIPPING"], "personalizedApology": null}'
    ))
    agent, fs, _ = build_agent(churn_probability=0.90, genai_client=gemini,
                               profile={"customerSegment": "ENTERPRISE_VIP"})
    offer = agent.process_session(SESSION_ID)
    check("Gemini copy is used", offer["title"], "A parting gift")
    check("Gemini promo code is used", offer["promoCode"], "STAY15")
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
        check_true(f"{label} falls back to the deterministic copy",
                   offer["promoCode"].startswith("RETENTION-DET-"))


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


def test_session_claim() -> None:
    print("\n[session claim]")

    # The claim has to be visible on the document, because it is what the
    # operator console uses to separate event delivery from agent work.
    agent, fs, _ = build_agent(churn_probability=0.90)
    agent.process_session(SESSION_ID)
    session = fs.session()
    check_true("claim records a start time", session.get("processingStartedAt") is not None)
    check_true("claim records a worker", session.get("agentWorkerId") is not None)

    # The case the claim exists for. Eventarc is at-least-once, so the same
    # session can arrive twice; the second delivery must not re-evaluate it.
    agent, fs, _ = build_agent(churn_probability=0.90)
    first = agent.process_session(SESSION_ID)
    check_true("first delivery issues an offer", first is not None)
    second = agent.process_session(SESSION_ID)
    check("redelivery is ignored", second, None)
    check("session keeps its terminal status", fs.session()["agentProcessingStatus"], "PROCESSED")
    check("no second offer written", len(fs.offers()), 1)

    # A session another worker is mid-way through must be left alone rather
    # than raced.
    agent, fs, _ = build_agent(
        churn_probability=0.90,
        session={
            "sessionId": SESSION_ID,
            "customerId": CUSTOMER_ID,
            "agentProcessingStatus": "PROCESSING",
        },
    )
    check("in-flight session is left alone", agent.process_session(SESSION_ID), None)

    # Claiming a session the agent then cannot act on must still release it,
    # or it sits in PROCESSING forever with nothing scheduled to revisit it.
    agent, fs, _ = build_agent(churn_probability=0.90, session={"sessionId": SESSION_ID})
    agent.process_session(SESSION_ID)
    check("unusable session is released", fs.session()["agentProcessingStatus"], "SKIPPED")
    check("release records why", fs.session()["skipReason"], "NO_CUSTOMER_ID")


def main() -> int:
    print("=" * 62)
    print(" Redwood loyalty agent self-test")
    print("=" * 62)

    test_churn_gate_boundaries()
    test_complaint_detection()
    test_escalation_judgement()
    test_score_is_bigquerys_alone()
    test_churn_lookup()
    test_profile_flattening()
    test_cooldown()
    test_followup_offers()
    test_llm_reasoning_span()
    test_trace_document()
    test_discount_guardrails()
    test_offer_id_and_schema()
    test_offer_synthesis()
    test_session_state_machine()
    test_session_claim()

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

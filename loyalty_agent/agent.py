from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import bigquery
from google.cloud.firestore_v1.base_query import FieldFilter

from loyalty_agent import offers, policy
from loyalty_agent.config import config
from loyalty_agent.policy import evaluate_churn_tier  # re-exported

logger = logging.getLogger("loyalty_agent.agent")

SESSIONS_COLLECTION = "customer_sessions"
CUSTOMERS_COLLECTION = "customers"
OFFERS_COLLECTION = "loyalty_offers"
ORDERS_COLLECTION = "retail"

# Firestore collection for latency traces.
TRACES_COLLECTION = "pipeline_traces"
TRACE_TTL_DAYS = 30

# Upper bound on offers scanned for cooldown evaluation.
COOLDOWN_SCAN_LIMIT = 10

_CLAIMABLE_STATUSES = (None, "", "PENDING")


class LoyaltyAgent:
    """Evaluates a customer login session and issues a retention offer."""

    def __init__(
        self,
        firestore_client: Any = None,
        bigquery_client: Any = None,
        genai_client: Any = None,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        table_id: Optional[str] = None,
        model_name: Optional[str] = None,
        cooldown_days: Optional[int] = None,
        orders_collection: Optional[str] = None,
        escalation_judge: Any = None,
    ):
        self.fs = firestore_client
        self.bq = bigquery_client
        self.genai = genai_client

        self.project_id = project_id or config.project_id
        self.dataset_id = dataset_id or config.bigquery_dataset
        self.table_id = table_id or config.churn_predictions_table
        self.model_name = model_name or config.reasoning_model
        self.orders_collection = orders_collection or ORDERS_COLLECTION

        self.cooldown_days = (
            cooldown_days if cooldown_days is not None else config.cooldown_days
        )
        self.offer_tiers = tuple(t.upper() for t in config.offer_tiers)
        self.offer_validity_days = config.offer_validity_days
        self.audit_ttl_days = config.offer_audit_ttl_days

        # Injected by the self-test; built lazily in production so that a
        # deployment which never escalates never imports ADK.
        self._escalation_judge = escalation_judge

        self.worker_id = f"agent-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def process_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Run the retention evaluation for one session."""
        if not self.fs:
            logger.error("Firestore client is not configured on LoyaltyAgent.")
            return None

        sess_ref = self.fs.collection(SESSIONS_COLLECTION).document(session_id)
        now = datetime.now(timezone.utc)
        spans: Dict[str, Any] = {}

        claim_started = time.monotonic()
        claimed, sess_data = self._claim_session(sess_ref, session_id, now)
        spans["agentClaimMs"] = (time.monotonic() - claim_started) * 1000.0
        if not claimed:
            return None

        customer_id = sess_data.get("customerId")
        if not customer_id:
            logger.warning("Session %s has no customerId.", session_id)
            self._close(sess_ref, session_id, None, spans, now, "NO_CUSTOMER_ID")
            return None

        lookup_started = time.monotonic()
        profile = self._load_profile(customer_id)
        churn = self._lookup_churn(customer_id)
        cooldown = self._check_cooldown(customer_id, now)
        spans["churnLookupMs"] = (time.monotonic() - lookup_started) * 1000.0

        if churn["status"] != "OK":
            reason = (
                "CHURN_LOOKUP_FAILED" if churn["status"] == "LOOKUP_FAILED"
                else "CUSTOMER_NOT_SCORED"
            )
            self._close(sess_ref, session_id, customer_id, spans, now, reason)
            return None

        context = policy.resolve_customer_context(customer_id, profile, churn)

        if cooldown.get("hasActiveOffer"):
            active_offer = cooldown["activeOffer"]
            self._close(
                sess_ref, session_id, customer_id, spans, now,
                "ACTIVE_OFFER_ALREADY_EXISTS",
                offer_id=active_offer.get("offerId"), status="PROCESSED",
            )
            return active_offer

        if cooldown.get("inCooldown"):
            self._close(
                sess_ref, session_id, customer_id, spans, now, "COOLDOWN_ACTIVE"
            )
            return None

        redeemed_count = int(cooldown.get("redeemedCount") or 0)
        if redeemed_count > int(config.max_followup_offers):
            logger.info(
                "Session %s: %s has redeemed %d offer(s) in the %d-day window; "
                "the cap is %d follow-up(s).",
                session_id, customer_id, redeemed_count, self.cooldown_days,
                config.max_followup_offers,
            )
            # Both gates say skip, but they say different things. A customer
            # who has spent her offers *and* recovered should read as
            # recovered -- that is the demo's closing beat -- so the churn
            # reason wins when the model no longer rates her an offer tier.
            # The judge is not consulted: the cap forbids an offer either way.
            capped_tier = str(churn.get("churnTier") or "").upper()
            if capped_tier not in self.offer_tiers:
                self._close(
                    sess_ref, session_id, customer_id, spans, now,
                    "LOW_CHURN_RISK",
                    detail=(
                        f"Model rates {customer_id} {capped_tier or 'UNSCORED'}; "
                        f"the follow-up cap ({config.max_followup_offers}) is "
                        f"also reached."
                    ),
                )
                return None
            self._close(
                sess_ref, session_id, customer_id, spans, now,
                "FOLLOW_UP_LIMIT_REACHED",
            )
            return None

        churn_prob = float(churn["churnProbability"])
        model_tier = str(churn["churnTier"]).upper()
        eligibility_tier = model_tier
        escalation: Optional[Dict[str, Any]] = None
        friction: Optional[Dict[str, Any]] = None

        if model_tier not in self.offer_tiers:
            friction, escalation, gate = self._consider_escalation(
                customer_id, churn, context, model_tier, spans
            )
            if not (escalation and escalation.get("escalate")):
                judged = gate == policy.GATE_JUDGED
                reason = "ESCALATION_DECLINED" if judged else "LOW_CHURN_RISK"
                logger.info(
                    "Session %s: %s is %s (p=%.4f); %s qualify. %s",
                    session_id, customer_id, model_tier or "UNSCORED", churn_prob,
                    "/".join(self.offer_tiers), reason,
                )
                self._close(
                    sess_ref, session_id, customer_id, spans, now, reason,
                    # The judge's own sentence when it reached a verdict, the
                    # gate's when it never ran, and nothing at all when the
                    # customer simply has not complained. A skip that cannot
                    # say which of those happened is the bug this replaces.
                    detail=(escalation or {}).get("reasoning")
                    or policy.gate_note(gate, model_tier),
                    gate=gate,
                )
                return None

            # Promotion only. The judge can move a customer onto the offer
            # path; it cannot set the discount, and it never touches the
            # probability BigQuery produced.
            eligibility_tier = "HIGH"
            spans["escalationGate"] = gate
            logger.info(
                "Session %s: %s escalated from %s on a complaint -- %s",
                session_id, customer_id, model_tier, escalation["reasoning"],
            )

        step_down = redeemed_count * int(config.followup_step_down_percent)

        generation_started = time.monotonic()
        offer_payload = offers.synthesize_offer(
            genai_client=self.genai,
            model_name=self.model_name,
            customer_id=customer_id,
            churn_probability=churn_prob,
            churn_tier=eligibility_tier,
            complaint=(friction or {}).get("reason")
            or context["primaryComplaintReason"],
            segment=context["customerSegment"],
            gross_margin_percent=context["grossMarginPercent"],
            step_down_percent=step_down,
            spans=spans,
        )
        spans["offerGenerationMs"] = (time.monotonic() - generation_started) * 1000.0

        offer_payload.update({
            "churnProbability": churn_prob,
            "churnRiskTier": model_tier,
            "eligibilityTier": eligibility_tier,
            "escalated": escalation is not None and escalation.get("escalate", False),
            "escalationReason": (escalation or {}).get("reasoning"),
            "escalationTrigger": _trigger_summary(friction) if escalation else None,
            "historicalSpend90d": context["totalSpend90d"],
            "sentimentScore": context["sentimentScore"],
            "offerSequence": redeemed_count + 1,
            "supersedesOfferId": (
                cooldown.get("lastRedeemedOffer") or {}
            ).get("offerId"),
        })

        write_started = time.monotonic()
        offer = offers.persist_offer(
            self.fs, customer_id, session_id, offer_payload, now,
            offer_validity_days=self.offer_validity_days,
            cooldown_days=self.cooldown_days,
            audit_ttl_days=self.audit_ttl_days,
        )
        spans["offerWriteMs"] = (time.monotonic() - write_started) * 1000.0

        logger.info(
            "Session %s: Offer issued and persisted (%s).",
            session_id, offer.get("offerId"),
        )
        self._write_trace(session_id, customer_id, spans, "OFFER_ISSUED")
        return offer

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _claim_session(
        self, sess_ref: Any, session_id: str, now: datetime
    ) -> Tuple[bool, Dict[str, Any]]:
        """Atomically claim a session from PENDING to PROCESSING."""
        claim = {
            "agentProcessingStatus": "PROCESSING",
            "processingStartedAt": now.isoformat(),
            "agentWorkerId": self.worker_id,
        }

        def claimable(snap: Any) -> Optional[Dict[str, Any]]:
            """The session data if it is ours to take, else None."""
            if not snap.exists:
                logger.warning("Session %s not found in Firestore.", session_id)
                return None
            data = snap.to_dict() or {}
            status = data.get("agentProcessingStatus")
            if status not in _CLAIMABLE_STATUSES:
                logger.info(
                    "Session %s is already %s; leaving it alone.",
                    session_id, status,
                )
                return None
            return data

        transaction_factory = getattr(self.fs, "transaction", None)
        if transaction_factory is None:
            data = claimable(sess_ref.get())
            if data is None:
                return False, {}
            sess_ref.update(claim)
            return True, {**data, "agentProcessingStatus": "PROCESSING"}

        from google.cloud import firestore as _firestore

        claimed: Dict[str, Any] = {}

        @_firestore.transactional
        def _claim(txn) -> bool:
            data = claimable(sess_ref.get(transaction=txn))
            if data is None:
                claimed.clear()
                claimed.update(data or {})
                return False
            claimed.update(data)
            txn.update(sess_ref, claim)
            return True

        if not _claim(transaction_factory()):
            return False, claimed

        claimed["agentProcessingStatus"] = "PROCESSING"
        return True, claimed

    def _close(
        self,
        sess_ref: Any,
        session_id: str,
        customer_id: Optional[str],
        spans: Dict[str, Any],
        now: datetime,
        reason: str,
        offer_id: Optional[str] = None,
        status: str = "SKIPPED",
        detail: Optional[str] = None,
        gate: Optional[str] = None,
    ) -> None:
        """Close a session without issuing, and record why.

        Every exit from ``process_session`` that is not an offer comes through
        here, so the session document, the log line and the console trace can
        never disagree about the reason.

        ``detail`` is the sentence a human reads and ``gate`` is the branch a
        machine reads. Both are written where they exist, because the reason
        code alone cannot distinguish a customer who never complained from one
        whose complaint a judge weighed and dismissed.
        """
        update = {
            "agentProcessingStatus": status,
            "status": "PROCESSED",
            "offerId": offer_id,
            "activeOfferId": offer_id,
            "skipReason": reason,
            "processedAt": now.isoformat(),
        }
        if detail:
            update["skipDetail"] = detail
        if gate:
            update["escalationGate"] = gate
            spans["escalationGate"] = gate
        sess_ref.update(update)
        logger.info("Session %s -> %s (%s).", session_id, reason, customer_id)
        self._write_trace(session_id, customer_id, spans, reason, detail=detail)

    def mark_session_error(self, session_id: str, message: str) -> None:
        """Record a processing failure on the session."""
        if not self.fs:
            return
        try:
            self.fs.collection(SESSIONS_COLLECTION).document(session_id).update({
                "agentProcessingStatus": "ERROR",
                "errorMessage": message,
                "processedAt": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not mark session %s as ERROR: %s", session_id, exc)

    # ------------------------------------------------------------------
    # Tracing
    # ------------------------------------------------------------------

    def _write_trace(
        self,
        session_id: str,
        customer_id: Optional[str],
        spans: Dict[str, Any],
        outcome: str,
        detail: Optional[str] = None,
    ) -> None:
        """Merge this run's spans into ``pipeline_traces/{session_id}``.

        The bridge writes the two spans it alone can see (Eventarc delivery
        and the Agent Engine round trip) to the same document, so this merges
        rather than sets.

        Failures are logged and swallowed. A trace is an observation of the
        run, and an observation that can fail the run it is observing is worse
        than no observation: the bridge would see a 500, Eventarc would
        redeliver, and the agent would be invoked again.
        """
        if not self.fs:
            return

        try:
            now = datetime.now(timezone.utc)
            document: Dict[str, Any] = {
                # cdc_service writes order traces into this same collection.
                # The console needs to tell the two apart to know which node
                # each document lights up.
                "kind": "SESSION",
                "sessionId": session_id,
                "customerId": customer_id,
                "agentOutcome": outcome,
                "agentDetail": detail,
                "agentRecordedAt": now,
                "recordedAt": now,
                "expireAt": now + timedelta(days=TRACE_TTL_DAYS),
            }
            # spans carries model names and outcomes as well as durations, so
            # only the numbers are rounded.
            document.update({
                key: round(value, 2) if isinstance(value, (int, float)) else value
                for key, value in spans.items()
            })

            self.fs.collection(TRACES_COLLECTION).document(session_id).set(
                document, merge=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write trace for session %s: %s", session_id, exc)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _load_profile(self, customer_id: str) -> Dict[str, Any]:
        """Read /customers/{id}, flattened, or an empty dict if absent."""
        if not self.fs:
            return {}
        try:
            snap = self.fs.collection(CUSTOMERS_COLLECTION).document(customer_id).get()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Customer profile read failed for %s: %s", customer_id, exc)
            return {}
        if not snap.exists:
            return {}
        return policy.flatten_profile(snap.to_dict() or {})

    def _lookup_churn(self, customer_id: str) -> Dict[str, Any]:
        """Read this customer's score from the BigQuery model output.

        Always returns a dict carrying a ``status``. ``LOOKUP_FAILED`` and
        ``NOT_SCORED`` are kept apart because they mean different things: the
        first is an outage worth alerting on, the second is a customer the
        nightly run has never seen. Neither produces a substitute score.
        """
        if not self.bq:
            return {"status": "LOOKUP_FAILED"}

        sql = (
            "SELECT customer_id, churn_probability, churn_risk_tier, "
            "customer_segment, total_spend_90d, sentiment_score, "
            "calculation_timestamp\n"
            f"FROM `{self.project_id}.{self.dataset_id}.{self.table_id}`\n"
            "WHERE customer_id = @customer_id\n"
            "ORDER BY calculation_timestamp DESC\n"
            "LIMIT 1"
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("customer_id", "STRING", customer_id)
            ]
        )

        try:
            rows = list(self.bq.query(sql, job_config=job_config).result())
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "BigQuery churn lookup failed for %s: %s. No offer will be "
                "issued; the agent does not substitute its own score.",
                customer_id, exc,
            )
            return {"status": "LOOKUP_FAILED"}

        if not rows:
            logger.info("No churn score on file for %s.", customer_id)
            return {"status": "NOT_SCORED"}

        row = rows[0]
        prob = _row_value(row, "churn_probability")
        if prob is None:
            return {"status": "NOT_SCORED"}

        prob = float(prob)
        tier = _row_value(row, "churn_risk_tier")
        return {
            "status": "OK",
            "customerId": customer_id,
            "churnProbability": prob,
            "churnTier": str(tier).upper() if tier else evaluate_churn_tier(prob),
            # When the score was calculated. The escalation path compares
            # friction against this to decide whether the model could
            # possibly have seen it.
            "scoredAt": policy.parse_timestamp(
                _row_value(row, "calculation_timestamp")
            ),
            "totalSpend90d": _row_value(row, "total_spend_90d"),
            "sentimentScore": _row_value(row, "sentiment_score"),
            "customerSegment": _row_value(row, "customer_segment"),
        }

    def _latest_order(self, customer_id: str) -> Optional[Dict[str, Any]]:
        """The customer's most recent order, or None.

        Reads orders rather than /customers because the mobile client never
        writes back to the profile: the complaint fields seeded there are
        frozen, and reacting to them would fire on every login forever. Uses
        the existing ``orders_by_customer`` composite index.
        """
        if not self.fs:
            return None
        try:
            query = (
                self.fs.collection(self.orders_collection)
                .where(filter=FieldFilter("customerId", "==", customer_id))
                .order_by("createdAt", direction="DESCENDING")
                .limit(1)
            )
            for doc in query.stream():
                return doc.to_dict() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Latest order lookup failed for %s: %s", customer_id, exc)
        return None

    def _check_cooldown(self, customer_id: str, now: datetime) -> Dict[str, Any]:
        """Classify the customer's recent offers. See policy.classify_recent_offers."""
        if not self.fs:
            return policy.no_recent_offers()

        cutoff = policy.cooldown_cutoff(now, self.cooldown_days)
        try:
            query = (
                self.fs.collection(OFFERS_COLLECTION)
                .where(filter=FieldFilter("customerId", "==", customer_id))
                .where(filter=FieldFilter("createdAt", ">=", cutoff.isoformat()))
                .order_by("createdAt", direction="DESCENDING")
                .limit(COOLDOWN_SCAN_LIMIT)
            )
            recent: List[Dict[str, Any]] = [
                doc.to_dict() or {} for doc in query.stream()
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Cooldown lookup failed for %s (%s). Treating as in cooldown.",
                customer_id, exc,
            )
            return {**policy.no_recent_offers(), "inCooldown": True}

        return policy.classify_recent_offers(recent, now)

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    def _consider_escalation(
        self,
        customer_id: str,
        churn: Dict[str, Any],
        context: Dict[str, Any],
        model_tier: str,
        spans: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
        """Decide whether to ask the judge, then ask it.

        Returns ``(complaint, verdict, gate)``, where ``gate`` names the branch
        taken so the session document can say which one it was.

        One deterministic gate stands in front of the model: the tier must be
        one the operator allows to be promoted. The freshness of the complaint
        used to be a second gate, and is now a fact in the brief instead --
        the judge is told how the complaint sits against
        ``calculation_timestamp`` and has to say in its own words whether the
        score already accounts for it. That costs a model call on complaints
        the old code discarded silently, and buys a decision that is visible
        and arguable rather than one that left no trace.
        """
        if model_tier not in config.escalation_candidate_tiers:
            return None, None, policy.GATE_TIER_NOT_CANDIDATE

        complaint = policy.latest_complaint(
            self._latest_order(customer_id), churn.get("scoredAt")
        )
        if not complaint:
            return None, None, policy.GATE_NO_COMPLAINT

        logger.info(
            "Customer %s is %s but rated order %s %d star(s) (%s); asking the "
            "escalation judge. Complaint is %s the score.",
            customer_id, model_tier, complaint.get("orderId"),
            complaint["rating"], complaint.get("reason") or "no reason given",
            "older than" if complaint.get("alreadyScored") else "newer than",
        )
        verdict = self._judge().decide(churn, complaint, context, spans)
        if not verdict.get("available", True):
            return complaint, verdict, policy.GATE_JUDGE_UNAVAILABLE
        return complaint, verdict, policy.GATE_JUDGED

    def _judge(self):
        """The escalation judge, built on first use."""
        if self._escalation_judge is None:
            from loyalty_agent.escalation import EscalationJudge

            self._escalation_judge = EscalationJudge(model_name=self.model_name)
        return self._escalation_judge


def _trigger_summary(friction: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The evidence behind an escalation, in a form Firestore can store.

    ``alreadyScored`` is kept because it is the fact the judge was asked to
    weigh: an escalation granted despite the model having already seen the
    complaint is a different decision from one granted because it could not
    have, and the offer document should not flatten the two.
    """
    if not friction:
        return None
    submitted = friction.get("submittedAt")
    scored = friction.get("scoredAt")
    gap_hours = friction.get("gapHours")
    return {
        "orderId": friction.get("orderId"),
        "rating": friction.get("rating"),
        "reason": friction.get("reason"),
        "submittedAt": submitted.isoformat() if submitted else None,
        "scoredAt": scored.isoformat() if scored else None,
        "gapHours": round(gap_hours, 2) if gap_hours is not None else None,
        "alreadyScored": friction.get("alreadyScored"),
    }


def _row_value(row: Any, key: str) -> Any:
    """Read a column from a BigQuery Row, which supports both access styles."""
    value = getattr(row, key, None)
    if value is None and hasattr(row, "get"):
        try:
            value = row.get(key)
        except Exception:  # noqa: BLE001
            value = None
    return value

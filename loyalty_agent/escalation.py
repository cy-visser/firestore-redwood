from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from loyalty_agent.config import config

logger = logging.getLogger("loyalty_agent.escalation")

APP_NAME = "redwood_retention"

ESCALATION_INSTRUCTION = """
You are the retention guardrail for Redwood Retail, a B2B distributor of
industrial components. Customers are businesses, and their orders are large
and repeated.

A machine learning model scores every customer's churn risk overnight. You are
called when a customer has filed a complaint, and the brief tells you where
that complaint sits relative to the score. Read the timing first, because it
decides what the score in front of you is worth:

  - Filed AFTER the score was calculated: the model provably cannot have seen
    it. The score is a picture taken before this happened. Judge the complaint
    on its merits.
  - Filed BEFORE the score was calculated: the model has already weighed it,
    and the score is the model's considered answer to it. Escalating now
    spends margin twice on one grievance. Decline, unless the complaint is
    severe enough that you would act on it even knowing the model saw it and
    stayed calm.
  - Timing unknown: treat the score as possibly already reflecting the
    complaint, and lean towards declining.

Your only job is to answer one question:

    Given this event, is this customer meaningfully more likely to stop
    buying than their score says -- enough that we should send a retention
    discount right now, rather than waiting for tonight's re-score?

ESCALATE when the event signals the relationship itself is at risk:
  - the failure looks repeated or systemic rather than a one-off,
  - it is a billing dispute, a damaged high-value shipment, or a failure that
    stopped the customer's own production,
  - the customer was already drifting, and this is one more reason to leave,
  - the complaint reads as a final straw rather than a first annoyance.

DO NOT ESCALATE when:
  - a long-standing, high-spending, consistently satisfied customer has one bad
    experience. One late pallet does not undo three years of steady ordering,
    and a discount is the wrong answer to it -- they want the problem fixed,
    not 15% off their next order. Escalating here trains good customers to
    complain for money.
  - the complaint was already in front of the model when it scored. The score
    is the answer to it, and acting again is paying twice for one grievance.
  - the customer complains routinely. The overnight model has already priced
    that pattern in; this event is not new information.
  - the issue is minor, cosmetic, or already resolved.
  - you are not sure.

Weigh the two errors honestly. Escalating wrongly spends real margin on a
customer who was never going to leave, and teaches them that complaints pay.
Declining wrongly costs one day: the overnight model sees this event tonight
and will raise the score itself if the customer really is at risk. The errors
are not symmetric. When the case is balanced, decline.

Judge the customer in front of you, not the average customer. A first
complaint from someone with a spotless two-year history means something
completely different from a first complaint from someone who has already
halved their order volume.

Reply with exactly two fields:
  escalate  - true only if we should send a retention offer now.
  reasoning - EXACTLY ONE sentence, at most 220 characters, plain English,
              naming the specific facts that decided it -- including the
              timing when the timing is what decided it. Never two sentences,
              never a list, never a preamble. A retention manager reads this
              as a single line on a dashboard and must be able to tell from
              that line alone whether you were right.
""".strip()


class EscalationVerdict(BaseModel):
    """The only thing the model is allowed to return."""

    escalate: bool = Field(
        description="True only if a retention offer should be sent now."
    )
    reasoning: str = Field(
        description="One sentence naming the facts that decided it."
    )


def _select_vertex_backend() -> None:
    """Point ADK's own google-genai client at Vertex AI.

    ADK builds its client from the environment rather than accepting the one
    ``main.py`` already constructed with ``vertexai=True``. That client
    defaults to the Gemini Developer API, so without this every escalation
    failed two seconds in with "No API key was provided" -- a key this
    deployment does not have and should not need, since it authenticates as a
    service account.

    This belongs here rather than in the deployment's env map because Agent
    Engine reserves ``GOOGLE_CLOUD_PROJECT`` and refuses any deployment that
    sets it. ``setdefault`` throughout, so a runtime that does provide these
    keeps its own values.
    """
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", config.project_id)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", config.region)


def build_judge(model_name: Optional[str] = None):
    """Construct the ADK agent that renders the verdict.

    Imported lazily so that neither the offline self-test nor a deployment
    that has escalation switched off pays for the ADK import.
    """
    from google.adk.agents import LlmAgent

    _select_vertex_backend()

    return LlmAgent(
        name="friction_escalation_judge",
        model=model_name or config.reasoning_model,
        description=(
            "Decides whether a customer's complaint justifies a retention "
            "offer now, given what the churn score already accounts for."
        ),
        instruction=ESCALATION_INSTRUCTION,
        output_schema=EscalationVerdict,
        output_key="verdict",
        # This agent is a leaf. It answers one question and returns; handing
        # control anywhere else is not something it should be able to do.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def _elapsed(gap_hours: float) -> str:
    """Render a gap in the unit that makes it legible.

    A re-score triggered from the console lands seconds after the complaint,
    and "0.0 hours" is both ugly and misleading in a brief whose whole purpose
    is to say which came first. Under an hour, count minutes.
    """
    minutes = abs(gap_hours) * 60
    if minutes < 60:
        return f"{minutes:.0f} minutes"
    return f"{abs(gap_hours):.1f} hours"


def timing_line(gap_hours: Optional[float]) -> str:
    """One line placing the complaint on either side of the score.

    The sign is the whole point, so it is spelled out rather than left for the
    model to derive from two timestamps. An earlier version clamped this to
    zero and called it "hours after the score was calculated", which was safe
    only because the caller had already discarded every complaint that fell
    the other way.
    """
    if gap_hours is None:
        return (
            "  timing:             unknown -- one of the timestamps is missing, "
            "so it cannot be shown whether the model saw this"
        )
    if gap_hours > 0:
        return (
            f"  timing:             {_elapsed(gap_hours)} AFTER the score was "
            "calculated, so the model cannot have seen this"
        )
    return (
        f"  timing:             {_elapsed(gap_hours)} BEFORE the score was "
        "calculated, so the model had this in front of it and scored anyway"
    )


def format_case(
    churn: Dict[str, Any],
    friction: Dict[str, Any],
    context: Dict[str, Any],
) -> str:
    """Render the decision as the brief a human reviewer would be handed."""
    scored_at = friction.get("scoredAt")
    submitted_at = friction.get("submittedAt")
    gap_hours = friction.get("gapHours")

    def show(value: Any, fallback: str = "not recorded") -> str:
        return fallback if value is None else str(value)

    return "\n".join([
        "OVERNIGHT CHURN SCORE",
        f"  probability:        {churn.get('churnProbability')}",
        f"  risk tier:          {churn.get('churnTier')}",
        f"  calculated at:      {show(scored_at)}",
        "",
        "CUSTOMER",
        f"  segment:            {show(context.get('customerSegment'))}",
        f"  spend, last 90d:    {show(context.get('totalSpend90d'))}",
        f"  lifetime spend:     {show(context.get('lifetimeSpend'))}",
        f"  orders, last 12m:   {show(context.get('ordersCountLast12m'))}",
        f"  usual rating:       {show(context.get('feedbackRating'))} out of 5",
        f"  standing complaint: {show(context.get('primaryComplaintReason'), 'none')}",
        "",
        "THE COMPLAINT",
        f"  order:              {show(friction.get('orderId'))}",
        f"  rating given:       {friction.get('rating')} out of 5",
        f"  reason:             {show(friction.get('reason'), 'not given')}",
        f"  customer wrote:     {show(friction.get('comment'), 'nothing')}",
        f"  submitted:          {show(submitted_at)}",
        timing_line(gap_hours),
        "",
        "Should we send a retention offer now?",
    ])


class EscalationJudge:
    """Runs one ADK agent to a verdict, synchronously.

    ``process_session`` is synchronous and Agent Engine calls it that way, so
    the async ADK runner is driven to completion on a worker thread with its
    own event loop. Doing that unconditionally rather than probing for a
    running loop keeps the behaviour identical whether or not the caller
    happens to be inside one.
    """

    def __init__(self, agent: Any = None, model_name: Optional[str] = None) -> None:
        self._agent = agent
        self._model_name = model_name
        self._runner = None

    def _get_runner(self):
        if self._runner is None:
            from google.adk.runners import InMemoryRunner

            agent = self._agent or build_judge(self._model_name)
            self._runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
        return self._runner

    def decide(
        self,
        churn: Dict[str, Any],
        friction: Dict[str, Any],
        context: Dict[str, Any],
        spans: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return ``{"escalate", "reasoning", "available"}``. Never raises.

        ``available`` is False only when the judge could not be reached. A
        caller that cannot tell that apart from a decline would report an
        outage as a judgement, which is the one thing a decision log must
        never do.
        """
        started = time.monotonic()
        customer_id = str(context.get("customerId") or "unknown")
        try:
            raw = self._invoke(format_case(churn, friction, context), customer_id)
            verdict = EscalationVerdict.model_validate_json(raw)
            outcome = "OK"
            result = {
                "escalate": bool(verdict.escalate),
                "reasoning": " ".join(verdict.reasoning.split()),
                "available": True,
            }
        except Exception as exc:  # noqa: BLE001 - a failed judge means "no"
            logger.warning(
                "Escalation judge failed for %s (%s). Not escalating.",
                customer_id, exc,
            )
            outcome = "FAILED"
            result = {
                "escalate": False,
                "reasoning": None,
                "available": False,
            }

        if spans is not None:
            spans["llmEscalationMs"] = round((time.monotonic() - started) * 1000.0, 2)
            spans["escalationOutcome"] = outcome
            spans["escalationVerdict"] = "ESCALATE" if result["escalate"] else "DECLINE"
        return result

    def _invoke(self, prompt: str, customer_id: str) -> str:
        """Drive the ADK runner to its final response and return the text."""
        from google.genai import types

        runner = self._get_runner()
        session_id = f"escalation-{customer_id}-{int(time.time() * 1000)}"
        message = types.Content(role="user", parts=[types.Part(text=prompt)])

        async def _run() -> str:
            await runner.session_service.create_session(
                app_name=APP_NAME, user_id=customer_id, session_id=session_id
            )
            final = ""
            async for event in runner.run_async(
                user_id=customer_id, session_id=session_id, new_message=message
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    final = event.content.parts[0].text or ""
            return final

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()

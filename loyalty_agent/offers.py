"""Composing and persisting a retention offer.

Gemini writes the copy and may propose a number; it never decides one. Every
payload that leaves ``synthesize_offer`` has been through
``apply_discount_guardrails``, which is the single place a discount is settled
and is a pure function in :mod:`loyalty_agent.policy`.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loyalty_agent import policy
from loyalty_agent.schemas import LoyaltyOffer

logger = logging.getLogger("loyalty_agent.offers")

OFFERS_COLLECTION = "loyalty_offers"
SESSIONS_COLLECTION = "customer_sessions"


def generate_deterministic_offer(
    customer_id: str,
    churn_tier: str,
    complaint: Optional[str] = None,
) -> Dict[str, Any]:
    """Rule engine used when Vertex AI is unreachable or quota is exhausted."""
    is_critical = (churn_tier == "CRITICAL")
    suffix = customer_id[-4:] if len(customer_id) >= 4 else customer_id

    return {
        "title": "Special Customer Loyalty Incentive",
        "description": (
            "We appreciate your ongoing business and want to offer you an "
            "exclusive discount."
        ),
        "discountPercent": 25 if is_critical else 15,
        "promoCode": f"RETENTION-DET-{churn_tier}-{suffix}",
        "freeExpressShipping": is_critical,
        "perks": ["FREE_EXPRESS_SHIPPING"] if is_critical else ["FREE_SHIPPING"],
        "personalizedApology": (
            "We apologize for any past shipping inconveniences."
            if complaint else None
        ),
        "generationSource": "DETERMINISTIC_RULES",
    }


def generate_with_gemini(
    genai_client: Any,
    model_name: str,
    customer_id: str,
    churn_probability: float,
    churn_tier: str,
    complaint: Optional[str],
    discount_ceiling: int,
    spans: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Ask Gemini for the offer copy, or None if it cannot be obtained."""
    if not genai_client:
        return None

    prompt = (
        "You write retention offers for a retailer. Reply with a single JSON "
        "object and nothing else, with keys: title (string), description "
        "(string, at most two sentences, addressed to the customer), "
        "promoCode (string, uppercase, no spaces), discountPercent "
        "(integer), freeExpressShipping (boolean), perks (array of strings), "
        "personalizedApology (string or null; only when a complaint is given).\n"
        f"Customer: {customer_id}\n"
        f"Churn probability: {churn_probability}\n"
        f"Churn tier: {churn_tier}\n"
        f"Open complaint: {complaint or 'none'}\n"
        f"Maximum discount percent: {discount_ceiling}"
    )

    # Starts at the call, stops at the final response object. The JSON parse
    # below is this process's own work and is not reasoning time.
    llm_started = time.monotonic()
    try:
        response = genai_client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        llm_ms = (time.monotonic() - llm_started) * 1000.0
        payload = json.loads(response.text)
        if not isinstance(payload, dict):
            raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    except Exception as exc:  # noqa: BLE001
        if spans is not None:
            spans["llmReasoningMs"] = round(
                (time.monotonic() - llm_started) * 1000.0, 2
            )
            spans["llmOutcome"] = "FAILED"
            spans["llmModel"] = model_name
        logger.warning(
            "Gemini generation failed for %s (%s). Falling back to "
            "deterministic rules.",
            customer_id, exc,
        )
        return None

    if spans is not None:
        spans["llmReasoningMs"] = round(llm_ms, 2)
        spans["llmOutcome"] = "OK"
        spans["llmModel"] = model_name

    payload["generationSource"] = "GEMINI_AI"
    return payload


def synthesize_offer(
    genai_client: Any,
    model_name: str,
    customer_id: str,
    churn_probability: float,
    churn_tier: str,
    complaint: Optional[str] = None,
    segment: Optional[str] = None,
    gross_margin_percent: Optional[float] = None,
    step_down_percent: int = 0,
    spans: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compose offer copy, preferring Gemini and falling back to rules.

    ``spans`` collects the model timing, which cannot be measured from
    outside: this function also covers the deterministic fallback, the JSON
    parse and the guardrail clamp, and the console needs the model call alone.
    """
    ceiling = policy.ceiling_for(segment, step_down_percent)

    payload = generate_with_gemini(
        genai_client, model_name, customer_id, churn_probability, churn_tier,
        complaint, ceiling, spans=spans,
    )
    if payload is None:
        payload = generate_deterministic_offer(customer_id, churn_tier, complaint)

    payload["discountPercent"] = policy.apply_discount_guardrails(
        payload.get("discountPercent", 15),
        segment,
        gross_margin_percent,
        step_down_percent=step_down_percent,
    )
    payload["discountCeilingApplied"] = ceiling
    return payload


def build_offer_document(
    customer_id: str,
    session_id: str,
    payload: Dict[str, Any],
    now: datetime,
    offer_validity_days: int,
    cooldown_days: int,
    audit_ttl_days: int,
) -> Dict[str, Any]:
    """Assemble the Firestore document for an offer.

    ``churnProbability`` is whatever BigQuery produced and is never adjusted
    here. When the agent acted on a different tier than the model assigned --
    because a complaint was escalated -- the two are recorded side by side
    as ``churnRiskTier`` and ``eligibilityTier`` rather than reconciled into
    one number. Anyone reading this document later can see exactly which part
    was the model's and which part was the agent's.
    """
    valid_until = now + timedelta(days=offer_validity_days)
    churn_tier = payload.get("churnRiskTier", "HIGH")
    discount = int(payload.get("discountPercent", 15))
    perks: List[str] = payload.get("perks", [])

    return {
        "offerId": payload.get("offerId") or f"off_{session_id}_retention",
        "customerId": customer_id,
        "sessionId": session_id,
        "churnProbability": float(payload.get("churnProbability", 0.75)),
        "churnRiskTier": churn_tier,
        "eligibilityTier": payload.get("eligibilityTier", churn_tier),
        "escalated": bool(payload.get("escalated", False)),
        "escalationReason": payload.get("escalationReason"),
        "escalationTrigger": payload.get("escalationTrigger"),
        "title": payload.get("title") or "Special Customer Loyalty Incentive",
        "description": (
            payload.get("description") or "Exclusive discount on your next order."
        ),
        "promoCode": payload.get("promoCode") or f"RETENTION-{customer_id[-4:]}",
        "discountPercent": discount,
        "freeExpressShipping": bool(
            payload.get("freeExpressShipping", "FREE_EXPRESS_SHIPPING" in perks)
        ),
        "perks": perks,
        "personalizedApology": payload.get("personalizedApology"),
        "offerSequence": int(payload.get("offerSequence", 1)),
        "supersedesOfferId": payload.get("supersedesOfferId"),
        "discountCeilingApplied": payload.get("discountCeilingApplied"),
        "status": "ACTIVE",
        "createdAt": now.isoformat(),
        "validUntil": valid_until.isoformat(),
        "cooldownUntil": (now + timedelta(days=cooldown_days)).isoformat(),
        "claimedAt": None,
        # Stored as a datetime for the Firestore TTL expiration policy.
        "ttlExpiryAt": now + timedelta(days=audit_ttl_days),
        "metadata": {
            "mlModelVersion": "redwood_churn_v1",
            "retentionAction": f"DISPATCH_{churn_tier}_OFFER",
            "historicalSpend90d": payload.get("historicalSpend90d", 0.0),
            "supportSentimentScore": payload.get("sentimentScore", 0.5),
        },
    }


def persist_offer(
    firestore_client: Any,
    customer_id: str,
    session_id: str,
    payload: Dict[str, Any],
    now: Optional[datetime] = None,
    offer_validity_days: int = 14,
    cooldown_days: int = 7,
    audit_ttl_days: int = 90,
) -> Dict[str, Any]:
    """Validate the offer, write it, and close the session as PROCESSED."""
    now = now or datetime.now(timezone.utc)
    offer_doc = build_offer_document(
        customer_id, session_id, payload, now,
        offer_validity_days, cooldown_days, audit_ttl_days,
    )

    logger.info(
        "Offer %s for %s: generation=%s tier=%s (model said %s) "
        "p(churn)=%.4f discount=%d%%",
        offer_doc["offerId"], customer_id,
        payload.get("generationSource", "DETERMINISTIC_RULES"),
        offer_doc["eligibilityTier"], offer_doc["churnRiskTier"],
        offer_doc["churnProbability"], offer_doc["discountPercent"],
    )

    # Pydantic drops keys it does not declare, so anything added to the
    # document above must also exist on LoyaltyOffer or it vanishes here.
    validated = LoyaltyOffer.model_validate(offer_doc).model_dump()

    if firestore_client:
        firestore_client.collection(OFFERS_COLLECTION).document(
            validated["offerId"]
        ).set(validated)
        try:
            firestore_client.collection(SESSIONS_COLLECTION).document(
                session_id
            ).set({
                "agentProcessingStatus": "PROCESSED",
                "status": "PROCESSED",
                "offerId": validated["offerId"],
                "activeOfferId": validated["offerId"],
                "processedAt": now.isoformat(),
                "skipReason": None,
            }, merge=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not update session %s in Firestore: %s", session_id, exc
            )

    return validated

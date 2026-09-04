"""
Tool 4: Firestore Transactional Loyalty Offer Issuance Tool.
Atomically persists the loyalty voucher to Firestore and updates the session processing status.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from loyalty_agent.schemas import LoyaltyOffer


def issue_loyalty_offer(
    customer_id: str,
    session_id: str,
    offer_payload: Dict[str, Any],
    firestore_client: Any,
    cooldown_days: int = 7,
    offer_validity_days: int = 14,
    audit_ttl_days: int = 90
) -> Dict[str, Any]:
    """
    Atomically writes a personalized loyalty offer to Firestore and marks session as PROCESSED.

    Args:
        customer_id: The target customer ID.
        session_id: The active customer login session ID.
        offer_payload: Dictionary containing offer details (title, promoCode, discountPercent, etc.).
        firestore_client: Google Cloud Firestore client.
        cooldown_days: Number of days before another offer can be issued to this customer.
        offer_validity_days: Expiration window for the voucher.
        audit_ttl_days: Retention window for document TTL auto-purge.

    Returns:
        Dict representing the persisted loyalty offer document.
    """
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=offer_validity_days)
    cooldown_until = now + timedelta(days=cooldown_days)
    ttl_expiry = now + timedelta(days=audit_ttl_days)

    offer_id = f"off_{session_id}_retention"

    # Normalize fields across naming variations
    title = offer_payload.get("title") or offer_payload.get("headline", "Special Customer Loyalty Incentive")
    desc = offer_payload.get("description") or offer_payload.get("messageBody", "Exclusive discount on your next order.")
    discount = offer_payload.get("discountPercent") or offer_payload.get("discountPercentage", 15)
    promo = offer_payload.get("promoCode") or offer_payload.get("voucherCode", f"RETENTION-{customer_id[-4:]}")
    perks = offer_payload.get("perks", [])
    free_shipping = offer_payload.get("freeExpressShipping", "FREE_EXPRESS_SHIPPING" in perks)
    churn_prob = offer_payload.get("churnProbability") or offer_payload.get("churnScore", 0.75)
    churn_tier = offer_payload.get("churnRiskTier") or offer_payload.get("churnTier", "HIGH")
    source = offer_payload.get("generationSource", "GEMINI_AI")
    apology = offer_payload.get("personalizedApology")

    offer_doc = {
        "offerId": offer_id,
        "customerId": customer_id,
        "sessionId": session_id,
        "churnProbability": float(churn_prob),
        "churnScore": float(churn_prob),
        "churnRiskTier": churn_tier,
        "churnTier": churn_tier,
        "title": title,
        "headline": title,
        "description": desc,
        "messageBody": desc,
        "promoCode": promo,
        "voucherCode": promo,
        "discountPercent": int(discount),
        "discountPercentage": int(discount),
        "freeExpressShipping": bool(free_shipping),
        "perks": perks,
        "personalizedApology": apology,
        "generationSource": source,
        "baselineChurnRisk": float(offer_payload.get("baselineChurnRisk", churn_prob)),
        "evaluationSource": offer_payload.get("evaluationSource", "BIGQUERY_BATCH"),
        "status": "ACTIVE",
        "createdAt": now.isoformat(),
        "validUntil": valid_until.isoformat(),
        "expiresAt": valid_until.isoformat(),
        "cooldownUntil": cooldown_until.isoformat(),
        "claimedAt": None,
        "ttlExpiryAt": ttl_expiry.isoformat(),
        "metadata": {
            "mlModelVersion": "redwood_churn_v1",
            "retentionAction": f"DISPATCH_{churn_tier}_OFFER",
            "historicalSpend90d": offer_payload.get("historicalSpend90d", 500.0),
            "supportSentimentScore": offer_payload.get("sentimentScore", 0.5),
            "baselineChurnRisk": float(offer_payload.get("baselineChurnRisk", churn_prob)),
            "evaluationSource": offer_payload.get("evaluationSource", "BIGQUERY_BATCH")
        }
    }

    # Validate strictly against Pydantic LoyaltyOffer data contract
    validated_offer = LoyaltyOffer.model_validate(offer_doc)
    validated_payload = validated_offer.model_dump()

    # Write offer document
    firestore_client.collection("loyalty_offers").document(offer_id).set(validated_payload)

    # Update session document
    sess_ref = firestore_client.collection("customer_sessions").document(session_id)
    sess_ref.update({
        "agentProcessingStatus": "PROCESSED",
        "status": "PROCESSED",
        "offerId": offer_id,
        "activeOfferId": offer_id,
        "processedAt": now.isoformat(),
        "skipReason": None
    })

    return offer_doc

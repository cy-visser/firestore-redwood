"""
Tool 1: Cooldown & Active Offer Inspection Tool.
Verifies whether a customer already has an ACTIVE offer or is within the 7-day cooldown window.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional


def check_active_offers_cooldown(
    customer_id: str,
    firestore_client: Any,
    cooldown_days: int = 7
) -> Dict[str, Any]:
    """
    Checks if a customer has an existing ACTIVE offer or an active cooldown.

    Returns:
        Dict containing:
            has_active_offer (bool): True if an ACTIVE offer exists.
            active_offer (dict | None): The existing active offer data if present.
            in_cooldown (bool): True if customer received an offer recently.
            cooldown_until (str | None): Timestamp string when cooldown expires.
    """
    now = datetime.now(timezone.utc)
    offers_ref = firestore_client.collection("loyalty_offers")
    query = offers_ref.where("customerId", "==", customer_id)

    for doc in query.stream():
        offer = doc.to_dict()
        status = offer.get("status", "")

        # If customer already has an ACTIVE offer, reuse it
        if status == "ACTIVE":
            return {
                "has_active_offer": True,
                "active_offer": offer,
                "in_cooldown": False,
                "cooldown_until": None,
                "reason": "ACTIVE_OFFER_ALREADY_EXISTS"
            }

        # Check cooldown date on redeemed/expired offers
        cooldown_str = offer.get("cooldownUntil") or offer.get("validUntil")
        if cooldown_str:
            try:
                cooldown_dt = datetime.fromisoformat(cooldown_str)
                if cooldown_dt > now:
                    return {
                        "has_active_offer": False,
                        "active_offer": None,
                        "in_cooldown": True,
                        "cooldown_until": cooldown_str,
                        "reason": "COOLDOWN_ACTIVE"
                    }
            except (ValueError, TypeError):
                pass

    return {
        "has_active_offer": False,
        "active_offer": None,
        "in_cooldown": False,
        "cooldown_until": None,
        "reason": "ELIGIBLE_FOR_EVALUATION"
    }

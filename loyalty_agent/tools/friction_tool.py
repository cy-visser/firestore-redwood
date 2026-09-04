"""
Tool 3: Customer Friction & Operational History Tool.
Retrieves customer tier, 90-day spend, and recent operational grievances from Firestore.
"""

from typing import Dict, Any, Optional


def get_customer_friction_and_profile(
    customer_id: str,
    firestore_client: Any
) -> Dict[str, Any]:
    """
    Retrieves customer account profile, historical spend, and operational friction point.

    Returns:
        Dict containing:
            customer_id (str)
            customer_segment (str): ENTERPRISE_VIP, RETAIL_PRO, STANDARD_LOYALTY, CASUAL.
            primary_complaint_reason (str | None): LATE_DELIVERY, DEFECTIVE_COMPONENT, etc.
            total_spend_90d (float): Total historical spend over 90 days.
            sentiment_score (float): 0.0 (very negative) to 1.0 (very positive).
            discount_cap_percent (int): Maximum allowable retention discount.
    """
    doc_ref = firestore_client.collection("customers").document(customer_id)
    doc_snap = doc_ref.get()

    if not doc_snap.exists:
        # Default profile for unknown/cold-start customer
        return {
            "customer_id": customer_id,
            "customer_segment": "STANDARD_LOYALTY",
            "primary_complaint_reason": None,
            "total_spend_90d": 0.0,
            "sentiment_score": 0.5,
            "account_age_days": 365,
            "discount_cap_percent": 15
        }

    data = doc_snap.to_dict()
    segment = data.get("customerSegment") or data.get("loyaltyTier", "STANDARD_LOYALTY")
    complaint = data.get("primaryComplaintReason") or data.get("recentFrictionEvent") or data.get("recent_friction_event")
    recent_friction = data.get("recentFrictionEvent") or data.get("recent_friction_event")
    spend = float(data.get("totalSpend90d", data.get("historicalSpend90d", 500.0)))
    sentiment = float(data.get("sentimentScore", 0.5))

    # Derive discount cap based on segment
    caps = {
        "ENTERPRISE_VIP": 25,
        "RETAIL_PRO": 20,
        "PLATINUM": 20,
        "GOLD": 20,
        "STANDARD_LOYALTY": 15,
        "SILVER": 15,
        "BRONZE": 15,
        "CASUAL": 12
    }
    cap = caps.get(segment.upper(), 15)

    return {
        "customer_id": customer_id,
        "customer_name": data.get("name"),
        "customer_email": data.get("email"),
        "customer_segment": segment,
        "primary_complaint_reason": complaint,
        "recent_friction_event": recent_friction,
        "total_spend_90d": spend,
        "sentiment_score": sentiment,
        "account_age_days": data.get("accountAgeDays", 365),
        "discount_cap_percent": cap
    }

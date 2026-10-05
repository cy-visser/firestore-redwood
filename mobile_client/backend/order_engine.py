"""Order Schema Engine for Redwood Mobile Retail Client."""

import os
import sys
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

# Add parent directory to access retail catalog
PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from retail_catalog import (
    CATALOG_ITEMS, SHIPPING_CITIES, WAREHOUSES, CARRIERS,
    POSITIVE_FEEDBACK, NEUTRAL_FEEDBACK, NEGATIVE_FEEDBACK, COMPLAINT_REASONS,
    LOYALTY_TIERS, FEEDBACK_SENTIMENT_RANGES
)
from customer_profiles import DEMO_PERSONAS

# Demo personas keyed by principal short name.
_PERSONA = {p.principal_id.removesuffix("-user"): p for p in DEMO_PERSONAS}

# Demo IAM Principal Profiles
DEMO_PRINCIPALS = {
    "demo1": {
        "iamPrincipal": "demo1-user@elevate-cyvisser.iam.gserviceaccount.com",
        "customerId": _PERSONA["demo1"].customer_id,        # cust_demo1
        "displayName": _PERSONA["demo1"].display_name,      # Meridian Industrial Supply
        "customerSegment": "ENTERPRISE_VIP",
        "loyaltyTier": "ENTERPRISE_VIP",
        "isLoyaltyMember": 1,
        "discountRate": 0.0,
        "accountAgeDays": 720,
        "defaultAddress": {
            "streetAddress": "Industrial Park Way 104",
            "city": "Amsterdam",
            "province": "North Holland",
            "postalCode": "1016 BS",
            "countryCode": "NL"
        },
        "defaultCarrier": "DHL_EXPRESS",
        "defaultWarehouse": "WH-ROTTERDAM-1",
        "historicalMetrics": {
            "totalSpend90d": 18450.00,
            "lifetimeSpend": 64200.00,
            "avgOrderValue": 3690.00,
            "purchaseFrequencyMonthly": 2.5,
            "daysSinceLastPurchase": 12,
            "ordersCountLast12m": 22
        },
        "engagementMetrics": {
            "loginFrequencyMonthly": 24,
            "avgSessionDurationMinutes": 14.5,
            "appEngagementScore": 0.92,
            "appSessionsLast30d": 38,
            "cartAbandonmentCount": 1,
            "abandonedCartValue90d": 420.00
        },
        "supportMetrics": {
            "supportTicketsCount": 1,
            "openSupportTicketsCount": 0,
            "complaintsCount": 0,
            "returnFrequency": 0,
            "returnRatePercent": 1.5
        }
    },
    "demo2": {
        "iamPrincipal": "demo2-user@elevate-cyvisser.iam.gserviceaccount.com",
        "customerId": _PERSONA["demo2"].customer_id,        # cust_demo2
        "displayName": _PERSONA["demo2"].display_name,      # Bavaria Components GmbH
        "customerSegment": "STANDARD_LOYALTY",
        "loyaltyTier": "SILVER",
        "isLoyaltyMember": 1,
        "discountRate": 0.0,
        "accountAgeDays": 210,
        "defaultAddress": {
            "streetAddress": "Gewerbepark Allee 45",
            "city": "Munich",
            "province": "Bavaria",
            "postalCode": "80331",
            "countryCode": "DE"
        },
        "defaultCarrier": "POSTNL_CARGO",
        "defaultWarehouse": "WH-FRANKFURT-1",
        "historicalMetrics": {
            "totalSpend90d": 4200.00,
            "lifetimeSpend": 12800.00,
            "avgOrderValue": 1050.00,
            "purchaseFrequencyMonthly": 0.65,
            "daysSinceLastPurchase": 42,
            "ordersCountLast12m": 6
        },
        "engagementMetrics": {
            "loginFrequencyMonthly": 6,
            "avgSessionDurationMinutes": 5.8,
            "appEngagementScore": 0.48,
            "appSessionsLast30d": 8,
            "cartAbandonmentCount": 3,
            "abandonedCartValue90d": 1150.00
        },
        # Tuned against the trained model rather than picked: with 3 tickets
        # and 12% returns, two orders and a 5-star rating left cust_demo2 at
        # p=0.6013 -- HIGH by 0.0013, so the follow-up offer in demo step 9
        # depended on a rounding error. These values land it near 0.70.
        "supportMetrics": {
            "supportTicketsCount": 4,
            "openSupportTicketsCount": 1,
            "complaintsCount": 1,
            "returnFrequency": 2,
            "returnRatePercent": 15.0
        }
    }
}

# ------------------------------------------------------------------------------
# demo2 recovery
#
# The churn feature view reads engagement and support from a customer's latest
# order, so the snapshot written here is the only way those features move.
# Frozen, it pinned cust_demo2 at roughly p=0.60 however many orders she
# placed: the engagement/support group alone was worth about +0.56 logit, and
# each extra order moved the score by only a few points.
#
# Once she has come back -- at least RECOVERY_MIN_PRIOR_ORDERS app orders
# already placed, and this one rated RECOVERY_MIN_RATING or better -- the order
# carries a recovered snapshot (roughly the 'steady' archetype midpoint). On
# the deployed model that scores LOW, so the agent stops because she recovered,
# not merely because the follow-up cap was hit. A poor rating always falls back
# to the struggling snapshot: recovery is earned per order, not latched.
# ------------------------------------------------------------------------------
RECOVERY_MIN_PRIOR_ORDERS = 2
RECOVERY_MIN_RATING = 4

DEMO2_RECOVERED_METRICS: Dict[str, Dict[str, Any]] = {
    "engagementMetrics": {
        "loginFrequencyMonthly": 14,
        "avgSessionDurationMinutes": 10.0,
        "appEngagementScore": 0.68,
        "appSessionsLast30d": 18,
        "cartAbandonmentCount": 1,
        "abandonedCartValue90d": 300.00
    },
    "supportMetrics": {
        "supportTicketsCount": 1,
        "openSupportTicketsCount": 0,
        "complaintsCount": 0,
        "returnFrequency": 1,
        "returnRatePercent": 4.0
    }
}


def is_recovered(principal_id: str, prior_mobile_orders: int, feedback_rating: int) -> bool:
    """Whether this order should carry demo2's recovered snapshot."""
    return (
        principal_id == "demo2"
        and prior_mobile_orders >= RECOVERY_MIN_PRIOR_ORDERS
        and feedback_rating >= RECOVERY_MIN_RATING
    )


def behavioural_snapshot(
    principal_id: str, prior_mobile_orders: int, feedback_rating: int
) -> Dict[str, Dict[str, Any]]:
    """The engagement and support readings to stamp on an order."""
    principal = DEMO_PRINCIPALS.get(principal_id, DEMO_PRINCIPALS["demo1"])
    if is_recovered(principal_id, prior_mobile_orders, feedback_rating):
        return DEMO2_RECOVERED_METRICS
    return {
        "engagementMetrics": principal["engagementMetrics"],
        "supportMetrics": principal["supportMetrics"],
    }

CATALOG_BY_SKU = {item["sku"]: item for item in CATALOG_ITEMS}

# Order ID prefix for mobile client orders.
MOBILE_ORDER_ID_PREFIX = "ORD-26-MOB-"

NO_OFFER_PRICING: Dict[str, Any] = {
    "offerApplied": False,
    "offerId": None,
    "promoCode": None,
    "discountPercent": 0,
    "freeExpressShipping": False,
}


def calculate_sentiment_score(rating: int) -> float:
    """Computes sentiment score between -1.0 and 1.0 based on feedback rating."""
    if rating >= 4:
        s_min, s_max = FEEDBACK_SENTIMENT_RANGES["POSITIVE"]
    elif rating == 3:
        s_min, s_max = FEEDBACK_SENTIMENT_RANGES["NEUTRAL"]
    else:
        s_min, s_max = FEEDBACK_SENTIMENT_RANGES["NEGATIVE"]
    return round(random.uniform(s_min, s_max), 3)


def offer_pricing(offer: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reduce a loyalty offer document to the parts that change the price.

    The offer is the agent's, so this reads it defensively: the discount is
    clamped to 0..100 because a percentage outside that range would either
    charge the customer more than the subtotal or hand them the order.

    Validation of *whether* the offer may be used at all -- ownership, status,
    expiry -- belongs to session_engine, which has the Firestore client. By the
    time an offer reaches here it has already passed that.
    """
    if not offer:
        return dict(NO_OFFER_PRICING)

    try:
        percent = int(offer.get("discountPercent") or 0)
    except (TypeError, ValueError):
        percent = 0

    return {
        "offerApplied": True,
        "offerId": offer.get("offerId"),
        "promoCode": offer.get("promoCode"),
        "discountPercent": max(0, min(percent, 100)),
        "freeExpressShipping": bool(offer.get("freeExpressShipping")),
    }



def create_order_from_cart(
    cart_items: List[Dict[str, Any]],
    principal_id: str = "demo1",
    shipping_address: Optional[Dict[str, str]] = None,
    payment_method: str = "INVOICE_NET30",
    carrier_code: Optional[str] = None,
    service_level: str = "NEXT_DAY_AIR",
    feedback_rating: int = 5,
    feedback_text: Optional[str] = None,
    complaint_reason: Optional[str] = None,
    order_status: str = "PROCESSING",
    payment_status: str = "SETTLED",
    custom_order_id: Optional[str] = None,
    offer: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    prior_mobile_orders: int = 0
) -> Dict[str, Any]:
    """
    Builds a retail transaction order dictionary with 100% schema parity
    to generate_retail_dataset.generate_single_order.

    ``offer`` is the loyalty offer the agent issued to this customer, already
    validated by the caller. It, and nothing else, decides the discount: pass
    None and the order is priced at list.

    ``prior_mobile_orders`` is how many app orders this customer already has
    (Reset Demo deletes them, so it restarts at zero). It selects demo2's
    recovered engagement snapshot; see ``behavioural_snapshot``.
    """
    if principal_id not in DEMO_PRINCIPALS:
        principal_id = "demo1"
    
    principal = DEMO_PRINCIPALS[principal_id]
    created_at = now or datetime.now(timezone.utc)
    updated_at = created_at + timedelta(minutes=random.randint(1, 15))
    est_delivery = created_at + timedelta(days=2 if service_level == "NEXT_DAY_AIR" else 4)

    # Generate standardized Order ID matching format
    if not custom_order_id:
        rand_id = f"{random.randint(100000, 999999)}{random.choice(['A', 'B', 'C', 'D', 'E'])}{random.randint(10, 99)}"
        timestamp_idx = int(created_at.timestamp()) % 10000000
        order_id = f"{MOBILE_ORDER_ID_PREFIX}{rand_id}-IDX{timestamp_idx:07d}"
    else:
        order_id = custom_order_id

    # Line Items Calculation
    line_items = []
    subtotal = 0.0
    total_cost = 0.0
    total_weight = 0.0

    for item in cart_items:
        sku = item.get("sku")
        cat_prod = CATALOG_BY_SKU.get(sku, {})
        qty = max(1, int(item.get("quantity", 1)))
        unit_price = float(cat_prod.get("unitPrice", item.get("unitPrice", 100.00)))
        cost = float(cat_prod.get("cost", unit_price * 0.60))
        item_total = round(qty * unit_price, 2)
        warehouse = item.get("allocatedWarehouse") or principal.get("defaultWarehouse") or random.choice(WAREHOUSES)

        subtotal += item_total
        total_cost += round(qty * cost, 2)
        total_weight += round(qty * random.uniform(1.5, 9.8), 2)

        line_items.append({
            "sku": sku or "SKU-CUSTOM",
            "name": item.get("name") or cat_prod.get("name", "Industrial Component"),
            "category": item.get("category") or cat_prod.get("category", "Hardware"),
            "quantity": qty,
            "unitPrice": unit_price,
            "totalPrice": item_total,
            "allocatedWarehouse": warehouse
        })

    # Financials
    tax_rate = 0.21
    tax_amount = round(subtotal * tax_rate, 2)

    pricing = offer_pricing(offer)
    discount_rate = pricing["discountPercent"] / 100.0
    discount_total = round(subtotal * discount_rate, 2)
    shipping_fee = 0.0 if pricing["freeExpressShipping"] else 45.00
    grand_total = round(subtotal - discount_total + tax_amount + shipping_fee, 2)
    profit_margin = round((grand_total - total_cost - tax_amount) / max(grand_total, 1.0), 3)

    # Address & Logistics
    addr = shipping_address or principal["defaultAddress"]
    carrier = carrier_code or principal.get("defaultCarrier", random.choice(CARRIERS))
    origin_hub = principal.get("defaultWarehouse", random.choice(WAREHOUSES))

    # Sentiment & Feedback
    sentiment_score = calculate_sentiment_score(feedback_rating)
    has_active_complaint = (feedback_rating <= 2)
    
    if not feedback_text:
        if feedback_rating >= 4:
            feedback_text = random.choice(POSITIVE_FEEDBACK)
        elif feedback_rating == 3:
            feedback_text = random.choice(NEUTRAL_FEEDBACK)
        else:
            feedback_text = random.choice(NEGATIVE_FEEDBACK)

    resolved_complaint_reason = None
    if has_active_complaint:
        resolved_complaint_reason = complaint_reason or random.choice(COMPLAINT_REASONS)

    # Metrics from Profile
    hist = principal["historicalMetrics"]
    snapshot = behavioural_snapshot(principal_id, prior_mobile_orders, feedback_rating)
    eng = snapshot["engagementMetrics"]
    sup = snapshot["supportMetrics"]

    doc = {
        "_id": order_id,
        "orderId": order_id,
        "customerId": principal["customerId"],
        "customerName": principal["displayName"],
        "customerEmail": principal["iamPrincipal"],
        "customerSegment": principal["customerSegment"],
        "orderStatus": order_status,
        "paymentStatus": payment_status,
        "paymentMethod": payment_method,
        "currency": "EUR",
        "financials": {
            "subtotal": subtotal,
            "taxAmount": tax_amount,
            "shippingFee": shipping_fee,
            "discountTotal": discount_total,
            "grandTotal": grand_total,
            "profitMargin": profit_margin
        },
        # Why this order cost what it cost. financials.discountTotal is the
        # amount; this is the provenance, so a document can be read months
        # later and still say which offer moved the price and which did not
        # exist. It is the one field the seeded schema does not have -- the
        # seeder has no agent -- and it is deliberately a single nested object
        # rather than four loose top-level keys, because cdc_service replicates
        # unmapped fields only inside document_data and the typed mirror stays
        # exactly as it was.
        "loyaltyOffer": {
            "offerApplied": pricing["offerApplied"],
            "offerId": pricing["offerId"],
            "promoCode": pricing["promoCode"],
            "discountPercent": pricing["discountPercent"],
            "freeExpressShipping": pricing["freeExpressShipping"]
        },
        "transactionalMetrics": {
            "totalSpend90d": hist["totalSpend90d"],
            "lifetimeSpend": round(hist["lifetimeSpend"] + grand_total, 2),
            "avgOrderValue": hist["avgOrderValue"],
            "purchaseFrequencyMonthly": hist["purchaseFrequencyMonthly"],
            "daysSinceLastPurchase": hist["daysSinceLastPurchase"],
            "ordersCountLast12m": hist["ordersCountLast12m"] + 1
        },
        "engagement": {
            "loginFrequencyMonthly": eng["loginFrequencyMonthly"],
            "avgSessionDurationMinutes": eng["avgSessionDurationMinutes"],
            "appEngagementScore": eng["appEngagementScore"],
            "appSessionsLast30d": eng["appSessionsLast30d"],
            "cartAbandonmentCount": eng["cartAbandonmentCount"],
            "abandonedCartValue90d": eng["abandonedCartValue90d"]
        },
        "supportMetrics": {
            "supportTicketsCount": sup["supportTicketsCount"] + (1 if has_active_complaint else 0),
            "openSupportTicketsCount": sup["openSupportTicketsCount"] + (1 if has_active_complaint else 0),
            "complaintsCount": sup["complaintsCount"] + (1 if has_active_complaint else 0),
            "returnFrequency": sup["returnFrequency"],
            "returnRatePercent": sup["returnRatePercent"],
            "sentimentScore": sentiment_score,
            "hasActiveComplaint": has_active_complaint,
            "primaryComplaintReason": resolved_complaint_reason
        },
        "accountState": {
            "loyaltyTier": principal["loyaltyTier"],
            "isLoyaltyMember": principal["isLoyaltyMember"],
            "accountAgeDays": principal["accountAgeDays"],
            "customerSegment": principal["customerSegment"]
        },
        "logistics": {
            "carrierCode": carrier,
            "serviceLevel": service_level,
            "originHub": origin_hub,
            "totalWeightKg": round(total_weight, 2),
            "requireSignature": True
        },
        "shippingAddress": {
            "streetAddress": addr.get("streetAddress", "Industrial Ave 1"),
            "city": addr.get("city", "Amsterdam"),
            "province": addr.get("province", "North Holland"),
            "postalCode": addr.get("postalCode", "1016 BS"),
            "countryCode": addr.get("countryCode", "NL")
        },
        "lineItems": line_items,
        "customerFeedback": {
            "feedbackText": feedback_text,
            "rating": feedback_rating,
            "sentimentScore": sentiment_score,
            "channel": "MOBILE_APP",
            "hasActiveComplaint": has_active_complaint,
            "primaryComplaintReason": resolved_complaint_reason,
            "feedbackTimestamp": created_at.isoformat()
        },
        "metadata": {
            "apiVersion": "v2.0",
            "sourcePlatform": "CUSTOM_MOBILE_APP",
            "clientIpAddress": f"10.77.{random.randint(1, 254)}.{random.randint(1, 254)}",
            "retryCount": 0
        },
        "createdAt": created_at.isoformat(),
        "updatedAt": updated_at.isoformat(),
        "estimatedDeliveryDate": est_delivery.isoformat()
    }

    return doc

"""
Order Schema Engine for Redwood Mobile Retail Client.
Guarantees 100% field, nested structure, and data type parity with generate_retail_dataset.py.
Integrates with demo1 and demo2 IAM service account principals.
"""

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

# Demo IAM Principal Profiles
DEMO_PRINCIPALS = {
    "demo1": {
        "iamPrincipal": "demo1-user@elevate-cyvisser.iam.gserviceaccount.com",
        "displayName": "Enterprise VIP Demo Client",
        "customerSegment": "ENTERPRISE_VIP",
        "loyaltyTier": "ENTERPRISE_VIP",
        "isLoyaltyMember": 1,
        "discountRate": 0.25,
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
        "displayName": "Standard Loyalty Demo Client",
        "customerSegment": "STANDARD_LOYALTY",
        "loyaltyTier": "SILVER",
        "isLoyaltyMember": 1,
        "discountRate": 0.10,
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
        "supportMetrics": {
            "supportTicketsCount": 3,
            "openSupportTicketsCount": 1,
            "complaintsCount": 1,
            "returnFrequency": 2,
            "returnRatePercent": 12.0
        }
    }
}

CATALOG_BY_SKU = {item["sku"]: item for item in CATALOG_ITEMS}


def calculate_sentiment_score(rating: int) -> float:
    """Computes sentiment score between -1.0 and 1.0 based on feedback rating."""
    if rating >= 4:
        s_min, s_max = FEEDBACK_SENTIMENT_RANGES["POSITIVE"]
    elif rating == 3:
        s_min, s_max = FEEDBACK_SENTIMENT_RANGES["NEUTRAL"]
    else:
        s_min, s_max = FEEDBACK_SENTIMENT_RANGES["NEGATIVE"]
    return round(random.uniform(s_min, s_max), 3)


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
    now: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Builds a retail transaction order dictionary with 100% schema parity
    to generate_retail_dataset.generate_single_order.
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
        order_id = f"ORD-26-MOB-{rand_id}-IDX{timestamp_idx:07d}"
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
    tier_discount_rate = principal["discountRate"]
    discount_total = round(subtotal * tier_discount_rate, 2)
    shipping_fee = 0.0 if (principal_id == "demo1" and subtotal > 1000.0) else 45.00
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
    eng = principal["engagementMetrics"]
    sup = principal["supportMetrics"]

    doc = {
        "_id": order_id,
        "orderId": order_id,
        "customerId": principal_id,
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

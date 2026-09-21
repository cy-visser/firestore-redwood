"""
Table definitions and Firestore-to-BigQuery column mapping for the CDC service.

Two shapes of table are produced per replicated collection:

``<collection>_cdc``
    An append-only ledger. One row per change event, carrying the operation
    type, the commit timestamp and the full document as JSON. Nothing is ever
    updated or removed, so it doubles as an audit trail and lets you replay
    history.

``<collection>_current``
    A mirror of live state, maintained through the BigQuery Storage Write API's
    CDC support: each row is sent with a ``_CHANGE_TYPE`` of ``UPSERT`` or
    ``DELETE`` against a non-enforced primary key. This is the table analytics
    should read, because it answers "what is true now" without a
    ``QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY ts DESC) = 1`` over
    the whole ledger.

Columns on the ``_current`` tables are typed rather than JSON. The previous
pipeline landed everything in a single JSON column and the churn feature view
dug fields back out with ``COALESCE(SAFE_CAST(JSON_VALUE(...)), default)``,
which silently substituted defaults whenever the document shape drifted. Typed
columns make that drift a load error instead of a wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

# BigQuery types we emit. Kept narrow on purpose; the proto builder in
# bq_cdc_writer.py must know how to encode every one of these.
STRING = "STRING"
INT64 = "INT64"
FLOAT64 = "FLOAT64"
BOOL = "BOOL"
TIMESTAMP = "TIMESTAMP"
JSON = "JSON"


def _dig(source: Dict[str, Any], path: str) -> Any:
    """Follow a dotted path into nested dicts, returning None if it breaks."""
    node: Any = source
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


@dataclass(frozen=True)
class Column:
    """One BigQuery column and how to obtain it from a Firestore document."""

    name: str
    bq_type: str
    #: Dotted path into the decoded document. Ignored when ``derive`` is set.
    source: Optional[str] = None
    #: Computes the value from the whole event instead of a single field.
    derive: Optional[Callable[["ColumnContext"], Any]] = None
    description: str = ""

    def extract(self, ctx: "ColumnContext") -> Any:
        if self.derive is not None:
            return self.derive(ctx)
        if self.source is None:
            return None
        return _dig(ctx.document, self.source)


@dataclass
class ColumnContext:
    """Everything a column extractor may need."""

    document: Dict[str, Any]
    document_id: str
    operation: str
    change_timestamp: datetime
    raw_json: str


@dataclass(frozen=True)
class TableSpec:
    """A BigQuery destination and the columns written to it."""

    table_id: str
    columns: Sequence[Column]
    #: Non-enforced primary key. Required for CDC UPSERT/DELETE; empty means
    #: the table is append-only and no ``_CHANGE_TYPE`` is sent.
    primary_key: Sequence[str] = field(default_factory=tuple)
    partition_field: Optional[str] = None
    clustering: Sequence[str] = field(default_factory=tuple)
    description: str = ""

    @property
    def supports_cdc(self) -> bool:
        return bool(self.primary_key)


# ---------------------------------------------------------------------------
# Shared derivations
# ---------------------------------------------------------------------------

def _op(ctx: ColumnContext) -> str:
    return ctx.operation


def _doc_id(ctx: ColumnContext) -> str:
    return ctx.document_id


def _change_ts(ctx: ColumnContext) -> datetime:
    return ctx.change_timestamp


def _raw(ctx: ColumnContext) -> str:
    return ctx.raw_json


# ---------------------------------------------------------------------------
# retail_cdc: append-only ledger (schema preserved from the Dataflow pipeline
# so existing queries and dashboards keep working)
# ---------------------------------------------------------------------------

ORDERS_CDC_COLUMNS: List[Column] = [
    Column("order_id", STRING, derive=_doc_id, description="Unique order identifier"),
    Column("operation_type", STRING, derive=_op,
           description="Change operation (insert, update, delete)"),
    Column("customer_id", STRING, "customerId"),
    Column("customer_name", STRING, "customerName"),
    Column("customer_email", STRING, "customerEmail"),
    Column("customer_segment", STRING, "customerSegment"),
    Column("order_status", STRING, "orderStatus"),
    Column("payment_status", STRING, "paymentStatus"),
    Column("payment_method", STRING, "paymentMethod"),
    Column("currency", STRING, "currency"),
    Column("grand_total", FLOAT64, "financials.grandTotal"),
    Column("subtotal", FLOAT64, "financials.subtotal"),
    Column("profit_margin", FLOAT64, "financials.profitMargin"),
    Column("change_timestamp", TIMESTAMP, derive=_change_ts),
    Column("document_data", JSON, derive=_raw,
           description="Full raw document payload"),
]

# ---------------------------------------------------------------------------
# retail_current: typed mirror of live order state, keyed by order_id
# ---------------------------------------------------------------------------

ORDERS_CURRENT_COLUMNS: List[Column] = [
    Column("order_id", STRING, derive=_doc_id),
    Column("customer_id", STRING, "customerId"),
    Column("customer_name", STRING, "customerName"),
    Column("customer_email", STRING, "customerEmail"),
    Column("customer_segment", STRING, "customerSegment"),
    Column("order_status", STRING, "orderStatus"),
    Column("payment_status", STRING, "paymentStatus"),
    Column("payment_method", STRING, "paymentMethod"),
    Column("currency", STRING, "currency"),

    # Financials
    Column("subtotal", FLOAT64, "financials.subtotal"),
    Column("tax_amount", FLOAT64, "financials.taxAmount"),
    Column("shipping_fee", FLOAT64, "financials.shippingFee"),
    Column("discount_total", FLOAT64, "financials.discountTotal"),
    Column("grand_total", FLOAT64, "financials.grandTotal"),
    Column("profit_margin", FLOAT64, "financials.profitMargin"),

    # Transactional history as observed at order time. These are retained for
    # convenience, but the churn feature view recomputes them from raw rows so
    # that features and labels can be windowed independently.
    Column("total_spend_90d", FLOAT64, "transactionalMetrics.totalSpend90d"),
    Column("lifetime_spend", FLOAT64, "transactionalMetrics.lifetimeSpend"),
    Column("avg_order_value", FLOAT64, "transactionalMetrics.avgOrderValue"),
    Column("purchase_frequency_monthly", FLOAT64,
           "transactionalMetrics.purchaseFrequencyMonthly"),
    Column("days_since_last_purchase", INT64,
           "transactionalMetrics.daysSinceLastPurchase"),
    Column("orders_count_last_12m", INT64, "transactionalMetrics.ordersCountLast12m"),

    # Engagement
    Column("login_frequency_monthly", INT64, "engagement.loginFrequencyMonthly"),
    Column("avg_session_duration_minutes", FLOAT64,
           "engagement.avgSessionDurationMinutes"),
    Column("app_engagement_score", FLOAT64, "engagement.appEngagementScore"),
    Column("app_sessions_last_30d", INT64, "engagement.appSessionsLast30d"),
    Column("cart_abandonment_count", INT64, "engagement.cartAbandonmentCount"),
    Column("abandoned_cart_value_90d", FLOAT64, "engagement.abandonedCartValue90d"),

    # Support and satisfaction
    Column("support_tickets_count", INT64, "supportMetrics.supportTicketsCount"),
    Column("open_support_tickets_count", INT64,
           "supportMetrics.openSupportTicketsCount"),
    Column("complaints_count", INT64, "supportMetrics.complaintsCount"),
    Column("return_rate_percent", FLOAT64, "supportMetrics.returnRatePercent"),
    Column("sentiment_score", FLOAT64, "supportMetrics.sentimentScore"),
    Column("has_active_complaint", BOOL, "supportMetrics.hasActiveComplaint"),
    Column("primary_complaint_reason", STRING, "supportMetrics.primaryComplaintReason"),
    Column("feedback_rating", INT64, "customerFeedback.rating"),
    Column("feedback_channel", STRING, "customerFeedback.channel"),

    # Account state
    Column("loyalty_tier", STRING, "accountState.loyaltyTier"),
    Column("is_loyalty_member", BOOL, "accountState.isLoyaltyMember"),
    Column("account_age_days", INT64, "accountState.accountAgeDays"),

    # Shipping
    Column("shipping_city", STRING, "shippingAddress.city"),
    Column("shipping_country_code", STRING, "shippingAddress.countryCode"),

    # Timestamps
    Column("created_at", TIMESTAMP, "createdAt"),
    Column("updated_at", TIMESTAMP, "updatedAt"),
    Column("change_timestamp", TIMESTAMP, derive=_change_ts),
]

# ---------------------------------------------------------------------------
# customers_current: typed mirror of customer profiles, keyed by customer_id
# ---------------------------------------------------------------------------

CUSTOMERS_CURRENT_COLUMNS: List[Column] = [
    Column("customer_id", STRING, derive=_doc_id),
    Column("customer_name", STRING, "customerName"),
    Column("customer_email", STRING, "customerEmail"),
    Column("customer_segment", STRING, "customerSegment"),
    Column("loyalty_tier", STRING, "loyaltyTier"),
    Column("is_loyalty_member", BOOL, "isLoyaltyMember"),
    Column("account_age_days", INT64, "accountAgeDays"),
    Column("lifetime_spend", FLOAT64, "lifetimeSpend"),
    Column("orders_count", INT64, "ordersCount"),
    Column("last_order_at", TIMESTAMP, "lastOrderAt"),
    Column("days_since_last_purchase", INT64, "daysSinceLastPurchase"),
    Column("login_frequency_monthly", INT64, "engagement.loginFrequencyMonthly"),
    Column("avg_session_duration_minutes", FLOAT64,
           "engagement.avgSessionDurationMinutes"),
    Column("app_engagement_score", FLOAT64, "engagement.appEngagementScore"),
    Column("cart_abandonment_count", INT64, "engagement.cartAbandonmentCount"),
    Column("support_tickets_count", INT64, "supportMetrics.supportTicketsCount"),
    Column("open_support_tickets_count", INT64, "supportMetrics.openSupportTicketsCount"),
    Column("complaints_count", INT64, "supportMetrics.complaintsCount"),
    Column("return_rate_percent", FLOAT64, "supportMetrics.returnRatePercent"),
    Column("iam_principal", STRING, "iamPrincipal"),
    Column("is_demo_persona", BOOL, "isDemoPersona"),
    Column("updated_at", TIMESTAMP, "updatedAt"),
    Column("change_timestamp", TIMESTAMP, derive=_change_ts),
]


@dataclass(frozen=True)
class CollectionRoute:
    """Which tables a Firestore collection replicates into."""

    collection: str
    tables: Sequence[TableSpec]


def build_routes(
    orders_collection: str,
    orders_cdc_table: str,
    customers_collection: str,
) -> Dict[str, CollectionRoute]:
    """Build the collection-to-table routing table.

    Names come from configuration rather than constants so the same image can
    be pointed at a different database or dataset without a rebuild.
    """
    orders_cdc = TableSpec(
        table_id=orders_cdc_table,
        columns=ORDERS_CDC_COLUMNS,
        primary_key=(),  # append-only ledger
        partition_field="change_timestamp",
        clustering=("order_id", "customer_id", "order_status"),
        description="Append-only change ledger replicated from Firestore orders",
    )
    orders_current = TableSpec(
        table_id=f"{orders_collection}_current",
        columns=ORDERS_CURRENT_COLUMNS,
        primary_key=("order_id",),
        clustering=("customer_id", "order_status"),
        description="Live mirror of Firestore orders, maintained by CDC UPSERT/DELETE",
    )
    customers_current = TableSpec(
        table_id=f"{customers_collection}_current",
        columns=CUSTOMERS_CURRENT_COLUMNS,
        primary_key=("customer_id",),
        clustering=("customer_segment", "loyalty_tier"),
        description="Live mirror of Firestore customer profiles",
    )

    return {
        orders_collection: CollectionRoute(orders_collection, (orders_cdc, orders_current)),
        customers_collection: CollectionRoute(customers_collection, (customers_current,)),
    }

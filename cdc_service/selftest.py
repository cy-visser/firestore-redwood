"""
Offline checks for the CDC mapping layer.

These exercise decoding, coercion, proto construction and DDL rendering with no
BigQuery or Firestore connection, so a broken column mapping fails here in
milliseconds rather than after a container build and deploy.

Run with:  .venv/bin/python cdc_service/selftest.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bq_cdc_writer import (  # noqa: E402
    CHANGE_SEQUENCE_COLUMN,
    CHANGE_TYPE_COLUMN,
    build_message_class,
    coerce,
    create_table_ddl,
    extract_row,
    sequence_number,
)
from firestore_event import decode_fields, parse_document_event  # noqa: E402
from schemas import BOOL, FLOAT64, INT64, TIMESTAMP, ColumnContext, build_routes  # noqa: E402

FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok    {label}")


def check_true(label: str, value) -> None:
    check(label, bool(value), True)


ROUTES = build_routes("retail", "retail_cdc", "customers")
ORDERS_CDC, ORDERS_CURRENT = ROUTES["retail"].tables
CUSTOMERS_CURRENT = ROUTES["customers"].tables[0]


def sample_event(operation: str = "created") -> dict:
    """A Firestore protojson payload shaped like what Eventarc delivers."""
    document = {
        "name": "projects/demo/databases/redwood/documents/retail/ORD-2609-DEMO1-0017",
        "createTime": "2026-08-31T10:15:00.123456Z",
        "updateTime": "2026-08-31T10:15:00.123456Z",
        "fields": {
            "orderId": {"stringValue": "ORD-2609-DEMO1-0017"},
            "customerId": {"stringValue": "cust_demo1"},
            "customerName": {"stringValue": "Demo One BV"},
            "customerEmail": {"stringValue": "demo1@example.com"},
            "customerSegment": {"stringValue": "ENTERPRISE_VIP"},
            "orderStatus": {"stringValue": "DELIVERED"},
            "paymentStatus": {"stringValue": "SETTLED"},
            "paymentMethod": {"stringValue": "INVOICE_NET30"},
            "currency": {"stringValue": "EUR"},
            "financials": {
                "mapValue": {
                    "fields": {
                        "subtotal": {"doubleValue": 1200.5},
                        "taxAmount": {"doubleValue": 252.11},
                        "shippingFee": {"doubleValue": 0.0},
                        "discountTotal": {"doubleValue": 180.08},
                        "grandTotal": {"doubleValue": 1272.53},
                        "profitMargin": {"doubleValue": 0.31},
                    }
                }
            },
            "transactionalMetrics": {
                "mapValue": {
                    "fields": {
                        "totalSpend90d": {"doubleValue": 4210.0},
                        "lifetimeSpend": {"doubleValue": 2589010.0},
                        "avgOrderValue": {"doubleValue": 1521.0},
                        "purchaseFrequencyMonthly": {"doubleValue": 1.4},
                        "daysSinceLastPurchase": {"integerValue": "21"},
                        "ordersCountLast12m": {"integerValue": "11"},
                    }
                }
            },
            "engagement": {
                "mapValue": {
                    "fields": {
                        "loginFrequencyMonthly": {"integerValue": "18"},
                        "avgSessionDurationMinutes": {"doubleValue": 12.4},
                        "appEngagementScore": {"doubleValue": 0.82},
                        "appSessionsLast30d": {"integerValue": "24"},
                        "cartAbandonmentCount": {"integerValue": "1"},
                        "abandonedCartValue90d": {"doubleValue": 0.0},
                    }
                }
            },
            "supportMetrics": {
                "mapValue": {
                    "fields": {
                        "supportTicketsCount": {"integerValue": "0"},
                        "openSupportTicketsCount": {"integerValue": "0"},
                        "complaintsCount": {"integerValue": "0"},
                        "returnRatePercent": {"doubleValue": 1.2},
                        "sentimentScore": {"doubleValue": 0.74},
                        "hasActiveComplaint": {"booleanValue": False},
                        "primaryComplaintReason": {"nullValue": None},
                    }
                }
            },
            "customerFeedback": {
                "mapValue": {
                    "fields": {
                        "rating": {"integerValue": "5"},
                        "channel": {"stringValue": "MOBILE_APP"},
                    }
                }
            },
            "accountState": {
                "mapValue": {
                    "fields": {
                        "loyaltyTier": {"stringValue": "ENTERPRISE_VIP"},
                        "isLoyaltyMember": {"booleanValue": True},
                        "accountAgeDays": {"integerValue": "900"},
                    }
                }
            },
            "shippingAddress": {
                "mapValue": {
                    "fields": {
                        "city": {"stringValue": "Amsterdam"},
                        "countryCode": {"stringValue": "NL"},
                    }
                }
            },
            "lineItems": {
                "arrayValue": {
                    "values": [
                        {
                            "mapValue": {
                                "fields": {
                                    "sku": {"stringValue": "SKU-1"},
                                    "quantity": {"integerValue": "3"},
                                }
                            }
                        }
                    ]
                }
            },
            "createdAt": {"stringValue": "2026-08-31T10:15:00"},
            "updatedAt": {"stringValue": "2026-08-31T10:42:00"},
        },
    }

    if operation == "deleted":
        return {"oldValue": document, "updateMask": {}}
    if operation == "updated":
        return {"oldValue": document, "value": document, "updateMask": {"fieldPaths": ["orderStatus"]}}
    return {"value": document, "updateMask": {}}


def test_value_decoding() -> None:
    print("\n[decoding]")
    fields = sample_event()["value"]["fields"]
    decoded = decode_fields(fields)

    check("stringValue", decoded["customerId"], "cust_demo1")
    check("nested mapValue", decoded["financials"]["grandTotal"], 1272.53)
    # integerValue arrives as a JSON string because int64 is not JSON-safe.
    check("integerValue cast to int", decoded["transactionalMetrics"]["daysSinceLastPurchase"], 21)
    check_true("integerValue is int not str",
               isinstance(decoded["engagement"]["loginFrequencyMonthly"], int))
    check("booleanValue", decoded["accountState"]["isLoyaltyMember"], True)
    check("nullValue", decoded["supportMetrics"]["primaryComplaintReason"], None)
    check("arrayValue length", len(decoded["lineItems"]), 1)
    check("arrayValue element", decoded["lineItems"][0]["quantity"], 3)


def test_event_parsing() -> None:
    print("\n[event parsing]")
    created = parse_document_event(sample_event("created"), "evt-1",
                                   "google.cloud.firestore.document.v1.created")
    check("created operation", created.operation, "insert")
    check("collection", created.collection, "retail")
    check("document id", created.document_id, "ORD-2609-DEMO1-0017")
    check("database", created.database, "redwood")
    check("document path", created.document_path, "retail/ORD-2609-DEMO1-0017")
    check_true("is_delete false", not created.is_delete)
    # Nanosecond-precision timestamps must not blow up fromisoformat.
    check("commit time", created.event_time,
          datetime(2026, 8, 31, 10, 15, 0, 123456, tzinfo=timezone.utc))

    deleted = parse_document_event(sample_event("deleted"), "evt-2",
                                   "google.cloud.firestore.document.v1.deleted")
    check("deleted operation", deleted.operation, "delete")
    check_true("is_delete true", deleted.is_delete)
    # A delete carries no `value`, so identifiers must come from oldValue.
    check("delete falls back to oldValue", deleted.effective_data["customerId"], "cust_demo1")

    updated = parse_document_event(sample_event("updated"), "evt-3",
                                   "google.cloud.firestore.document.v1.updated")
    check("updated operation", updated.operation, "update")
    check("updateMask", updated.updated_fields, ["orderStatus"])

    # `written` is ambiguous; it must be resolved from which side is populated.
    inferred = parse_document_event(sample_event("created"), "evt-4",
                                    "google.cloud.firestore.document.v1.written")
    check("written inferred as insert", inferred.operation, "insert")


def test_coercion() -> None:
    print("\n[coercion]")
    check("int from numeric string", coerce("42", INT64), 42)
    check("int from float", coerce(42.9, INT64), 42)
    check("float from string", coerce("3.5", FLOAT64), 3.5)
    check("bool from 'true'", coerce("true", BOOL), True)
    check("bool from 'no'", coerce("no", BOOL), False)
    check("None passes through", coerce(None, INT64), None)
    check("garbage becomes None", coerce("not-a-number", INT64), None)
    check("naive iso timestamp",
          coerce("2026-08-31T10:15:00", TIMESTAMP), 1788171300000000)
    check("zulu timestamp",
          coerce("2026-08-31T10:15:00Z", TIMESTAMP), 1788171300000000)
    check("datetime timestamp",
          coerce(datetime(2026, 8, 31, 10, 15, tzinfo=timezone.utc), TIMESTAMP),
          1788171300000000)


def test_row_extraction() -> None:
    print("\n[row extraction]")
    event = parse_document_event(sample_event("created"), "evt-1",
                                 "google.cloud.firestore.document.v1.created")
    ctx = ColumnContext(
        document=event.effective_data,
        document_id=event.document_id,
        operation=event.operation,
        change_timestamp=event.event_time,
        raw_json=json.dumps(event.effective_data, default=str),
    )

    ledger = extract_row(ORDERS_CDC, ctx)
    check("ledger order_id", ledger["order_id"], "ORD-2609-DEMO1-0017")
    check("ledger operation_type", ledger["operation_type"], "insert")
    check("ledger grand_total", ledger["grand_total"], 1272.53)
    check_true("ledger document_data is JSON text", isinstance(ledger["document_data"], str))
    check_true("ledger document_data round-trips",
               json.loads(ledger["document_data"])["customerId"] == "cust_demo1")

    current = extract_row(ORDERS_CURRENT, ctx)
    check("current order_id", current["order_id"], "ORD-2609-DEMO1-0017")
    check("current nested float", current["tax_amount"], 252.11)
    check("current nested int", current["days_since_last_purchase"], 21)
    check("current bool", current["is_loyalty_member"], True)
    check("current feedback rating", current["feedback_rating"], 5)
    check("current shipping city", current["shipping_city"], "Amsterdam")
    check("current created_at micros", current["created_at"], 1788171300000000)
    # Absent optional field must stay None so it lands as SQL NULL.
    check("absent field stays None", current["primary_complaint_reason"], None)

    missing = [c.name for c in ORDERS_CURRENT.columns if c.name not in current]
    check("every current column produced", missing, [])


def test_proto_build() -> None:
    print("\n[protobuf]")
    cdc_class = build_message_class(ORDERS_CURRENT, include_cdc=True)
    message = cdc_class()
    message.order_id = "ORD-1"
    message.grand_total = 10.5
    message.days_since_last_purchase = 3
    setattr(message, CHANGE_TYPE_COLUMN, "UPSERT")
    setattr(message, CHANGE_SEQUENCE_COLUMN, "1a2b")

    blob = message.SerializeToString()
    check_true("serialises", len(blob) > 0)

    decoded = cdc_class()
    decoded.ParseFromString(blob)
    check("round-trip order_id", decoded.order_id, "ORD-1")
    check("round-trip change type", getattr(decoded, CHANGE_TYPE_COLUMN), "UPSERT")

    # proto2 presence: an unset numeric must be distinguishable from zero,
    # otherwise BigQuery would store 0.0 where the document had nothing.
    check_true("unset field reports absent", not decoded.HasField("subtotal"))
    check_true("set field reports present", decoded.HasField("grand_total"))

    zeroed = cdc_class()
    zeroed.subtotal = 0.0
    check_true("explicit zero reports present", zeroed.HasField("subtotal"))

    # The append-only ledger must not carry CDC pseudo-columns.
    ledger_class = build_message_class(ORDERS_CDC, include_cdc=False)
    names = {f.name for f in ledger_class.DESCRIPTOR.fields}
    check("ledger has no _CHANGE_TYPE", CHANGE_TYPE_COLUMN in names, False)

    # Building twice must not collide in the descriptor pool.
    build_message_class(ORDERS_CURRENT, include_cdc=True)
    print("  ok    rebuild does not collide")


def test_ddl() -> None:
    print("\n[ddl]")
    ledger_ddl = create_table_ddl("proj", "ds", ORDERS_CDC)
    check_true("ledger partitioned", "PARTITION BY DATE(`change_timestamp`)" in ledger_ddl)
    check_true("ledger has no primary key", "PRIMARY KEY" not in ledger_ddl)
    check_true("ledger document_data is JSON", "`document_data` JSON" in ledger_ddl)

    current_ddl = create_table_ddl("proj", "ds", ORDERS_CURRENT)
    # CDC writes are rejected unless the key is declared NOT ENFORCED.
    check_true("current has non-enforced PK",
               "PRIMARY KEY (`order_id`) NOT ENFORCED" in current_ddl)
    check_true("current clustered", "CLUSTER BY `customer_id`, `order_status`" in current_ddl)

    customers_ddl = create_table_ddl("proj", "ds", CUSTOMERS_CURRENT)
    check_true("customers keyed on customer_id",
               "PRIMARY KEY (`customer_id`) NOT ENFORCED" in customers_ddl)


def test_sequence_numbers() -> None:
    print("\n[sequence numbers]")
    earlier = sequence_number(datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc))
    later = sequence_number(datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc))
    # Eventarc does not guarantee ordering, so BigQuery relies on this being
    # monotonic to stop a redelivered old version overwriting a newer one.
    check_true("hex encoded", all(c in "0123456789abcdef" for c in later))
    check_true("later sorts above earlier", int(later, 16) > int(earlier, 16))
    check("stable for same instant", earlier,
          sequence_number(datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)))


def test_delete_row_is_key_only() -> None:
    print("\n[delete rows]")

    class _StubWriter:
        """Exercises build_row without opening a stream."""

        def __init__(self, spec):
            from bq_cdc_writer import TableWriter

            self._spec = spec
            self.build_row = TableWriter.build_row.__get__(self)
            self._message_class = build_message_class(spec, include_cdc=spec.supports_cdc)

    stub = _StubWriter(ORDERS_CURRENT)
    blob = stub.build_row(
        {"order_id": "ORD-1", "grand_total": 99.0, "customer_id": "cust_demo1"},
        change_type="DELETE",
        change_sequence="ff",
    )
    parsed = build_message_class(ORDERS_CURRENT, include_cdc=True)()
    parsed.ParseFromString(blob)

    check("delete keeps key", parsed.order_id, "ORD-1")
    check_true("delete drops non-key columns", not parsed.HasField("grand_total"))
    check_true("delete drops non-key ids", not parsed.HasField("customer_id"))
    check("delete tagged", getattr(parsed, CHANGE_TYPE_COLUMN), "DELETE")


def main() -> int:
    print("=" * 62)
    print(" Redwood CDC mapping self-test")
    print("=" * 62)

    test_value_decoding()
    test_event_parsing()
    test_coercion()
    test_row_extraction()
    test_proto_build()
    test_ddl()
    test_sequence_numbers()
    test_delete_row_is_key_only()

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

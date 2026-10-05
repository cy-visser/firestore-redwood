"""Offline checks for the CDC mapping layer."""

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


def test_appends_overlap() -> None:
    """Appends to one table must be able to be in flight simultaneously.

    The writer lock is meant to cover handing a request to the stream, not
    waiting for BigQuery to answer. Widening it back over ``future.result``
    would serialise a table down to one round trip at a time, which throttled
    the whole service to roughly two events a second and left a re-seed backlog
    draining for the better part of an hour. The barrier below only releases
    once all three appends are waiting at once, so that regression shows up
    here as a timeout rather than in production as a slow queue.
    """
    print("\n[append pipelining]")

    import threading

    from bq_cdc_writer import TableWriter

    barrier = threading.Barrier(3, timeout=5)

    class _Future:
        def result(self, timeout=None):
            barrier.wait()

    class _Stream:
        def send(self, request):
            return _Future()

    table_writer = TableWriter.__new__(TableWriter)
    table_writer._spec = ORDERS_CURRENT
    table_writer._lock = threading.Lock()
    table_writer._stream = _Stream()
    table_writer._message_class = build_message_class(ORDERS_CURRENT, include_cdc=True)

    errors: list[BaseException] = []

    def run() -> None:
        try:
            table_writer.append([b"row"])
        except BaseException as err:  # noqa: BLE001 - reported through `errors`
            errors.append(err)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    check_true("three appends in flight at once", not errors)
    check_true("no appender left hanging", not any(t.is_alive() for t in threads))


def test_protobuf_wire_format() -> None:
    """The real wire format must decode to the same values as the JSON fixture.

    Eventarc only accepts application/protobuf for Firestore sources, so every
    production event takes this path. Building the message, serialising it and
    reading it back proves the MessageToDict normalisation actually yields the
    protojson shape the decoder expects, rather than us assuming it does.
    """
    print("\n[protobuf wire format]")

    try:
        from google.events.cloud import firestore_v1
    except ImportError:
        print("  SKIP  google-events not installed")
        return

    from firestore_event import parse_event_body

    event_data = firestore_v1.DocumentEventData()
    document = event_data.value
    document.name = (
        "projects/demo/databases/redwood/documents/retail/ORD-2609-DEMO1-0017"
    )
    document.fields["orderId"] = firestore_v1.Value(
        string_value="ORD-2609-DEMO1-0017"
    )
    document.fields["customerId"] = firestore_v1.Value(string_value="cust_demo1")
    # Exercises the int64-as-string rule that trips up naive JSON handling.
    document.fields["quantity"] = firestore_v1.Value(integer_value=21)
    document.fields["isLoyaltyMember"] = firestore_v1.Value(boolean_value=True)
    document.fields["financials"] = firestore_v1.Value(
        map_value=firestore_v1.MapValue(
            fields={"grandTotal": firestore_v1.Value(double_value=1272.53)}
        )
    )

    raw = type(event_data).pb(event_data).SerializeToString()
    check_true("serialises to bytes", isinstance(raw, bytes) and len(raw) > 0)

    payload = parse_event_body(raw, "application/protobuf")
    check_true("protojson has value", "value" in payload)

    decoded = decode_fields(payload["value"]["fields"])
    check("proto string", decoded["customerId"], "cust_demo1")
    check("proto int64", decoded["quantity"], 21)
    check_true("proto int64 is int", isinstance(decoded["quantity"], int))
    check("proto bool", decoded["isLoyaltyMember"], True)
    check("proto nested double", decoded["financials"]["grandTotal"], 1272.53)

    event = parse_document_event(
        payload, "evt-proto", "google.cloud.firestore.document.v1.created"
    )
    check("proto collection", event.collection, "retail")
    check("proto document id", event.document_id, "ORD-2609-DEMO1-0017")

    # A JSON body must still work, so a mislabelled or future-JSON trigger does
    # not take the service down.
    as_json = json.dumps(sample_event("created")).encode()
    check_true("json body still parses",
               "value" in parse_event_body(as_json, "application/json"))
    check_true("json body parses despite protobuf header",
               "value" in parse_event_body(as_json, "application/protobuf"))
    check("empty body yields empty dict", parse_event_body(b"", None), {})


class _FakeDocument:
    def __init__(self) -> None:
        self.data: dict = {}
        self.writes = 0

    def set(self, document: dict, merge: bool = False) -> None:
        self.writes += 1
        if merge:
            self.data.update(document)
        else:
            self.data = dict(document)


class _FakeCollection:
    def __init__(self) -> None:
        self.documents: dict[str, _FakeDocument] = {}

    def document(self, document_id: str) -> _FakeDocument:
        return self.documents.setdefault(document_id, _FakeDocument())


class _FakeFirestore:
    """Just enough of the Firestore client for the trace write path."""

    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def collection(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())

    def trace(self, document_id: str):
        """The trace body written under ``document_id``, or None."""
        collection = self.collections.get("pipeline_traces")
        if collection is None:
            return None
        document = collection.documents.get(document_id)
        return document.data if document else None


def _load_cdc_main():
    """Import ``main`` with the BigQuery sink stubbed out.

    ``main`` builds a :class:`CdcSink` at import time and that constructor
    opens a BigQuery Storage Write client, which wants credentials this
    offline test does not have. Patch the name on ``bq_cdc_writer`` before
    ``main`` binds it, then restore the real class so nothing else is
    affected.
    """
    import bq_cdc_writer

    class _StubSink:
        def __init__(self, project: str, dataset: str) -> None:
            self.project = project
            self.dataset = dataset

    original = bq_cdc_writer.CdcSink
    bq_cdc_writer.CdcSink = _StubSink
    os.environ.setdefault("GCP_PROJECT_ID", "redwood-selftest")
    os.environ.setdefault("BIGQUERY_DATASET", "redwood_selftest")
    os.environ["ENSURE_TABLES"] = "false"
    try:
        import main as cdc_main
    finally:
        bq_cdc_writer.CdcSink = original
    return cdc_main


def order_event(document_id: str, operation: str = "created"):
    """A sample event re-keyed onto ``document_id``."""
    payload = json.loads(json.dumps(sample_event(operation)))
    for key in ("value", "oldValue"):
        if key in payload:
            payload[key]["name"] = (
                "projects/demo/databases/redwood/documents/retail/" + document_id
            )
            payload[key]["fields"]["orderId"]["stringValue"] = document_id
    return parse_document_event(
        payload,
        "evt-order",
        "google.cloud.firestore.document.v1.created",
        datetime(2026, 8, 31, 10, 15, 0, tzinfo=timezone.utc),
    )


def test_order_trace() -> None:
    print("\n[order trace]")
    cdc_main = _load_cdc_main()
    fake = _FakeFirestore()
    original_get_firestore = cdc_main.get_firestore
    cdc_main.get_firestore = lambda: fake

    try:
        event_time = datetime(2026, 8, 31, 10, 15, 0, tzinfo=timezone.utc)
        received_at = datetime(2026, 8, 31, 10, 15, 0, 250000, tzinfo=timezone.utc)

        traced = order_event("ORD-26-MOB-0042")
        cdc_main._write_order_trace(
            traced,
            "evt-mob",
            event_time,
            received_at,
            118.4,
            {"retail_cdc": "APPENDED", "retail_current": "APPENDED"},
        )

        trace = fake.trace("order_ORD-26-MOB-0042")
        check_true("mobile order writes a trace", trace is not None)
        check("trace is tagged as an order", trace["kind"], "ORDER")
        check("trace carries the order id", trace["orderId"], "ORD-26-MOB-0042")
        check("trace carries the customer id", trace["customerId"], "cust_demo1")
        check("eventarc delivery measured", trace["cdcDeliveryMs"], 250.0)
        check("bigquery append measured", trace["bqWriteMs"], 118.4)
        check("total is delivery plus append", trace["bqTotalMs"], 368.4)
        check(
            "destination tables recorded",
            trace["bqTables"],
            ["retail_cdc", "retail_current"],
        )

        # Eventarc stamps ce-time on a different machine's clock. Skew must
        # never surface as a negative latency in the console.
        cdc_main._write_order_trace(
            traced, "evt-mob", received_at, event_time, 5.0, {}
        )
        check(
            "clock skew clamps delivery to zero",
            fake.trace("order_ORD-26-MOB-0042")["cdcDeliveryMs"],
            0.0,
        )

        # The seeded history replays through this same endpoint on a backfill.
        # Tracing all of it would bury the order the presenter just placed.
        seeded = order_event("ORD-2609-DEMO1-0017")
        cdc_main._write_order_trace(
            seeded, "evt-seed", event_time, received_at, 90.0, {}
        )
        check(
            "seeded order is not traced",
            fake.trace("order_ORD-2609-DEMO1-0017"),
            None,
        )
    finally:
        cdc_main.get_firestore = original_get_firestore


def main() -> int:
    print("=" * 62)
    print(" Redwood CDC mapping self-test")
    print("=" * 62)

    test_value_decoding()
    test_event_parsing()
    test_protobuf_wire_format()
    test_coercion()
    test_row_extraction()
    test_proto_build()
    test_ddl()
    test_sequence_numbers()
    test_delete_row_is_key_only()
    test_appends_overlap()
    test_order_trace()

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

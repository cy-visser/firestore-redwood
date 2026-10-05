"""Firestore test doubles for offline unit tests."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Comparators for query filter operators.
_OPERATORS = {
    "==": lambda value, target: value == target,
    ">=": lambda value, target: value is not None and value >= target,
    "<": lambda value, target: value is not None and value < target,
}


class IndexMissingError(Exception):
    """What Firestore raises when a composite index does not exist yet.

    Stands in for google.api_core.exceptions.FailedPrecondition, which is what
    an ordered query over an unindexed field pair actually produces.
    """


class FakeSnapshot:
    def __init__(self, doc_id, data, collection=None):
        self.id = doc_id
        self._data = data
        self._collection = collection

    @property
    def exists(self):
        return self._data is not None

    @property
    def reference(self):
        return FakeDocument(self._collection, self.id)

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocument:
    def __init__(self, collection, doc_id):
        self._collection = collection
        self.id = doc_id

    def get(self):
        return FakeSnapshot(self.id, self._collection.docs.get(self.id), self._collection)

    def set(self, data, merge=False):
        if merge and self.id in self._collection.docs:
            self._collection.docs[self.id].update(data)
        else:
            self._collection.docs[self.id] = dict(data)

    def update(self, data):
        if self.id not in self._collection.docs:
            raise KeyError(self.id)
        self._collection.docs[self.id].update(data)

    def delete(self):
        self._collection.docs.pop(self.id, None)


class FakeQuery:
    """Filters, ordering and a limit, which is all the code under test uses."""

    def __init__(self, collection, filters=None, limit=None, order=None):
        self._collection = collection
        self._filters = filters or []
        self._limit = limit
        self._order = order

    def where(self, filter=None):  # noqa: A002 - matches the Firestore API
        return FakeQuery(
            self._collection,
            self._filters + [(filter.field_path, filter.op_string, filter.value)],
            self._limit,
            self._order,
        )

    def limit(self, count):
        return FakeQuery(self._collection, self._filters, count, self._order)

    def order_by(self, field_path, direction="ASCENDING"):
        return FakeQuery(
            self._collection,
            self._filters,
            self._limit,
            (field_path, direction),
        )

    def _matches(self, data: Dict[str, Any]) -> bool:
        for field, op, target in self._filters:
            compare = _OPERATORS.get(op)
            if compare is None:
                raise NotImplementedError(f"FakeQuery does not implement '{op}'")
            if not compare(data.get(field), target):
                return False
        return True

    def stream(self):
        if self._order is not None and self._collection.fail_ordered_queries:
            raise IndexMissingError(
                "The query requires an index that does not exist."
            )

        results: List[FakeSnapshot] = [
            FakeSnapshot(doc_id, data, self._collection)
            for doc_id, data in self._collection.docs.items()
            if self._matches(data)
        ]

        if self._order is not None:
            field, direction = self._order
            results.sort(
                key=lambda snap: _sort_key(snap.to_dict().get(field)),
                reverse=direction.upper().startswith("DESC"),
            )

        if self._limit is not None:
            results = results[: self._limit]
        return iter(results)


def _sort_key(value: Any):
    """Order mixed-type fields without raising.

    createdAt is an ISO string on an order and a datetime on a session, and a
    sort that crashes when a document is missing the field is a fake that
    fails for reasons the real thing would not.
    """
    if value is None:
        return (0, "")
    if hasattr(value, "isoformat"):
        return (1, value.isoformat())
    return (1, str(value))


class FakeCollection:
    def __init__(self):
        self.docs: Dict[str, Dict[str, Any]] = {}
        # Set to make every ordered query raise, which is how Firestore
        # behaves before a composite index has been built.
        self.fail_ordered_queries = False

    def document(self, doc_id):
        return FakeDocument(self, doc_id)

    def where(self, filter=None):  # noqa: A002 - matches the Firestore API
        return FakeQuery(self).where(filter=filter)

    def order_by(self, field_path, direction="ASCENDING"):
        return FakeQuery(self).order_by(field_path, direction=direction)

    def limit(self, count):
        return FakeQuery(self).limit(count)


class FakeFirestore:
    def __init__(self):
        self.collections: Dict[str, FakeCollection] = {}

    def collection(self, name) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


def make_offer(
    offer_id: str = "off_sess_1_retention",
    customer_id: str = "cust_demo2",
    session_id: str = "sess_1",
    discount_percent: int = 20,
    status: str = "ACTIVE",
    valid_until: Optional[str] = "2099-01-01T00:00:00+00:00",
    created_at: str = "2026-09-22T08:00:00+00:00",
    free_express_shipping: bool = False,
    order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """One loyalty_offers document, shaped like the agent's persist_offer."""
    return {
        "offerId": offer_id,
        "customerId": customer_id,
        "sessionId": session_id,
        "status": status,
        "promoCode": f"RETENTION-{discount_percent}",
        "discountPercent": discount_percent,
        "freeExpressShipping": free_express_shipping,
        "createdAt": created_at,
        "validUntil": valid_until,
        "claimedAt": None,
        "orderId": order_id,
    }

"""
Pytest Fixtures for Redwood Retail Loyalty Offer Agent Test Suite.
Provides hermetic mock implementations of Firestore Native, BigQuery ML,
and Vertex AI Gemini clients without external network calls.
"""

import sys
import copy
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))



class MockFirestoreDocumentSnapshot:
    """Mock implementation of Google Cloud Firestore DocumentSnapshot."""
    def __init__(self, doc_id, data, exists=True):
        self.id = doc_id
        self._data = copy.deepcopy(data) if data else {}
        self.exists = exists

    def to_dict(self):
        return copy.deepcopy(self._data)

    def get(self, field_path):
        parts = field_path.split(".")
        curr = self._data
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return None
        return curr


class MockFirestoreDocumentReference:
    """Mock implementation of Google Cloud Firestore DocumentReference."""
    def __init__(self, doc_id, collection_ref, store):
        self.id = doc_id
        self.collection_ref = collection_ref
        self._store = store

    def get(self, transaction=None):
        full_path = f"{self.collection_ref.path}/{self.id}"
        if full_path in self._store:
            return MockFirestoreDocumentSnapshot(self.id, self._store[full_path], exists=True)
        return MockFirestoreDocumentSnapshot(self.id, None, exists=False)

    def set(self, data, merge=False):
        full_path = f"{self.collection_ref.path}/{self.id}"
        if merge and full_path in self._store:
            self._store[full_path].update(copy.deepcopy(data))
        else:
            self._store[full_path] = copy.deepcopy(data)
        self.collection_ref._notify_listeners(self.id, self._store[full_path])

    def update(self, data):
        full_path = f"{self.collection_ref.path}/{self.id}"
        if full_path not in self._store:
            raise KeyError(f"Document {full_path} not found for update.")
        self._store[full_path].update(copy.deepcopy(data))
        self.collection_ref._notify_listeners(self.id, self._store[full_path])


class MockFirestoreCollectionReference:
    """Mock implementation of Google Cloud Firestore CollectionReference."""
    def __init__(self, path, store):
        self.path = path
        self._store = store
        self._listeners = []

    def document(self, doc_id=None):
        if not doc_id:
            doc_id = f"doc_{int(time.time() * 1000)}_{len(self._store)}"
        return MockFirestoreDocumentReference(doc_id, self, self._store)

    def where(self, field, op, value):
        return MockFirestoreQuery(self, [(field, op, value)])

    def on_snapshot(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify_listeners(self, doc_id, data):
        snapshot = MockFirestoreDocumentSnapshot(doc_id, data, exists=True)
        for listener in list(self._listeners):
            listener([snapshot], None, None)


class MockFirestoreQuery:
    """Mock implementation of Firestore Query filtering."""
    def __init__(self, collection_ref, filters):
        self.collection_ref = collection_ref
        self.filters = list(filters)

    def where(self, field, op, value):
        new_filters = list(self.filters)
        new_filters.append((field, op, value))
        return MockFirestoreQuery(self.collection_ref, new_filters)

    def stream(self):
        results = []
        prefix = f"{self.collection_ref.path}/"
        for path, doc_data in self.collection_ref._store.items():
            if not path.startswith(prefix):
                continue
            doc_id = path[len(prefix):]
            if "/" in doc_id:
                continue

            matches = True
            for field, op, val in self.filters:
                doc_val = doc_data.get(field)
                if op == "==" and doc_val != val:
                    matches = False
                    break
                elif op == ">" and not (doc_val is not None and doc_val > val):
                    matches = False
                    break
                elif op == "<" and not (doc_val is not None and doc_val < val):
                    matches = False
                    break
            if matches:
                results.append(MockFirestoreDocumentSnapshot(doc_id, doc_data, exists=True))
        return iter(results)


class MockFirestoreClient:
    """In-memory Firestore client with transaction and batch support."""
    def __init__(self):
        self._store = {}
        self._collections = {}

    def collection(self, path):
        if path not in self._collections:
            self._collections[path] = MockFirestoreCollectionReference(path, self._store)
        return self._collections[path]

    def transaction(self):
        return MockFirestoreTransaction(self)

    def batch(self):
        return MockFirestoreBatch(self)


class MockFirestoreBatch:
    """Mock Firestore WriteBatch."""
    def __init__(self, client):
        self.client = client
        self._mutations = []

    def set(self, doc_ref, data, merge=False):
        self._mutations.append((doc_ref.set, (data,), {"merge": merge}))

    def update(self, doc_ref, data):
        self._mutations.append((doc_ref.update, (data,), {}))

    def commit(self):
        for fn, args, kwargs in self._mutations:
            fn(*args, **kwargs)
        self._mutations.clear()


class MockFirestoreTransaction:
    """Mock Firestore Transaction."""
    def __init__(self, client):
        self.client = client
        self._mutations = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            for fn, args, kwargs in self._mutations:
                fn(*args, **kwargs)

    def get(self, doc_ref):
        return doc_ref.get()

    def set(self, doc_ref, data, merge=False):
        self._mutations.append((doc_ref.set, (data,), {"merge": merge}))

    def update(self, doc_ref, data):
        self._mutations.append((doc_ref.update, (data,), {}))


class MockBigQueryRow:
    """Mock row object for BigQuery query results."""
    def __init__(self, data_dict):
        self._data = data_dict
        for k, v in data_dict.items():
            setattr(self, k, v)

    def items(self):
        return self._data.items()

    def get(self, key, default=None):
        return self._data.get(key, default)


class MockBigQueryClient:
    """Mock BigQuery client returning configured prediction records."""
    def __init__(self):
        self.prediction_registry = {}
        self.fail_with_exception = None

    def set_predictions(self, customer_id, churn_prob, tier="SILVER", segment="STANDARD_LOYALTY", **extra):
        record = {
            "customer_id": customer_id,
            "churn_probability": churn_prob,
            "predicted_is_churned": 1 if churn_prob >= 0.50 else 0,
            "churn_risk_tier": "CRITICAL" if churn_prob >= 0.75 else ("HIGH" if churn_prob >= 0.50 else "LOW"),
            "loyalty_tier": tier,
            "customer_segment": segment,
            "automated_retention_action": "RETAIN" if churn_prob >= 0.50 else "NONE",
            "calculation_timestamp": datetime.now(timezone.utc),
            "days_since_last_purchase": 45,
            "total_spend_90d": 1250.0,
            "cart_abandonment_count": 1,
            "support_tickets_count": 1,
            "sentiment_score": 0.45,
        }
        record.update(extra)
        self.prediction_registry[customer_id] = record

    def query(self, sql_query):
        if self.fail_with_exception:
            raise self.fail_with_exception

        matching_row = None
        for cust_id, record in self.prediction_registry.items():
            if cust_id in sql_query:
                matching_row = record
                break

        mock_job = MagicMock()
        if matching_row:
            mock_job.result.return_value = [MockBigQueryRow(matching_row)]
        elif "customer_churn_risk" in sql_query and not any(f"'{cid}'" in sql_query for cid in self.prediction_registry):
            mock_job.result.return_value = [MockBigQueryRow(r) for r in self.prediction_registry.values()]
        else:
            mock_job.result.return_value = []
        return mock_job


class MockGenerativeModel:
    """Mock Vertex AI Gemini model returning structured JSON offer payloads."""
    def __init__(self):
        self.fail_with_exception = None
        self.last_prompt = None

    def generate_content(self, prompt, generation_config=None):
        self.last_prompt = prompt
        if self.fail_with_exception:
            raise self.fail_with_exception

        # Deterministic dynamic response matching context
        is_critical = "0.75" in prompt or "CRITICAL" in prompt or "LATE_DELIVERY" in prompt
        response_payload = {
            "headline": "We Value Your Partnership - Exclusive Retention Offer" if is_critical else "Special Offer for You",
            "messageBody": "Please accept our apology for recent logistical delays. Here is a special incentive for your next order." if is_critical else "Thank you for being a valued customer.",
            "discountPercentage": 25 if is_critical else 15,
            "perks": ["FREE_EXPRESS_SHIPPING", "PRIORITY_SUPPORT"] if is_critical else ["FREE_SHIPPING"],
            "personalizedApology": "We sincerely apologize for the delay with your recent delivery and have addressed this with our carrier." if is_critical else None,
            "voucherCode": "RETENTION-CRIT-25" if is_critical else "LOYALTY-HIGH-15"
        }

        mock_resp = MagicMock()
        mock_resp.text = json.dumps(response_payload)
        return mock_resp


@pytest.fixture
def mock_firestore():
    return MockFirestoreClient()


@pytest.fixture
def mock_bigquery():
    return MockBigQueryClient()


@pytest.fixture
def mock_gemini():
    return MockGenerativeModel()

"""Decoding for Firestore document events delivered over Eventarc."""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Event types Eventarc emits for Firestore.
EVENT_CREATED = "google.cloud.firestore.document.v1.created"
EVENT_UPDATED = "google.cloud.firestore.document.v1.updated"
EVENT_DELETED = "google.cloud.firestore.document.v1.deleted"
EVENT_WRITTEN = "google.cloud.firestore.document.v1.written"

# Maps a CloudEvent type to the CDC operation we record.
_OPERATION_BY_EVENT = {
    EVENT_CREATED: "insert",
    EVENT_UPDATED: "update",
    EVENT_DELETED: "delete",
}


def parse_event_body(body: bytes, content_type: Optional[str]) -> Dict[str, Any]:
    """Normalise a raw Eventarc request body into the protojson dict shape."""
    if not body:
        return {}

    looks_like_json = body.lstrip()[:1] in (b"{", b"[")
    wants_json = bool(content_type and "json" in content_type.lower())

    if looks_like_json or wants_json:
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            if looks_like_json:
                raise
            # Header said JSON but bytes are not; fall through to protobuf.

    from google.events.cloud import firestore_v1
    from google.protobuf.json_format import MessageToDict

    event_data = firestore_v1.DocumentEventData()
    type(event_data).pb(event_data).ParseFromString(body)

    return MessageToDict(
        type(event_data).pb(event_data),
        preserving_proto_field_name=False,
    )


def _parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    """Parse an RFC 3339 timestamp into an aware UTC datetime."""
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        # Firestore emits nanosecond precision; Python only accepts six digits.
        if "." in text:
            head, _, tail = text.partition(".")
            digits = "".join(c for c in tail if c.isdigit())[:6]
            offset = tail[len(digits):].lstrip("0123456789") or "+00:00"
            try:
                parsed = datetime.fromisoformat(f"{head}.{digits:0<6}{offset}")
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decode_value(value: Any) -> Any:
    """Unwrap a single Firestore Value union into a plain Python value."""
    if not isinstance(value, dict):
        return value

    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "integerValue" in value:
        try:
            return int(value["integerValue"])
        except (TypeError, ValueError):
            return None
    if "doubleValue" in value:
        try:
            return float(value["doubleValue"])
        except (TypeError, ValueError):
            return None
    if "timestampValue" in value:
        return _parse_timestamp(value["timestampValue"])
    if "stringValue" in value:
        return value["stringValue"]
    if "bytesValue" in value:
        try:
            return base64.b64decode(value["bytesValue"])
        except Exception:  # noqa: BLE001 - malformed bytes should not kill the event
            return None
    if "referenceValue" in value:
        return value["referenceValue"]
    if "geoPointValue" in value:
        point = value["geoPointValue"] or {}
        return {
            "latitude": point.get("latitude", 0.0),
            "longitude": point.get("longitude", 0.0),
        }
    if "arrayValue" in value:
        return [decode_value(v) for v in (value["arrayValue"] or {}).get("values", [])]
    if "mapValue" in value:
        return decode_fields((value["mapValue"] or {}).get("fields", {}))

    return value


def decode_fields(fields: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Unwrap a Firestore fields map into a plain dict."""
    if not fields:
        return {}
    return {key: decode_value(val) for key, val in fields.items()}


def split_document_name(name: str) -> Tuple[str, str, List[str]]:
    """Split a Firestore resource name into (database, collection, path parts)."""
    if not name:
        return "", "", []
    parts = name.split("/")
    database = ""
    if "databases" in parts:
        idx = parts.index("databases")
        if idx + 1 < len(parts):
            database = parts[idx + 1]
    if "documents" not in parts:
        return database, "", []
    tail = parts[parts.index("documents") + 1:]
    collection = tail[0] if tail else ""
    return database, collection, tail


class DocumentEvent:
    """A normalised view of one Firestore change, ready for BigQuery."""

    __slots__ = (
        "event_id",
        "event_type",
        "event_time",
        "operation",
        "database",
        "collection",
        "document_id",
        "document_path",
        "data",
        "old_data",
        "updated_fields",
    )

    def __init__(
        self,
        event_id: str,
        event_type: str,
        event_time: datetime,
        operation: str,
        database: str,
        collection: str,
        document_id: str,
        document_path: str,
        data: Dict[str, Any],
        old_data: Dict[str, Any],
        updated_fields: List[str],
    ) -> None:
        self.event_id = event_id
        self.event_type = event_type
        self.event_time = event_time
        self.operation = operation
        self.database = database
        self.collection = collection
        self.document_id = document_id
        self.document_path = document_path
        self.data = data
        self.old_data = old_data
        self.updated_fields = updated_fields

    @property
    def is_delete(self) -> bool:
        return self.operation == "delete"

    @property
    def effective_data(self) -> Dict[str, Any]:
        """The document body to key on, using old_data for deletes."""
        return self.old_data if self.is_delete else self.data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"DocumentEvent({self.operation} {self.collection}/{self.document_id} "
            f"@ {self.event_time.isoformat()})"
        )


def parse_document_event(
    payload: Dict[str, Any],
    event_id: str,
    event_type: str,
    event_time: Optional[datetime] = None,
) -> DocumentEvent:
    """Turn a raw DocumentEventData protojson body into a DocumentEvent."""
    value = payload.get("value") or {}
    old_value = payload.get("oldValue") or {}

    data = decode_fields(value.get("fields"))
    old_data = decode_fields(old_value.get("fields"))

    operation = _OPERATION_BY_EVENT.get(event_type)
    if operation is None:
        # written or unrecognised event type: infer from present values.
        if value and not old_value:
            operation = "insert"
        elif old_value and not value:
            operation = "delete"
        else:
            operation = "update"

    name = value.get("name") or old_value.get("name") or ""
    database, collection, path_parts = split_document_name(name)
    document_id = path_parts[-1] if path_parts else ""
    document_path = "/".join(path_parts)

    commit_time = _parse_timestamp(value.get("updateTime")) or _parse_timestamp(
        old_value.get("updateTime")
    )
    resolved_time = commit_time or event_time or datetime.now(timezone.utc)

    return DocumentEvent(
        event_id=event_id,
        event_type=event_type,
        event_time=resolved_time,
        operation=operation,
        database=database,
        collection=collection,
        document_id=document_id,
        document_path=document_path,
        data=data,
        old_data=old_data,
        updated_fields=list((payload.get("updateMask") or {}).get("fieldPaths", [])),
    )

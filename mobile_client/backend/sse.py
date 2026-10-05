"""Bridge Firestore on_snapshot listeners to Server-Sent Events (SSE)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

logger = logging.getLogger("redwood-mobile-api.sse")

# How long a stream waits for a change before emitting a keepalive comment.
KEEPALIVE_SECONDS = 15


def _json_default(value: Any) -> Any:
    """Render the types Firestore returns that JSON does not cover."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "isoformat"):  # DatetimeWithNanoseconds
        return value.isoformat()
    if hasattr(value, "path"):  # DocumentReference
        return str(value.path)
    return str(value)


def sse(event: str, data: Any) -> str:
    """Format one SSE frame."""
    payload = json.dumps(data, default=_json_default)
    return f"event: {event}\ndata: {payload}\n\n"


def sse_comment(text: str = "keepalive") -> str:
    """Format an SSE comment frame, which clients receive but ignore."""
    return f": {text}\n\n"


def snapshot_payload(snap: Any) -> Dict[str, Any]:
    """Describe one document snapshot for the browser.

    ``createTime`` and ``updateTime`` are Firestore's own commit timestamps.
    They are the only clock shared by the session document and the offer
    document, so they are the only pair that can be subtracted from each other
    to produce a number that means something. The browser's clock and the
    FastAPI process's clock are both wrong for that purpose, and the console
    is careful never to mix them.
    """
    return {
        "id": snap.id,
        "data": snap.to_dict() or {},
        "createTime": snap.create_time.isoformat() if snap.create_time else None,
        "updateTime": snap.update_time.isoformat() if snap.update_time else None,
    }


@dataclass(frozen=True)
class Watch:
    """A Firestore reference or query to stream under a named SSE event."""

    event: str
    ref: Any


async def stream_watches(
    watches: List[Watch],
    is_disconnected: Callable[[], Any],
    extra_queue: Optional[asyncio.Queue] = None,
    keepalive_seconds: int = KEEPALIVE_SECONDS,
) -> AsyncIterator[str]:
    """Stream Firestore changes for ``watches`` until the client goes away.

    ``extra_queue`` lets a caller push frames of its own into the same stream
    -- the console uses it for telemetry and control-action events, which do
    not come from Firestore but belong in the same ordered feed.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    subscriptions: List[Any] = []

    def make_callback(event: str) -> Callable[..., None]:
        def on_snapshot(snapshots: Any, changes: Any, read_time: Any) -> None:
            # Firestore's thread, not the event loop. Anything beyond handing
            # the payload over would be a data race.
            try:
                for snap in snapshots:
                    # A document watch reports a deletion as a snapshot that
                    # does not exist. Forwarding it would send `data: {}` and
                    # blank whatever the client was showing, which is worse
                    # than leaving the last known state on screen. Query
                    # watches never produce these: a removed document simply
                    # drops out of the result set.
                    if not snap.exists:
                        continue
                    loop.call_soon_threadsafe(
                        queue.put_nowait, (event, snapshot_payload(snap))
                    )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Snapshot callback for %s failed: %s", event, exc)

        return on_snapshot

    for watch in watches:
        subscriptions.append(watch.ref.on_snapshot(make_callback(watch.event)))

    async def next_frame() -> Optional[tuple]:
        """Return the next item from either queue, or None on timeout.

        The losing task is cancelled on every pass. That is safe rather than
        lossy: ``asyncio.Queue.get`` puts nothing aside until it returns, and
        on cancellation it wakes the next waiter, so an item that arrived in
        the same tick is still in the queue for the following call.
        """
        sources = [asyncio.create_task(queue.get())]
        if extra_queue is not None:
            sources.append(asyncio.create_task(extra_queue.get()))

        done, pending = await asyncio.wait(
            sources,
            timeout=keepalive_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            return task.result()
        return None

    try:
        while not await is_disconnected():
            item = await next_frame()
            if item is None:
                yield sse_comment()
                continue
            event, payload = item
            yield sse(event, payload)
    finally:
        # Runs on every exit path, including the browser closing the tab,
        # which is the common one.
        for subscription in subscriptions:
            try:
                subscription.unsubscribe()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to unsubscribe a Firestore watch: %s", exc)


async def stream_lines(
    lines: AsyncIterator[str],
    event: str = "log",
    done_event: str = "done",
) -> AsyncIterator[str]:
    """Wrap an async line source as SSE frames, closing with ``done_event``."""
    try:
        async for line in lines:
            if line.startswith("[SUMMARY] "):
                yield sse("summary", json.loads(line[10:]))
                continue
            yield sse(event, {"line": line})
    except Exception as exc:
        logger.exception("Log stream failed")
        yield sse("error", {"message": str(exc)})
    finally:
        yield sse(done_event, {})

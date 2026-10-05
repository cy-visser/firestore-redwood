#!/usr/bin/env python3
"""Authenticating localhost tunnel to a private Cloud Run service.

`redwood-app` allows no unauthenticated access, and a browser has no way to
present a Google ID token. Something local has to sign each request, which is
what `gcloud run services proxy` exists for -- but it cannot do the job here:

  * With user credentials it signs requests with the ID token gcloud can mint
    for itself, whose audience is the gcloud OAuth client rather than the
    service URL. Cloud Run answers 401. On an installation using a custom
    OAuth client -- a corporate one, say -- there is no allow-listed audience
    to fall back on, so this never works.
  * With `--impersonate-service-account`, which would produce a token with the
    right audience, the underlying cloud-run-proxy binary refuses outright:
    "failed to get idtoken source: idtoken: unsupported credentials type".

So this does the same job with the token logic the backend already uses
(mobile_client.backend.idtoken): the metadata server when there is one, and
impersonation of the pipeline service account when there is not.

Nothing of the application runs here. This forwards bytes; the container in
Cloud Run serves. In particular the response is streamed rather than buffered,
because the Redwood Console is built on Server-Sent Events and a proxy that
waits for the end of a response would hold every event until the stream closed.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlsplit

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import httpx  # noqa: E402

# idtoken finds the account to impersonate through PIPELINE_SERVICE_ACCOUNT /
# DATAFLOW_SERVICE_ACCOUNT, which deploy.sh and start_mobile_app.sh export but
# a bare `python3 scripts/run_proxy.py` would not. Without them the token is
# minted off the metadata server instead -- valid, wrong principal, and every
# request comes back 403 looking like a missing IAM binding. override=False so
# anything already exported still wins.
try:  # noqa: E402
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO_ROOT, ".env"), override=False)
except ImportError:  # pragma: no cover - python-dotenv is in requirements
    pass

from mobile_client.backend import idtoken  # noqa: E402

# Headers that describe one hop of a connection rather than the message, and so
# must not be copied to the next hop. Content-Length is dropped separately
# because httpx recomputes it, and Host because it has to name the upstream.
HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
})

_REQUEST_DROP = HOP_BY_HOP | {"host", "content-length", "authorization"}
_RESPONSE_DROP = HOP_BY_HOP | {"content-length"}


class TunnelHandler(BaseHTTPRequestHandler):
    """Forwards one request upstream with an Authorization header attached."""

    protocol_version = "HTTP/1.1"
    server_version = "redwood-tunnel"

    # Set by the server factory below.
    target: str = ""
    client: httpx.Client = None  # type: ignore[assignment]
    verbose: bool = False

    def log_message(self, fmt: str, *args) -> None:
        if self.verbose:
            sys.stderr.write("   %s\n" % (fmt % args))

    def _forward(self, method: str) -> None:
        try:
            token = idtoken.fetch(self.target)
        except Exception as exc:  # noqa: BLE001 - report to the browser
            self._fail(502, f"Could not mint an ID token for {self.target}: {exc}")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in _REQUEST_DROP
        }
        headers["Authorization"] = f"Bearer {token}"

        url = self.target + self.path

        try:
            with self.client.stream(
                method, url, headers=headers, content=body
            ) as upstream:
                self._relay(upstream, include_body=method != "HEAD")
        except httpx.HTTPError as exc:
            self._fail(502, f"Upstream request failed: {exc}")
        except (BrokenPipeError, ConnectionResetError):
            # The browser navigated away mid-stream, which for an SSE
            # connection is the normal way one ends.
            pass

    def _relay(self, upstream: httpx.Response, include_body: bool) -> None:
        """Copy status, headers and body downstream without buffering.

        A streaming response arrives with no Content-Length, so it goes out
        chunked. Keeping HTTP/1.1 rather than closing the connection per
        response matters here: the console holds several SSE streams open at
        once alongside ordinary requests.
        """
        declared_length = upstream.headers.get("content-length")

        self.send_response(upstream.status_code)
        for key, value in upstream.headers.multi_items():
            if key.lower() not in _RESPONSE_DROP:
                self.send_header(key, value)

        if declared_length is not None:
            self.send_header("Content-Length", declared_length)
        else:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        if not include_body:
            return

        for chunk in upstream.iter_raw():
            if not chunk:
                continue
            if declared_length is not None:
                self.wfile.write(chunk)
            else:
                self.wfile.write(b"%x\r\n" % len(chunk))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
            self.wfile.flush()

        if declared_length is None:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def _fail(self, status: int, message: str) -> None:
        payload = message.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        sys.stderr.write(f"[ERROR] {message}\n")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        self._forward("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._forward("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._forward("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._forward("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._forward("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._forward("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._forward("OPTIONS")


def serve(target: str, port: int, verbose: bool = False) -> None:
    target = target.rstrip("/")
    if not urlsplit(target).scheme:
        raise SystemExit(f"--url must be absolute, got {target!r}")

    # read=None because an SSE stream is idle for long stretches by design and
    # a read timeout would sever it.
    client = httpx.Client(
        timeout=httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0),
        follow_redirects=False,
    )

    handler = type(
        "BoundTunnelHandler",
        (TunnelHandler,),
        {"target": target, "client": client, "verbose": verbose},
    )

    # Minted before the socket is bound, not after. deploy.sh waits for the
    # port and then announces the app as live, so a token failure discovered
    # after binding would be announced as a success and then die.
    idtoken.fetch(target)

    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True

    # Printed only now that the token works and the socket is bound. Some IDEs
    # scan terminal output for localhost URLs and immediately bind the port for
    # auto-forwarding, which would steal it from a server still starting up.
    print(f"Tunnelling http://localhost:{port} -> {target}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="The Cloud Run service URL")
    parser.add_argument("--port", type=int, default=8080, help="Local port to bind")
    parser.add_argument("--verbose", action="store_true", help="Log each request")
    args = parser.parse_args()
    serve(args.url, args.port, args.verbose)


if __name__ == "__main__":
    main()

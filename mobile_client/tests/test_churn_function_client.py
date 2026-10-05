"""
Unit tests for the console's HTTP client to the redwood-churn Cloud Run
function.

This is the seam that replaced ``asyncio.create_subprocess_exec``. The thing
worth protecting is not the happy path but the failure paths: the console is
already streaming to the browser by the time most of these go wrong, so a
failure that raises instead of yielding ``[exit 1]`` shows up as a reset that
silently skips its re-score -- which is exactly the failure that leaves
cust_demo2 scored as healthy in front of an audience.

httpx is exercised for real through a ``MockTransport`` rather than mocked
away, so the streaming, status handling and timeout code paths are the ones
that actually run.
"""

import json
import unittest
from unittest.mock import patch

import httpx

from mobile_client.backend import console_service

URL = "https://redwood-churn-abc123-ez.a.run.app"

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def transport_patch(handler):
    """Patch ``httpx.AsyncClient`` so it routes through a mock transport.

    The client is constructed inside ``_stream_churn_function``, so there is
    nowhere to inject a transport from the outside; wrapping the constructor
    is the least invasive way in.
    """

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    return patch.object(httpx, "AsyncClient", factory)


class ChurnFunctionClientTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []

        token_patch = patch.object(
            console_service.idtoken, "fetch", return_value="fake-id-token"
        )
        self.mock_fetch = token_patch.start()
        self.addCleanup(token_patch.stop)

        url_patch = patch.object(console_service, "CHURN_FUNCTION_URL", URL)
        url_patch.start()
        self.addCleanup(url_patch.stop)

    def responder(self, status_code=200, body=""):
        def handler(request):
            self.requests.append(request)
            return httpx.Response(status_code, content=body.encode())

        return handler

    async def collect(self, mode=console_service.CHURN_MODE_FULL, report=True):
        return [line async for line in
                console_service._stream_churn_function(mode, report=report)]

    async def test_happy_path_forwards_lines_verbatim(self):
        body = (
            "Connecting to BigQuery in 'test-prj'...\n"
            "[1/2] Run customer_churn_model\n"
            "    done - 1.2 MB scanned\n"
            "[2/2] Merge customer_churn_risk\n"
            "    done - 500 row(s) affected\n"
            "[exit 0]\n"
        )
        with transport_patch(self.responder(200, body)):
            lines = await self.collect()

        # The first line is the echoed request, which the Event Log shows so
        # the audience can see what the button did.
        self.assertTrue(lines[0].startswith("$ POST "))
        self.assertIn(URL, lines[0])
        self.assertEqual(lines[-1], "[exit 0]")
        self.assertIn("[1/2] Run customer_churn_model", lines)
        self.assertIn("    done - 500 row(s) affected", lines)

    async def test_request_carries_the_mode_and_a_bearer_token(self):
        with transport_patch(self.responder(200, "[exit 0]\n")):
            await self.collect(mode=console_service.CHURN_MODE_RESCORE, report=False)

        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["authorization"], "Bearer fake-id-token")
        self.assertEqual(
            json.loads(request.content.decode()),
            {"mode": "rescore", "report": False},
        )
        # The token audience must be the service URL, not a fixed one.
        self.mock_fetch.assert_called_once_with(URL)

    async def test_missing_url_reports_without_calling_out(self):
        called = []

        def handler(request):  # pragma: no cover - must never run
            called.append(request)
            return httpx.Response(200)

        with patch.object(console_service, "CHURN_FUNCTION_URL", ""), \
                transport_patch(handler):
            lines = await self.collect()

        self.assertEqual(called, [])
        self.mock_fetch.assert_not_called()
        self.assertTrue(lines[0].startswith("[ERROR] CHURN_FUNCTION_URL is not set"))
        self.assertEqual(lines[-1], "[exit 1]")

    async def test_non_200_is_reported_as_an_error_and_exit_one(self):
        with transport_patch(self.responder(500, "boom")):
            lines = await self.collect()

        self.assertTrue(any("returned 500" in line for line in lines))
        self.assertTrue(any("boom" in line for line in lines))
        self.assertEqual(lines[-1], "[exit 1]")

    async def test_403_adds_the_run_invoker_hint(self):
        # The overwhelmingly likely cause, and not one the raw body explains.
        with transport_patch(self.responder(403, "Forbidden")):
            lines = await self.collect()

        self.assertTrue(any("roles/run.invoker" in line for line in lines))
        self.assertEqual(lines[-1], "[exit 1]")

    async def test_token_failure_is_reported_in_band(self):
        self.mock_fetch.side_effect = RuntimeError("no metadata server")

        with transport_patch(self.responder(200, "[exit 0]\n")):
            lines = await self.collect()

        self.assertEqual(self.requests, [])
        self.assertTrue(any("Could not mint an ID token" in line for line in lines))
        self.assertTrue(any("no metadata server" in line for line in lines))
        self.assertEqual(lines[-1], "[exit 1]")

    async def test_stream_ending_without_an_exit_line_is_a_failure(self):
        # A function that dies mid-run closes the connection cleanly, having
        # already sent a 200. Without this check the console would call that a
        # success.
        with transport_patch(self.responder(200, "[1/2] Run customer_churn_model\n")):
            lines = await self.collect()

        self.assertTrue(any("closed the stream without an exit line" in line
                            for line in lines))
        self.assertEqual(lines[-1], "[exit 1]")

    async def test_read_timeout_is_reported_in_band(self):
        def handler(request):
            raise httpx.ReadTimeout("timed out", request=request)

        with transport_patch(handler):
            lines = await self.collect()

        self.assertTrue(any("No output from the churn function" in line
                            for line in lines))
        self.assertEqual(lines[-1], "[exit 1]")

    async def test_transport_error_is_reported_in_band(self):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        with transport_patch(handler):
            lines = await self.collect()

        self.assertTrue(any("Churn function call failed" in line for line in lines))
        self.assertTrue(any("connection refused" in line for line in lines))
        self.assertEqual(lines[-1], "[exit 1]")

    async def test_exactly_one_exit_line_on_every_path(self):
        # reset_demo_lines() parses the exit code out of this line; a second
        # one would be read as the verdict on a step that never ran.
        cases = [
            self.responder(200, "[exit 0]\n"),
            self.responder(200, "no exit line here\n"),
            self.responder(503, "unavailable"),
        ]
        for handler in cases:
            with self.subTest(handler=handler), transport_patch(handler):
                lines = await self.collect()
                exits = [line for line in lines if line.startswith("[exit ")]
                self.assertEqual(len(exits), 1, lines)


class ChurnFunctionConfiguredTest(unittest.TestCase):
    def test_reports_whether_the_url_is_known(self):
        with patch.object(console_service, "CHURN_FUNCTION_URL", URL):
            self.assertTrue(console_service.churn_function_configured())
        with patch.object(console_service, "CHURN_FUNCTION_URL", ""):
            self.assertFalse(console_service.churn_function_configured())


if __name__ == "__main__":
    unittest.main()

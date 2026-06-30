from __future__ import annotations

import unittest

from scripts.orion_terminal.errors import (
    AuthExpired,
    ConflictState,
    RuntimeUnavailable,
    TimeoutExceeded,
    TransientUpstreamError,
    ValidationFailed,
    extract_status_code,
    map_runtime_exception,
    should_retry_error,
)


class ErrorMappingTests(unittest.TestCase):
    def test_extract_status_code(self) -> None:
        self.assertEqual(extract_status_code("HTTP 404: Not Found"), 404)
        self.assertEqual(extract_status_code("status=503 upstream"), 503)
        self.assertIsNone(extract_status_code("network error"))

    def test_map_runtime_exception_http(self) -> None:
        self.assertIsInstance(map_runtime_exception(RuntimeError("HTTP 401: unauthorized")), AuthExpired)
        self.assertIsInstance(map_runtime_exception(RuntimeError("HTTP 422: bad payload")), ValidationFailed)
        self.assertIsInstance(map_runtime_exception(RuntimeError("HTTP 409: conflict")), ConflictState)
        self.assertIsInstance(map_runtime_exception(RuntimeError("HTTP 503: unavailable")), TransientUpstreamError)

    def test_map_runtime_exception_network_timeout(self) -> None:
        self.assertIsInstance(map_runtime_exception(RuntimeError("Network error: connection refused")), RuntimeUnavailable)
        timeout = map_runtime_exception(RuntimeError("request timeout exceeded"), method="GET")
        self.assertIsInstance(timeout, TimeoutExceeded)
        self.assertTrue(should_retry_error(timeout))
        post_timeout = map_runtime_exception(RuntimeError("request timeout exceeded"), method="POST")
        self.assertFalse(should_retry_error(post_timeout))
        self.assertTrue(should_retry_error(post_timeout, allow_unsafe_retry=True))


if __name__ == "__main__":
    unittest.main()


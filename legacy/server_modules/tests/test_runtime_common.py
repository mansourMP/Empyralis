from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.requests import Request

from server_modules import runtime_common


def _request(
    path: str,
    *,
    method: str = "POST",
    api_key: str = "test-key",
    extra_headers: dict[str, str] | None = None,
) -> Request:
    headers = [(b"host", b"127.0.0.1:8001")]
    if api_key:
        headers.append((b"x-api-key", api_key.encode("utf-8")))
    for key, value in dict(extra_headers or {}).items():
        headers.append((key.lower().encode("utf-8"), str(value).encode("utf-8")))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8001),
    }
    return Request(scope)


class RuntimeCommonRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        with runtime_common.RATE_LIMIT_LOCK:
            runtime_common.RATE_LIMIT_BUCKETS.clear()

    def tearDown(self) -> None:
        with runtime_common.RATE_LIMIT_LOCK:
            runtime_common.RATE_LIMIT_BUCKETS.clear()

    def test_turn_is_exempt_from_control_plane_rate_limit(self) -> None:
        with (
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE", 0),
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_BURST", 0),
        ):
            first = runtime_common._control_plane_rate_limit(_request("/turn"))
            second = runtime_common._control_plane_rate_limit(_request("/turn"))

        self.assertIsNone(first)
        self.assertIsNone(second)

    def test_api_turn_is_exempt_from_control_plane_rate_limit(self) -> None:
        with (
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE", 0),
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_BURST", 0),
        ):
            first = runtime_common._control_plane_rate_limit(_request("/api/turn"))
            second = runtime_common._control_plane_rate_limit(_request("/api/turn"))

        self.assertIsNone(first)
        self.assertIsNone(second)

    def test_api_sessions_is_exempt_from_control_plane_rate_limit(self) -> None:
        with (
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE", 0),
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_BURST", 0),
        ):
            first = runtime_common._control_plane_rate_limit(_request("/api/sessions"))
            second = runtime_common._control_plane_rate_limit(_request("/api/sessions"))

        self.assertIsNone(first)
        self.assertIsNone(second)

    def test_api_thread_turns_is_exempt_from_control_plane_rate_limit(self) -> None:
        with (
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE", 0),
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_BURST", 0),
        ):
            first = runtime_common._control_plane_rate_limit(_request("/api/threads/thread-1/turns"))
            second = runtime_common._control_plane_rate_limit(_request("/api/threads/thread-1/turns"))

        self.assertIsNone(first)
        self.assertIsNone(second)

    def test_runtime_register_is_not_exempt_from_control_plane_rate_limit(self) -> None:
        with (
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE", 0),
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_BURST", 0),
        ):
            first = runtime_common._control_plane_rate_limit(_request("/runtime/runtimes/worker-1/register"))
            second = runtime_common._control_plane_rate_limit(_request("/runtime/runtimes/worker-1/register"))

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.status_code, 429)

    def test_runtime_heartbeat_and_tasks_remain_hot_path_exempt(self) -> None:
        with (
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE", 0),
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_BURST", 0),
        ):
            runtime_heartbeat = runtime_common._control_plane_rate_limit(
                _request("/runtime/runtimes/worker-1/heartbeat")
            )
            task_claim = runtime_common._control_plane_rate_limit(_request("/runtime/tasks/claim"))

        self.assertIsNone(runtime_heartbeat)
        self.assertIsNone(task_claim)

    def test_other_mutations_still_rate_limited(self) -> None:
        with (
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE", 0),
            patch.object(runtime_common, "CONTROL_PLANE_RATE_LIMIT_BURST", 0),
        ):
            first = runtime_common._control_plane_rate_limit(_request("/skills/install"))
            second = runtime_common._control_plane_rate_limit(_request("/skills/install"))

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.body.decode("utf-8").count("\"detail\""), 1)
        self.assertIn("\"error\"", second.body.decode("utf-8"))

    def test_control_plane_origin_denial_uses_structured_error_envelope(self) -> None:
        with patch.object(runtime_common, "CONTROL_PLANE_ORIGINS", ["https://allowed.example"]):
            response = runtime_common._check_control_plane_origin(
                _request(
                    "/api/workspaces",
                    extra_headers={"origin": "https://blocked.example", "x-request-id": "req-1"},
                )
            )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.status_code, 403)
        payload = response.body.decode("utf-8")
        self.assertIn("\"detail\":\"Origin is not allowed.\"", payload)
        self.assertIn("\"code\":\"origin_not_allowed\"", payload)
        self.assertIn("\"class\":\"authorization_error\"", payload)
        self.assertIn("\"request_id\":\"req-1\"", payload)

    def test_control_plane_origin_configuration_is_required_outside_local_env(self) -> None:
        with (
            patch.object(runtime_common, "CONTROL_PLANE_ORIGINS", []),
            patch.dict("os.environ", {"ORION_ENV": "beta"}, clear=True),
        ):
            response = runtime_common._check_control_plane_origin(
                _request(
                    "/api/workspaces",
                    extra_headers={"origin": "https://app.example", "x-request-id": "req-2"},
                )
            )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.status_code, 503)
        payload = response.body.decode("utf-8")
        self.assertIn("\"code\":\"origin_configuration_required\"", payload)
        self.assertIn("CONTROL_PLANE_ORIGINS must be configured", payload)

    def test_fetch_workflow_snapshot_uses_control_plane_service(self) -> None:
        with patch(
            "server_modules.workflow_service.fetch_workflow_snapshot_sync",
            return_value={"id": "wf-1", "workflowVersionId": "wfver-1", "definition": {"version": "v1"}},
        ) as snapshot_mock:
            payload = runtime_common.fetch_workflow_snapshot(
                "wf-1",
                workflow_version_id="wfver-1",
                workspace_id="workspace-1",
                tenant_id="tenant-1",
            )

        self.assertEqual(payload["workflowVersionId"], "wfver-1")
        snapshot_mock.assert_called_once_with(
            "wf-1",
            workflow_version_id="wfver-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
        )


if __name__ == "__main__":
    unittest.main()

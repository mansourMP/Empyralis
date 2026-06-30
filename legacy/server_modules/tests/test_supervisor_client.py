from __future__ import annotations

import os
from unittest.mock import patch

from server_modules import supervisor_client


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {"success": True, "result": {"ok": True}}

    def json(self) -> dict:
        return dict(self._body)


class _FakeInterruptController:
    def __init__(self) -> None:
        self.registered = []
        self.cleared = []

    def register_supervisor_execution(self, *, run_id: str, request_id: str, interrupt_fn) -> None:
        self.registered.append((run_id, request_id, interrupt_fn))

    def clear_supervisor_execution(self, *, request_id: str | None = None) -> None:
        self.cleared.append(request_id)


def test_execute_uses_bound_execution_context():
    controller = _FakeInterruptController()
    captured = {}
    context = supervisor_client.SupervisorExecutionContext(
        run_id="run-123",
        workspace_id="ws-1",
        trace_id="trace-abc",
        machine_id="machine-1",
        interrupt_controller=controller,
    )

    def _post(url, json, timeout):
        if url.endswith("/execute"):
            captured["url"] = url
            captured["json"] = dict(json)
            captured["timeout"] = timeout
        return _FakeResponse()

    with (
        patch.dict(os.environ, {"EMPYRALIS_SUPERVISOR_SECRET": "test-secret"}, clear=False),
        patch("server_modules.supervisor_client.requests.post", side_effect=_post),
        supervisor_client.bind_execution_context(context),
    ):
        result = supervisor_client.click(x=1, y=2)

    assert result["ok"] is True
    assert captured["url"].endswith("/execute")
    assert captured["json"]["run_id"] == "run-123"
    assert captured["json"]["workspace_id"] == "ws-1"
    assert captured["json"]["trace_id"] == "trace-abc"
    assert controller.registered
    assert controller.cleared == [controller.registered[0][1]]


def test_interrupt_execution_posts_signed_interrupt_request():
    captured = {}

    def _post(url, json, timeout):
        captured["url"] = url
        captured["json"] = dict(json)
        captured["timeout"] = timeout
        return _FakeResponse(body={"success": True, "interrupted": True, "interrupt_count": 1})

    with (
        patch.dict(os.environ, {"EMPYRALIS_SUPERVISOR_SECRET": "test-secret"}, clear=False),
        patch("server_modules.supervisor_client.requests.post", side_effect=_post),
    ):
        payload = supervisor_client.interrupt_execution(
            run_id="run-123",
            target_request_id="req-456",
            reason="Operator stop",
            trace_id="trace-abc",
            workspace_id="ws-1",
        )

    assert payload["success"] is True
    assert captured["url"].endswith("/interrupt")
    assert captured["json"]["run_id"] == "run-123"
    assert captured["json"]["target_request_id"] == "req-456"
    assert captured["json"]["workspace_id"] == "ws-1"


def test_direct_supervisor_loopback_is_blocked_in_api_server_process():
    with (
        patch.dict(os.environ, {"EMPYRALIS_SUPERVISOR_SECRET": "test-secret"}, clear=False),
        patch.object(supervisor_client.sys, "argv", ["uvicorn", "server:app"]),
    ):
        try:
            supervisor_client.click(x=1, y=2)
        except RuntimeError as exc:
            assert str(exc) == supervisor_client.DIRECT_SUPERVISOR_BLOCKED_MESSAGE
        else:
            raise AssertionError("Expected direct supervisor loopback to be blocked in API server mode.")

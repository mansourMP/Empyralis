from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from server_modules import routes_agent_traces


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_agent_traces.router)
    return app


def _owner_user() -> dict[str, object]:
    return {
        "user_id": "user-owner",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "owner",
            }
        },
    }


def _trace_record() -> dict[str, object]:
    return {
        "id": "trace-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "root_agent_id": "sage",
        "surface": "web",
        "runtime_target": "cloud",
        "provider": "openai",
        "model": "gpt-test",
        "started_at": "2026-04-13T12:00:00Z",
        "finished_at": "2026-04-13T12:00:02Z",
        "outcome": "success",
        "final_message_id": "msg-1",
    }


def _trace_event(seq: int, event_type: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": f"evt-{seq}",
        "trace_id": "trace-1",
        "seq": seq,
        "parent_id": None,
        "ts": f"2026-04-13T12:00:0{seq}Z",
        "event_type": event_type,
        "persisted": True,
        "agent_id": "sage",
        "item_id": None,
        "tool_call_id": None,
        "child_run_id": None,
        "approval_id": None,
        "artifact_id": None,
        "payload": payload or {},
    }


@pytest.mark.anyio
async def test_trace_stream_requires_auth():
    app = _build_app()

    def _unauthorized() -> dict[str, object]:
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[routes_agent_traces.get_current_user] = _unauthorized
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/agent-traces/trace-1/stream", params={"workspace_id": "ws-1"})

    assert response.status_code == 401


@pytest.mark.anyio
async def test_trace_list_filters_by_workspace(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_agent_traces.get_current_user] = lambda: _owner_user()
    captured: dict[str, object] = {}

    async def fake_list_agent_traces(**kwargs):
        captured.update(kwargs)
        return [_trace_record()]

    monkeypatch.setattr(
        routes_agent_traces.control_plane_repository,
        "list_agent_traces",
        fake_list_agent_traces,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/agent-traces",
            params={
                "workspace_id": "ws-1",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "surface": "web",
                "outcome": "success",
                "root_agent_id": "sage",
                "limit": 20,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "trace-1"
    assert captured["tenant_id"] == "tenant-1"
    assert captured["workspace_id"] == "ws-1"
    assert captured["thread_id"] == "thread-1"
    assert captured["run_id"] == "run-1"
    assert captured["surface"] == "web"
    assert captured["outcome"] == "success"
    assert captured["root_agent_id"] == "sage"
    assert captured["limit"] == 20


@pytest.mark.anyio
async def test_trace_detail_returns_trace_and_events(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_agent_traces.get_current_user] = lambda: _owner_user()

    async def fake_get_agent_trace_by_id(trace_id: str, **kwargs):
        assert trace_id == "trace-1"
        assert kwargs["tenant_id"] == "tenant-1"
        assert kwargs["workspace_id"] == "ws-1"
        return _trace_record()

    async def fake_get_agent_trace_events(trace_id: str, **kwargs):
        assert trace_id == "trace-1"
        assert kwargs["persisted_only"] is True
        return [
            _trace_event(1, "trace.started", {"input_mode": "text"}),
            _trace_event(2, "trace.completed", {"duration_ms": 1200}),
        ]

    monkeypatch.setattr(
        routes_agent_traces.control_plane_repository,
        "get_agent_trace_by_id",
        fake_get_agent_trace_by_id,
    )
    monkeypatch.setattr(
        routes_agent_traces.control_plane_repository,
        "get_agent_trace_events",
        fake_get_agent_trace_events,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/agent-traces/trace-1", params={"workspace_id": "ws-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["id"] == "trace-1"
    assert payload["events"][0]["event_type"] == "trace.started"
    assert payload["events"][0]["data"]["input_mode"] == "text"
    assert payload["events"][1]["event_type"] == "trace.completed"


@pytest.mark.anyio
async def test_trace_stream_replays_persisted_events_on_connect(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_agent_traces.get_current_user] = lambda: _owner_user()

    async def fake_get_agent_trace_by_id(trace_id: str, **kwargs):
        return _trace_record()

    async def fake_get_agent_trace_events(trace_id: str, **kwargs):
        return [
            _trace_event(1, "trace.started", {"input_mode": "text"}),
            _trace_event(2, "trace.completed", {"duration_ms": 1200}),
        ]

    monkeypatch.setattr(
        routes_agent_traces.control_plane_repository,
        "get_agent_trace_by_id",
        fake_get_agent_trace_by_id,
    )
    monkeypatch.setattr(
        routes_agent_traces.control_plane_repository,
        "get_agent_trace_events",
        fake_get_agent_trace_events,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream(
            "GET",
            "/agent-traces/trace-1/stream",
            params={"workspace_id": "ws-1"},
        ) as response:
            body = ""
            async for chunk in response.aiter_text():
                body += chunk

    assert response.status_code == 200
    assert "event: trace" in body
    assert "trace.started" in body
    assert "trace.completed" in body


@pytest.mark.anyio
async def test_trace_stream_closes_after_terminal_event(monkeypatch: pytest.MonkeyPatch):
    app = _build_app()
    app.dependency_overrides[routes_agent_traces.get_current_user] = lambda: _owner_user()
    calls = {"count": 0}

    async def fake_get_agent_trace_by_id(trace_id: str, **kwargs):
        return _trace_record()

    async def fake_get_agent_trace_events(trace_id: str, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return [_trace_event(1, "trace.started", {"input_mode": "text"})]
        return [
            _trace_event(1, "trace.started", {"input_mode": "text"}),
            _trace_event(2, "trace.completed", {"duration_ms": 1200}),
        ]

    async def fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        routes_agent_traces.control_plane_repository,
        "get_agent_trace_by_id",
        fake_get_agent_trace_by_id,
    )
    monkeypatch.setattr(
        routes_agent_traces.control_plane_repository,
        "get_agent_trace_events",
        fake_get_agent_trace_events,
    )
    monkeypatch.setattr(routes_agent_traces.asyncio, "sleep", fast_sleep)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream(
            "GET",
            "/agent-traces/trace-1/stream",
            params={"workspace_id": "ws-1", "poll_seconds": 0.05},
        ) as response:
            body = ""
            async for chunk in response.aiter_text():
                body += chunk

    assert response.status_code == 200
    assert calls["count"] >= 2
    assert "trace.completed" in body

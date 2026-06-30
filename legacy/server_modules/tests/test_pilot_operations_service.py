from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server_modules import pilot_operations_service, routes_pilot


def _event(**overrides):
    event = {
        "id": "evt-1",
        "created_at": datetime.now(timezone.utc),
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "actor_type": "user",
        "actor_id": "user-1",
        "event_class": "sage_activity",
        "detail_level": "timeline_detail",
        "channel": "telegram",
        "direction": "inbound",
        "action": "sage_chat.completed",
        "status": "completed",
        "trace_id": "trace-1",
        "title": "Handled message",
        "summary": "Customer question handled.",
        "payload": {"workflow": "customer_question_handling", "response_time_seconds": 2.0},
        "metadata": {},
        "review_required": False,
    }
    event.update(overrides)
    return event


def test_pilot_contract_matches_bible_phase_7_requirements():
    contract = pilot_operations_service.get_pilot_operations_contract()

    assert contract["workflow"]["id"] == "customer_question_handling"
    assert "owner_admin" in contract["users_roles"]
    assert "operator" in contract["users_roles"]
    assert "external_customer" in contract["users_roles"]
    assert "requires_approval" in contract["safety_boundaries"]
    assert "manual_intervention_rate" in contract["metrics_plan"]
    assert "daily_review_script" in contract["daily_operating_procedure"]
    assert "logs_trace_id" in contract["issue_template"]
    assert contract["acceptance_criteria"]


@pytest.mark.anyio
async def test_daily_report_computes_closed_pilot_metrics():
    rows = [
        _event(id="evt-1", actor_id="customer-1", session_key="s1", direction="inbound", status="completed"),
        _event(id="evt-2", actor_id="agent", session_key="s1", direction="outbound", status="success"),
        _event(id="evt-3", actor_id="customer-1", session_key="s1", direction="inbound", status="completed"),
        _event(id="evt-4", actor_id="agent", event_class="approval", review_required=True, action="approval.requested"),
        _event(id="evt-5", actor_id="agent", event_class="blocked_action", status="blocked", action="tool.blocked"),
        _event(id="evt-6", channel="pilot", action="pilot_feedback.reported", payload={"usefulness_score": 4}),
    ]

    with patch(
        "server_modules.pilot_operations_service.control_plane_repository.list_activity_ledger_events",
        new=AsyncMock(return_value=rows),
    ):
        report = await pilot_operations_service.build_pilot_operations_report(
            tenant_id="tenant-1",
            workspace_id="ws-1",
        )

    assert report["metrics"]["active_users"] == 3
    assert report["metrics"]["messages_handled"] == 6
    assert report["metrics"]["tasks_completed"] == 4
    assert report["metrics"]["tasks_failed"] == 1
    assert report["metrics"]["approval_count"] == 1
    assert report["metrics"]["blocked_action_count"] == 1
    assert report["metrics"]["manual_intervention_rate"] == 0.167
    assert report["metrics"]["repeated_usage_by_user"] == 1
    assert report["metrics"]["user_reported_usefulness"] == 4.0


@pytest.mark.anyio
async def test_record_feedback_writes_activity_event():
    with patch(
        "server_modules.pilot_operations_service.activity_ledger_service.append_activity_event",
        new=AsyncMock(return_value={"id": "evt-feedback"}),
    ) as append_event:
        result = await pilot_operations_service.record_pilot_feedback(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            actor_id="user-1",
            usefulness_score=5,
            comment="Useful.",
            trace_id="trace-1",
        )

    assert result["ok"] is True
    kwargs = append_event.await_args.kwargs
    assert kwargs["action"] == "pilot_feedback.reported"
    assert kwargs["payload"]["usefulness_score"] == 5
    assert kwargs["trace_id"] == "trace-1"


@pytest.mark.anyio
async def test_record_p0_issue_requires_review_and_trace():
    with patch(
        "server_modules.pilot_operations_service.activity_ledger_service.append_activity_event",
        new=AsyncMock(return_value={"id": "evt-issue"}),
    ) as append_event:
        result = await pilot_operations_service.record_pilot_issue(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            actor_id="operator-1",
            issue={
                "user": "customer-1",
                "workflow": "customer_question_handling",
                "expected_behavior": "Escalate.",
                "actual_behavior": "Answered directly.",
                "logs_trace_id": "trace-9",
                "severity": "p0",
                "fix_status": "open",
            },
        )

    assert result["ok"] is True
    kwargs = append_event.await_args.kwargs
    assert kwargs["event_class"] == "blocked_action"
    assert kwargs["review_required"] is True
    assert kwargs["trace_id"] == "trace-9"


class TestPilotOperationsRoutes:
    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(routes_pilot.router, prefix="/api")
        current_user = {
            "user_id": "user-1",
            "is_admin": True,
            "workspace_access": {"ws-1": {"role": "owner", "tenant_id": "tenant-1"}},
        }
        app.dependency_overrides[routes_pilot.auth.get_current_user] = lambda: current_user
        for route in app.routes:
            dependant = getattr(route, "dependant", None)
            for dependency in getattr(dependant, "dependencies", []) or []:
                if getattr(dependency.call, "__name__", "") == "get_current_user":
                    app.dependency_overrides[dependency.call] = lambda current_user=current_user: current_user
        return app

    @pytest.mark.anyio
    async def test_contract_route_returns_workflow(self):
        app = self._build_app()
        with patch("server_modules.routes_pilot.auth.enforce_workspace_access", return_value="ws-1"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/pilot/operations/contract?workspace_id=ws-1")

        assert response.status_code == 200
        assert response.json()["workflow"]["id"] == "customer_question_handling"

    @pytest.mark.anyio
    async def test_report_route_requires_workspace_and_returns_metrics(self):
        app = self._build_app()
        report = {
            "workflow": {"id": "customer_question_handling"},
            "window": {"days": 1},
            "metrics": {"messages_handled": 1},
        }
        with patch("server_modules.routes_pilot.auth.enforce_workspace_access", return_value="ws-1"), patch(
            "server_modules.routes_pilot.auth.workspace_tenant_id",
            return_value="tenant-1",
        ), patch(
            "server_modules.routes_pilot.pilot_operations_service.build_pilot_operations_report",
            new=AsyncMock(return_value=report),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/pilot/operations/report?workspace_id=ws-1")

        assert response.status_code == 200
        assert response.json()["metrics"]["messages_handled"] == 1

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server_modules import pilot_proof_service, routes_pilot


def _report(**metrics_overrides):
    metrics = {
        "active_users": 1,
        "messages_handled": 0,
        "tasks_completed": 0,
        "tasks_failed": 0,
        "approval_count": 0,
        "blocked_action_count": 0,
        "average_response_time_seconds": None,
        "manual_intervention_rate": 0.0,
        "repeated_usage_by_user": 0,
        "user_reported_usefulness": None,
    }
    metrics.update(metrics_overrides)
    return {
        "workflow": {"id": "customer_question_handling", "name": "WhatsApp/Telegram customer question handling"},
        "metrics": metrics,
        "daily_summary": {"top_workflows": [{"workflow": "customer_question_handling", "count": 1}]},
        "failures": [],
        "blocked_actions": [],
        "risky_events": [],
        "user_feedback": [],
    }


def _row(**overrides):
    row = {
        "id": "evt-1",
        "created_at": datetime.now(timezone.utc),
        "event_class": "sage_activity",
        "action": "sage_chat.completed",
        "status": "completed",
        "channel": "telegram",
        "direction": "inbound",
        "actor_id": "customer-1",
        "trace_id": "trace-1",
        "summary": "Handled customer question.",
        "payload": {"workflow": "customer_question_handling"},
        "metadata": {},
    }
    row.update(overrides)
    return row


def test_phase7_row_filter_only_allows_pilot_channels_or_pilot_actions():
    assert pilot_proof_service._is_phase7_pilot_row(_row(channel="telegram")) is True
    assert pilot_proof_service._is_phase7_pilot_row(_row(channel="telegram_personal", action="sage_chat.completed")) is True
    assert pilot_proof_service._is_phase7_pilot_row(_row(channel="pilot", action="pilot_feedback.reported")) is True
    assert pilot_proof_service._is_phase7_pilot_row(_row(channel="email", action="pilot_feedback.reported")) is True
    assert pilot_proof_service._is_phase7_pilot_row(_row(channel="pilot", action="sage_chat.completed")) is False


def test_project_row_uses_action_or_event_class_for_workflow_fallback():
    projected = pilot_proof_service._project_row(
        _row(
            channel="pilot",
            action="sage_chat.completed",
            event_class="sage_activity",
            metadata={},
            payload={"elapsed_seconds": 2.5},
        )
    )

    assert projected["workflow"] == "sage_chat.completed"
    assert projected["response_time_seconds"] == 2.5


def test_open_p0_or_p1_issue_requires_open_status_and_review_required():
    base_issue = {
        "workflow": "customer_question_handling",
        "severity": "p0",
        "issue": {"severity": "p0", "fix_status": "open"},
    }
    assert (
        pilot_proof_service._is_open_p0_p1_issue(
            _row(
                action="pilot_issue.reported",
                status="open",
                channel="pilot",
                payload=base_issue,
                review_required=False,
            )
        )
        is False
    )
    assert (
        pilot_proof_service._is_open_p0_p1_issue(
            _row(
                action="pilot_issue.reported",
                status="open",
                channel="pilot",
                payload=base_issue,
                review_required=True,
            )
        )
        is True
    )
    assert (
        pilot_proof_service._is_open_p0_p1_issue(
            _row(
                action="pilot_issue.reported",
                status="resolved",
                channel="pilot",
                payload=base_issue,
                review_required=True,
            )
        )
        is False
    )


@pytest.mark.anyio
async def test_insufficient_data_blocks_proof():
    with patch(
        "server_modules.pilot_proof_service.pilot_operations_service.build_pilot_operations_report",
        new=AsyncMock(return_value=_report()),
    ), patch(
        "server_modules.pilot_proof_service.control_plane_repository.list_activity_ledger_events",
        new=AsyncMock(return_value=[]),
    ):
        package = await pilot_proof_service.build_pilot_proof_package(
            tenant_id="tenant-1",
            workspace_id="ws-1",
        )

    readiness = package["proof_readiness"]
    assert readiness["proof_status"] == "insufficient_data"
    assert readiness["can_prepare_ads"] is False
    assert readiness["missing_evidence"]


@pytest.mark.anyio
async def test_real_metrics_produce_case_study_ready_summary_with_trace_ids():
    with patch(
        "server_modules.pilot_proof_service.pilot_operations_service.build_pilot_operations_report",
        new=AsyncMock(
            return_value=_report(
                active_users=2,
                messages_handled=10,
                tasks_completed=3,
                tasks_failed=1,
                approval_count=2,
                blocked_action_count=1,
                average_response_time_seconds=1.4,
                repeated_usage_by_user=1,
                user_reported_usefulness=4.0,
            )
        ),
    ), patch(
        "server_modules.pilot_proof_service.control_plane_repository.list_activity_ledger_events",
        new=AsyncMock(return_value=[_row(trace_id="trace-case")]),
    ):
        package = await pilot_proof_service.build_pilot_proof_package(
            tenant_id="tenant-1",
            workspace_id="ws-1",
        )

    assert package["proof_readiness"]["proof_status"] == "ready_for_case_study"
    assert package["proof_metrics"]["failure_rate"] == 0.25
    assert package["proof_metrics"]["manual_approval_rate"] == 0.2
    assert package["proof_metrics"]["blocked_action_rate"] == 0.1
    assert package["proof_metrics"]["trace_ids"] == ["trace-case"]


def test_case_study_uses_not_enough_data_instead_of_inventing_claims():
    package = {
        "proof_readiness": {"proof_status": "insufficient_data", "missing_evidence": ["Need real pilot messages."]},
        "proof_metrics": {"messages_handled": 0},
        "evidence": {"trace_ids": []},
    }

    case_study = pilot_proof_service.build_case_study(package)

    assert case_study["measured_result"] == "not_enough_data"
    assert case_study["user_type"] == "not_enough_data"
    assert case_study["what_is_still_not_proven"] == ["Need real pilot messages."]


def test_investor_memo_includes_limitations_and_evidence():
    package = {
        "proof_readiness": {"proof_status": "ready_for_case_study", "missing_evidence": []},
        "proof_metrics": {"messages_handled": 12, "repeat_usage_rate": 0.5},
        "evidence": {"trace_ids": ["trace-1"], "failures": [{"trace_id": "trace-fail"}]},
    }

    memo = pilot_proof_service.build_investor_memo(package)

    assert memo["workflow_proven"] == "WhatsApp/Telegram customer question handling"
    assert memo["evidence_trace_ids"] == ["trace-1"]
    assert memo["what_is_not_proven_yet"]


@pytest.mark.anyio
async def test_unresolved_p0_issue_blocks_readiness_and_ads():
    p0_issue = _row(
        event_class="blocked_action",
        action="pilot_issue.reported",
        status="open",
        channel="pilot",
        trace_id="trace-p0",
        payload={
            "workflow": "customer_question_handling",
            "severity": "p0",
            "issue": {"severity": "p0", "fix_status": "open"},
        },
        review_required=True,
    )
    with patch(
        "server_modules.pilot_proof_service.pilot_operations_service.build_pilot_operations_report",
        new=AsyncMock(
            return_value=_report(
                active_users=5,
                messages_handled=50,
                tasks_completed=15,
                user_reported_usefulness=4.0,
            )
        ),
    ), patch(
        "server_modules.pilot_proof_service.control_plane_repository.list_activity_ledger_events",
        new=AsyncMock(return_value=[_row(), p0_issue]),
    ):
        package = await pilot_proof_service.build_pilot_proof_package(
            tenant_id="tenant-1",
            workspace_id="ws-1",
        )

    assert package["proof_readiness"]["proof_status"] == "insufficient_data"
    assert package["proof_readiness"]["unresolved_p0_p1_issues"][0]["trace_id"] == "trace-p0"
    ads = pilot_proof_service.build_ads_readiness(package)
    assert ads["ads_ready"] is False
    assert "Open P0/P1 pilot issues remain." in ads["reasons"]


@pytest.mark.anyio
async def test_ads_readiness_passes_only_with_investor_level_proof():
    with patch(
        "server_modules.pilot_proof_service.pilot_operations_service.build_pilot_operations_report",
        new=AsyncMock(
            return_value=_report(
                active_users=5,
                messages_handled=50,
                tasks_completed=15,
                tasks_failed=1,
                approval_count=4,
                blocked_action_count=1,
                repeated_usage_by_user=3,
                user_reported_usefulness=4.5,
            )
        ),
    ), patch(
        "server_modules.pilot_proof_service.control_plane_repository.list_activity_ledger_events",
        new=AsyncMock(return_value=[_row(trace_id="trace-investor")]),
    ):
        package = await pilot_proof_service.build_pilot_proof_package(
            tenant_id="tenant-1",
            workspace_id="ws-1",
        )

    assert package["proof_readiness"]["proof_status"] == "ready_for_investor_memo"
    ads = pilot_proof_service.build_ads_readiness(package)
    assert ads["ads_ready"] is True
    assert "launch_campaign" not in ads["forbidden_actions"]


class TestPilotProofRoutes:
    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(routes_pilot.router, prefix="/api")
        current_user = {
            "user_id": "user-1",
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
    async def test_readiness_route_enforces_workspace_access(self):
        app = self._build_app()
        with patch("server_modules.routes_pilot.auth.enforce_workspace_access", return_value="ws-1") as access, patch(
            "server_modules.routes_pilot.auth.workspace_tenant_id",
            return_value="tenant-1",
        ), patch(
            "server_modules.routes_pilot.pilot_proof_service.build_pilot_proof_package",
            new=AsyncMock(return_value={"proof_readiness": {"proof_status": "insufficient_data"}}),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/pilot/proof/readiness?workspace_id=ws-1")

        assert response.status_code == 200
        access.assert_called_once()
        assert response.json()["proof_readiness"]["proof_status"] == "insufficient_data"

    @pytest.mark.anyio
    async def test_ads_readiness_route_returns_gate(self):
        app = self._build_app()
        package = {
            "proof_readiness": {"proof_status": "insufficient_data", "unresolved_p0_p1_issues": []},
            "proof_metrics": {"failure_rate": 0, "average_usefulness_score": None},
        }
        with patch("server_modules.routes_pilot.auth.enforce_workspace_access", return_value="ws-1"), patch(
            "server_modules.routes_pilot.auth.workspace_tenant_id",
            return_value="tenant-1",
        ), patch(
            "server_modules.routes_pilot.pilot_proof_service.build_pilot_proof_package",
            new=AsyncMock(return_value=package),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/pilot/proof/ads-readiness?workspace_id=ws-1")

        assert response.status_code == 200
        assert response.json()["ads_readiness"]["ads_ready"] is False

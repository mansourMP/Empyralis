from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import platform_analytics_service


def _deployed_agent(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "dagent_1",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "backing_install_id": "ainstall_1",
        "name": "HealthGuide",
        "deployment_state": "live",
        "runtime_target": "cloud",
        "billing_plan": "pro",
        "provider": "openai",
        "model": "gpt-4o",
        "metadata": {},
    }
    payload.update(overrides)
    return payload


class PlatformAnalyticsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_platform_analytics_payload_aggregates_cross_workspace_totals(self) -> None:
        deployments = [
            _deployed_agent(id="dagent_1", owner_workspace_id="ws-1", name="HealthGuide", provider="openai", model="gpt-4o"),
            _deployed_agent(id="dagent_2", owner_workspace_id="ws-2", name="StudyMate", provider="deepseek", model="deepseek-chat"),
        ]
        workspaces = {
            "ws-1": {"workspace_id": "ws-1", "tenant_id": "tenant-1", "name": "Workspace One"},
            "ws-2": {"workspace_id": "ws-2", "tenant_id": "tenant-1", "name": "Workspace Two"},
        }
        billing_summaries = {
            "ws-1": {
                "workspace_id": "ws-1",
                "workspace_name": "Workspace One",
                "provider": "stripe",
                "account": {"billing_email": "owner1@example.com", "provider_customer_id": "cus_1"},
                "subscription": {"plan_id": "pro", "status": "active", "current_period_end": "2026-04-30T00:00:00Z"},
            },
            "ws-2": {
                "workspace_id": "ws-2",
                "workspace_name": "Workspace Two",
                "provider": "stripe",
                "account": {"billing_email": "owner2@example.com", "provider_customer_id": "cus_2"},
                "subscription": {"plan_id": "free", "status": "inactive", "current_period_end": None},
            },
        }
        activity_rollups = {
            "dagent_1": {
                "active_users_30d": 9,
                "message_count_day": 4,
                "message_count_week": 21,
                "message_count_month": 64,
                "latest_message_at": "2026-04-13T12:00:00Z",
            },
            "dagent_2": {
                "active_users_30d": 5,
                "message_count_day": 2,
                "message_count_week": 11,
                "message_count_month": 31,
                "latest_message_at": "2026-04-13T11:00:00Z",
            },
        }
        cost_summaries = {
            "dagent_1": {"usage_month": "2026-04-01", "total_cost_usd": 12.5, "runs_count": 18},
            "dagent_2": {"usage_month": "2026-04-01", "total_cost_usd": 4.5, "runs_count": 9},
        }
        cost_ledger_rows = [
            {"provider": "openai", "model": "gpt-4o", "estimated_cost_usd": 12.5, "runs_count": 18, "total_tokens": 50000},
            {"provider": "deepseek", "model": "deepseek-chat", "estimated_cost_usd": 4.5, "runs_count": 9, "total_tokens": 22000},
        ]

        async def fake_get_workspace_by_id(workspace_id: str):
            return workspaces[workspace_id]

        async def fake_get_workspace_billing_summary(workspace_id: str):
            return billing_summaries[workspace_id]

        async def fake_summarize_activity_rollup(*, deployed_agent_id: str, **_: object):
            return activity_rollups[deployed_agent_id]

        async def fake_summarize_cost_ledger(*, deployed_agent_id: str, **_: object):
            return cost_summaries[deployed_agent_id]

        with (
            patch.object(
                platform_analytics_service.control_plane_repository,
                "list_platform_deployed_agents",
                new=AsyncMock(return_value=deployments),
            ),
            patch.object(
                platform_analytics_service.control_plane_repository,
                "get_workspace_by_id",
                new=AsyncMock(side_effect=fake_get_workspace_by_id),
            ),
            patch.object(
                platform_analytics_service.control_plane_repository,
                "get_workspace_billing_summary",
                new=AsyncMock(side_effect=fake_get_workspace_billing_summary),
            ),
            patch.object(
                platform_analytics_service.control_plane_repository,
                "list_platform_deployed_agent_monthly_cost_ledger_entries",
                new=AsyncMock(return_value=cost_ledger_rows),
            ),
            patch.object(
                platform_analytics_service.control_plane_repository,
                "summarize_deployed_agent_activity_rollup",
                new=AsyncMock(side_effect=fake_summarize_activity_rollup),
            ),
            patch.object(
                platform_analytics_service.control_plane_repository,
                "summarize_deployed_agent_monthly_cost_ledger",
                new=AsyncMock(side_effect=fake_summarize_cost_ledger),
            ),
        ):
            payload = await platform_analytics_service.build_platform_analytics_payload(
                current_user={"user_id": "admin-1", "auth_type": "bearer", "auth_admin": True},
                usage_month="2026-04-01",
            )

        self.assertEqual(payload["scope"], "platform")
        self.assertEqual(payload["summary"]["total_deployed_agents"], 2)
        self.assertEqual(payload["summary"]["total_active_external_users_30d"], 14)
        self.assertEqual(payload["summary"]["total_messages"], 95)
        self.assertEqual(payload["summary"]["current_month_cost_usd"], 17.0)
        self.assertEqual(payload["deployments"][0]["deployed_agent_id"], "dagent_1")
        self.assertEqual(payload["deployments"][0]["billing_proxy"]["effective_plan_label"], "Pro")
        self.assertEqual(payload["most_active_deployed_agents"][0]["deployed_agent_id"], "dagent_1")
        self.assertEqual(payload["model_cost_breakdown"][0]["provider"], "openai")
        self.assertEqual(payload["model_cost_breakdown"][0]["model"], "gpt-4o")
        self.assertEqual(payload["model_cost_breakdown"][0]["total_cost_usd"], 12.5)

    async def test_build_platform_analytics_payload_rejects_non_admin(self) -> None:
        with self.assertRaises(HTTPException) as exc_info:
            await platform_analytics_service.build_platform_analytics_payload(
                current_user={"user_id": "member-1", "auth_type": "bearer", "auth_admin": False},
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "Admin role required.")

from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import deployed_agent_analytics_service


def _deployed_agent(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "dagent_1",
        "backing_install_id": "ainstall_1",
        "metadata": {
            "monthly_cost_cap_usd": 25.0,
        },
    }
    payload.update(overrides)
    return payload


class DeployedAgentAnalyticsServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global deployed_agent_analytics_service
        deployed_agent_analytics_service = importlib.import_module(
            "server_modules.deployed_agent_analytics_service"
        )

    async def test_summarize_deployed_agent_analytics_aggregates_durable_truth(self) -> None:
        with (
            patch(
                "server_modules.deployed_agent_analytics_service.control_plane_repository.summarize_deployed_agent_activity_rollup",
                new=AsyncMock(
                    return_value={
                        "active_users_30d": 12,
                        "message_count_day": 3,
                        "message_count_week": 18,
                        "message_count_month": 58,
                        "latest_message_at": "2026-04-13T12:05:00Z",
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_analytics_service.control_plane_repository.list_deployed_agent_session_analytics",
                new=AsyncMock(
                    return_value=[
                        {
                            "session_id": "sess-1",
                            "had_escalation": True,
                            "latest_outcome": "completed",
                            "latest_run_id": "run-1",
                        },
                        {
                            "session_id": "sess-2",
                            "had_escalation": False,
                            "latest_outcome": None,
                            "latest_run_id": "run-2",
                        },
                    ]
                ),
            ),
            patch(
                "server_modules.deployed_agent_analytics_service.control_plane_repository.summarize_deployed_agent_monthly_cost_ledger",
                new=AsyncMock(
                    return_value={
                        "usage_month": "2026-04-01",
                        "total_cost_usd": 12.5,
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_analytics_service.control_plane_repository.list_deployed_agent_daily_activity_series",
                new=AsyncMock(
                    return_value=[
                        {"date": "2026-04-12", "conversation_count": 1, "message_count": 5},
                        {"date": "2026-04-13", "conversation_count": 2, "message_count": 8},
                    ]
                ),
            ),
            patch(
                "server_modules.deployed_agent_analytics_service.run_state_repository.get_live_run",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.deployed_agent_analytics_service.run_state_repository.get_archived_run",
                new=AsyncMock(return_value={"status": "failed"}),
            ),
        ):
            payload = await deployed_agent_analytics_service.summarize_deployed_agent_analytics(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agent=_deployed_agent(),
            )

        self.assertEqual(payload["deployed_agent_id"], "dagent_1")
        self.assertEqual(payload["active_users_last_30d"], 12)
        self.assertEqual(payload["message_volume"]["month"], 58)
        self.assertEqual(payload["escalation"]["total_sessions"], 2)
        self.assertEqual(payload["escalation"]["escalated_sessions"], 1)
        self.assertEqual(payload["escalation"]["rate_percent"], 50.0)
        self.assertEqual(payload["outcomes"]["counts"]["completed"], 1)
        self.assertEqual(payload["outcomes"]["counts"]["failed"], 1)
        self.assertEqual(payload["cost_burn"]["current_burn_usd"], 12.5)
        self.assertEqual(payload["cost_burn"]["cap_usd"], 25.0)
        self.assertEqual(payload["cost_burn"]["percent_used"], 50.0)
        self.assertEqual(
            payload["daily_series"],
            [
                {"date": "2026-04-12", "conversation_count": 1, "message_count": 5},
                {"date": "2026-04-13", "conversation_count": 2, "message_count": 8},
            ],
        )

    async def test_summarize_workspace_deployed_agent_analytics_returns_item_per_deployment(self) -> None:
        with patch(
            "server_modules.deployed_agent_analytics_service.summarize_deployed_agent_analytics",
            new=AsyncMock(
                side_effect=[
                    {"deployed_agent_id": "dagent_1", "active_users_last_30d": 5},
                    {"deployed_agent_id": "dagent_2", "active_users_last_30d": 7},
                ]
            ),
        ) as summarize_mock:
            payload = await deployed_agent_analytics_service.summarize_workspace_deployed_agent_analytics(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agents=[
                    _deployed_agent(id="dagent_1", backing_install_id="ainstall_1"),
                    _deployed_agent(id="dagent_2", backing_install_id="ainstall_2"),
                ],
            )

        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["deployed_agent_id"], "dagent_1")
        self.assertEqual(payload["items"][1]["deployed_agent_id"], "dagent_2")
        self.assertEqual(summarize_mock.await_count, 2)

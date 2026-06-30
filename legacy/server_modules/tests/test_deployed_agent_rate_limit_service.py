from __future__ import annotations

import importlib
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

from server_modules import control_plane_repository, deployed_agent_rate_limit_service


def _fake_scoped_connection(connection):
    @asynccontextmanager
    async def _manager(*args, **kwargs):
        yield connection

    return _manager


class DeployedAgentRateLimitRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global control_plane_repository
        control_plane_repository = importlib.import_module("server_modules.control_plane_repository")

    def test_control_plane_schema_declares_daily_message_usage_table_and_indexes(self) -> None:
        schema = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        self.assertIn("CREATE TABLE IF NOT EXISTS deployed_agent_daily_message_usage", schema)
        self.assertIn("UNIQUE(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id, usage_day)", schema)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_deployed_agent_daily_message_usage_scope_day", schema)

    async def test_consume_deployed_agent_daily_message_quota_returns_normalized_record(self) -> None:
        row = {
            "id": "dusage_1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "deployed_agent_id": "dagent_1",
            "channel_key": "telegram",
            "external_user_id": "telegram-user-1",
            "usage_day": date(2026, 4, 13),
            "message_count": 2,
            "metadata": {"message_id": "tg-msg-2"},
            "last_message_at": datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
            "created_at": datetime(2026, 4, 13, 11, 0, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
            "quota_consumed": True,
        }
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(return_value=row)

        with patch(
            "server_modules.control_plane_repository._scoped_connection",
            new=_fake_scoped_connection(connection),
        ):
            payload = await control_plane_repository.consume_deployed_agent_daily_message_quota(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent_1",
                channel_key="telegram",
                external_user_id="telegram-user-1",
                usage_day="2026-04-13",
                limit=3,
                metadata={"message_id": "tg-msg-2"},
            )

        self.assertEqual(payload["message_count"], 2)
        self.assertEqual(payload["usage_day"], "2026-04-13")
        self.assertTrue(payload["quota_consumed"])
        query = connection.fetchrow.await_args.args[0]
        self.assertIn("INSERT INTO deployed_agent_daily_message_usage", query)
        self.assertIn("ON CONFLICT (tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id, usage_day)", query)

    async def test_get_deployed_agent_daily_message_usage_queries_full_scope(self) -> None:
        row = {
            "id": "dusage_1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "deployed_agent_id": "dagent_1",
            "channel_key": "telegram",
            "external_user_id": "telegram-user-1",
            "usage_day": date(2026, 4, 13),
            "message_count": 1,
            "metadata": {},
            "last_message_at": datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
            "created_at": datetime(2026, 4, 13, 11, 0, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
        }
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(return_value=row)

        with patch(
            "server_modules.control_plane_repository._scoped_connection",
            new=_fake_scoped_connection(connection),
        ):
            payload = await control_plane_repository.get_deployed_agent_daily_message_usage(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent_id="dagent_1",
                channel_key="telegram",
                external_user_id="telegram-user-1",
                usage_day="2026-04-13",
            )

        self.assertEqual(payload["external_user_id"], "telegram-user-1")
        self.assertEqual(connection.fetchrow.await_args.args[1:], (
            "tenant-1",
            "workspace-1",
            "dagent_1",
            "telegram",
            "telegram-user-1",
            date(2026, 4, 13),
        ))


class DeployedAgentRateLimitServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global deployed_agent_rate_limit_service
        deployed_agent_rate_limit_service = importlib.import_module(
            "server_modules.deployed_agent_rate_limit_service"
        )

    async def test_enforce_daily_message_limit_is_disabled_without_configured_limit(self) -> None:
        verdict = await deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            deployed_agent={"id": "dagent_1", "metadata": {}},
            channel_key="telegram",
            external_user_id="telegram-user-1",
            now=datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(verdict["applied"])
        self.assertTrue(verdict["allowed"])

    async def test_enforce_daily_message_limit_is_keyed_by_user_and_deployment(self) -> None:
        counters: dict[tuple[str, str, str, str, str, str], int] = {}

        async def fake_consume(**kwargs):
            key = (
                kwargs["tenant_id"],
                kwargs["workspace_id"],
                kwargs["deployed_agent_id"],
                kwargs["channel_key"],
                kwargs["external_user_id"],
                kwargs["usage_day"],
            )
            current = counters.get(key, 0)
            limit = int(kwargs["limit"])
            allowed = current < limit
            if allowed:
                current += 1
                counters[key] = current
            return {
                "id": "dusage_1",
                "tenant_id": kwargs["tenant_id"],
                "workspace_id": kwargs["workspace_id"],
                "deployed_agent_id": kwargs["deployed_agent_id"],
                "channel_key": kwargs["channel_key"],
                "external_user_id": kwargs["external_user_id"],
                "usage_day": kwargs["usage_day"],
                "message_count": current,
                "metadata": kwargs.get("metadata") or {},
                "quota_consumed": allowed,
            }

        with patch(
            "server_modules.deployed_agent_rate_limit_service.control_plane_repository.consume_deployed_agent_daily_message_quota",
            new=AsyncMock(side_effect=fake_consume),
        ):
            deployed_agent_one = {
                "id": "dagent_1",
                "metadata": {
                    "daily_message_limit": 2,
                    "upgrade_cta_url": "https://app.empyralist.com/signup",
                    "upgrade_cta_label": "Continue on Empyralist",
                },
            }
            deployed_agent_two = {
                "id": "dagent_2",
                "metadata": {
                    "daily_message_limit": 2,
                    "upgrade_cta_url": "https://app.empyralist.com/signup",
                    "upgrade_cta_label": "Continue on Empyralist",
                },
            }
            now = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)

            first_user_first = await deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent=deployed_agent_one,
                channel_key="telegram",
                external_user_id="telegram-user-1",
                now=now,
            )
            second_user_first = await deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent=deployed_agent_one,
                channel_key="telegram",
                external_user_id="telegram-user-2",
                now=now,
            )
            first_user_second = await deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent=deployed_agent_one,
                channel_key="telegram",
                external_user_id="telegram-user-1",
                now=now,
            )
            first_user_denied = await deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent=deployed_agent_one,
                channel_key="telegram",
                external_user_id="telegram-user-1",
                now=now,
            )
            other_deployment_first = await deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                deployed_agent=deployed_agent_two,
                channel_key="telegram",
                external_user_id="telegram-user-1",
                now=now,
            )

        self.assertTrue(first_user_first["allowed"])
        self.assertEqual(first_user_first["message_count"], 1)
        self.assertTrue(second_user_first["allowed"])
        self.assertEqual(second_user_first["message_count"], 1)
        self.assertTrue(first_user_second["allowed"])
        self.assertEqual(first_user_second["message_count"], 2)
        self.assertFalse(first_user_denied["allowed"])
        self.assertEqual(first_user_denied["message_count"], 2)
        self.assertEqual(first_user_denied["remaining"], 0)
        self.assertTrue(other_deployment_first["allowed"])
        self.assertEqual(other_deployment_first["message_count"], 1)

from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import external_user_privacy_service


class ExternalUserPrivacyServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global external_user_privacy_service
        external_user_privacy_service = importlib.import_module("server_modules.external_user_privacy_service")

    def test_public_command_action_detects_privacy_and_delete_for_deployed_agents(self) -> None:
        service = external_user_privacy_service.ExternalUserPrivacyService(
            privacy_policy_url_resolver=lambda: "https://app.empyralist.com/privacy",
        )
        profile = {"deployed_agent_id": "dagent_1"}

        privacy = service.public_command_action(profile, "/privacy")
        deletion = service.public_command_action(profile, "/delete please remove my data")
        ignored = service.public_command_action({}, "/privacy")

        self.assertEqual(privacy, {"action": "privacy_policy", "routed": {"command": "privacy"}})
        self.assertEqual(
            deletion,
            {"action": "privacy_delete_request", "routed": {"note": "please remove my data", "command": "delete"}},
        )
        self.assertIsNone(ignored)

    def test_append_privacy_policy_line_is_idempotent(self) -> None:
        service = external_user_privacy_service.ExternalUserPrivacyService(
            privacy_policy_url_resolver=lambda: "https://app.empyralist.com/privacy",
        )

        first = service.append_privacy_policy_line("Quota reached.")
        second = service.append_privacy_policy_line(first)

        self.assertIn("https://app.empyralist.com/privacy", first)
        self.assertEqual(first, second)

    def test_workspace_admin_defaults_override_privacy_policy_url(self) -> None:
        def _control_plane_runner(coro):
            try:
                coro.close()
            except Exception:
                pass
            return {
                "id": "ws-1",
                "metadata": {
                    "admin_defaults": {
                        "payload": {
                            "privacy_policy_url": "https://workspace.example.com/privacy",
                        }
                    }
                },
            }

        service = external_user_privacy_service.ExternalUserPrivacyService(
            control_plane_runner=_control_plane_runner,
            privacy_policy_url_resolver=lambda: "https://app.empyralist.com/privacy",
        )

        rendered = service.append_privacy_policy_line("Quota reached.", workspace_id="ws-1")

        self.assertIn("https://workspace.example.com/privacy", rendered)
        self.assertNotIn("https://app.empyralist.com/privacy", rendered)

    async def test_purge_deployed_agent_external_user_data_updates_request_and_audit(self) -> None:
        service = external_user_privacy_service.ExternalUserPrivacyService(
            privacy_policy_url_resolver=lambda: "https://app.empyralist.com/privacy",
        )

        with (
            patch(
                "server_modules.external_user_privacy_service.control_plane_repository.upsert_external_user_privacy_request",
                new=AsyncMock(return_value={"id": "privreq_1", "status": "completed"}),
            ) as upsert_request_mock,
            patch(
                "server_modules.external_user_privacy_service.control_plane_repository.delete_deployed_agent_external_user_data",
                new=AsyncMock(
                    return_value={
                        "deleted_channel_event_count": 4,
                        "deleted_memory_count": 1,
                        "deleted_daily_usage_count": 2,
                        "deleted_acquisition_touch_count": 1,
                    }
                ),
            ) as delete_mock,
            patch(
                "server_modules.external_user_privacy_service.control_plane_repository.append_external_user_privacy_delete_audit",
                new=AsyncMock(return_value={"id": "privaudit_1"}),
            ) as append_audit_mock,
        ):
            result = await service.purge_deployed_agent_external_user_data(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agent_id="dagent_1",
                channel_key="telegram",
                external_user_id="customer-1",
                actor_user_id="user-owner",
                note="Customer requested deletion",
                metadata={"session_id": "sess-1"},
            )

        self.assertEqual(result["request"]["id"], "privreq_1")
        self.assertEqual(result["audit"]["id"], "privaudit_1")
        self.assertEqual(result["deleted_counts"]["deleted_channel_event_count"], 4)
        self.assertEqual(upsert_request_mock.await_args.kwargs["status"], "completed")
        self.assertEqual(delete_mock.await_args.kwargs["channel_key"], "telegram")
        self.assertEqual(append_audit_mock.await_args.kwargs["deleted_memory_count"], 1)

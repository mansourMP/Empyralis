from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import gateway_health_service


class GatewayHealthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global gateway_health_service
        gateway_health_service = importlib.import_module("server_modules.gateway_health_service")
        gateway_health_service._PROVIDER_PROBE_CACHE.clear()
        self.sweep_patcher = patch(
            "server_modules.gateway_health_service.gateway_state_repository.sweep_stale_gateway_sessions",
            return_value=0,
        )
        self.sweep_patcher.start()

    def tearDown(self) -> None:
        self.sweep_patcher.stop()

    def test_gateway_doctor_payload_includes_specialist_provider_and_quota_facets(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "status": "active",
            "device_trust_state": "trusted",
            "journal_cursor": 3,
            "checkpoint_cursor": 1,
            "metadata": {},
        }
        latest_session = {
            "status": "connected",
            "last_heartbeat_at": "2099-01-01T00:00:00Z",
            "last_seq": 3,
            "last_ack": 3,
            "metadata": {
                "health_state": "healthy",
                "service_inventory": [
                    {
                        "id": "postgres",
                        "label": "Postgres",
                        "kind": "database",
                        "status": "ready",
                        "detected": True,
                        "passive": True,
                        "execution_enabled": False,
                        "check": "pg_isready -q",
                        "summary": "ready",
                        "last_checked_at": "2026-05-29T00:00:00Z",
                    }
                ],
                "native_runtime": {
                    "os": "darwin",
                    "arch": "arm64",
                    "release": "25.0.0",
                    "hostname": "mansur-mac",
                    "desktop_session": "user_session",
                    "system_service_mode": False,
                },
            },
        }
        projected_specialist = {
            "id": "da-1",
            "name": "Cafe Orders",
            "deployment_state": "live",
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "config": {
                "channels": {
                    "telegram_bot": {"enabled": True},
                },
            },
            "operational_state": {"deployment_state": "live"},
        }
        provider_catalog = {
            "providers": [
                {
                    "id": "gemini",
                    "label": "Gemini",
                    "state": "degraded",
                    "state_detail": "Provider key is cooling down.",
                }
            ]
        }
        billing_summary = {
            "limits": {
                "max_specialists": 3,
                "hosted_runtime_minutes_monthly": 1500,
                "hosted_ai_enabled": True,
            },
            "usage": {
                "specialists_in_use": 1,
                "hosted_runtime_minutes_monthly": 42,
            },
            "subscription": {"effective_plan_id": "pro"},
        }

        with patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_gateway_registration",
            return_value=registration,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_latest_gateway_session",
            return_value=latest_session,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_browser_sessions",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_events",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_approval_service.list_gateway_tool_approvals",
            return_value={"pending_count": 0, "retryable_count": 0},
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_whatsapp_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_telegram_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"workspace_id": "ws-1", "tenant_id": "tenant-1"}),
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.list_deployed_agents_for_workspace",
            new=AsyncMock(return_value=[{"id": "da-1"}]),
        ), patch(
            "server_modules.gateway_health_service.deployed_agent_service.project_deployed_agent",
            return_value=projected_specialist,
        ), patch(
            "server_modules.gateway_health_service.provider_catalog_service.list_workspace_provider_catalog",
            new=AsyncMock(return_value=provider_catalog),
        ), patch(
            "server_modules.gateway_health_service.billing_service.workspace_billing_summary_for_workspace_id",
            return_value=billing_summary,
        ):
            payload = gateway_health_service.gateway_doctor_payload("gw-1")

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["specialists"]["count"], 1)
        self.assertEqual(payload["specialists"]["items"][0]["name"], "Cafe Orders")
        self.assertEqual(payload["specialists"]["items"][0]["status"], "fail")
        self.assertEqual(payload["providers"]["status"], "fail")
        self.assertEqual(payload["providers"]["items"][0]["id"], "gemini")
        self.assertEqual(payload["quota"]["status"], "pass")
        self.assertEqual(payload["service_inventory"]["status"], "pass")
        self.assertEqual(payload["service_inventory"]["items"][0]["id"], "postgres")
        self.assertFalse(payload["service_inventory"]["items"][0]["execution_enabled"])
        self.assertEqual(payload["service_inventory"]["native_runtime"]["desktop_session"], "user_session")
        check_ids = {item["id"] for item in payload["checks"]}
        self.assertIn("studio_specialists", check_ids)
        self.assertIn("provider_reachability", check_ids)
        self.assertIn("workspace_quota", check_ids)
        self.assertIn("passive_service_inventory", check_ids)

    def test_gateway_inventory_redacts_secret_like_diagnostics(self) -> None:
        inventory = gateway_health_service.gateway_inventory_service.sanitize_service_inventory(
            [
                {
                    "id": "postgres",
                    "label": "Postgres",
                    "kind": "database",
                    "status": "ready",
                    "detected": True,
                    "execution_enabled": True,
                    "check": "api-key: TEST_BEARER_CANARY_VALUE",
                    "summary": "ready with api_key=TEST_SECRET_CANARY_VALUE",
                    "metadata": {
                        "api_key": "TEST_SECRET_CANARY_VALUE",
                        "logs": ["debug token=TEST_COOKIE_CANARY_VALUE"],
                    },
                }
            ]
        )

        rendered = str(inventory)
        self.assertEqual(inventory[0]["id"], "postgres")
        self.assertFalse(inventory[0]["execution_enabled"])
        self.assertTrue(inventory[0]["passive"])
        self.assertNotIn("TEST_SECRET_CANARY_VALUE", rendered)
        self.assertNotIn("TEST_BEARER_CANARY_VALUE", rendered)
        self.assertNotIn("TEST_COOKIE_CANARY_VALUE", rendered)
        self.assertIn("[redacted", rendered)

    def test_gateway_capability_readiness_redacts_permission_diagnostics(self) -> None:
        readiness = gateway_health_service.gateway_inventory_service.sanitize_capability_readiness(
            {
                "requested": ["screenshot.capture", "computer_control.click"],
                "ready": ["screenshot.capture"],
                "blocked": ["computer_control.click"],
                "permission_states": {
                    "computer_control.click": {
                        "capability_id": "computer_control.click",
                        "permission": "accessibility",
                        "state": "denied",
                        "prompt_available": True,
                        "summary": "denied with token=TEST_SECRET_CANARY_VALUE",
                    }
                },
                "service_statuses": {"desktop_permissions": "blocked"},
            }
        )

        rendered = str(readiness)
        self.assertEqual(readiness["ready"], ["screenshot.capture"])
        self.assertEqual(readiness["blocked"], ["computer_control.click"])
        self.assertEqual(
            readiness["permission_states"]["computer_control.click"]["state"],
            "denied",
        )
        self.assertNotIn("TEST_SECRET_CANARY_VALUE", rendered)
        self.assertIn("[redacted", rendered)

    def test_gateway_doctor_payload_uses_cached_provider_probe_until_forced(self) -> None:
        registration = {
            "gateway_id": "gw-probe",
            "workspace_id": "ws-1",
            "status": "active",
            "device_trust_state": "trusted",
            "journal_cursor": 0,
            "checkpoint_cursor": 0,
            "metadata": {},
        }
        latest_session = {
            "status": "connected",
            "last_heartbeat_at": "2099-01-01T00:00:00Z",
            "last_seq": 0,
            "last_ack": 0,
            "metadata": {"health_state": "healthy"},
        }
        projected_specialist = {
            "id": "da-probe",
            "name": "Cafe Orders",
            "deployment_state": "live",
            "provider": "gemini",
            "config": {"channels": {"telegram_bot": {"enabled": True}}},
            "operational_state": {"deployment_state": "live"},
        }
        provider_catalog = {
            "providers": [{"id": "gemini", "label": "Gemini", "state": "active"}]
        }
        billing_summary = {
            "limits": {"max_specialists": 3},
            "usage": {"specialists_in_use": 1},
            "subscription": {"effective_plan_id": "pro"},
        }

        with patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_gateway_registration",
            return_value=registration,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_latest_gateway_session",
            return_value=latest_session,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_browser_sessions",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_events",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_approval_service.list_gateway_tool_approvals",
            return_value={"pending_count": 0, "retryable_count": 0},
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_whatsapp_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_telegram_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"workspace_id": "ws-1", "tenant_id": "tenant-1"}),
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.list_deployed_agents_for_workspace",
            new=AsyncMock(return_value=[{"id": "da-probe"}]),
        ), patch(
            "server_modules.gateway_health_service.deployed_agent_service.project_deployed_agent",
            return_value=projected_specialist,
        ), patch(
            "server_modules.gateway_health_service.provider_catalog_service.list_workspace_provider_catalog",
            new=AsyncMock(return_value=provider_catalog),
        ), patch(
            "server_modules.gateway_health_service.billing_service.workspace_billing_summary_for_workspace_id",
            return_value=billing_summary,
        ), patch(
            "server_modules.gateway_health_service.connectors_core.probe_provider",
            new=AsyncMock(return_value={"ok": True, "message": "ok", "model": "gemini-2.5-flash"}),
        ) as probe_mock:
            first = gateway_health_service.gateway_doctor_payload("gw-probe")
            second = gateway_health_service.gateway_doctor_payload("gw-probe")
            forced = gateway_health_service.gateway_doctor_payload("gw-probe", force_provider_probe=True)

        self.assertEqual(probe_mock.await_count, 2)
        self.assertFalse(first["providers"]["items"][0]["probe_cached"])
        self.assertTrue(second["providers"]["items"][0]["probe_cached"])
        self.assertFalse(forced["providers"]["items"][0]["probe_cached"])

    def test_gateway_doctor_payload_flags_quota_failures(self) -> None:
        registration = {
            "gateway_id": "gw-2",
            "workspace_id": "ws-1",
            "status": "active",
            "device_trust_state": "trusted",
            "journal_cursor": 0,
            "checkpoint_cursor": 0,
            "metadata": {},
        }
        latest_session = {
            "status": "connected",
            "last_heartbeat_at": "2099-01-01T00:00:00Z",
            "last_seq": 0,
            "last_ack": 0,
            "metadata": {"health_state": "healthy"},
        }

        with patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_gateway_registration",
            return_value=registration,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_latest_gateway_session",
            return_value=latest_session,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_browser_sessions",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_events",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_approval_service.list_gateway_tool_approvals",
            return_value={"pending_count": 0, "retryable_count": 0},
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_whatsapp_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_telegram_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"workspace_id": "ws-1", "tenant_id": "tenant-1"}),
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.list_deployed_agents_for_workspace",
            new=AsyncMock(return_value=[]),
        ), patch(
            "server_modules.gateway_health_service.provider_catalog_service.list_workspace_provider_catalog",
            new=AsyncMock(return_value={"providers": []}),
        ), patch(
            "server_modules.gateway_health_service.billing_service.workspace_billing_summary_for_workspace_id",
            return_value={
                "limits": {
                    "max_specialists": 1,
                    "hosted_runtime_minutes_monthly": 1500,
                    "hosted_ai_enabled": True,
                },
                "usage": {
                    "specialists_in_use": 2,
                    "hosted_runtime_minutes_monthly": 1600,
                },
                "subscription": {"effective_plan_id": "pro"},
            },
        ):
            payload = gateway_health_service.gateway_doctor_payload("gw-2")

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["quota"]["status"], "fail")
        self.assertIn("exceeded", payload["quota"]["summary"].lower())

    def test_gateway_doctor_payload_includes_existing_session_attach_readiness(self) -> None:
        registration = {
            "gateway_id": "gw-attach",
            "workspace_id": "ws-1",
            "status": "active",
            "device_trust_state": "trusted",
            "journal_cursor": 0,
            "checkpoint_cursor": 0,
            "metadata": {},
        }
        latest_session = {
            "status": "connected",
            "last_heartbeat_at": "2099-01-01T00:00:00Z",
            "last_seq": 0,
            "last_ack": 0,
            "metadata": {"health_state": "healthy"},
        }
        browser_sessions = [
            {
                "browser_session_id": "gbsess-1",
                "status": "attached",
                "reviewed_approval_required": True,
                "metadata": {
                    "browser_session_mode": "existing_session_attach",
                    "browser_attach_state": "attached",
                    "browser_reviewed_approval_required": True,
                },
            },
            {
                "browser_session_id": "gbsess-2",
                "status": "attach_required",
                "reviewed_approval_required": False,
                "metadata": {
                    "browser_session_mode": "existing_session_attach",
                    "browser_attach_state": "attach_required",
                },
            },
        ]

        with patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_gateway_registration",
            return_value=registration,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_latest_gateway_session",
            return_value=latest_session,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_browser_sessions",
            return_value=browser_sessions,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_events",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_approval_service.list_gateway_tool_approvals",
            return_value={"pending_count": 0, "retryable_count": 0},
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_whatsapp_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_telegram_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"workspace_id": "ws-1", "tenant_id": "tenant-1"}),
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.list_deployed_agents_for_workspace",
            new=AsyncMock(return_value=[]),
        ), patch(
            "server_modules.gateway_health_service.provider_catalog_service.list_workspace_provider_catalog",
            new=AsyncMock(return_value={"providers": []}),
        ), patch(
            "server_modules.gateway_health_service.billing_service.workspace_billing_summary_for_workspace_id",
            return_value={
                "limits": {"max_specialists": 1, "hosted_runtime_minutes_monthly": 0, "hosted_ai_enabled": False},
                "usage": {"specialists_in_use": 0, "hosted_runtime_minutes_monthly": 0},
                "subscription": {"effective_plan_id": "free"},
            },
        ):
            payload = gateway_health_service.gateway_doctor_payload("gw-attach")

        self.assertEqual(payload["browser_attach"]["status"], "pass")
        self.assertEqual(payload["browser_attach"]["count"], 2)
        self.assertEqual(payload["browser_attach"]["attached_count"], 1)
        self.assertEqual(payload["browser_attach"]["pending_count"], 1)
        self.assertEqual(payload["browser_attach"]["approval_required_count"], 1)

    def test_gateway_doctor_payload_includes_agent_computer_personal_channel_readiness(self) -> None:
        registration = {
            "gateway_id": "gw-personal-channels",
            "workspace_id": "ws-1",
            "status": "active",
            "device_trust_state": "trusted",
            "journal_cursor": 0,
            "checkpoint_cursor": 0,
            "metadata": {
                "personal_channel_manifests": [
                    {
                        "channel_key": "signal_personal",
                        "label": "Signal Personal",
                        "provider": "signal_local_bridge",
                        "stage": "live",
                        "status": "connected",
                        "live_capable": True,
                        "api_token": "must-not-leak",
                    },
                    {
                        "channel_key": "imessage_personal",
                        "label": "iMessage Personal",
                        "provider": "bluebubbles_local_bridge",
                        "stage": "live",
                        "status": "not_configured",
                        "live_capable": True,
                    },
                ],
                "personal_channel_health": [
                    {
                        "channel_key": "signal_personal",
                        "provider": "signal_local_bridge",
                        "status": "connected",
                        "running": True,
                        "connected": True,
                    }
                ],
            },
        }
        latest_session = {
            "status": "connected",
            "last_heartbeat_at": "2099-01-01T00:00:00Z",
            "last_seq": 0,
            "last_ack": 0,
            "metadata": {"health_state": "healthy"},
        }

        with patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_gateway_registration",
            return_value=registration,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.get_latest_gateway_session",
            return_value=latest_session,
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_browser_sessions",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_state_repository.list_gateway_events",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.gateway_approval_service.list_gateway_tool_approvals",
            return_value={"pending_count": 0, "retryable_count": 0},
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_whatsapp_state",
            return_value=None,
        ), patch(
            "server_modules.gateway_health_service.personal_channels_repository.get_telegram_state",
            return_value=None,
        ), patch(
            "server_modules.personal_channels_service.personal_channels_repository.list_recent_gateway_messages",
            return_value=[],
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"workspace_id": "ws-1", "tenant_id": "tenant-1"}),
        ), patch(
            "server_modules.gateway_health_service.control_plane_repository.list_deployed_agents_for_workspace",
            new=AsyncMock(return_value=[]),
        ), patch(
            "server_modules.gateway_health_service.provider_catalog_service.list_workspace_provider_catalog",
            new=AsyncMock(return_value={"providers": []}),
        ), patch(
            "server_modules.gateway_health_service.billing_service.workspace_billing_summary_for_workspace_id",
            return_value={
                "limits": {"max_specialists": 1, "hosted_runtime_minutes_monthly": 0, "hosted_ai_enabled": False},
                "usage": {"specialists_in_use": 0, "hosted_runtime_minutes_monthly": 0},
                "subscription": {"effective_plan_id": "free"},
            },
        ):
            payload = gateway_health_service.gateway_doctor_payload("gw-personal-channels")

        personal_channels = payload["personal_channels"]
        by_key = {item["channel_key"]: item for item in personal_channels["items"]}
        self.assertEqual(personal_channels["connected_count"], 1)
        self.assertEqual(by_key["signal_personal"]["status"], "connected")
        self.assertTrue(by_key["signal_personal"]["running"])
        self.assertEqual(by_key["signal_personal"]["manifest"]["api_token"], "[redacted]")
        self.assertIn("imessage_personal", by_key)
        check_ids = {item["id"] for item in payload["checks"]}
        self.assertIn("personal_messaging_channels", check_ids)


if __name__ == "__main__":
    unittest.main()

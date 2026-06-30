"""Tests for SageDoctorService diagnostics."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server_modules import routes_doctor
from server_modules.sage_doctor_service import CHECK_SPECS, SageDoctorService


class TestSageDoctorService:
    """Test the Sage Doctor diagnostic checks."""

    # ------------------------------------------------------------------
    #   test 1: all checks return structured results
    # ------------------------------------------------------------------

    @patch("server_modules.sage_doctor_service.SageDoctorService._resolve_gateway_id", return_value="gw-1")
    @patch("server_modules.sage_doctor_service.SageDoctorService._list_vault_connectors", return_value=[
        {"connector": "slack", "id": "cred-1"},
        {"connector": "discord_bot", "id": "cred-2"},
        {"connector": "google_workspace", "id": "cred-3"},
    ])
    def test_all_checks_return_structured_results(self, mock_vault, mock_gw):
        """Every check must contain the six expected keys."""
        # Patch individual service calls inside checks
        patchers = [
            patch(
                "server_modules.sage_agent_computer_selection_service.get_selection",
                return_value={"selected_gateway_id": "gw-1"},
            ),
            patch(
                "server_modules.gateway_registry_service.list_workspace_gateways",
                return_value={
                    "items": [
                        {
                            "gateway_id": "gw-1",
                            "connection_status": "online",
                            "capabilities": ["browser_automation"],
                        }
                    ]
                },
            ),
            patch(
                "server_modules.personal_channels_repository.get_telegram_state",
                return_value={"status": "connected"},
            ),
            patch(
                "server_modules.personal_channels_repository.get_whatsapp_state",
                return_value={"status": "connected"},
            ),
            patch(
                "server_modules.runtime_common.list_vault_connectors",
                return_value=[
                    {"connector": "slack"},
                    {"connector": "discord_bot"},
                    {"connector": "google_workspace"},
                ],
            ),
            patch(
                "server_modules.mcp_registry_service.list_workspace_mcp_servers",
                return_value=[{"id": "mcp-1"}],
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                return_value=("deepseek", {"api_key": "test"}),
            ),
            patch(
                "server_modules.outbox_service.get_outbox_delivery_status",
                return_value={"undelivered_count": 0, "poisoned_count": 0},
            ),
            patch(
                "server_modules.policy_service.action_policy_from_app_permissions",
                return_value={"allowed": True},
            ),
        ]

        for p in patchers:
            p.start()

        try:
            result = SageDoctorService.check_all(workspace_id="ws-1", tenant_id="tenant-1")
        finally:
            for p in patchers:
                p.stop()

        assert "checks" in result
        assert "summary" in result
        assert len(result["checks"]) == len(CHECK_SPECS)

        required_keys = {"id", "label", "status", "detail", "next_action", "group"}
        for check in result["checks"]:
            missing = required_keys - set(check.keys())
            assert not missing, f"Check {check.get('id', '?')} missing keys: {missing}"

    # ------------------------------------------------------------------
    #   test 2: offline gateway reports specific next_action
    # ------------------------------------------------------------------

    def test_offline_gateway_reports_specific_next_action(self):
        """When gateways exist but none are online, the check must mention 'offline'."""
        patchers = [
            patch(
                "server_modules.sage_agent_computer_selection_service.get_selection",
                return_value={"selected_gateway_id": "gw-1"},
            ),
            patch(
                "server_modules.gateway_registry_service.list_workspace_gateways",
                return_value={
                    "items": [
                        {
                            "gateway_id": "gw-1",
                            "connection_status": "offline",
                            "capabilities": [],
                        }
                    ]
                },
            ),
            patch(
                "server_modules.personal_channels_repository.get_telegram_state",
                return_value=None,
            ),
            patch(
                "server_modules.personal_channels_repository.get_whatsapp_state",
                return_value=None,
            ),
            patch(
                "server_modules.runtime_common.list_vault_connectors",
                return_value=[],
            ),
            patch(
                "server_modules.mcp_registry_service.list_workspace_mcp_servers",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                side_effect=RuntimeError("No cloud provider is configured for Sage."),
            ),
            patch(
                "server_modules.outbox_service.get_outbox_delivery_status",
                return_value={"undelivered_count": 0, "poisoned_count": 0},
            ),
            patch(
                "server_modules.policy_service.action_policy_from_app_permissions",
                return_value={"allowed": True},
            ),
        ]

        for p in patchers:
            p.start()

        try:
            result = SageDoctorService.check_all(workspace_id="ws-1", tenant_id="tenant-1")
        finally:
            for p in patchers:
                p.stop()

        checks = {c["id"]: c for c in result["checks"]}
        gw_check = checks.get("gateway_connection")

        assert gw_check is not None, "gateway_connection check missing"
        # Must mention 'offline' in the detail or next_action
        combined = (gw_check["detail"] + " " + gw_check["next_action"]).lower()
        assert (
            "offline" in combined
        ), f"Expected 'offline' in gateway check, got: {gw_check}"
        # Must NOT say "not configured"
        assert "not configured" not in combined, (
            f"Expected no 'not configured' in gateway check, got: {gw_check}"
        )

    # ------------------------------------------------------------------
    #   test 3: missing google workspace credentials returns specific
    #   instruction
    # ------------------------------------------------------------------

    def test_missing_google_credentials_returns_specific_instruction(self):
        """When Google Workspace vault is empty, next_action must reference OAuth."""
        patchers = [
            patch(
                "server_modules.sage_agent_computer_selection_service.get_selection",
                return_value={"selected_gateway_id": "gw-1"},
            ),
            patch(
                "server_modules.gateway_registry_service.list_workspace_gateways",
                return_value={
                    "items": [{"gateway_id": "gw-1", "connection_status": "online", "capabilities": []}]
                },
            ),
            patch(
                "server_modules.personal_channels_repository.get_telegram_state",
                return_value={"status": "connected"},
            ),
            patch(
                "server_modules.personal_channels_repository.get_whatsapp_state",
                return_value={"status": "connected"},
            ),
            patch(
                "server_modules.runtime_common.list_vault_connectors",
                return_value=[{"connector": "slack"}, {"connector": "discord_bot"}],
            ),
            # google_workspace explicitly missing from vault
            patch(
                "server_modules.mcp_registry_service.list_workspace_mcp_servers",
                return_value=[{"id": "mcp-1"}],
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                return_value=("deepseek", {"api_key": "test"}),
            ),
            patch(
                "server_modules.outbox_service.get_outbox_delivery_status",
                return_value={"undelivered_count": 0, "poisoned_count": 0},
            ),
            patch(
                "server_modules.policy_service.action_policy_from_app_permissions",
                return_value={"allowed": True},
            ),
        ]

        for p in patchers:
            p.start()

        try:
            result = SageDoctorService.check_all(workspace_id="ws-1", tenant_id="tenant-1")
        finally:
            for p in patchers:
                p.stop()

        checks = {c["id"]: c for c in result["checks"]}
        gws = checks.get("google_workspace_credentials")

        assert gws is not None, "google_workspace_credentials check missing"
        assert gws["status"] in ("fail", "warn"), (
            f"Expected fail/warn for missing Google Workspace, got {gws['status']}"
        )
        next_action_lower = gws["next_action"].lower()
        assert "connect" in next_action_lower or "oauth" in next_action_lower, (
            f"Expected next_action to reference Connect/OAuth, got: {gws['next_action']}"
        )

    # ------------------------------------------------------------------
    #   test 4: no vague "not configured" messages
    # ------------------------------------------------------------------

    def test_no_vague_not_configured_messages(self):
        """Failure/warn checks must not have vague 'not configured' without root cause."""
        patchers = [
            patch(
                "server_modules.sage_agent_computer_selection_service.get_selection",
                return_value=None,
            ),
            patch(
                "server_modules.gateway_registry_service.list_workspace_gateways",
                return_value={"items": []},
            ),
            patch(
                "server_modules.personal_channels_repository.get_telegram_state",
                return_value=None,
            ),
            patch(
                "server_modules.personal_channels_repository.get_whatsapp_state",
                return_value=None,
            ),
            patch(
                "server_modules.runtime_common.list_vault_connectors",
                return_value=[],
            ),
            patch(
                "server_modules.mcp_registry_service.list_workspace_mcp_servers",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                side_effect=RuntimeError("No cloud provider is configured for Sage."),
            ),
            patch(
                "server_modules.outbox_service.get_outbox_delivery_status",
                return_value={"undelivered_count": 0, "poisoned_count": 0},
            ),
            patch(
                "server_modules.policy_service.action_policy_from_app_permissions",
                return_value={"allowed": True},
            ),
        ]

        for p in patchers:
            p.start()

        try:
            result = SageDoctorService.check_all(workspace_id="ws-1", tenant_id="tenant-1")
        finally:
            for p in patchers:
                p.stop()

        # Collect all fail/warn/skip checks
        non_pass = [c for c in result["checks"] if c["status"] == "fail"]
        for check in non_pass:
            detail = check.get("detail", "").strip()
            next_action = check.get("next_action", "").strip()
            combined = (detail + " " + next_action).lower()
            # If it says "not configured", there must be a specific subject
            assert "not configured" not in combined or any(
                keyword in combined
                for keyword in [
                    "agent computer",
                    "telegram",
                    "whatsapp",
                    "slack",
                    "discord",
                    "google workspace",
                    "model provider",
                    "openai",
                    "anthropic",
                    "gemini",
                    "sage model provider",
                    "gateway",
                    "mcp",
                ]
            ), f"Check {check['id']} has vague 'not configured' without specifying what: {check}"

    # ------------------------------------------------------------------
    #   test 5: all checks run without raising
    # ------------------------------------------------------------------

    def test_all_checks_run_without_raising(self):
        """Minimal mocks still produce a complete result without exceptions."""
        patchers = [
            patch(
                "server_modules.sage_agent_computer_selection_service.get_selection",
                return_value=None,
            ),
            patch(
                "server_modules.gateway_registry_service.list_workspace_gateways",
                return_value={"items": []},
            ),
            patch(
                "server_modules.personal_channels_repository.get_telegram_state",
                return_value=None,
            ),
            patch(
                "server_modules.personal_channels_repository.get_whatsapp_state",
                return_value=None,
            ),
            patch(
                "server_modules.runtime_common.list_vault_connectors",
                return_value=[],
            ),
            patch(
                "server_modules.mcp_registry_service.list_workspace_mcp_servers",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                side_effect=RuntimeError("No cloud provider is configured for Sage."),
            ),
            patch(
                "server_modules.outbox_service.get_outbox_delivery_status",
                return_value={"undelivered_count": 0, "poisoned_count": 0},
            ),
            patch(
                "server_modules.policy_service.action_policy_from_app_permissions",
                return_value={"allowed": True},
            ),
        ]

        for p in patchers:
            p.start()

        try:
            result = SageDoctorService.check_all(workspace_id="ws-1", tenant_id="tenant-1")
        finally:
            for p in patchers:
                p.stop()

        assert isinstance(result, dict)
        assert "checks" in result
        assert "summary" in result
        assert len(result["checks"]) == len(CHECK_SPECS)

        summary = result["summary"]
        assert summary["total"] == len(CHECK_SPECS)
        assert "passed" in summary
        assert "failed" in summary
        assert "warned" in summary
        assert "skipped" in summary
        assert "healthy" in summary

    def test_agent_computer_selection_uses_user_id_not_tenant_id(self):
        with patch(
            "server_modules.sage_agent_computer_selection_service.get_selection",
            return_value={"selected_gateway_id": "gw-user"},
        ) as get_selection:
            result = SageDoctorService._check_agent_computer_selection(
                "ws-1",
                "tenant-1",
                "user-1",
            )

        assert result["status"] == "pass"
        get_selection.assert_called_once_with(workspace_id="ws-1", user_id="user-1")


class TestSageDoctorRoute:
    """Route-level auth and scope checks for Sage Doctor."""

    @staticmethod
    def _app() -> FastAPI:
        app = FastAPI()
        app.include_router(routes_doctor.router)
        return app

    def test_route_requires_authenticated_user(self):
        app = self._app()

        def _unauthorized():
            raise HTTPException(status_code=401, detail="Unauthorized")

        app.dependency_overrides[routes_doctor.get_current_user] = _unauthorized
        client = TestClient(app)

        response = client.get("/api/sage/doctor/check?workspace_id=ws-1")

        assert response.status_code == 401
        app.dependency_overrides.clear()

    def test_route_derives_tenant_and_user_from_auth_context(self):
        app = self._app()
        current_user = {"user_id": "user-1", "workspace_ids": ["ws-1"]}
        app.dependency_overrides[routes_doctor.get_current_user] = lambda: current_user

        with (
            patch(
                "server_modules.routes_doctor.auth_module.enforce_workspace_access",
                return_value="ws-1",
            ) as enforce_access,
            patch(
                "server_modules.routes_doctor.auth_module.workspace_tenant_id",
                return_value="tenant-1",
            ) as workspace_tenant,
            patch(
                "server_modules.routes_doctor.SageDoctorService.check_all",
                return_value={"checks": [], "summary": {"total": 0}},
            ) as check_all,
        ):
            client = TestClient(app)
            response = client.get("/api/sage/doctor/check?workspace_id=ws-1&tenant_id=forged")

        assert response.status_code == 200
        enforce_access.assert_called_once_with(current_user, "ws-1", minimum_role="viewer")
        workspace_tenant.assert_called_once_with(current_user, "ws-1")
        check_all.assert_called_once_with(
            workspace_id="ws-1",
            tenant_id="tenant-1",
            user_id="user-1",
        )
        app.dependency_overrides.clear()

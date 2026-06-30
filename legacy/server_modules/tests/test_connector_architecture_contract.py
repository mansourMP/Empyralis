import types
import unittest
import importlib
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import agent_channel_router, connectors_actions, file_bridge_service, routes_connectors
from server_modules.schemas import ConnectorDocumentCreateRequest


class ConnectorArchitectureContractTests(unittest.TestCase):
    def setUp(self) -> None:
        global agent_channel_router
        global connectors_actions
        global file_bridge_service
        global routes_connectors

        agent_channel_router = importlib.import_module("server_modules.agent_channel_router")
        connectors_actions = importlib.import_module("server_modules.connectors_actions")
        file_bridge_service = importlib.import_module("server_modules.file_bridge_service")
        routes_connectors = importlib.import_module("server_modules.routes_connectors")

    def test_shell_classes_share_one_captain_identity_but_not_one_control_depth(self) -> None:
        mobile_shell = agent_channel_router.full_shell_contract("mobile")
        telegram_shell = agent_channel_router.channel_shell_contract("telegram")

        self.assertEqual(mobile_shell["surface_class"], agent_channel_router.FULL_SHELL_CLASS)
        self.assertEqual(telegram_shell["surface_class"], agent_channel_router.CHANNEL_SHELL_CLASS)
        self.assertEqual(mobile_shell["control_depth"], "full")
        self.assertEqual(telegram_shell["control_depth"], "lightweight")
        self.assertTrue(mobile_shell["shares_captain_identity"])
        self.assertTrue(telegram_shell["shares_captain_identity"])
        self.assertTrue(mobile_shell["uses_shared_run_engine"])
        self.assertTrue(telegram_shell["uses_shared_run_engine"])
        self.assertIn("application_navigation", mobile_shell["allowed_capabilities"])
        self.assertIn("deep_admin_surface", telegram_shell["forbidden_capabilities"])

    def test_connector_contract_maps_api_browser_and_media_classes(self) -> None:
        google = connectors_actions.connector_contract("google_workspace")
        self.assertEqual(google["connector_class"], connectors_actions.CONNECTOR_CLASS_API)
        self.assertEqual(google["preferred_execution"], connectors_actions.CONNECTOR_CLASS_API)
        self.assertIn(connectors_actions.CONNECTOR_CLASS_BROWSER, google["fallback_connectors"])
        self.assertEqual(google["surface_role"], connectors_actions.CONNECTOR_SURFACE_DEEP_APP)

        browser = connectors_actions.connector_contract("browser_automation")
        self.assertEqual(browser["connector_class"], connectors_actions.CONNECTOR_CLASS_BROWSER)
        self.assertEqual(browser["preferred_execution"], connectors_actions.CONNECTOR_CLASS_BROWSER)

        media = connectors_actions.connector_contract("openai_image")
        self.assertEqual(media["connector_class"], connectors_actions.CONNECTOR_CLASS_MEDIA)
        self.assertEqual(media["preferred_execution"], connectors_actions.CONNECTOR_CLASS_MEDIA)

    def test_channel_shell_contract_is_separate_from_deep_connector_control(self) -> None:
        telegram_shell = agent_channel_router.channel_shell_contract("telegram")
        self.assertEqual(telegram_shell["surface_class"], "channel_shell")
        self.assertFalse(telegram_shell["deep_connector_control_surface"])
        self.assertIn("connector_management", telegram_shell["forbidden_capabilities"])

        telegram_connector = connectors_actions.connector_contract("telegram_bot")
        self.assertEqual(telegram_connector["surface_role"], connectors_actions.CONNECTOR_SURFACE_CHANNEL_SHELL)
        self.assertEqual(telegram_connector["connector_class"], connectors_actions.CONNECTOR_CLASS_API)

    def test_media_connector_artifacts_require_managed_export_bridge(self) -> None:
        media_policy = file_bridge_service.connector_artifact_bridge_contract("media_generation_connector")
        self.assertTrue(media_policy["managed_export_required"])
        self.assertEqual(media_policy["default_bridge_mode"], file_bridge_service.FILE_BRIDGE_MODE_EXPORT_BRIDGE)

        channel_policy = file_bridge_service.connector_artifact_bridge_contract(
            "api_connector",
            surface_role="channel_shell",
        )
        self.assertFalse(channel_policy["channel_shell_direct_export_allowed"])
        self.assertFalse(channel_policy["sync_folder_allowed"])


class ConnectorActionExecutionContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global agent_channel_router
        global connectors_actions
        global file_bridge_service
        global routes_connectors

        agent_channel_router = importlib.import_module("server_modules.agent_channel_router")
        connectors_actions = importlib.import_module("server_modules.connectors_actions")
        file_bridge_service = importlib.import_module("server_modules.file_bridge_service")
        routes_connectors = importlib.import_module("server_modules.routes_connectors")

    async def test_google_drive_browse_rejects_direct_execution_bypass(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await connectors_actions.browse_google_connector_drive("cred-google", workspace_id="workspace-1")

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("Direct connector execution is not allowed", raised.exception.detail)

    async def test_google_drive_browse_allows_human_admin_route_and_projects_secret_scope(self) -> None:
        with (
            patch(
                "server_modules.connectors_actions.secrets_broker.resolve_connector_secret",
                return_value={"_provider": "google_workspace", "access_token": "token-1"},
            ) as secret_mock,
            patch(
                "server_modules.connectors_actions.google_workspace_list_drive_children",
                return_value={"path": "gdrive:/", "items": [{"id": "file-1"}]},
            ),
        ):
            result = await connectors_actions.browse_google_connector_drive(
                "cred-google",
                workspace_id="workspace-1",
                execution_context={
                    "execution_path": connectors_actions.EXECUTION_PATH_HUMAN_ADMIN_ROUTE,
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "actor_type": "user",
                    "actor_id": "user-1",
                    "purpose": "human_admin_connector_browse_drive",
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(secret_mock.call_args.kwargs["connector_id"], "google_workspace")
        self.assertEqual(secret_mock.call_args.kwargs["connector_class"], "api_connector")
        self.assertEqual(secret_mock.call_args.kwargs["allowed_fields"], ["access_token"])

    async def test_google_doc_create_brokered_runtime_uses_tool_broker_contract(self) -> None:
        with (
            patch(
                "server_modules.connectors_actions.tool_broker.authorize_connector_action",
                return_value={"grant_id": "grant-1"},
            ) as authorize_mock,
            patch(
                "server_modules.connectors_actions.secrets_broker.resolve_connector_secret",
                return_value={"_provider": "google_workspace", "access_token": "token-1"},
            ),
            patch(
                "server_modules.connectors_actions.google_workspace_create_document",
                return_value={"documentId": "doc-1"},
            ),
        ):
            result = await connectors_actions.create_google_connector_document(
                "cred-google",
                body=ConnectorDocumentCreateRequest(title="Q2 Summary"),
                workspace_id="workspace-1",
                execution_context={
                    "execution_path": connectors_actions.EXECUTION_PATH_BROKERED_RUNTIME,
                    "capability_token": "grant-token",
                    "manifest_id": "manifest-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "runtime_mode": "hosted_secure",
                    "run_id": "run-1",
                    "actor_type": "runtime",
                    "actor_id": "run-actor",
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(authorize_mock.call_args.kwargs["connector_scope"], "google_workspace")
        self.assertEqual(authorize_mock.call_args.kwargs["connector_class"], "api_connector")
        self.assertEqual(authorize_mock.call_args.kwargs["action_class"], "write")
        self.assertTrue(authorize_mock.call_args.kwargs["approval_required"])

    async def test_google_drive_route_passes_human_admin_execution_context(self) -> None:
        request = types.SimpleNamespace(query_params={"workspace_id": "workspace-1", "path": "/team"})
        current_user = {"user_id": "user-1", "tenant_id": "tenant-1"}
        with (
            patch("server_modules.routes_connectors.enforce_workspace_access", return_value="workspace-1"),
            patch(
                "server_modules.routes_connectors.actions.browse_google_connector_drive",
                new=AsyncMock(return_value={"ok": True}),
            ) as browse_mock,
        ):
            result = await routes_connectors.browse_google_connector_drive(
                request,
                connector_id="cred-google",
                current_user=current_user,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            browse_mock.await_args.kwargs["execution_context"]["execution_path"],
            connectors_actions.EXECUTION_PATH_HUMAN_ADMIN_ROUTE,
        )
        self.assertEqual(browse_mock.await_args.kwargs["execution_context"]["actor_id"], "user-1")

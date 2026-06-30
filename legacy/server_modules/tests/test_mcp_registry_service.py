import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import mcp_registry_service


class McpRegistryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "mcp_servers.json"
        self.registry_patcher = patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", self.registry_path)
        self.registry_patcher.start()

    def tearDown(self) -> None:
        self.registry_patcher.stop()
        self.temp_dir.cleanup()
        super().tearDown()

    def test_upsert_can_discover_tools_and_expose_virtual_skill_entries(self) -> None:
        with patch.object(
            mcp_registry_service,
            "_list_tools_streamable_http_async",
            new=AsyncMock(
                return_value=[
                    {
                        "name": "lookup_stock",
                        "label": "Lookup Stock",
                        "description": "Read inventory data.",
                        "action_class": "read",
                        "connector_scopes": ["inventory"],
                        "trigger_terms": ["lookup stock"],
                    }
                ]
            ),
        ):
            record = mcp_registry_service.upsert_workspace_mcp_server(
                workspace_id="workspace-1",
                server_id="inventory-feed",
                label="Inventory Feed",
                transport="streamable_http",
                endpoint="https://example.com/mcp",
                discover_tools=True,
            )

        self.assertEqual(record["id"], "inventory-feed")
        self.assertEqual(record["tools"][0]["name"], "lookup_stock")
        self.assertFalse(record["tools"][0]["approved"])
        self.assertEqual(record["tools"][0]["risk_level"], "low")
        self.assertEqual(record["tools"][0]["cost_class"], "standard")
        self.assertEqual(record["tools"][0]["permission_manifest"]["action_class"], "read")
        self.assertFalse(record["tools"][0]["permission_manifest"]["requires_approval"])

        servers = mcp_registry_service.list_workspace_mcp_servers("workspace-1")
        self.assertEqual(servers[0]["tool_count"], 1)
        self.assertEqual(servers[0]["skill_ids"], ["mcp:inventory-feed:lookup_stock"])

        mcp_registry_service.approve_mcp_tool(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            tool_name="lookup_stock",
        )
        skills = mcp_registry_service.list_workspace_mcp_skill_entries("workspace-1")
        self.assertEqual(skills[0]["id"], "mcp:inventory-feed:lookup_stock")
        self.assertEqual(skills[0]["execution_adapter"], "mcp_tool")

    def test_discovered_tools_require_explicit_approval_before_use(self) -> None:
        with patch.object(
            mcp_registry_service,
            "_list_tools_streamable_http_async",
            new=AsyncMock(
                return_value=[
                    {
                        "name": "lookup_stock",
                        "label": "Lookup Stock",
                        "description": "Read inventory data.",
                        "action_class": "read",
                        "connector_scopes": ["inventory"],
                    },
                    {
                        "name": "write_stock",
                        "label": "Write Stock",
                        "description": "Update inventory data.",
                        "action_class": "write",
                        "connector_scopes": ["inventory"],
                    },
                ]
            ),
        ):
            record = mcp_registry_service.upsert_workspace_mcp_server(
                workspace_id="workspace-1",
                server_id="inventory-feed",
                label="Inventory Feed",
                transport="streamable_http",
                endpoint="https://example.com/mcp",
                discover_tools=True,
            )

        self.assertEqual([tool["approved"] for tool in record["tools"]], [False, False])
        self.assertEqual(mcp_registry_service.list_workspace_mcp_skill_entries("workspace-1"), [])

        approved = mcp_registry_service.approve_mcp_tool(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            tool_name="lookup_stock",
        )

        approval_state = {
            tool["name"]: tool["approved"]
            for tool in approved["tools"]
        }
        self.assertEqual(approval_state, {"lookup_stock": True, "write_stock": False})
        skills = mcp_registry_service.list_workspace_mcp_skill_entries("workspace-1")
        self.assertEqual([item["id"] for item in skills], ["mcp:inventory-feed:lookup_stock"])

    def test_manual_tools_default_to_unapproved(self) -> None:
        record = mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            label="Inventory Feed",
            transport="streamable_http",
            endpoint="https://example.com/mcp",
            tools=[
                {
                    "name": "lookup_stock",
                    "label": "Lookup Stock",
                    "description": "Read inventory data.",
                    "action_class": "read",
                    "connector_scopes": ["inventory"],
                }
            ],
            metadata={},
        )

        self.assertEqual(record["tools"][0]["approved"], False)
        self.assertEqual(mcp_registry_service.list_workspace_mcp_skill_entries("workspace-1"), [])

    def test_refresh_preserves_existing_approvals_and_hides_new_tools(self) -> None:
        mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            label="Inventory Feed",
            transport="streamable_http",
            endpoint="https://example.com/mcp",
            tools=[
                {
                    "name": "lookup_stock",
                    "label": "Lookup Stock",
                    "description": "Read inventory data.",
                    "action_class": "read",
                    "connector_scopes": ["inventory"],
                    "approved": True,
                }
            ],
            metadata={},
        )

        with patch.object(
            mcp_registry_service,
            "_list_tools_streamable_http_async",
            new=AsyncMock(
                return_value=[
                    {
                        "name": "lookup_stock",
                        "label": "Lookup Stock",
                        "description": "Read inventory data.",
                        "action_class": "read",
                        "connector_scopes": ["inventory"],
                    },
                    {
                        "name": "delete_stock",
                        "label": "Delete Stock",
                        "description": "Delete inventory data.",
                        "action_class": "write",
                        "connector_scopes": ["inventory"],
                    },
                ]
            ),
        ):
            refreshed = mcp_registry_service.refresh_workspace_mcp_server_tools(
                workspace_id="workspace-1",
                server_id="inventory-feed",
            )

        approval_state = {
            tool["name"]: tool["approved"]
            for tool in refreshed["tools"]
        }
        self.assertEqual(approval_state, {"lookup_stock": True, "delete_stock": False})
        skills = mcp_registry_service.list_workspace_mcp_skill_entries("workspace-1")
        self.assertEqual([item["id"] for item in skills], ["mcp:inventory-feed:lookup_stock"])

    def test_rejects_private_or_loopback_mcp_endpoints(self) -> None:
        for endpoint in ("https://127.0.0.1/mcp", "https://10.0.0.2/mcp", "https://localhost/mcp"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(Exception):
                    mcp_registry_service.upsert_workspace_mcp_server(
                        workspace_id="workspace-1",
                        server_id="unsafe",
                        label="Unsafe",
                        transport="streamable_http",
                        endpoint=endpoint,
                        metadata={},
                    )

    def test_invoke_workspace_mcp_skill_returns_tool_payload_and_arguments(self) -> None:
        mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            label="Inventory Feed",
            transport="streamable_http",
            endpoint="https://example.com/mcp",
            tools=[
                {
                    "name": "lookup_stock",
                    "label": "Lookup Stock",
                    "description": "Read inventory data.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"sku": {"type": "string"}},
                        "required": ["sku"],
                    },
                    "action_class": "read",
                    "connector_scopes": ["inventory"],
                    "approved": True,
                }
            ],
            metadata={},
        )

        with patch.object(
            mcp_registry_service,
            "_call_streamable_http_tool_async",
            new=AsyncMock(return_value={"reply": "SKU-1 is in stock.", "sku": "SKU-1"}),
        ), patch(
            "server_modules.mcp_registry_service.agent_action_metering_service.record_started_sync"
        ) as record_started, patch(
            "server_modules.mcp_registry_service.agent_action_metering_service.record_completed_sync"
        ) as record_completed:
            result = mcp_registry_service.invoke_workspace_mcp_skill(
                workspace_id="workspace-1",
                skill_id="mcp:inventory-feed:lookup_stock",
                goal='{"sku":"SKU-1"}',
                agent_label="Sage",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reply"], "SKU-1 is in stock.")
        self.assertEqual(result["mcp"]["arguments"], {"sku": "SKU-1"})
        self.assertEqual(result["mcp"]["server_id"], "inventory-feed")
        record_started.assert_called_once()
        record_completed.assert_called_once()
        self.assertEqual(record_completed.call_args.kwargs["action_domain"], "mcp")
        self.assertEqual(record_completed.call_args.kwargs["mcp_server_id"], "inventory-feed")

    def test_invoke_workspace_mcp_skill_rejects_unapproved_direct_skill_id(self) -> None:
        mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            label="Inventory Feed",
            transport="streamable_http",
            endpoint="https://example.com/mcp",
            tools=[
                {
                    "name": "lookup_stock",
                    "label": "Lookup Stock",
                    "description": "Read inventory data.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"sku": {"type": "string"}},
                        "required": ["sku"],
                    },
                    "action_class": "read",
                    "connector_scopes": ["inventory"],
                    "approved": False,
                }
            ],
            metadata={},
        )

        with patch.object(
            mcp_registry_service,
            "_call_streamable_http_tool_async",
            new=AsyncMock(return_value={"reply": "should not be called"}),
        ) as call_mock, patch(
            "server_modules.mcp_registry_service.agent_action_metering_service.record_blocked_sync"
        ) as record_blocked:
            with self.assertRaises(PermissionError):
                mcp_registry_service.invoke_workspace_mcp_skill(
                    workspace_id="workspace-1",
                    skill_id="mcp:inventory-feed:lookup_stock",
                    goal='{"sku":"SKU-1"}',
                    agent_label="Sage",
                )

        call_mock.assert_not_awaited()
        record_blocked.assert_called_once()
        self.assertEqual(record_blocked.call_args.kwargs["policy_decision"], "blocked")

    def test_upsert_exposes_trust_metadata_for_execute_tools(self) -> None:
        record = mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id="workspace-1",
            server_id="shell-tools",
            label="Shell Tools",
            transport="streamable_http",
            endpoint="https://example.com/mcp",
            tools=[
                {
                    "name": "run_shell",
                    "label": "Run Shell",
                    "description": "Run a local command.",
                    "action_class": "execute",
                    "connector_scopes": ["terminal"],
                    "allowed_runtime_modes": ["privileged_device"],
                    "cost_class": "metered",
                }
            ],
            metadata={},
        )

        tool = record["tools"][0]
        self.assertEqual(tool["risk_level"], "critical")
        self.assertTrue(tool["requires_approval"])
        self.assertEqual(tool["audit_event_type"], "mcp.tool.execute")
        self.assertEqual(tool["permission_manifest"]["cost_class"], "metered")
        self.assertEqual(tool["permission_manifest"]["allowed_runtime_modes"], ["privileged_device"])


if __name__ == "__main__":
    unittest.main()

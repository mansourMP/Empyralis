import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules.agent_manifest import AgentManifest, AgentManifestIdentity, AgentManifestSkillBinding, SAGE_GLOBAL_MANIFEST
from server_modules import egress_policy, mcp_registry_service, safe_mode_service, tool_broker, tools_http


def _manifest(*, skills: list[str] | None = None, connectors: list[str] | None = None) -> AgentManifest:
    return AgentManifest(
        manifest_id="manifest-parts-pro",
        identity=AgentManifestIdentity(
            name="Parts Pro",
            role="Inventory Specialist",
            archetype="support_specialist",
            summary="Help customers find available parts.",
        ),
        skills=[AgentManifestSkillBinding(id=skill_id, enabled=True) for skill_id in (skills or ["inventory-tool"])],
        connectors={
            "requested": list(connectors or []),
            "bound": list(connectors or []),
        },
    )


class ToolBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._env_patcher = patch.dict(os.environ, {"EMPYRALIS_TOOL_BROKER_SECRET": "empyralis-test-broker-secret"}, clear=False)
        self._env_patcher.start()

    def tearDown(self) -> None:
        self._env_patcher.stop()
        safe_mode_service.reset_state_for_tests()
        tool_broker.tool_broker_guard_service.reset_state_for_tests()

    def _register_mcp_server(self, registry_path: Path) -> None:
        with patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", registry_path):
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
                        "description": "Read live inventory data.",
                        "action_class": "read",
                        "connector_scopes": ["inventory"],
                        "trigger_terms": ["lookup stock"],
                        "approved": True,
                    }
                ],
                metadata={},
            )

    def test_verify_capability_token_rejects_expired_grant(self):
        grant = tool_broker.issue_capability_token(
            manifest=_manifest(),
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            runtime_mode="hosted_secure",
            runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
            ttl_seconds=1,
        )

        with self.assertRaises(tool_broker.ToolExecutionDeniedError) as raised:
            tool_broker.verify_capability_token(
                grant.token,
                expected_manifest_id="manifest-parts-pro",
                expected_tenant_id="tenant-1",
                expected_workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                now=grant.claims["expires_at"] + 1,
            )

        self.assertEqual(raised.exception.code, "token_expired")

    def test_issue_capability_token_carries_state_layer_policy_from_runtime_scope(self):
        grant = tool_broker.issue_capability_token(
            manifest=_manifest(),
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            runtime_mode="hosted_secure",
            runtime_scope={
                "driver": "sandbox_exec",
                "workspace_kind": "ephemeral",
                "state_layer_policy": {
                    "cross_install_private_memory_allowed": False,
                    "specialist_to_captain_private_access": False,
                    "artifacts_history": {"cross_install_exchange_mode": "artifacts_only"},
                },
            },
        )

        constraints = grant.claims["runtime_constraints"]
        self.assertFalse(constraints["state_layer_policy"]["cross_install_private_memory_allowed"])
        self.assertFalse(constraints["state_layer_policy"]["specialist_to_captain_private_access"])
        self.assertEqual(
            constraints["state_layer_policy"]["artifacts_history"]["cross_install_exchange_mode"],
            "artifacts_only",
        )

    def test_authorize_connector_action_rejects_missing_scope(self):
        grant = tool_broker.issue_capability_token(
            manifest=_manifest(skills=["inventory-tool"], connectors=["inventory"]),
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            runtime_mode="hosted_secure",
            runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
        )

        with self.assertRaises(tool_broker.ToolExecutionDeniedError) as raised:
            tool_broker.authorize_connector_action(
                capability_token=grant.token,
                manifest_id="manifest-parts-pro",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                connector_scope="email",
                action_class="read",
            )

        self.assertEqual(raised.exception.code, "connector_scope_not_granted")

    def test_execute_skill_rejects_unbound_skill(self):
        async def _run() -> None:
            grant = tool_broker.issue_capability_token(
                manifest=_manifest(skills=["email-access"]),
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
            )
            with self.assertRaises(tool_broker.ToolExecutionDeniedError) as raised:
                await tool_broker.execute_skill(
                    capability_token=grant.token,
                    manifest_id="manifest-parts-pro",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    runtime_mode="hosted_secure",
                    skill_id="inventory-tool",
                    goal="Do you have Tesla Model 3 wipers?",
                    agent_label="Parts Pro",
                    hard_context="",
                    operational_policy="",
                    seed_demo_if_empty=False,
                )
            self.assertEqual(raised.exception.code, "skill_not_granted")

        asyncio.run(_run())

    def test_execute_skill_allows_bound_inventory_skill(self):
        async def _run() -> None:
            grant = tool_broker.issue_capability_token(
                manifest=_manifest(skills=["inventory-tool"]),
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
            )
            with patch("server_modules.tool_broker.agent_action_metering_service.record_started") as record_started, patch(
                "server_modules.tool_broker.agent_action_metering_service.record_completed"
            ) as record_completed:
                result = await tool_broker.execute_skill(
                    capability_token=grant.token,
                    manifest_id="manifest-parts-pro",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    runtime_mode="hosted_secure",
                    skill_id="inventory-tool",
                    goal="Do you have Tesla Model 3 wipers?",
                    agent_label="Parts Pro",
                    hard_context="Use inventory only.",
                    operational_policy="Never invent stock.",
                    seed_demo_if_empty=True,
                )
            self.assertIn(result["status"], {"ok", "no_match"})
            self.assertIn("steps", result)
            record_started.assert_awaited_once()
            record_completed.assert_awaited_once()
            self.assertEqual(record_completed.await_args.kwargs["action_domain"], "skill")

        asyncio.run(_run())

    def test_master_manifest_can_execute_web_search_skill(self):
        async def _run() -> None:
            grant = tool_broker.issue_capability_token(
                manifest=SAGE_GLOBAL_MANIFEST,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
            )
            html = (
                '<a class="result__a" href="https://example.com">Example Domain</a>'
                '<a class="result__snippet">A canonical example result.</a>'
            )
            with patch(
                "server_modules.skill_registry.tools_http.http_request",
                new=AsyncMock(return_value={"status_code": 200, "body": html}),
            ):
                result = await tool_broker.execute_skill(
                    capability_token=grant.token,
                    manifest_id=SAGE_GLOBAL_MANIFEST.manifest_id,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    runtime_mode="hosted_secure",
                    skill_id="web-search",
                    goal="Research example domain",
                    agent_label="Sage",
                    hard_context="",
                    operational_policy="",
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["results"][0]["url"], "https://example.com")

        asyncio.run(_run())

    def test_master_manifest_can_execute_browser_skill(self):
        class _FakeBrowser:
            async def navigate(self, url: str):
                return {"url": url, "title": "Example Domain", "status_code": 200}

            async def observe(self):
                return {
                    "url": "https://example.com",
                    "title": "Example Domain",
                    "text": "Example page body",
                    "interactive_elements": [],
                    "screenshot_path": "/tmp/example.png",
                }

        async def _run() -> None:
            grant = tool_broker.issue_capability_token(
                manifest=SAGE_GLOBAL_MANIFEST,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
            )
            with patch("server_modules.browser_engine.BrowserEngine", return_value=_FakeBrowser()):
                result = await tool_broker.execute_skill(
                    capability_token=grant.token,
                    manifest_id=SAGE_GLOBAL_MANIFEST.manifest_id,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    runtime_mode="hosted_secure",
                    skill_id="browser",
                    goal="Open https://example.com and inspect the page",
                    agent_label="Sage",
                    hard_context="",
                    operational_policy="",
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["observation"]["url"], "https://example.com")

        asyncio.run(_run())

    def test_master_manifest_can_execute_mcp_skill(self):
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                registry_path = Path(temp_dir) / "mcp_servers.json"
                with (
                    patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", registry_path),
                    patch(
                        "server_modules.mcp_registry_service._call_streamable_http_tool_async",
                        new=AsyncMock(return_value={"reply": "SKU-1 is in stock."}),
                    ),
                ):
                    self._register_mcp_server(registry_path)
                    grant = tool_broker.issue_capability_token(
                        manifest=SAGE_GLOBAL_MANIFEST,
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        runtime_mode="hosted_secure",
                        runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
                    )
                    result = await tool_broker.execute_skill(
                        capability_token=grant.token,
                        manifest_id=SAGE_GLOBAL_MANIFEST.manifest_id,
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        runtime_mode="hosted_secure",
                        skill_id="mcp:inventory-feed:lookup_stock",
                        goal='{"sku":"SKU-1"}',
                        agent_label="Sage",
                        hard_context="",
                        operational_policy="",
                    )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["mcp"]["server_id"], "inventory-feed")
            self.assertEqual(result["mcp"]["arguments"], {"sku": "SKU-1"})

        asyncio.run(_run())

    def test_specialist_manifest_can_execute_bound_mcp_skill(self):
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                registry_path = Path(temp_dir) / "mcp_servers.json"
                with (
                    patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", registry_path),
                    patch(
                        "server_modules.mcp_registry_service._call_streamable_http_tool_async",
                        new=AsyncMock(return_value={"reply": "Brake pad set is available."}),
                    ),
                ):
                    self._register_mcp_server(registry_path)
                    manifest = _manifest(skills=["mcp:inventory-feed:lookup_stock"])
                    grant = tool_broker.issue_capability_token(
                        manifest=manifest,
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        runtime_mode="hosted_secure",
                        runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
                    )
                    result = await tool_broker.execute_skill(
                        capability_token=grant.token,
                        manifest_id="manifest-parts-pro",
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        runtime_mode="hosted_secure",
                        skill_id="mcp:inventory-feed:lookup_stock",
                        goal="brake pad set",
                        agent_label="Parts Pro",
                        hard_context="Use MCP live data only.",
                        operational_policy="Never invent stock.",
                    )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["reply"], "Brake pad set is available.")
            self.assertEqual(result["mcp"]["tool_name"], "lookup_stock")

        asyncio.run(_run())

    def test_authorize_connector_action_rejects_when_connector_is_disabled(self):
        safe_mode_service.set_kill_switch(
            scope="connector",
            enabled=True,
            workspace_id="workspace-1",
            connector_id="inventory",
            reason="incident",
        )
        grant = tool_broker.issue_capability_token(
            manifest=_manifest(skills=["inventory-tool"], connectors=["inventory"]),
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            runtime_mode="hosted_secure",
            runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
        )

        with self.assertRaises(tool_broker.ToolExecutionDeniedError) as raised:
            tool_broker.authorize_connector_action(
                capability_token=grant.token,
                manifest_id="manifest-parts-pro",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                connector_scope="inventory",
                action_class="read",
            )

        self.assertEqual(raised.exception.code, "connector_disabled")

    def test_authorize_connector_action_rejects_when_connector_is_paused(self):
        safe_mode_service.set_incident_control(
            scope="connector",
            mode="pause",
            workspace_id="workspace-1",
            connector_id="inventory",
            reason="connector pause",
        )
        grant = tool_broker.issue_capability_token(
            manifest=_manifest(skills=["inventory-tool"], connectors=["inventory"]),
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            runtime_mode="hosted_secure",
            runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
        )

        with self.assertRaises(tool_broker.ToolExecutionDeniedError) as raised:
            tool_broker.authorize_connector_action(
                capability_token=grant.token,
                manifest_id="manifest-parts-pro",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                connector_scope="inventory",
                action_class="read",
            )

        self.assertEqual(raised.exception.code, "connector_paused")

    def test_master_manifest_does_not_auto_bypass_approval_when_policy_is_guarded(self):
        guarded_master = AgentManifest(
            manifest_id="manifest-sage-guarded",
            identity=AgentManifestIdentity(
                name="Sage",
                role="Master Agent",
                archetype="master_os",
                summary="Coordinate the system.",
            ),
            policy={"approval_mode": "guarded"},
        )
        grant = tool_broker.issue_capability_token(
            manifest=guarded_master,
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            runtime_mode="hosted_secure",
            runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
        )

        with self.assertRaises(tool_broker.ToolExecutionDeniedError) as raised:
            tool_broker.authorize_connector_action(
                capability_token=grant.token,
                manifest_id="manifest-sage-guarded",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                connector_scope="email",
                action_class="write",
            )

        self.assertEqual(raised.exception.code, "approval_required")

    def test_authorize_connector_action_rejects_execute_for_api_connector_class(self):
        master_manifest = AgentManifest(
            manifest_id="manifest-sage-system",
            identity=AgentManifestIdentity(
                name="Sage",
                role="Master Agent",
                archetype="master_os",
                summary="Coordinate the system.",
            ),
            connectors={"requested": ["google_workspace"], "bound": ["google_workspace"]},
            policy={"approval_mode": "system"},
        )
        grant = tool_broker.issue_capability_token(
            manifest=master_manifest,
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            runtime_mode="hosted_secure",
            runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
        )

        with self.assertRaises(tool_broker.ToolExecutionDeniedError) as raised:
            tool_broker.authorize_connector_action(
                capability_token=grant.token,
                manifest_id="manifest-sage-system",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                connector_scope="google_workspace",
                connector_class="api_connector",
                action_class="execute",
            )

        self.assertEqual(raised.exception.code, "connector_action_not_supported_for_class")

    def test_execute_skill_returns_egress_denied_when_outbound_host_is_not_allowed(self):
        async def _network_skill(**_kwargs):
            return await tools_http.http_request(method="GET", url="https://example.com/data")

        async def _run() -> None:
            grant = tool_broker.issue_capability_token(
                manifest=_manifest(skills=["web-search"], connectors=["web"]),
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
            )
            with patch("server_modules.skill_registry.execute_skill", new=_network_skill):
                with self.assertRaises(tool_broker.ToolExecutionDeniedError) as raised:
                    await tool_broker.execute_skill(
                        capability_token=grant.token,
                        manifest_id="manifest-parts-pro",
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        runtime_mode="hosted_secure",
                        skill_id="web-search",
                        goal="Research this",
                        agent_label="Parts Pro",
                        hard_context="",
                        operational_policy="",
                        seed_demo_if_empty=False,
                    )
            self.assertEqual(raised.exception.code, "egress_denied")

        asyncio.run(_run())

    def test_execute_skill_inherits_outer_allowlisted_host_policy(self):
        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}

            async def aread(self) -> bytes:
                return b'{"ok": true}'

        class _FakeAsyncClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def request(self, **_kwargs):
                return _FakeResponse()

        async def _network_skill(**_kwargs):
            return await tools_http.http_request(method="GET", url="https://example.com/data")

        async def _run() -> None:
            grant = tool_broker.issue_capability_token(
                manifest=_manifest(skills=["web-search"], connectors=["web"]),
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
            )
            outer_policy = egress_policy.build_egress_policy(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_mode="hosted_secure",
                runtime_scope={
                    "network_policy": {
                        "allow_outbound": True,
                        "allowed_hosts": ["example.com"],
                        "allowed_providers": [],
                        "allow_private_hosts": False,
                    }
                },
                allowed_action_classes=["read"],
            )
            with patch("server_modules.skill_registry.execute_skill", new=_network_skill):
                with patch("server_modules.tools_http.httpx.AsyncClient", _FakeAsyncClient):
                    with egress_policy.activate_egress_policy(outer_policy):
                        result = await tool_broker.execute_skill(
                            capability_token=grant.token,
                            manifest_id="manifest-parts-pro",
                            tenant_id="tenant-1",
                            workspace_id="workspace-1",
                            runtime_mode="hosted_secure",
                            skill_id="web-search",
                            goal="Research this",
                            agent_label="Parts Pro",
                            hard_context="",
                            operational_policy="",
                            seed_demo_if_empty=False,
                        )
            self.assertEqual(result["status_code"], 200)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()

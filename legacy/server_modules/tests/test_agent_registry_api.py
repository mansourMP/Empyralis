import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from server_modules import agent_registry_api
from server_modules.agent_manifest import AgentManifest
from server_modules.api_contract import request_body_to_turn_request


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def patch(self, path, **kwargs):
        return self._register("PATCH", path, **kwargs)

    def put(self, path, **kwargs):
        return self._register("PUT", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register("DELETE", path, **kwargs)


class AgentRegistryApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tenant_id_patcher = patch(
            "server_modules.agent_registry_api._tenant_id_for_request",
            return_value="tenant-1",
        )
        self._tenant_id_patcher.start()

    def tearDown(self) -> None:
        self._tenant_id_patcher.stop()
        super().tearDown()

    def test_register_agent_registry_routes_adds_install_run_endpoint(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            self.assertIn(("GET", "/agent-registry/definitions"), app.routes)
            self.assertIn(("GET", "/agent-registry/definitions/{definition_id}"), app.routes)
            self.assertIn(("GET", "/agent-registry/runtime-profiles"), app.routes)
            self.assertIn(("POST", "/agent-registry/self-hosted-nodes"), app.routes)
            self.assertIn(("POST", "/agent-registry/self-hosted-nodes/{runtime_profile_id}/approve"), app.routes)
            self.assertIn(("GET", "/agent-registry/mcp/servers"), app.routes)
            self.assertIn(("GET", "/agent-registry/mcp/servers/{server_id}"), app.routes)
            self.assertIn(("PUT", "/agent-registry/mcp/servers/{server_id}"), app.routes)
            self.assertIn(("POST", "/agent-registry/mcp/servers/{server_id}/refresh"), app.routes)
            self.assertIn(("POST", "/agent-registry/mcp/servers/{server_id}/tools/approve"), app.routes)
            self.assertIn(("DELETE", "/agent-registry/mcp/servers/{server_id}"), app.routes)
            self.assertIn(("GET", "/agent-registry/chat-context"), app.routes)
            self.assertIn(("GET", "/agent-registry/runtime-targets"), app.routes)
            self.assertIn(("GET", "/agent-registry/shared-board"), app.routes)
            self.assertIn(("GET", "/agent-registry/shared-board/{entry_id}/history"), app.routes)
            self.assertIn(("POST", "/agent-registry/shared-board"), app.routes)
            self.assertIn(("GET", "/agent-registry/installs"), app.routes)
            self.assertIn(("POST", "/agent-registry/installs"), app.routes)
            self.assertIn(("GET", "/agent-registry/installs/{install_id}"), app.routes)
            self.assertIn(("POST", "/agent-registry/installs/{install_id}/artifacts/export"), app.routes)
            self.assertIn(("PATCH", "/agent-registry/installs/{install_id}"), app.routes)
            self.assertIn(("POST", "/agent-registry/specialists"), app.routes)
            self.assertIn(("GET", "/agent-registry/specialists/{install_id}"), app.routes)
            self.assertIn(("PATCH", "/agent-registry/specialists/{install_id}"), app.routes)
            self.assertIn(("PUT", "/agent-registry/specialists/{install_id}/bible"), app.routes)
            self.assertIn(("PUT", "/agent-registry/specialists/{install_id}/skills"), app.routes)
            self.assertIn(("PUT", "/agent-registry/specialists/{install_id}/connectors"), app.routes)
            self.assertIn(("PUT", "/agent-registry/specialists/{install_id}/channels"), app.routes)
            self.assertIn(("PUT", "/agent-registry/specialists/{install_id}/runtime"), app.routes)
            self.assertIn(("POST", "/agent-registry/channels/inbound"), app.routes)
            self.assertIn(("POST", "/agent-registry/personal-context/events"), app.routes)
            self.assertIn(("GET", "/agent-registry/personal-context/events"), app.routes)
            self.assertIn(("POST", "/agent-registry/personal-context/events/seen"), app.routes)
            self.assertIn(("POST", "/agent-registry/scheduler/self-wakeups"), app.routes)
            self.assertIn(("GET", "/agent-registry/scheduler/status"), app.routes)
            self.assertIn(("POST", "/agent-registry/security/kill-switches"), app.routes)
            self.assertIn(("POST", "/agent-registry/reliability/incident-controls"), app.routes)
            self.assertIn(("POST", "/agent-registry/security/connectors/revoke"), app.routes)
            self.assertIn(("POST", "/agent-registry/security/connectors/rotate"), app.routes)
            self.assertIn(("POST", "/agents/customer-preview/inventory"), app.routes)
            self.assertIn(("POST", "/agents/customer-preview/respond"), app.routes)
            self.assertIn(("POST", "/agents/{install_id}/run"), app.routes)
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_preview_customer_turn_enforces_workspace_and_uses_universal_operator(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agents/customer-preview/respond")]

            manifest = AgentManifest(
                manifest_id="manifest-parts-pro",
                identity={
                    "name": "Parts Pro",
                    "role": "Inventory Specialist",
                    "archetype": "support_specialist",
                    "summary": "Help customers find available parts.",
                },
                skills=[{"id": "inventory-tool", "enabled": True}],
            )

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.universal_operator.execute_customer_turn",
                    new=AsyncMock(return_value={
                        "status": "ok",
                        "reply": "I found 3 Tesla wipers in stock.",
                        "artifact": {"label": "Inventory tool result"},
                        "needed_skill_id": "inventory-tool",
                    }),
                ) as execute_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.AgentCustomerPreviewRequest(
                            workspace_id="workspace-1",
                            customer_message="Do you have Tesla wipers?",
                            manifest=manifest,
                            seed_demo_if_empty=True,
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["workspace_id"], "workspace-1")
            self.assertEqual(result["tenant_id"], "tenant-1")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["needed_skill_id"], "inventory-tool")
            self.assertEqual(execute_mock.await_args.kwargs["goal"], "Do you have Tesla wipers?")
            self.assertEqual(execute_mock.await_args.kwargs["manifest"].identity.name, "Parts Pro")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_save_mcp_server_enforces_workspace_and_returns_registry_payload(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("PUT", "/agent-registry/mcp/servers/{server_id}")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1") as access_mock,
                patch(
                    "server_modules.agent_registry_api.mcp_registry_service.upsert_workspace_mcp_server_async",
                    new=AsyncMock(return_value={"id": "inventory-feed", "label": "Inventory Feed"}),
                ) as upsert_mock,
                patch(
                    "server_modules.agent_registry_api.mcp_registry_service.list_workspace_mcp_servers",
                    return_value=[
                        {
                            "id": "inventory-feed",
                            "label": "Inventory Feed",
                            "transport": "streamable_http",
                            "endpoint": "https://example.com/mcp",
                            "enabled": True,
                            "tool_count": 1,
                            "skill_ids": ["mcp:inventory-feed:lookup_stock"],
                        }
                    ],
                ),
            ):
                result = asyncio.run(
                    route(
                        "inventory-feed",
                        agent_registry_api.McpServerUpsertRequest(
                            workspace_id="workspace-1",
                            label="Inventory Feed",
                            endpoint="https://example.com/mcp",
                            tools=[{"name": "lookup_stock", "approved": True}],
                            discover_tools=True,
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertTrue(result["advanced_only"])
            self.assertEqual(result["id"], "inventory-feed")
            self.assertEqual(result["skill_ids"], ["mcp:inventory-feed:lookup_stock"])
            self.assertEqual(access_mock.call_args.kwargs["minimum_role"], "owner")
            self.assertEqual(upsert_mock.call_args.kwargs["workspace_id"], "workspace-1")
            self.assertTrue(upsert_mock.call_args.kwargs["discover_tools"])
            self.assertNotIn("approved", upsert_mock.call_args.kwargs["tools"][0])
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_approve_mcp_server_tool_requires_owner_role(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/mcp/servers/{server_id}/tools/approve")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1") as access_mock,
                patch(
                    "server_modules.agent_registry_api.mcp_registry_service.approve_mcp_tool",
                    return_value={"id": "inventory-feed", "tools": [{"name": "lookup_stock", "approved": True}]},
                ) as approve_mock,
                patch(
                    "server_modules.agent_registry_api.mcp_registry_service.list_workspace_mcp_servers",
                    return_value=[
                        {
                            "id": "inventory-feed",
                            "label": "Inventory Feed",
                            "transport": "streamable_http",
                            "endpoint": "https://example.com/mcp",
                            "enabled": True,
                            "tool_count": 1,
                            "skill_ids": ["mcp:inventory-feed:lookup_stock"],
                        }
                    ],
                ),
            ):
                result = asyncio.run(
                    route(
                        "inventory-feed",
                        agent_registry_api.McpToolApproveRequest(
                            workspace_id="workspace-1",
                            tool_name="lookup_stock",
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["id"], "inventory-feed")
            self.assertEqual(access_mock.call_args.kwargs["minimum_role"], "owner")
            approve_mock.assert_called_once()
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_create_self_hosted_node_requires_owner_and_returns_enrollment_token(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/self-hosted-nodes")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.agent_registry_repository.create_self_hosted_runtime_profile_enrollment_intent",
                    new=AsyncMock(
                        return_value={
                            "runtime_profile_id": "rprof_1",
                            "runtime_node_id": "node_1",
                            "enrollment_token": "tok-1",
                            "enrollment_expires_at": "2026-05-11T00:10:00Z",
                            "token_ttl_seconds": 600,
                            "runtime_profile": {"id": "rprof_1"},
                        }
                    ),
                ) as create_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.SelfHostedNodeCreateRequest(
                            workspace_id="workspace-1",
                            label="Mansur Mac mini",
                            node_kind="mac_mini",
                            capabilities=["shell.execute"],
                            max_concurrent_sessions=2,
                            root_policy={"filesystem": "workspace_scoped"},
                            token_ttl_seconds=600,
                        ),
                        current_user={"user_id": "owner-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["runtime_profile_id"], "rprof_1")
            self.assertEqual(result["enrollment_token"], "tok-1")
            self.assertEqual(create_mock.await_args.kwargs["workspace_id"], "workspace-1")
            self.assertEqual(create_mock.await_args.kwargs["owner_user_id"], "owner-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_preview_inventory_skill_enforces_workspace_and_returns_live_payload(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agents/customer-preview/inventory")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.tool_broker.issue_capability_token",
                    return_value=agent_registry_api.tool_broker.CapabilityGrant(
                        token="grant-token",
                        claims={"allowed_skills": ["inventory-tool"]},
                    ),
                ),
                patch(
                    "server_modules.agent_registry_api.tool_broker.execute_skill",
                    new=AsyncMock(return_value={
                        "status": "ok",
                        "reply": "I found 3 Tesla Model 3 Aero Wiper Kit in stock. Price: $24.99.",
                        "artifact": {"label": "Inventory tool result"},
                    }),
                ) as execute_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.AgentCustomerInventoryPreviewRequest(
                            workspace_id="workspace-1",
                            agent_label="Parts Pro",
                            customer_message="Do you have 2022 Tesla Model 3 wipers?",
                            hard_context="Use only inventory rows.",
                            operational_policy="Never invent stock.",
                            seed_demo_if_empty=True,
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["workspace_id"], "workspace-1")
            self.assertEqual(result["tenant_id"], "tenant-1")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["artifact"]["label"], "Inventory tool result")
            self.assertEqual(execute_mock.await_args.kwargs["goal"], "Do you have 2022 Tesla Model 3 wipers?")
            self.assertEqual(execute_mock.await_args.kwargs["agent_label"], "Parts Pro")
            self.assertEqual(execute_mock.await_args.kwargs["skill_id"], "inventory-tool")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_route_inbound_channel_enforces_workspace_and_returns_owner_payload(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/channels/inbound")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_channel_router.route_inbound_channel_message",
                    new=AsyncMock(return_value={
                        "workspace_id": "workspace-1",
                        "tenant_id": "tenant-1",
                        "channel_key": "telegram",
                        "endpoint_key": "@partspro_bot",
                        "owner": {"install_id": "install-1", "label": "Parts Pro", "type": "specialist"},
                        "reply": "I found 3 Tesla wipers in stock.",
                        "status": "ok",
                        "audit": {"inbound_event_id": "evt-in", "outbound_event_id": "evt-out"},
                    }),
                ) as route_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.AgentChannelInboundRequest(
                            workspace_id="workspace-1",
                            channel_key="telegram",
                            endpoint_key="@partspro_bot",
                            customer_message="Do you have Model 3 wipers?",
                            actor_id="telegram-user-1",
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["owner"]["install_id"], "install-1")
            self.assertEqual(result["audit"]["inbound_event_id"], "evt-in")
            self.assertEqual(route_mock.await_args.kwargs["channel_key"], "telegram")
            self.assertEqual(route_mock.await_args.kwargs["endpoint_key"], "@partspro_bot")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_save_specialist_skills_rejects_unknown_skill_ids(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("PUT", "/agent-registry/specialists/{install_id}/skills")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.get_workspace_specialist",
                    new=AsyncMock(return_value={"install_id": "install-1", "specialist_mode": "owner_edit"}),
                ),
            ):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        route(
                            "install-1",
                            agent_registry_api.SpecialistSkillBindingsUpdateRequest(
                                workspace_id="workspace-1",
                                skill_ids=["not-a-real-skill"],
                            ),
                            current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("Unknown specialist skill binding", str(raised.exception.detail))
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_route_inbound_channel_returns_not_found_when_endpoint_has_no_owner(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/channels/inbound")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_channel_router.route_inbound_channel_message",
                    new=AsyncMock(side_effect=agent_registry_api.agent_channel_router.ChannelOwnerNotFoundError(
                        "No active channel owner is configured for this endpoint."
                    )),
                ),
            ):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(
                        route(
                            agent_registry_api.AgentChannelInboundRequest(
                                workspace_id="workspace-1",
                                channel_key="email",
                                endpoint_key="service@company.com",
                                customer_message="Can you help me?",
                            ),
                            current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(error.exception.detail, "No active channel owner is configured for this endpoint.")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_route_inbound_channel_returns_forbidden_when_security_control_blocks_ingress(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/channels/inbound")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_channel_router.route_inbound_channel_message",
                    new=AsyncMock(side_effect=agent_registry_api.agent_channel_router.ChannelSecurityDeniedError(
                        "This channel is temporarily disabled by a security control."
                    )),
                ),
            ):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(
                        route(
                            agent_registry_api.AgentChannelInboundRequest(
                                workspace_id="workspace-1",
                                channel_key="telegram",
                                endpoint_key="@partspro_bot",
                                customer_message="Can you help me?",
                            ),
                            current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(error.exception.status_code, 403)
            self.assertEqual(error.exception.detail, "This channel is temporarily disabled by a security control.")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_run_installed_agent_builds_canonical_turn_request(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agents/{install_id}/run")]
            compiled = {
                "install": {
                    "id": "install-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "agent_definition_id": "agent-1",
                    "agent_definition_version_id": "agentver-1",
                    "runtime_profile_id": "profile-1",
                    "runtime_mode": "local_secure",
                    "thread_id": "thread-1",
                    "runtime_profile": {
                        "id": "profile-1",
                        "label": "My Mac",
                        "runtime_class": "desktop_companion",
                        "machine_id": "machine-1",
                        "runtime_id": "runtime-1",
                    },
                    "agent_definition": {"name": "Desktop Operator", "slug": "desktop-operator"},
                },
                "workflow_id": "wf-compiled",
                "workflow_version_id": "wfver-compiled",
                "workflow_snapshot": {
                    "id": "wf-compiled",
                    "workflowVersionId": "wfver-compiled",
                    "definition": {"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
                },
                "run_metadata": {
                    "execution_target": "local_companion",
                    "execution_target_selected": "local_companion",
                },
            }

            with (
                patch("server_modules.agent_registry_api.template_compiler_service.ensure_install_compiled_artifact", new=AsyncMock(return_value=compiled)),
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api._run_execution_services", return_value={"run": "services"}),
                patch(
                    "server_modules.agent_registry_api.runtime_attachment_service.resolve_install_runtime_plan",
                    new=AsyncMock(
                        return_value={
                            "runtime_mode": "local_secure",
                            "deployment_mode": "hybrid",
                            "selected_attachment": {
                                "attachment_id": "local_companion:profile-1",
                                "attachment_kind": "local_companion",
                            },
                            "machine_target": "machine-1",
                            "execution_target_selected": "local_companion",
                        }
                    ),
                ),
                patch(
                    "server_modules.agent_registry_api.turn_ingress_service.start_turn",
                    new=AsyncMock(
                        side_effect=lambda **kwargs: agent_registry_api.turn_ingress_service.TurnIngressResult(
                            turn_request=request_body_to_turn_request(kwargs["payload"]),
                            result={
                                "status": "accepted",
                                "reply": "",
                                "run_id": "run-1",
                                "metadata": {"created_run": {"run_id": "run-1", "status": "queued"}},
                            },
                        )
                    ),
                ) as turn_mock,
            ):
                result = asyncio.run(
                    route(
                        "install-1",
                        agent_registry_api.AgentInstallRunRequest(message="Open Calculator"),
                        current_user={"user_id": "user-1", "email": "user@example.com", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result.status, "accepted")
            turn_payload = turn_mock.await_args.kwargs["payload"]
            self.assertEqual(turn_payload["context_hints"]["workflow_id"], "wf-compiled")
            self.assertEqual(turn_payload["context_hints"]["workflow_version_id"], "wfver-compiled")
            self.assertEqual(turn_payload["machine_target"], "machine-1")
            metadata = turn_payload["context_hints"]["metadata"]
            self.assertEqual(metadata["runtime_profile_id"], "profile-1")
            self.assertEqual(metadata["workflow_definition"]["version"], "empyralist.workflow.v2")
            self.assertEqual(turn_payload["thread_id"], "thread-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_create_install_compiles_before_return(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/installs")]
            install_record = {
                "id": "install-1",
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
                "label": "Executive Assistant",
            }
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.agent_registry_repository.get_agent_definition",
                    new=AsyncMock(return_value={"id": "agentdef-1", "name": "Executive Assistant", "agent_kind": "specialist"}),
                ),
                patch("server_modules.agent_registry_api.agent_registry_repository.create_workspace_agent_install", new=AsyncMock(return_value=install_record)) as create_mock,
                patch("server_modules.agent_registry_api.template_compiler_service.ensure_install_compiled_artifact", new=AsyncMock(return_value={"install": install_record})) as compile_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.AgentInstallUpsertRequest(
                            workspace_id="workspace-1",
                            agent_definition_id="agentdef-1",
                            label="Executive Assistant",
                        ),
                        current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["id"], "install-1")
            create_mock.assert_awaited_once()
            compile_mock.assert_awaited_once()
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_create_install_rejects_specialist_mode_for_captain_definition(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/installs")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.agent_registry_repository.get_agent_definition",
                    new=AsyncMock(return_value={"id": "agentdef-sage", "name": "Sage", "agent_kind": "master"}),
                ),
                patch(
                    "server_modules.agent_registry_api.agent_registry_repository.create_workspace_agent_install",
                    new=AsyncMock(),
                ) as create_mock,
            ):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(
                        route(
                            agent_registry_api.AgentInstallUpsertRequest(
                                workspace_id="workspace-1",
                                agent_definition_id="agentdef-sage",
                                label="Captain",
                                metadata={"specialist_mode": "owner_test"},
                            ),
                            current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("Captain installs do not support", str(ctx.exception.detail))
            create_mock.assert_not_called()
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_update_install_normalizes_captain_display_name_without_id_breakage(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("PATCH", "/agent-registry/installs/{install_id}")]
            existing_install = {
                "id": "install-sage",
                "label": "Sage",
                "metadata": {"system_agent": True},
                "agent_definition": {"name": "Sage", "agent_kind": "master"},
            }
            updated_install = {
                **existing_install,
                "label": "Mansur",
                "captain_identity": {
                    "install_id": "install-sage",
                    "stable_internal_id": "install-sage",
                    "role": "private_main_agent",
                    "display_name": "Mansur",
                    "editable_display_name": True,
                    "mode_switching_supported": False,
                },
            }
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.agent_registry_repository.get_workspace_agent_install_bundle",
                    new=AsyncMock(return_value=existing_install),
                ),
                patch(
                    "server_modules.agent_registry_api.agent_registry_repository.update_workspace_agent_install",
                    new=AsyncMock(return_value=updated_install),
                ) as update_mock,
                patch(
                    "server_modules.agent_registry_api.template_compiler_service.ensure_install_compiled_artifact",
                    new=AsyncMock(return_value={"install": updated_install}),
                ),
            ):
                result = asyncio.run(
                    route(
                        "install-sage",
                        agent_registry_api.AgentInstallUpsertRequest(
                            workspace_id="workspace-1",
                            label="Mansur",
                        ),
                        current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["id"], "install-sage")
            self.assertEqual(update_mock.await_args.kwargs["metadata"]["captain_profile"]["display_name"], "Mansur")
            self.assertEqual(update_mock.await_args.kwargs["metadata"]["captain_profile"]["stable_internal_id"], "install-sage")
            self.assertNotIn("specialist_mode", update_mock.await_args.kwargs["metadata"])
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_export_install_artifact_enforces_workspace_and_uses_file_bridge_service(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/installs/{install_id}/artifacts/export")]
            install_bundle = {
                "id": "install-1",
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
                "runtime_mode": "hosted_secure",
                "runtime_profile": {"id": "profile-cloud"},
            }
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_registry_repository.get_workspace_agent_install_bundle",
                    new=AsyncMock(return_value=install_bundle),
                ),
                patch(
                    "server_modules.agent_registry_api.file_bridge_service.export_artifact_for_install",
                    return_value={
                        "artifact_id": "artifact-1",
                        "artifact_uri": "artifact://artifact-1/report.txt",
                        "bridge_mode": "export_bridge",
                        "destination_path": "/tmp/exported/report.txt",
                        "byte_size": 5,
                        "policy": {"managed_root": "/tmp/exported", "approved_roots": [], "sync_folders": []},
                        "audit": {"event_id": "evt-1", "event_type": "artifact_exported"},
                    },
                ) as export_mock,
            ):
                result = asyncio.run(
                    route(
                        "install-1",
                        agent_registry_api.AgentArtifactExportRequest(
                            workspace_id="workspace-1",
                            artifact_reference="artifact://artifact-1/report.txt",
                            destination_path="/tmp/exported/report.txt",
                        ),
                        current_user={"user_id": "user-1", "email": "user@example.com", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["workspace_id"], "workspace-1")
            self.assertEqual(result["tenant_id"], "tenant-1")
            self.assertEqual(result["audit"]["event_id"], "evt-1")
            self.assertEqual(export_mock.call_args.kwargs["artifact_reference"], "artifact://artifact-1/report.txt")
            self.assertEqual(export_mock.call_args.kwargs["actor"]["user_id"], "user-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_master_chat_context_returns_sage_and_specialists(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("GET", "/agent-registry/chat-context")]
            master_install = {
                "id": "install-sage",
                "label": "Sage",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
            }
            specialists = [
                {"id": "install-research", "label": "Web Researcher", "enabled": True, "status": "active"},
                {"id": "install-desktop", "label": "Desktop Operator", "enabled": False, "status": "active"},
            ]
            thread_record = {"id": "thread-sage", "title": "Sage", "turns": []}
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.agent_registry_repository.get_workspace_master_agent_install", new=AsyncMock(return_value=master_install)),
                patch("server_modules.agent_registry_api.agent_registry_repository.list_workspace_agent_installs", new=AsyncMock(return_value=specialists)),
                patch("server_modules.agent_registry_api.agent_registry_repository.build_master_thread_id", return_value="thread-sage"),
                patch("server_modules.agent_registry_api.thread_service.ensure_master_thread", new=AsyncMock(return_value=thread_record)),
                patch("server_modules.agent_registry_api.thread_service.get_thread", new=AsyncMock(return_value=thread_record)),
                patch(
                    "server_modules.agent_registry_api.personal_context_engine.build_sage_context_payload",
                    new=AsyncMock(return_value={"recent_changes": [{"id": "evt-1"}], "summary": {"count": 1, "unseen_count": 1}}),
                ),
                patch(
                    "server_modules.agent_registry_api.unified_memory_service.build_sage_memory_payload",
                    new=AsyncMock(
                        return_value={
                            "layer_order": ["profile_memory", "episodic_memory"],
                            "summary": {"layer_count": 2},
                        }
                    ),
                ),
                patch(
                    "server_modules.agent_registry_api.runtime_attachment_service.list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "deployment_mode": "hybrid",
                            "attachments": [{"attachment_id": "managed_cloud:profile-cloud"}],
                        }
                    ),
                ),
                patch(
                    "server_modules.agent_registry_api.runtime_attachment_service.build_workspace_runtime_targets",
                    return_value={
                        "deployment_mode": "hybrid",
                        "default_target_id": "cloud_default",
                        "targets": [{"target_id": "cloud_default", "available": True}],
                        "routing_contract": {"mobile_entry_mode": "platform_first"},
                    },
                ),
                patch(
                    "server_modules.agent_registry_api.bounded_scheduler_service.scheduler_status_snapshot",
                    new=AsyncMock(return_value={"policy": {"plan_tier": "standard"}, "wake_queue": {"pending_count": 1}}),
                ),
                patch(
                    "server_modules.agent_registry_api.activity_ledger_service.list_sage_recent_activity_payload",
                    new=AsyncMock(return_value={"items": [{"id": "aevt-1"}], "summary": {"count": 1}}),
                ),
                patch(
                    "server_modules.agent_registry_api.specialist_service.list_workspace_specialist_service_summaries",
                    new=AsyncMock(
                        return_value=[
                            {
                                "install_id": "install-research",
                                "service_kind": "business_specialist",
                                "runtime_policy": {"selection_status": "ready"},
                            }
                        ]
                    ),
                ),
            ):
                result = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["thread_id"], "thread-sage")
            self.assertEqual(result["master_install"]["id"], "install-sage")
            self.assertEqual(len(result["specialist_installs"]), 1)
            self.assertEqual(result["runtime_targets"]["default_target_id"], "cloud_default")
            self.assertEqual(result["runtime_targets"]["routing_contract"]["mobile_entry_mode"], "platform_first")
            self.assertEqual(result["specialist_installs"][0]["id"], "install-research")
            self.assertEqual(result["specialist_services"][0]["install_id"], "install-research")
            self.assertEqual(result["personal_context"]["summary"]["count"], 1)
            self.assertEqual(result["unified_memory"]["summary"]["layer_count"], 2)
            self.assertEqual(result["runtime_attachments"]["deployment_mode"], "hybrid")
            self.assertEqual(result["scheduler"]["policy"]["plan_tier"], "standard")
            self.assertEqual(result["recent_activity"]["summary"]["count"], 1)
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_get_specialist_returns_service_contract(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("GET", "/agent-registry/specialists/{install_id}")]
            specialist = {
                "id": "install-support",
                "label": "Support Specialist",
                "workspace_id": "workspace-1",
                "tenant_id": "tenant-1",
                "runtime_mode": "hosted_secure",
            }
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.get_workspace_specialist",
                    new=AsyncMock(return_value=specialist),
                ),
                patch(
                    "server_modules.agent_registry_api.specialist_service.build_specialist_service_contract",
                    new=AsyncMock(
                        return_value={
                            "service_kind": "business_specialist",
                            "install_id": "install-support",
                            "separation_contract": {
                                "inherits_sage_memory_by_default": False,
                            },
                        }
                    ),
                ),
            ):
                result = asyncio.run(
                    route(
                        "install-support",
                        workspace_id="workspace-1",
                        current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["id"], "install-support")
            self.assertEqual(result["service_contract"]["service_kind"], "business_specialist")
            self.assertFalse(result["service_contract"]["separation_contract"]["inherits_sage_memory_by_default"])
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_create_specialist_normalizes_specialist_mode_metadata(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/specialists")]
            manifest = AgentManifest(
                manifest_id="manifest-support",
                identity={
                    "name": "Support Specialist",
                    "role": "Customer Service",
                    "archetype": "support_specialist",
                    "summary": "Handles support.",
                },
            )
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.create_workspace_specialist",
                    new=AsyncMock(return_value={"id": "install-support", "label": "Support Specialist"}),
                ) as create_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.SpecialistCreateRequest(
                            workspace_id="workspace-1",
                            manifest=manifest,
                            metadata={"specialist_mode": "owner_test"},
                        ),
                        current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["id"], "install-support")
            self.assertEqual(create_mock.await_args.kwargs["metadata"]["specialist_mode"], "owner_test")
            self.assertEqual(
                create_mock.await_args.kwargs["metadata"]["specialist_mode_contract"]["response_audience"],
                "owner_operator_preview",
            )
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_update_specialist_manifest_rejects_non_owner_edit_mode(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("PATCH", "/agent-registry/specialists/{install_id}")]
            manifest = AgentManifest(
                manifest_id="manifest-support",
                identity={
                    "name": "Support Specialist",
                    "role": "Customer Service",
                    "archetype": "support_specialist",
                    "summary": "Handles support.",
                },
            )
            existing = {
                "id": "install-support",
                "label": "Support Specialist",
                "metadata": {"specialist_mode": "customer_live"},
                "specialist_mode": "customer_live",
            }
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.get_workspace_specialist",
                    new=AsyncMock(return_value=existing),
                ),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.update_workspace_specialist_manifest",
                    new=AsyncMock(),
                ) as update_mock,
            ):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(
                        route(
                            "install-support",
                            agent_registry_api.SpecialistManifestUpdateRequest(
                                workspace_id="workspace-1",
                                manifest=manifest,
                            ),
                            current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("owner_edit", str(ctx.exception.detail))
            update_mock.assert_not_called()
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_list_runtime_attachments_returns_inventory(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("GET", "/agent-registry/runtime-attachments")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.runtime_attachment_service.list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "tenant_id": "tenant-1",
                            "workspace_id": "workspace-1",
                            "deployment_mode": "hybrid",
                            "attachments": [{"attachment_id": "local_companion:profile-local"}],
                        }
                    ),
                ),
            ):
                result = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["deployment_mode"], "hybrid")
            self.assertEqual(result["attachments"][0]["attachment_id"], "local_companion:profile-local")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_list_runtime_targets_returns_workspace_projection(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("GET", "/agent-registry/runtime-targets")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.runtime_attachment_service.list_workspace_runtime_targets",
                    new=AsyncMock(
                        return_value={
                            "tenant_id": "tenant-1",
                            "workspace_id": "workspace-1",
                            "deployment_mode": "hybrid",
                            "default_target_id": "cloud_default",
                            "targets": [{"target_id": "cloud_default", "available": True}],
                            "routing_contract": {"mobile_entry_mode": "platform_first"},
                        }
                    ),
                ),
            ):
                result = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["default_target_id"], "cloud_default")
            self.assertEqual(result["targets"][0]["target_id"], "cloud_default")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_list_shared_board_returns_published_entries(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("GET", "/agent-registry/shared-board")]
            fake_memory_service = types.SimpleNamespace(
                list_shared_operational_board_entries=Mock(
                    return_value=[
                        {
                            "entry_id": "board-1",
                            "revision_id": "rev-2",
                            "revision_number": 2,
                            "status": "published",
                            "entry_kind": "sop_playbook",
                            "title": "Refund playbook",
                        }
                    ]
                )
            )
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch.object(agent_registry_api, "memory_service", fake_memory_service, create=True),
            ):
                result = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        current_user={"user_id": "user-1", "tenant_id": "tenant-1", "role": "member", "is_admin": False},
                    )
                )

            self.assertEqual(result["summary"]["count"], 1)
            self.assertEqual(result["summary"]["permission_tier"], "propose_update")
            self.assertEqual(result["entries"][0]["entry_id"], "board-1")
            self.assertEqual(
                fake_memory_service.list_shared_operational_board_entries.call_args.kwargs["actor_permission"],
                "propose_update",
            )
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_write_shared_board_maps_owner_to_publish_permission(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/shared-board")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch(
                    "server_modules.agent_registry_api.shared_operational_board_service.write_shared_operational_board_entry",
                    new=AsyncMock(
                        return_value={
                            "entry": {"entry_id": "board-1", "status": "published"},
                            "revision": {"revision_id": "rev-1", "revision_number": 1},
                            "activity_event_id": "aevt-1",
                        }
                    ),
                ) as write_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.SharedOperationalBoardWriteRequest(
                            workspace_id="workspace-1",
                            action="publish_update",
                            entry_kind="shared_instruction",
                            title="Escalation rule",
                            body="Escalate urgent billing tickets within 10 minutes.",
                        ),
                        current_user={"user_id": "owner-1", "tenant_id": "tenant-1", "role": "owner", "is_admin": False, "email": "owner@example.com"},
                    )
                )

            self.assertEqual(result["permission_tier"], "publish_update")
            self.assertEqual(result["entry"]["entry_id"], "board-1")
            self.assertEqual(write_mock.await_args.kwargs["actor_permission"], "publish_update")
            self.assertEqual(write_mock.await_args.kwargs["actor"]["role"], "owner")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_publish_personal_context_event_enforces_workspace_and_uses_engine(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/personal-context/events")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.personal_context_engine.publish_event",
                    new=AsyncMock(return_value={"id": "pctx-1", "event_type": "message_awaiting_reply"}),
                ) as publish_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.PersonalContextEventPublishRequest(
                            workspace_id="workspace-1",
                            source_app="notifications",
                            event_type="message_awaiting_reply",
                            entity_id="thread-42",
                            payload={"contact_name": "Alex"},
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["workspace_id"], "workspace-1")
            self.assertEqual(result["tenant_id"], "tenant-1")
            self.assertEqual(result["event"]["id"], "pctx-1")
            self.assertEqual(publish_mock.await_args.kwargs["source_app"], "notifications")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_list_personal_context_events_returns_scoped_feed(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("GET", "/agent-registry/personal-context/events")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.personal_context_engine.list_events",
                    new=AsyncMock(return_value=[
                        {
                            "id": "pctx-1",
                            "event_type": "calendar_conflict",
                            "source_app": "calendar",
                            "summary": "Calendar conflict detected for Math Final.",
                            "priority": 82,
                        }
                    ]),
                ) as list_mock,
            ):
                result = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        audience="sage",
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["summary"]["by_type"]["calendar_conflict"], 1)
            self.assertEqual(list_mock.await_args.kwargs["audience"], "sage")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_mark_personal_context_events_seen_marks_scope(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/personal-context/events/seen")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.personal_context_engine.mark_seen_by_sage",
                    new=AsyncMock(return_value={"status": "ok", "marked_count": 1, "marked_ids": ["pctx-1"], "seen_by_sage_at": "2026-04-10T00:00:00Z"}),
                ) as mark_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.PersonalContextEventsSeenRequest(
                            workspace_id="workspace-1",
                            event_ids=["pctx-1"],
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["marked_count"], 1)
            self.assertEqual(mark_mock.await_args.kwargs["event_ids"], ["pctx-1"])
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_schedule_self_wakeup_enforces_workspace_and_returns_policy_result(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/scheduler/self-wakeups")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.bounded_scheduler_service.propose_self_wakeup",
                    new=AsyncMock(
                        return_value={
                            "accepted": True,
                            "policy": {"plan_tier": "pro"},
                            "wake_request": {"id": "wake-1", "status": "pending"},
                        }
                    ),
                ) as wake_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.SchedulerSelfWakeupRequest(
                            workspace_id="workspace-1",
                            summary="Check if the exam prep reminder needs a follow-up.",
                            reason="goal_followup",
                            due_at="2026-04-10T12:00:00Z",
                            payload={"goal": "exam_prep"},
                            policy_context={"approval_required": False},
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertTrue(result["accepted"])
            self.assertEqual(result["wake_request"]["id"], "wake-1")
            self.assertEqual(wake_mock.await_args.kwargs["requested_by"], "sage")
            self.assertEqual(wake_mock.await_args.kwargs["policy_context"]["requested_via"], "agent_registry_api")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_get_scheduler_status_returns_snapshot(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("GET", "/agent-registry/scheduler/status")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.bounded_scheduler_service.scheduler_status_snapshot",
                    new=AsyncMock(
                        return_value={
                            "policy": {"plan_tier": "starter"},
                            "ambient_monitor": {"registered": True},
                            "exact_jobs": {"count": 2},
                            "wake_queue": {"pending_count": 1, "claimed_count": 0},
                        }
                    ),
                ) as snapshot_mock,
            ):
                result = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["policy"]["plan_tier"], "starter")
            self.assertTrue(result["ambient_monitor"]["registered"])
            self.assertEqual(snapshot_mock.await_args.kwargs["workspace_id"], "workspace-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_set_security_kill_switch_enforces_workspace_and_updates_state(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/security/kill-switches")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1") as access_mock,
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.safe_mode_service.set_kill_switch",
                    return_value={"scope": "agent", "enabled": True, "workspace_id": "workspace-1", "agent_install_id": "install-1"},
                ) as switch_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.SecurityKillSwitchRequest(
                            workspace_id="workspace-1",
                            scope="agent",
                            enabled=True,
                            agent_install_id="install-1",
                            reason="incident",
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertTrue(result["enabled"])
            self.assertEqual(result["agent_install_id"], "install-1")
            self.assertEqual(access_mock.call_args.kwargs["minimum_role"], "owner")
            self.assertEqual(switch_mock.call_args.kwargs["tenant_id"], "tenant-1")
            self.assertEqual(switch_mock.call_args.kwargs["scope"], "agent")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_revoke_connector_access_enforces_workspace_and_routes_to_safe_mode_service(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/security/connectors/revoke")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1") as access_mock,
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.safe_mode_service.revoke_connector_access",
                    return_value={"scope": "connector", "status": "revoked", "connector_id": "crm"},
                ) as revoke_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.ConnectorRevokeRequest(
                            workspace_id="workspace-1",
                            connector_id="crm",
                            credential_id="cred-1",
                            reason="compromised",
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["status"], "revoked")
            self.assertEqual(access_mock.call_args.kwargs["minimum_role"], "owner")
            self.assertEqual(revoke_mock.call_args.kwargs["connector_id"], "crm")
            self.assertEqual(revoke_mock.call_args.kwargs["tenant_id"], "tenant-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_set_reliability_incident_control_enforces_workspace_and_updates_state(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/reliability/incident-controls")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1") as access_mock,
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.safe_mode_service.set_incident_control",
                    return_value={"scope": "channel", "mode": "drain", "workspace_id": "workspace-1", "channel_key": "telegram"},
                ) as incident_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.IncidentControlRequest(
                            workspace_id="workspace-1",
                            scope="channel",
                            mode="drain",
                            channel_key="telegram",
                            endpoint_key="@partspro_bot",
                            reason="draining",
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["mode"], "drain")
            self.assertEqual(access_mock.call_args.kwargs["minimum_role"], "owner")
            self.assertEqual(incident_mock.call_args.kwargs["tenant_id"], "tenant-1")
            self.assertEqual(incident_mock.call_args.kwargs["scope"], "channel")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_rotate_connector_access_enforces_workspace_and_routes_to_safe_mode_service(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/security/connectors/rotate")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1") as access_mock,
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.safe_mode_service.rotate_connector_access",
                    return_value={
                        "scope": "connector",
                        "status": "rotated",
                        "connector_id": "crm",
                        "rotated_to_credential_id": "cred-2",
                    },
                ) as rotate_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.ConnectorRotateRequest(
                            workspace_id="workspace-1",
                            connector_id="crm",
                            previous_credential_id="cred-1",
                            replacement_credential_id="cred-2",
                            reason="rotation",
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["status"], "rotated")
            self.assertEqual(result["rotated_to_credential_id"], "cred-2")
            self.assertEqual(access_mock.call_args.kwargs["minimum_role"], "owner")
            self.assertEqual(rotate_mock.call_args.kwargs["previous_credential_id"], "cred-1")
            self.assertEqual(rotate_mock.call_args.kwargs["replacement_credential_id"], "cred-2")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_create_specialist_uses_specialist_repository(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/specialists")]
            manifest = AgentManifest(
                manifest_id="manifest-parts-pro",
                identity={
                    "name": "Parts Pro",
                    "role": "Inventory Specialist",
                    "archetype": "support_specialist",
                    "summary": "Help customers find parts.",
                },
            )
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.create_workspace_specialist",
                    new=AsyncMock(return_value={"id": "install-1", "workspace_id": "workspace-1"}),
                ) as create_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.SpecialistCreateRequest(
                            workspace_id="workspace-1",
                            label="Parts Pro",
                            manifest=manifest,
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )
            self.assertEqual(result["id"], "install-1")
            self.assertEqual(create_mock.await_args.kwargs["tenant_id"], "tenant-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_create_specialist_returns_conflict_for_duplicate_inbound_owner(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/specialists")]
            manifest = AgentManifest(
                manifest_id="manifest-parts-pro",
                identity={
                    "name": "Parts Pro",
                    "role": "Inventory Specialist",
                    "archetype": "support_specialist",
                    "summary": "Help customers find parts.",
                },
            )
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.create_workspace_specialist",
                    new=AsyncMock(side_effect=agent_registry_api.agent_specialist_repository.ChannelOwnershipConflictError(
                        "This channel already has an inbound owner."
                    )),
                ),
            ):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(
                        route(
                            agent_registry_api.SpecialistCreateRequest(
                                workspace_id="workspace-1",
                                label="Parts Pro",
                                manifest=manifest,
                            ),
                            current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(error.exception.detail, "This channel already has an inbound owner.")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_save_specialist_channels_returns_conflict_for_duplicate_inbound_owner(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("PUT", "/agent-registry/specialists/{install_id}/channels")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.get_workspace_specialist",
                    new=AsyncMock(return_value={"id": "install-2", "specialist_mode": "owner_edit", "metadata": {"specialist_mode": "owner_edit"}}),
                ),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.save_workspace_specialist_channel_bindings",
                    new=AsyncMock(side_effect=agent_registry_api.agent_specialist_repository.ChannelOwnershipConflictError(
                        "This channel already has an inbound owner."
                    )),
                ),
            ):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(
                        route(
                            "install-2",
                            agent_registry_api.SpecialistChannelBindingsUpdateRequest(
                                workspace_id="workspace-1",
                                channel_bindings={
                                    "telegram": {
                                        "enabled": True,
                                        "is_inbound_owner": True,
                                        "endpoint_key": "@partspro_bot",
                                    },
                                },
                            ),
                            current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(error.exception.detail, "This channel already has an inbound owner.")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_save_specialist_runtime_returns_bad_request_for_invalid_profile(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("PUT", "/agent-registry/specialists/{install_id}/runtime")]
            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.get_workspace_specialist",
                    new=AsyncMock(return_value={"id": "install-2", "specialist_mode": "owner_edit", "metadata": {"specialist_mode": "owner_edit"}}),
                ),
                patch(
                    "server_modules.agent_registry_api.agent_specialist_repository.save_workspace_specialist_runtime_profile",
                    new=AsyncMock(side_effect=agent_registry_api.agent_specialist_repository.RuntimeProfileValidationError(
                        "Hosted Secure requires a cloud runtime profile."
                    )),
                ),
            ):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(
                        route(
                            "install-2",
                            agent_registry_api.SpecialistRuntimeProfileUpdateRequest(
                                workspace_id="workspace-1",
                                runtime_mode="hosted_secure",
                            ),
                            current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(error.exception.status_code, 400)
            self.assertEqual(error.exception.detail, "Hosted Secure requires a cloud runtime profile.")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_run_installed_agent_requires_explicit_approval_for_privileged_runtime(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agents/{install_id}/run")]
            compiled = {
                "install": {
                    "id": "install-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "agent_definition_id": "agent-1",
                    "agent_definition_version_id": "agentver-1",
                    "runtime_profile_id": "profile-1",
                    "runtime_mode": "privileged_device",
                    "thread_id": "thread-1",
                    "runtime_profile": {
                        "id": "profile-1",
                        "label": "Owner Mac",
                        "runtime_class": "desktop_companion",
                        "machine_id": "machine-1",
                        "runtime_id": "runtime-1",
                    },
                    "agent_definition": {"name": "Desktop Operator", "slug": "desktop-operator"},
                },
                "workflow_id": "wf-compiled",
                "workflow_version_id": "wfver-compiled",
                "workflow_snapshot": {
                    "id": "wf-compiled",
                    "workflowVersionId": "wfver-compiled",
                    "definition": {"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
                },
                "run_metadata": {
                    "execution_target": "local_companion",
                    "execution_target_selected": "local_companion",
                },
            }

            with (
                patch("server_modules.agent_registry_api.template_compiler_service.ensure_install_compiled_artifact", new=AsyncMock(return_value=compiled)),
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api._run_execution_services", return_value={"run": "services"}),
                patch("server_modules.agent_registry_api.turn_ingress_service.start_turn", new=AsyncMock()),
                patch(
                    "server_modules.agent_registry_api.runtime_attachment_service.resolve_install_runtime_plan",
                    new=AsyncMock(
                        return_value={
                            "runtime_mode": "privileged_device",
                            "deployment_mode": "hybrid",
                            "selected_attachment": {
                                "attachment_id": "local_companion:profile-1",
                                "attachment_kind": "local_companion",
                            },
                            "machine_target": "machine-1",
                            "execution_target_selected": "local_companion",
                        }
                    ),
                ),
            ):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(
                        route(
                            "install-1",
                            agent_registry_api.AgentInstallRunRequest(message="Open Calculator"),
                            current_user={"user_id": "user-1", "email": "user@example.com", "role": "owner", "is_admin": True},
                        )
                    )

            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(error.exception.detail, "Privileged device execution requires explicit owner approval.")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_run_installed_agent_scrubs_host_grants_for_hosted_secure_runtime(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agents/{install_id}/run")]
            compiled = {
                "install": {
                    "id": "install-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "agent_definition_id": "agent-1",
                    "agent_definition_version_id": "agentver-1",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_mode": "hosted_secure",
                    "thread_id": "thread-1",
                    "root_folder_uri": "/Users/mansur/Documents/Unsafe",
                    "folder_grants": ["/Users/mansur/Desktop"],
                    "runtime_profile": {
                        "id": "profile-cloud",
                        "label": "Empyralis Cloud",
                        "runtime_class": "cloud_worker",
                        "runtime_id": "runtime-cloud-1",
                        "machine_id": "machine-should-not-leak",
                        "metadata": {
                            "base_image_id": "empyralis-hosted-secure-v1",
                        },
                    },
                    "agent_definition": {"name": "Cloud Operator", "slug": "cloud-operator"},
                },
                "workflow_id": "wf-compiled",
                "workflow_version_id": "wfver-compiled",
                "workflow_snapshot": {
                    "id": "wf-compiled",
                    "workflowVersionId": "wfver-compiled",
                    "definition": {"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
                },
                "run_metadata": {
                    "execution_target": "local_companion",
                    "execution_target_selected": "local_companion",
                    "root_folder_uri": "/Users/mansur/Documents/Unsafe",
                    "folder_grants": ["/Users/mansur/Desktop"],
                },
            }

            with (
                patch("server_modules.agent_registry_api.template_compiler_service.ensure_install_compiled_artifact", new=AsyncMock(return_value=compiled)),
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api._run_execution_services", return_value={"run": "services"}),
                patch(
                    "server_modules.agent_registry_api.runtime_attachment_service.resolve_install_runtime_plan",
                    new=AsyncMock(
                        return_value={
                            "runtime_mode": "hosted_secure",
                            "deployment_mode": "hybrid",
                            "selected_attachment": {
                                "attachment_id": "managed_cloud:profile-cloud",
                                "attachment_kind": "managed_cloud",
                            },
                            "machine_target": None,
                            "execution_target_selected": "cloud",
                        }
                    ),
                ),
                patch(
                    "server_modules.agent_registry_api.turn_ingress_service.start_turn",
                    new=AsyncMock(
                        side_effect=lambda **kwargs: agent_registry_api.turn_ingress_service.TurnIngressResult(
                            turn_request=request_body_to_turn_request(kwargs["payload"]),
                            result={
                                "status": "accepted",
                                "reply": "",
                                "run_id": "run-1",
                                "metadata": {"created_run": {"run_id": "run-1", "status": "queued"}},
                            },
                        )
                    ),
                ) as turn_mock,
            ):
                result = asyncio.run(
                    route(
                        "install-1",
                        agent_registry_api.AgentInstallRunRequest(message="Compile a catalog summary"),
                        current_user={"user_id": "user-1", "email": "user@example.com", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result.status, "accepted")
            turn_payload = turn_mock.await_args.kwargs["payload"]
            self.assertIsNone(turn_payload["machine_target"])
            metadata = turn_payload["context_hints"]["metadata"]
            self.assertIsNone(metadata["root_folder_uri"])
            self.assertEqual(metadata["folder_grants"], [])
            self.assertEqual(metadata["file_mount_grants"], [])
            self.assertEqual(metadata["execution_target_selected"], "cloud")
            self.assertEqual(metadata["runtime_attachment_kind"], "managed_cloud")
            self.assertEqual(metadata["runtime_scope"]["mode"], "hosted_secure")
            self.assertFalse(metadata["runtime_scope"]["host_mounts_allowed"])
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_run_installed_agent_uses_runtime_attachment_selection_for_local_secure(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agents/{install_id}/run")]
            compiled = {
                "install": {
                    "id": "install-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "agent_definition_id": "agent-1",
                    "agent_definition_version_id": "agentver-1",
                    "runtime_profile_id": "profile-local",
                    "runtime_mode": "local_secure",
                    "thread_id": "thread-1",
                    "runtime_profile": {
                        "id": "profile-local",
                        "label": "Owner Mac mini",
                        "runtime_class": "desktop_companion",
                        "machine_id": "machine-local",
                        "runtime_id": "runtime-local",
                    },
                    "agent_definition": {"name": "Desktop Operator", "slug": "desktop-operator"},
                },
                "workflow_id": "wf-compiled",
                "workflow_version_id": "wfver-compiled",
                "workflow_snapshot": {
                    "id": "wf-compiled",
                    "workflowVersionId": "wfver-compiled",
                    "definition": {"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
                },
                "run_metadata": {
                    "execution_target": "local_companion",
                    "execution_target_selected": "local_companion",
                },
            }

            with (
                patch("server_modules.agent_registry_api.template_compiler_service.ensure_install_compiled_artifact", new=AsyncMock(return_value=compiled)),
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api._run_execution_services", return_value={"run": "services"}),
                patch(
                    "server_modules.agent_registry_api.runtime_attachment_service.resolve_install_runtime_plan",
                    new=AsyncMock(
                        return_value={
                            "runtime_mode": "local_secure",
                            "deployment_mode": "hybrid",
                            "selected_attachment": {
                                "attachment_id": "local_companion:profile-local",
                                "attachment_kind": "local_companion",
                            },
                            "machine_target": "machine-local",
                            "execution_target_selected": "local_companion",
                        }
                    ),
                ),
                patch(
                    "server_modules.agent_registry_api.turn_ingress_service.start_turn",
                    new=AsyncMock(
                        side_effect=lambda **kwargs: agent_registry_api.turn_ingress_service.TurnIngressResult(
                            turn_request=request_body_to_turn_request(kwargs["payload"]),
                            result={
                                "status": "accepted",
                                "reply": "",
                                "run_id": "run-1",
                                "metadata": {"created_run": {"run_id": "run-1", "status": "queued"}},
                            },
                        )
                    ),
                ) as turn_mock,
            ):
                result = asyncio.run(
                    route(
                        "install-1",
                        agent_registry_api.AgentInstallRunRequest(message="Open Finder"),
                        current_user={"user_id": "user-1", "email": "user@example.com", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result.status, "accepted")
            turn_payload = turn_mock.await_args.kwargs["payload"]
            self.assertEqual(turn_payload["machine_target"], "machine-local")
            metadata = turn_payload["context_hints"]["metadata"]
            self.assertEqual(metadata["runtime_attachment_kind"], "local_companion")
            self.assertEqual(metadata["workspace_runtime_deployment_mode"], "hybrid")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()

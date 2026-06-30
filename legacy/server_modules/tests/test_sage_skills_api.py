import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from server_modules import sage_skills_api


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)


class SageSkillsApiTests(unittest.TestCase):
    def test_get_route_normalizes_curated_skill_states_and_reasons(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_skills_api.register_sage_skills_routes(app)
            route = app.routes[("GET", "/api/sage-skills")]
            with (
                patch("server_modules.sage_skills_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_skills_api.workspace_tenant_id", return_value="tenant-1"),
                patch("server_modules.sage_skills_api.current_device_os_label", return_value="macOS"),
                patch(
                    "server_modules.sage_skills_api.list_installed_skills",
                    return_value=[
                        {
                            "id": "1password",
                            "name": "1Password",
                            "enabled": True,
                            "available": True,
                            "description": "Use vault items.",
                            "tools": ["vault.search"],
                            "skill_body": "# 1Password\n\napi_key=sk-test-secret-value\nUse vault items safely.",
                            "readme": "1Password skill package.",
                            "runtime_metadata": {"action_class": "read", "requires_approval": False},
                        },
                        {
                            "id": "tmux",
                            "name": "tmux",
                            "enabled": True,
                            "available": False,
                            "missing_bins": ["tmux"],
                            "missing_env_vars": ["TMUX_SOCKET"],
                            "supported_os": ["macos"],
                            "availability_reasons": [
                                "Missing runtime dependencies: tmux",
                                "Missing environment variables: TMUX_SOCKET",
                            ],
                            "runtime_metadata": {"action_class": "local_write", "requires_approval": True},
                        },
                        {
                            "id": "custom-helper",
                            "name": "Custom Helper",
                            "enabled": False,
                            "available": True,
                            "runtime_metadata": {"action_class": "read", "requires_approval": False},
                        },
                    ],
                ),
            ):
                payload = asyncio.run(route(workspace_id="workspace-1", current_user={"user_id": "user-1"}))
            self.assertEqual(payload["summary"]["ready_count"], 1)
            self.assertEqual(payload["summary"]["needs_setup_count"], 3)
            self.assertEqual(payload["summary"]["unsupported_count"], 0)
            self.assertEqual(payload["summary"]["disabled_count"], 1)
            self.assertEqual(payload["curated_pack"][0]["name"], "1Password")
            self.assertEqual(payload["curated_pack"][1]["name"], "Apple Notes")
            self.assertEqual(payload["curated_pack"][2]["name"], "Apple Reminders")
            self.assertEqual(payload["curated_pack"][3]["name"], "tmux")
            self.assertEqual(payload["curated_pack"][0]["status"], "ready")
            self.assertTrue(payload["curated_pack"][0]["active_now"])
            self.assertEqual(
                payload["curated_pack"][0]["skill_body"],
                "# 1Password\n\napi_key=[redacted-secret]\nUse vault items safely.",
            )
            self.assertEqual(payload["curated_pack"][0]["readme"], "1Password skill package.")
            self.assertEqual(payload["curated_pack"][1]["status"], "needs_setup")
            self.assertEqual(payload["curated_pack"][3]["status"], "needs_setup")
            self.assertEqual(
                payload["curated_pack"][3]["reason"],
                "Missing runtime dependencies: tmux; Missing environment variables: TMUX_SOCKET",
            )
            self.assertEqual(payload["curated_pack"][3]["supported_os"], ["macos"])
            self.assertIn("Install: tmux.", payload["curated_pack"][3]["setup_requirement"])
            self.assertEqual(payload["items"][-1]["status"], "disabled_policy")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_capabilities_route_combines_builtin_skills_and_mcp_state(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_skills_api.register_sage_skills_routes(app)
            route = app.routes[("GET", "/api/sage-capabilities")]
            with (
                patch("server_modules.sage_skills_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_skills_api.workspace_tenant_id", return_value="tenant-1"),
                patch("server_modules.sage_skills_api.current_device_os_label", return_value="macOS"),
                patch(
                    "server_modules.sage_skills_api.list_installed_skills",
                    return_value=[
                        {
                            "id": "custom-helper",
                            "name": "Custom Helper",
                            "enabled": True,
                            "available": True,
                            "description": "Read custom workspace data.",
                            "tools": ["custom.read"],
                            "runtime_metadata": {
                                "action_class": "read",
                                "requires_approval": False,
                                "execution_mode": "cloud",
                            },
                        },
                    ],
                ),
                patch(
                    "server_modules.sage_skills_api.mcp_registry_service.list_workspace_mcp_servers",
                    return_value=[
                        {
                            "server_id": "server-1",
                            "enabled": True,
                            "tools": [
                                {
                                    "name": "search_docs",
                                    "label": "Search docs",
                                    "description": "Search approved docs.",
                                    "approved": True,
                                    "action_class": "read",
                                },
                                {
                                    "name": "write_docs",
                                    "label": "Write docs",
                                    "approved": False,
                                    "requires_approval": True,
                                    "risk_level": "high",
                                    "action_class": "write",
                                },
                            ],
                        }
                    ],
                ),
            ):
                payload = asyncio.run(route(workspace_id="workspace-1", current_user={"user_id": "user-1"}))

            items = payload["items"]
            self.assertTrue(any(item["type"] == "memory" and item["tool_id"] == "memory_search" for item in items))
            self.assertTrue(
                any(
                    item["type"] == "skill"
                    and item["skill_id"] == "custom-helper"
                    and item["tool_id"] == "custom.read"
                    and item["status"] == "ready"
                    for item in items
                )
            )
            self.assertTrue(
                any(
                    item["type"] == "mcp"
                    and item["mcp_tool_id"] == "search_docs"
                    and item["status"] == "ready"
                    for item in items
                )
            )
            self.assertTrue(
                any(
                    item["type"] == "mcp"
                    and item["mcp_tool_id"] == "write_docs"
                    and item["status"] == "needs_approval"
                    and item["setup_action"] == "approve_mcp_tool"
                    for item in items
                )
            )
            self.assertGreaterEqual(payload["summary"]["memory_count"], 1)
            self.assertEqual(payload["summary"]["mcp_count"], 2)
            self.assertGreaterEqual(payload["summary"]["needs_approval_count"], 1)
            health_by_id = {item["id"]: item for item in payload["health_checks"]}
            self.assertEqual(health_by_id["model_route"]["status"], "ready")
            self.assertEqual(health_by_id["mcp_endpoint_safety"]["status"], "ready")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()

import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
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
    """Catalog unification (docs/design/audit-skills.md §3 item 3): the
    hardcoded _CURATED_SKILL_PACK (1Password/Apple Notes/Apple Reminders/
    tmux — zero execution implementation anywhere) is gone. Both
    /api/sage-skills and /api/sage-capabilities now source every skill from
    skill_registry.list_skill_definitions, the same catalog skill_invoke
    dispatches against. Patching server_modules.skill_registry.
    list_installed_skills (not sage_skills_api.list_installed_skills, which
    no longer exists in this module) controls the filesystem-scanned half of
    that catalog; the ~20 real _BUILT_IN_SKILLS entries are always present
    alongside it, so assertions below are existence-based rather than
    fixed-index."""

    def test_no_hardcoded_curated_pack_remains(self) -> None:
        self.assertFalse(hasattr(sage_skills_api, "_CURATED_SKILL_PACK"))
        self.assertFalse(hasattr(sage_skills_api, "CuratedSkillDefinition"))

    def test_get_route_normalizes_installed_skill_states_and_reasons(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        temp_dir = tempfile.TemporaryDirectory()
        try:
            # A real path is needed here (not just an inline "skill_body"
            # string) because skill_registry.SkillDefinition only carries a
            # `path`, not the body text itself — sage_skills_api re-reads
            # SKILL.md from disk for the Level-2 detail view
            # (_skill_definition_body), same as it does for the 6 real
            # bundled skills this task authored.
            skill_dir = Path(temp_dir.name) / "vault-helper"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "# Vault Helper\n\napi_key=sk-test-secret-value\nUse vault items safely.",
                encoding="utf-8",
            )
            (skill_dir / "README.md").write_text("Vault helper skill package.", encoding="utf-8")

            app = _FakeApp()
            sage_skills_api.register_sage_skills_routes(app)
            route = app.routes[("GET", "/api/sage-skills")]
            with (
                patch("server_modules.sage_skills_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_skills_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.skill_registry.list_installed_skills",
                    return_value=[
                        {
                            "id": "vault-helper",
                            "name": "Vault Helper",
                            "enabled": True,
                            "available": True,
                            "description": "Use vault items.",
                            "path": str(skill_dir),
                            "runtime_metadata": {"action_class": "read", "requires_approval": False, "execution_adapter": "handler"},
                        },
                        {
                            "id": "tmux-clone",
                            "name": "tmux-clone",
                            "enabled": True,
                            "available": False,
                            "missing_bins": ["tmux"],
                            "missing_env_vars": ["TMUX_SOCKET"],
                            "supported_os": ["macos"],
                            "availability_reasons": [
                                "Missing runtime dependencies: tmux",
                                "Missing environment variables: TMUX_SOCKET",
                            ],
                            "runtime_metadata": {"action_class": "read", "requires_approval": True, "execution_adapter": "handler"},
                        },
                        {
                            "id": "custom-helper",
                            "name": "Custom Helper",
                            "enabled": False,
                            "available": True,
                            "runtime_metadata": {"action_class": "read", "requires_approval": False, "execution_adapter": "handler"},
                        },
                    ],
                ),
            ):
                payload = asyncio.run(route(workspace_id="workspace-1", current_user={"user_id": "user-1"}))

            self.assertNotIn("curated_pack", payload)
            by_id = {item["id"]: item for item in payload["items"]}

            # A skill that has zero execution wiring anywhere (the old
            # curated pack's entire membership) must never appear again.
            self.assertNotIn("1password", by_id)
            self.assertNotIn("tmux", by_id)

            self.assertEqual(by_id["vault-helper"]["status"], "ready")
            self.assertTrue(by_id["vault-helper"]["active_now"])
            self.assertEqual(
                by_id["vault-helper"]["skill_body"],
                "# Vault Helper\n\napi_key=[redacted-secret]\nUse vault items safely.",
            )
            self.assertEqual(by_id["vault-helper"]["readme"], "Vault helper skill package.")

            self.assertEqual(by_id["tmux-clone"]["status"], "needs_setup")
            # Rough edge, disclosed honestly: skill_registry.SkillDefinition
            # has no supported_os field, so per-OS detail computed by
            # installed_skills.list_installed_skills (missing_bins etc.) is
            # preserved via unavailable_reason text below, but the
            # structured supported_os list itself does not survive the
            # SkillDefinition round-trip — it is always [] for
            # catalog-sourced items now, unlike the pre-unification payload.
            self.assertEqual(by_id["tmux-clone"]["supported_os"], [])
            self.assertEqual(
                by_id["tmux-clone"]["reason"],
                "Missing runtime dependencies: tmux; Missing environment variables: TMUX_SOCKET",
            )
            self.assertIn("Missing runtime dependencies: tmux", by_id["tmux-clone"]["setup_requirement"])

            # A disabled skill is still LISTED (with an honest status), not
            # silently dropped — this is the Tools/Skills tab, not the live
            # model manifest (that filtering happens one layer up, in
            # build_model_capability_manifest).
            self.assertEqual(by_id["custom-helper"]["status"], "disabled_policy")

            # The hardcoded _BUILT_IN_SKILLS entries (memory-manager,
            # code-runner, ...) are always part of the merged catalog
            # regardless of what the mocked filesystem scan above returns —
            # the real filesystem-backed versions (with skills/<id>/SKILL.md
            # bodies) are covered by test_skill_catalog_unification.py's
            # unpatched-filesystem tests instead of here.
            for skill_id in ("memory-manager", "code-runner", "file-manager", "telegram-bot", "vision-monitor"):
                self.assertIn(skill_id, by_id, f"expected {skill_id} in the unified skill catalog")
                self.assertEqual(by_id[skill_id]["status"], "ready")
        finally:
            temp_dir.cleanup()
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
                patch(
                    "server_modules.skill_registry.list_installed_skills",
                    return_value=[
                        {
                            "id": "custom-helper",
                            "name": "Custom Helper",
                            "enabled": True,
                            "available": True,
                            "description": "Read custom workspace data.",
                            "runtime_metadata": {
                                "action_class": "read",
                                "requires_approval": False,
                                "execution_mode": "cloud",
                                "execution_adapter": "handler",
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
            # Every skill capability record — including this custom one —
            # now points at the single real skill_invoke dispatcher, not a
            # per-skill tool name, and the skill_id the model must pass is
            # spelled out in the description.
            self.assertTrue(
                any(
                    item["type"] == "skill"
                    and item["skill_id"] == "custom-helper"
                    and item["tool_id"] == "skill_invoke"
                    and item["status"] == "ready"
                    and 'skill_id="custom-helper"' in item["description"]
                    for item in items
                )
            )
            # A real bundled skill from this task's unified catalog is in
            # the same combined payload.
            self.assertTrue(
                any(
                    item["type"] == "skill"
                    and item["skill_id"] == "memory-manager"
                    and item["tool_id"] == "skill_invoke"
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

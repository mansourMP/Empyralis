import asyncio
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import installed_skills, mcp_registry_service, skill_registry


def _write_skill(
    root: Path,
    *,
    name: str,
    description: str = "Sample skill.",
    runtime: dict | None = None,
    handler_body: str | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "description": description,
                "author": "Empyralis",
                "runtime": runtime or {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if handler_body is not None:
        (skill_dir / "handler.py").write_text(handler_body, encoding="utf-8")
    (skill_dir / "README.md").write_text(f"# {name}\n\n{description}\n", encoding="utf-8")
    return skill_dir


class SkillRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace_root = self.root / "workspace"
        self.global_root = self.root / "global"
        self.bundled_root = self.root / "bundled"
        self.mcp_registry_path = self.root / "mcp_servers.json"
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.global_root.mkdir(parents=True, exist_ok=True)
        self.bundled_root.mkdir(parents=True, exist_ok=True)
        self.patchers = [
            patch.object(installed_skills, "workspace_skills_root", return_value=self.workspace_root),
            patch.object(installed_skills, "global_skills_root", return_value=self.global_root),
            patch.object(installed_skills, "bundled_skills_root", return_value=self.bundled_root),
            patch.object(mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", self.mcp_registry_path),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_registry_loads_manifest_backed_skill_definition(self) -> None:
        _write_skill(
            self.bundled_root,
            name="workspace-browser-check",
            description="Inspect a browser page.",
            runtime={
                "skill_class": "system",
                "permission_label": "Browser runtime",
                "execution_mode": "live",
                "action_class": "read",
                "connector_scopes": ["browser"],
                "trigger_terms": ["inspect browser"],
                "allowed_runtime_modes": ["hosted_secure", "local_secure"],
                "execution_adapter": "browser",
            },
        )

        definition = skill_registry.get_skill_definition("workspace-browser-check", workspace_id="workspace-1")

        self.assertIsNotNone(definition)
        self.assertEqual(definition.skill_class, "system")
        self.assertEqual(definition.execution_adapter, "browser")
        self.assertEqual(definition.connector_scopes, ("browser",))
        self.assertEqual(definition.allowed_runtime_modes, ("hosted_secure", "local_secure"))

    def test_workspace_override_disables_skill(self) -> None:
        _write_skill(
            self.bundled_root,
            name="workspace-search-check",
            runtime={
                "skill_class": "system",
                "action_class": "read",
                "execution_mode": "live",
                "connector_scopes": ["web"],
                "execution_adapter": "web_search",
            },
        )

        before = installed_skills.list_installed_skills(workspace_id="workspace-1")
        self.assertTrue(before[0]["enabled"])

        installed_skills.set_workspace_installed_skill_enabled(
            skill_id="workspace-search-check",
            workspace_id="workspace-1",
            enabled=False,
        )

        after = installed_skills.list_installed_skills(workspace_id="workspace-1")
        self.assertFalse(after[0]["enabled"])
        self.assertIsNone(skill_registry.get_skill_definition("workspace-search-check", workspace_id="workspace-1"))
        disabled = skill_registry.get_skill_definition(
            "workspace-search-check",
            workspace_id="workspace-1",
            include_disabled=True,
        )
        self.assertIsNotNone(disabled)
        self.assertFalse(disabled.enabled)

    def test_skill_availability_gates_on_os_env_and_python_packages(self) -> None:
        _write_skill(
            self.bundled_root,
            name="mac-only-helper",
            runtime={
                "skill_class": "system",
                "action_class": "read",
                "execution_mode": "live",
                "execution_adapter": "handler",
                "supported_os": ["linux"],
                "required_env": ["EMPYRALIS_TEST_SECRET"],
                "required_python_packages": ["totally_missing_pkg_xyz"],
            },
            handler_body="print('ok')\n",
        )

        with patch.dict(os.environ, {}, clear=False):
            listed = installed_skills.list_installed_skills(workspace_id="workspace-1")

        self.assertEqual(len(listed), 1)
        item = listed[0]
        self.assertFalse(item["available"])
        self.assertIn("EMPYRALIS_TEST_SECRET", item["missing_env_vars"])
        self.assertIn("totally_missing_pkg_xyz", item["missing_python_packages"])
        self.assertEqual(item["supported_os"], ["linux"])
        self.assertIsNone(skill_registry.get_skill_definition("mac-only-helper", workspace_id="workspace-1"))
        disabled = skill_registry.get_skill_definition(
            "mac-only-helper",
            workspace_id="workspace-1",
            include_disabled=True,
        )
        self.assertIsNotNone(disabled)
        self.assertFalse(disabled.available)
        self.assertIn("Missing environment variables", str(disabled.unavailable_reason))
        self.assertEqual(installed_skills.skill_availability_state(item), "unsupported_device")

    def test_disabled_or_unsupported_skill_never_becomes_runnable(self) -> None:
        _write_skill(
            self.bundled_root,
            name="notes-helper",
            runtime={
                "skill_class": "system",
                "action_class": "read",
                "execution_mode": "live",
                "execution_adapter": "handler",
                "supported_os": ["definitely-not-this-os"],
            },
            handler_body="print('ok')\n",
        )

        listed = installed_skills.list_installed_skills(workspace_id="workspace-1")
        self.assertEqual(installed_skills.skill_availability_state(listed[0]), "unsupported_device")
        self.assertIsNone(skill_registry.get_skill_definition("notes-helper", workspace_id="workspace-1"))

        installed_skills.set_workspace_installed_skill_enabled(
            skill_id="notes-helper",
            workspace_id="workspace-1",
            enabled=False,
        )
        disabled = installed_skills.list_installed_skills(workspace_id="workspace-1")[0]
        self.assertEqual(installed_skills.skill_availability_state(disabled), "disabled_policy")
        self.assertIsNone(skill_registry.get_skill_definition("notes-helper", workspace_id="workspace-1"))

    def test_handler_skill_executes_without_core_dispatch_change(self) -> None:
        _write_skill(
            self.bundled_root,
            name="quote-helper",
            description="Custom quote helper.",
            runtime={
                "skill_class": "specialist_local",
                "action_class": "read",
                "execution_mode": "live",
                "execution_adapter": "handler",
                "trigger_terms": ["build quote"],
            },
            handler_body=(
                "import json, sys\n"
                "payload = json.loads(sys.stdin.read() or '{}')\n"
                "goal = str(payload.get('goal') or '').strip()\n"
                "print(json.dumps({'status': 'ok', 'reply': f'Handled: {goal}', 'artifact': None}))\n"
            ),
        )

        result = asyncio.run(
            skill_registry.execute_skill(
                skill_id="quote-helper",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                goal="Build quote for brake pads",
                agent_label="Sage",
                hard_context="",
                operational_policy="",
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reply"], "Handled: Build quote for brake pads")

    def test_registry_loads_workspace_mcp_skill_definition(self) -> None:
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
                    "description": "Look up inventory by sku.",
                    "action_class": "read",
                    "connector_scopes": ["inventory"],
                    "trigger_terms": ["lookup stock"],
                    "approved": True,
                }
            ],
            metadata={},
        )

        definition = skill_registry.get_skill_definition("mcp:inventory-feed:lookup_stock", workspace_id="workspace-1")

        self.assertIsNotNone(definition)
        self.assertEqual(definition.execution_adapter, "mcp_tool")
        self.assertEqual(definition.skill_class, "specialist_local")
        self.assertIn("mcp", definition.connector_scopes)
        self.assertIn("inventory", definition.connector_scopes)

    # ── Bundled skill execution bridge tests ──────────────────────────

    def test_execute_bundled_skill_with_executor(self) -> None:
        """A bundled skill with a built-in executor returns structured output."""
        result = asyncio.run(
            skill_registry.execute_skill(
                skill_id="memory-manager",
                tenant_id="default",
                workspace_id="workspace-1",
                goal="Show current memory facts",
                agent_label="Sage",
                hard_context="",
                operational_policy="",
            )
        )
        self.assertIsInstance(result, dict)
        self.assertIn(result.get("status"), ("ok", "manual"))
        self.assertIsNotNone(result.get("reply"))
        self.assertIn("steps", result)

    def test_execute_bundled_skill_via_adapter(self) -> None:
        """A bundled skill with an adapter executor returns structured output."""
        result = asyncio.run(
            skill_registry.execute_skill(
                skill_id="inventory-tool",
                tenant_id="default",
                workspace_id="workspace-1",
                goal="Check stock of brake pads",
                agent_label="Sage",
                hard_context="",
                operational_policy="",
            )
        )
        self.assertIsInstance(result, dict)
        self.assertIsNotNone(result.get("status"))
        self.assertIsNotNone(result.get("reply"))

    def test_execute_unknown_skill_returns_missing(self) -> None:
        """An unregistered skill returns a missing status."""
        result = asyncio.run(
            skill_registry.execute_skill(
                skill_id="nonexistent-skill-42",
                tenant_id="default",
                workspace_id="workspace-1",
                goal="Do something",
                agent_label="Sage",
                hard_context="",
                operational_policy="",
            )
        )
        self.assertEqual(result.get("status"), "missing")

    def test_execute_disabled_skill_returns_disabled(self) -> None:
        """A disabled skill returns a disabled status."""
        # Simulate: business-skill-template is disabled by default in bundled skills
        result = asyncio.run(
            skill_registry.execute_skill(
                skill_id="business-skill-template",
                tenant_id="default",
                workspace_id="workspace-1",
                goal="Use template",
                agent_label="Sage",
                hard_context="",
                operational_policy="",
            )
        )
        self.assertIsInstance(result, dict)
        # business-skill-template is disabled in SKILL.md → should be missing or disabled
        self.assertIn(result.get("status"), ("missing", "disabled"))

    def test_bundled_skill_definition_has_executor(self) -> None:
        """Every live bundled skill in _BUILT_IN_SKILLS has an executor."""
        for definition in skill_registry._BUILT_IN_SKILLS:
            if definition.execution_mode != "live":
                continue  # manual skills don't need executors
            with self.subTest(skill_id=definition.id):
                self.assertIsNotNone(
                    definition.executor,
                    f"Live skill '{definition.id}' must have an executor",
                )

    def test_bundled_skill_in_registry(self) -> None:
        """Bundled skills appear in the merged registry."""
        definition = skill_registry.get_skill_definition(
            "memory-manager", workspace_id="workspace-1"
        )
        self.assertIsNotNone(definition)
        self.assertEqual(definition.id, "memory-manager")
        self.assertIsNotNone(definition.executor)

    def test_enforcement_tool_name_maps_hyphenated_ids_to_llm_tool_names(self) -> None:
        """Skills with a real LLM tool-call equivalent resolve to that exact
        name, so Tools-tab toggles land on the id enforcement checks."""
        self.assertEqual(skill_registry.enforcement_tool_name("web-search"), "web__search")
        self.assertEqual(skill_registry.enforcement_tool_name("browser"), "browser__navigate")
        self.assertEqual(skill_registry.enforcement_tool_name("memory-manager"), "memory_update")
        self.assertEqual(skill_registry.enforcement_tool_name("code-runner"), "shell__exec")
        self.assertEqual(skill_registry.enforcement_tool_name("file-manager"), "file__read")
        self.assertEqual(skill_registry.enforcement_tool_name("telegram-bot"), "telegram_bot__send_message")
        self.assertEqual(skill_registry.enforcement_tool_name("fleet-create-agent"), "fleet__create_agent")
        self.assertEqual(skill_registry.enforcement_tool_name("fleet-list-agents"), "fleet__list_agents")
        self.assertEqual(skill_registry.enforcement_tool_name("fleet-get-agent-activity"), "fleet__get_agent_activity")
        self.assertEqual(skill_registry.enforcement_tool_name("fleet-configure-agent"), "fleet__configure_agent")
        # "fleet-message-agent" is deliberately absent: the whole
        # agent-to-agent messaging surface was removed (it was advertised on
        # three surfaces and always returned ok: false -- CLAUDE.md's "No dead
        # controls"). Its continued absence is asserted in
        # test_mcp_dead_and_credential_tools_removed.py; there is no mapping
        # here to check because there is no skill to enforce.
        self.assertEqual(skill_registry.enforcement_tool_name("memory-read"), "memory_read")
        self.assertEqual(skill_registry.enforcement_tool_name("memory-write"), "memory_write")

    def test_enforcement_tool_name_passes_through_unmapped_ids(self) -> None:
        """Skills with no live tool-calling equivalent (connector-scoped
        manual skills, keyword-dispatched skills) are returned unchanged —
        nothing enforces their id either way."""
        for skill_id in ("email-access", "calendar-access", "task-runner", "inventory-tool", "crm-notes", "vision-monitor", "memory-list"):
            self.assertEqual(skill_registry.enforcement_tool_name(skill_id), skill_id)

    def test_workspace_skill_with_handler_executes(self) -> None:
        """A workspace skill with handler.py executes via subprocess."""
        _write_skill(
            self.workspace_root,
            name="echo-skill",
            description="Echo test skill.",
            runtime={"execution_mode": "live", "execution_adapter": "handler"},
            handler_body=(
                "import sys, json\n"
                "payload = json.loads(sys.stdin.read())\n"
                "print(json.dumps({'reply': f\"Echo: {payload.get('goal', '')}\"}))\n"
            ),
        )
        result = asyncio.run(
            skill_registry.execute_skill(
                skill_id="echo-skill",
                tenant_id="default",
                workspace_id="workspace-1",
                goal="Hello world",
                agent_label="TestBot",
                hard_context="",
                operational_policy="",
            )
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "ok")
        self.assertIn("Echo: Hello world", str(result.get("reply") or ""))


if __name__ == "__main__":
    unittest.main()

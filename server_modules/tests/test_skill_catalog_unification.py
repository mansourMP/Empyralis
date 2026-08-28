"""Focused test module for docs/design/audit-skills.md's top-3 fix list:

1. One catalog — skill_registry.list_skill_definitions is the single source
   of truth; assistant_skills_api no longer carries a hardcoded fake pack; the 6
   bundled skills (skills/<id>/SKILL.md) are real files with real content.
2. skill_invoke — a real ToolDescriptor, dispatched in the live tool-call
   path (skills_service.execute_single_direct_tool_call AND the
   direct_chat_operator_binding_service closure that actually sits in front
   of it in the live turn loop), Level-2 body only reaches context on
   invoke.
3. skill_write — authors a new skill through the existing marketplace
   pipeline (skills_registry.install_marketplace_skill + skill_scanner),
   landing disabled/pending_owner_review rather than silently live.

Also covers progressive disclosure: a skill body never appears in the
Level-1 "## Callable Tools" prompt text, only in a skill_invoke result.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import installed_skills
from server_modules import rust_runtime_kernel_client
from server_modules import instruction_compiler_service
from server_modules import assistant_skills_api
from server_modules import skill_registry
from server_modules import skills_registry
from server_modules import skills_service


_BUNDLED_SKILL_IDS = (
    "memory-manager",
    "code-runner",
    "file-manager",
    "telegram-bot",
    "vision-monitor",
    "business-skill-template",
)


def _execution_callbacks() -> direct_tool_execution_service.DirectToolExecutionCallbacks:
    """Minimal-but-real callbacks bundle for skills_service.
    execute_single_direct_tool_call — parse_tool_name is the REAL function
    (not a stub), since this module's whole point is proving skill_invoke/
    skill_write actually round-trip through it."""
    return direct_tool_execution_service.DirectToolExecutionCallbacks(
        compact_step_detail=lambda value: " ".join(str(value or "").split()).strip() or None,
        titleize_direct_step_token=lambda value: " ".join(word.capitalize() for word in str(value or "").split("_")),
        run_async_tool_call=lambda coro: asyncio.run(coro),
        parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
        tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
        parse_json_object_loose=lambda value: {},
        safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
        normalize_reasoning_effort=lambda value: str(value or "").strip().lower() or None,
        build_direct_local_tool_config=skills_service.build_direct_local_tool_config,
        format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        build_direct_tool_config=lambda connector_id, action_id, tool_input: {
            "connector": connector_id,
            "action": action_id,
            "input": tool_input,
        },
        format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        llm_task=lambda *args, **kwargs: {"ok": True},
        web_search=lambda query: [],
        web_fetch=lambda url: f"Fetched {url}",
        search_memory_notebook=lambda *a, **k: [],
        get_memory_notebook_excerpt=lambda *a, **k: {},
    )


def _mock_runtime_state_store_decision(**request: object) -> dict:
    """conftest.py's autouse fixture mocks the LOWER-level run_runtime_kernel
    for non-@pytest.mark.kernel tests, returning ok=True/decision="allow"
    with no `next_action` — fine for callers that only check `ok`, but
    skills_registry._enforce_skills_registry_file_write and
    installed_skills._enforce_installed_skill_registry_state_decision both
    additionally require next_action == the specific operation requested
    (server_modules/skills_registry.py, server_modules/installed_skills.py),
    which the generic mock leaves blank and so unconditionally fails as
    'unexpected_next_action' — a pre-existing gap, not something this task's
    code introduced (server_modules/tests/test_skill_marketplace.py already
    has this exact failure mode on an unmodified checkout). Mirrors the
    higher-level mock test_skills_registry_rust_gate.py /
    test_installed_skills_rust_gate.py already use for exactly this reason."""
    operation = str(request.get("operation") or "").strip()
    return {
        "ok": True,
        "decision": "allow",
        "reason": "mock allow (skill catalog unification test fixture)",
        "operation": operation,
        "next_action": operation,
        "approval_required": False,
        "cacheable": False,
        "audit_visibility": "standard",
    }


class _SkillFixtureMixin:
    """Isolates skill_registry / installed_skills / skills_registry against
    a throwaway temp filesystem for every test in a subclass — the exact
    pattern server_modules/tests/test_skill_registry.py and
    test_skill_marketplace.py already use."""

    def _start_skill_roots(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace_root = self.root / "workspace"
        self.global_root = self.root / "global"
        self.bundled_root = self.root / "bundled"
        for path in (self.workspace_root, self.global_root, self.bundled_root):
            path.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.root / "marketplace" / "registry.json"
        self.patchers = [
            patch.object(installed_skills, "workspace_skills_root", return_value=self.workspace_root),
            patch.object(installed_skills, "global_skills_root", return_value=self.global_root),
            patch.object(installed_skills, "bundled_skills_root", return_value=self.bundled_root),
            patch.object(skills_registry, "bundled_skills_root", return_value=self.bundled_root),
            patch.object(skills_registry, "workspace_skills_root", return_value=self.workspace_root),
            patch.object(skills_registry, "MARKETPLACE_REGISTRY_FILE", self.registry_file),
            patch.object(skills_registry, "MARKETPLACE_PUBLISH_DIR", self.root / "marketplace" / "published"),
            patch.object(
                rust_runtime_kernel_client,
                "runtime_state_store_decision",
                side_effect=_mock_runtime_state_store_decision,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def _stop_skill_roots(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def _write_custom_skill(self, *, name: str, description: str, body: str) -> Path:
        skill_dir = self.workspace_root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
            encoding="utf-8",
        )
        return skill_dir


# ── 1. One catalog ───────────────────────────────────────────────────────


class BundledSkillFilesTests(unittest.TestCase):
    """Runs against the REAL repo skills/ directory (unpatched) — proves
    the 6 SKILL.md files docs/design/audit-skills.md §3 item 1 asked for
    actually exist on disk and load as real, described skills."""

    def test_six_bundled_skill_md_files_exist_on_disk(self) -> None:
        root = installed_skills.bundled_skills_root()
        for skill_id in _BUNDLED_SKILL_IDS:
            skill_md = root / skill_id / "SKILL.md"
            self.assertTrue(skill_md.exists(), f"expected {skill_md} to exist")
            text = skill_md.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), f"{skill_id} SKILL.md must start with frontmatter")
            self.assertIn(f"name: {skill_id}", text)
            self.assertIn("description:", text)

    def test_five_bundled_skills_are_enabled_and_real_in_the_catalog(self) -> None:
        definitions = {d.id: d for d in skill_registry.list_skill_definitions(workspace_id="ws-real-fs")}
        for skill_id in ("memory-manager", "code-runner", "file-manager", "telegram-bot", "vision-monitor"):
            self.assertIn(skill_id, definitions)
            definition = definitions[skill_id]
            self.assertTrue(definition.enabled)
            self.assertTrue(definition.available)
            self.assertEqual(definition.path, str(installed_skills.bundled_skills_root() / skill_id))
            self.assertIsNotNone(definition.executor, f"{skill_id} must keep its live executor")

    def test_business_skill_template_is_disabled_by_default(self) -> None:
        # Matches the pre-existing expectation in test_skill_registry.py's
        # test_execute_disabled_skill_returns_disabled — this is the
        # template/reference skill, never meant to run on its own.
        enabled_lookup = skill_registry.get_skill_definition(
            "business-skill-template", workspace_id="ws-real-fs", include_disabled=False
        )
        self.assertIsNone(enabled_lookup)
        disabled_lookup = skill_registry.get_skill_definition(
            "business-skill-template", workspace_id="ws-real-fs", include_disabled=True
        )
        self.assertIsNotNone(disabled_lookup)
        self.assertFalse(disabled_lookup.enabled)


class SkillCatalogUnificationTests(_SkillFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._start_skill_roots()

    def tearDown(self) -> None:
        self._stop_skill_roots()

    def test_no_hardcoded_curated_pack_survives_in_sage_skills_api(self) -> None:
        self.assertFalse(hasattr(assistant_skills_api, "_CURATED_SKILL_PACK"))
        self.assertFalse(hasattr(assistant_skills_api, "CuratedSkillDefinition"))

    def test_installed_skill_flows_through_skill_registry_into_sage_skills_api(self) -> None:
        """A skill dropped into the workspace skill root becomes visible
        through the SAME catalog (skill_registry.list_skill_definitions)
        both list_skill_definitions() and assistant_skills_api read — proving
        item 3's "Tools tab and the model's manifest describe the same set"
        requirement end-to-end, not just by code inspection."""
        self._write_custom_skill(
            name="acme-quote-builder",
            description="Builds a customer quote from the workspace price list.",
            body="# Acme Quote Builder\n\nProcedure: ...",
        )

        definitions = {d.id: d for d in skill_registry.list_skill_definitions(workspace_id="ws-1")}
        self.assertIn("acme-quote-builder", definitions)

        payload = assistant_skills_api._build_sage_skills_payload(workspace_id="ws-1", tenant_id="t-1")
        by_id = {item["id"]: item for item in payload["items"]}
        self.assertIn("acme-quote-builder", by_id)
        self.assertEqual(by_id["acme-quote-builder"]["status"], "ready")
        self.assertEqual(
            by_id["acme-quote-builder"]["description"],
            "Builds a customer quote from the workspace price list.",
        )

        capabilities = assistant_skills_api.build_sage_capabilities_payload(workspace_id="ws-1", tenant_id="t-1")
        skill_records = [item for item in capabilities["items"] if item.get("skill_id") == "acme-quote-builder"]
        self.assertEqual(len(skill_records), 1)
        # Every skill capability record dispatches through the ONE real
        # Level-2 tool — never a fabricated per-skill tool name.
        self.assertEqual(skill_records[0]["tool_id"], "skill_invoke")
        self.assertIn('skill_id="acme-quote-builder"', skill_records[0]["description"])


# ── 2. skill_invoke: list -> invoke -> body executed ────────────────────


class SkillInvokeDispatchTests(_SkillFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._start_skill_roots()

    def tearDown(self) -> None:
        self._stop_skill_roots()

    def test_skill_invoke_tool_descriptor_is_registered(self) -> None:
        descriptor = skills_service.tool_descriptor_for_name("skill_invoke")
        self.assertIsNotNone(descriptor)
        self.assertEqual(descriptor.connector_id, "skill")
        self.assertEqual(descriptor.action_id, "invoke")
        self.assertFalse(descriptor.audience_safe)
        self.assertIn("skill_id", descriptor.parameters["properties"])
        self.assertIn(descriptor.tool_name, skills_service.registered_direct_chat_tool_names_for_logging())

    def test_skill_invoke_reachable_via_the_tool_registry(self) -> None:
        """skill_invoke isn't always-on (token economy — same tier as
        fleet__*/browser__*/generate_image), but it must still be part of
        the lazily-discovered registry query_tool_registry searches — this
        is what makes it reachable at all from a live turn that doesn't
        already have it in its always-on schema."""
        from server_modules import tool_registry_service

        entries = tool_registry_service.build_registry_entries([], {})
        names = {entry.tool_name for entry in entries}
        self.assertIn("skill_invoke", names)
        self.assertIn("skill_write", names)
        results = tool_registry_service.search_tool_registry("invoke a skill by id", entries, max_results=5)
        self.assertTrue(any(r["function"]["name"] == "skill_invoke" for r in results))

    def test_parse_tool_name_recognizes_skill_invoke_and_skill_write(self) -> None:
        self.assertEqual(direct_chat_operator_binding_service.parse_tool_name("skill_invoke"), ("skill", "invoke"))
        self.assertEqual(direct_chat_operator_binding_service.parse_tool_name("skill_write"), ("skill", "write"))

    def test_execute_single_direct_tool_call_dispatches_skill_invoke_end_to_end(self) -> None:
        """The core deliverable: a skill's body is not in the base
        prompt (see ProgressiveDisclosureTests) but IS returned, verbatim,
        the moment skill_invoke actually calls skill_registry.execute_skill
        for it — proving Level-2 progressive disclosure fires on demand."""
        marker = "PROGRESSIVE-DISCLOSURE-BODY-MARKER-8f3c1a"
        self._write_custom_skill(
            name="quote-procedure",
            description="Builds a customer quote.",
            body=f"# Quote Procedure\n\n{marker}\n\nStep 1: look up the price list.",
        )
        with patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()):
            result = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "skill_invoke",
                    "arguments": {"skill_id": "quote-procedure", "args": {"goal": "build a quote"}},
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"authority_tier": "owner", "metadata": {"sage_agent_id": "sage"}},
                callbacks=_execution_callbacks(),
            )
        self.assertIsInstance(result, str)
        self.assertIn(marker, result)

    def test_skill_invoke_missing_skill_id_raises(self) -> None:
        with patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                skills_service.execute_single_direct_tool_call(
                    tool_call={"name": "skill_invoke", "arguments": {}},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={"authority_tier": "owner"},
                    callbacks=_execution_callbacks(),
                )

    def test_skill_invoke_unknown_skill_reports_missing_without_raising(self) -> None:
        with patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()):
            result = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "skill_invoke", "arguments": {"skill_id": "totally-not-a-real-skill-42"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"authority_tier": "owner"},
                callbacks=_execution_callbacks(),
            )
        self.assertIn("not registered", result.lower())

    def test_live_dispatch_wiring_through_direct_chat_operator_binding_service(self) -> None:
        """This is the ACTUAL chokepoint _run_sage_action_loop_v3 /
        stream_provider_backed_direct_chat call in production
        (direct_chat_operator_binding_service.build_direct_chat_tool_runtime_bindings's
        execute_single_direct_tool_call closure) — not just
        skills_service.execute_single_direct_tool_call in isolation. Before
        this task, "skill" was not in that closure's connector allowlist, so
        a skill_invoke call would have been silently misrouted to
        runs_execution._workflow_execute_connector_action instead of ever
        reaching skill_registry.execute_skill."""
        marker = "LIVE-WIRING-MARKER-2b91"
        self._write_custom_skill(
            name="live-wiring-check",
            description="Checks the live dispatch chokepoint.",
            body=f"# Live Wiring Check\n\n{marker}",
        )
        bindings = direct_chat_operator_binding_service.build_direct_chat_tool_runtime_bindings(
            direct_chat_runtime_facade_callbacks=lambda: object(),
            direct_tool_execution_callbacks=_execution_callbacks,
            execute_single_direct_tool_call_fn=lambda **kwargs: "unused",
        )
        with patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()):
            result = bindings.execute_single_direct_tool_call(
                tool_call={"name": "skill_invoke", "arguments": {"skill_id": "live-wiring-check"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"authority_tier": "owner"},
            )
        self.assertIn(marker, result)


# ── 3. skill_write: creates a pending skill ──────────────────────────────


class SkillWriteAuthoringTests(_SkillFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._start_skill_roots()

    def tearDown(self) -> None:
        self._stop_skill_roots()

    def test_author_pending_skill_installs_disabled_for_review(self) -> None:
        result = skills_registry.author_pending_skill(
            name="Weekly Report Builder",
            description="Compiles the weekly status report from ledger activity.",
            body="# Weekly Report Builder\n\nStep 1: pull last 7 days of activity.\nStep 2: summarize.",
            author="agent:sage-main",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["skill_id"], "weekly-report-builder")
        self.assertEqual(result["status"], "pending_owner_review")
        self.assertFalse(result["enabled"])

        # Written to disk and security-scanned for real.
        installed_dir = self.workspace_root / "weekly-report-builder"
        self.assertTrue((installed_dir / "SKILL.md").exists())

        # But NOT live: excluded from the default (enabled-only) catalog...
        self.assertIsNone(
            skill_registry.get_skill_definition("weekly-report-builder", workspace_id="ws-1", include_disabled=False)
        )
        # ...while still inspectable for owner review.
        pending = skill_registry.get_skill_definition(
            "weekly-report-builder", workspace_id="ws-1", include_disabled=True
        )
        self.assertIsNotNone(pending)
        self.assertFalse(pending.enabled)

        registry_entry = installed_skills.get_installed_skill_registry_entry("weekly-report-builder")
        self.assertEqual(registry_entry.get("review_status"), "pending_owner_review")
        self.assertEqual(registry_entry.get("authored_by"), "agent:sage-main")

    def test_author_pending_skill_requires_name_description_and_body(self) -> None:
        with self.assertRaises(ValueError):
            skills_registry.author_pending_skill(name="", description="x", body="y")
        with self.assertRaises(ValueError):
            skills_registry.author_pending_skill(name="x", description="", body="y")
        with self.assertRaises(ValueError):
            skills_registry.author_pending_skill(name="x", description="y", body="")

    def test_execute_single_direct_tool_call_dispatches_skill_write(self) -> None:
        with patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()):
            raw = skills_service.execute_single_direct_tool_call(
                tool_call={
                    "name": "skill_write",
                    "arguments": {
                        "name": "Invoice Chaser",
                        "description": "Follows up on overdue invoices via email.",
                        "body": "# Invoice Chaser\n\nStep 1: list overdue invoices.\nStep 2: draft a follow-up email.",
                    },
                },
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"authority_tier": "owner", "metadata": {"sage_agent_id": "sage"}},
                callbacks=_execution_callbacks(),
            )
        parsed = json.loads(raw)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["skill_id"], "invoice-chaser")
        self.assertEqual(parsed["status"], "pending_owner_review")
        self.assertFalse(parsed["enabled"])
        self.assertIsNone(
            skill_registry.get_skill_definition("invoice-chaser", workspace_id="ws-1", include_disabled=False)
        )

    def test_skill_write_requires_body(self) -> None:
        with patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                skills_service.execute_single_direct_tool_call(
                    tool_call={
                        "name": "skill_write",
                        "arguments": {"name": "No Body Skill", "description": "Missing a body."},
                    },
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={"authority_tier": "owner"},
                    callbacks=_execution_callbacks(),
                )


# ── 4. Progressive disclosure: bodies never in the base prompt ──────────


class ProgressiveDisclosureTests(_SkillFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._start_skill_roots()

    def tearDown(self) -> None:
        self._stop_skill_roots()

    def test_capability_manifest_text_never_includes_a_skill_body(self) -> None:
        marker = "SHOULD-NEVER-LEAK-INTO-LEVEL-1-9d2e"
        self._write_custom_skill(
            name="leak-check-skill",
            description="A skill used only to check progressive disclosure.",
            body=f"# Leak Check Skill\n\n{marker}\n\nA very long procedure body follows..." + ("x" * 2000),
        )
        capabilities = assistant_skills_api.build_sage_capabilities_payload(workspace_id="ws-1", tenant_id="t-1")
        manifest = instruction_compiler_service.build_model_capability_manifest(capabilities)
        text = instruction_compiler_service._capability_manifest_text(manifest)

        # The Level-1 hint (name/description/skill_id) must be present...
        self.assertIn("leak-check-skill", text)
        self.assertIn("A skill used only to check progressive disclosure.", text)
        # ...but the Level-2 body content must not leak into it.
        self.assertNotIn(marker, text)
        self.assertNotIn("x" * 2000, text)

    def test_full_instruction_bundle_excludes_skill_bodies_but_hints_skill_invoke(self) -> None:
        marker = "FULL-BUNDLE-BODY-MARKER-71ae"
        self._write_custom_skill(
            name="bundle-leak-check",
            description="Checks the full instruction bundle for body leakage.",
            body=f"# Bundle Leak Check\n\n{marker}",
        )
        capability_payload = assistant_skills_api.build_sage_capabilities_payload(workspace_id="ws-1", tenant_id="t-1")
        bundle = instruction_compiler_service.build_sage_instruction_bundle(
            workspace_id="ws-1",
            message="what skills do you have?",
            capability_payload=capability_payload,
        )
        self.assertNotIn(marker, bundle.system_prompt)
        self.assertIn("skill_invoke", bundle.system_prompt)

    def test_skill_invoke_returns_the_body_that_the_prompt_withheld(self) -> None:
        """Same skill as the first test in this class, verified end-to-end:
        absent from Level 1, present only once actually invoked."""
        marker = "SHOULD-NEVER-LEAK-INTO-LEVEL-1-9d2e"
        self._write_custom_skill(
            name="leak-check-skill",
            description="A skill used only to check progressive disclosure.",
            body=f"# Leak Check Skill\n\n{marker}",
        )
        with patch("server_modules.activity_ledger_service.append_execution_activity", new=AsyncMock()):
            result = skills_service.execute_single_direct_tool_call(
                tool_call={"name": "skill_invoke", "arguments": {"skill_id": "leak-check-skill"}},
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"authority_tier": "owner"},
                callbacks=_execution_callbacks(),
            )
        self.assertIn(marker, result)


if __name__ == "__main__":
    unittest.main()

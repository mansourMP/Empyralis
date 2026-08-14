"""Phase 4B — acting-agent threaded into the tool executor.

Proves the four guarantees of threading agent_install_id through the Sage
action loop into the tool-execution context:

  1. A specialist's toolset = core tools + the always-on judgment tools with
     no real integration behind them + exactly its bound connectors (Phase 2
     bindings) — there is no more per-tool enable/disable checklist
     (2026-08-14, CLAUDE.md, founder decision). Sage (no acting install) is
     unrestricted.
  2. A mid-turn memory tool WRITE lands in the acting install's namespace,
     both directions; Sage writes under the default (None) namespace.
  3. A specialist calling a tool it isn't bound to gets a clean denial that
     emits an escalation event — not a crash.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import sage_agent_runtime_service as sage
from server_modules import direct_tool_execution_service as dtx
from server_modules import skills_service
from server_modules.direct_chat_operator_binding_service import parse_tool_name as _parse_tool_name


def _noop(*args, **kwargs):
    return None


def _make_callbacks(**overrides) -> dtx.DirectToolExecutionCallbacks:
    """Minimal real-enough callbacks. The executor paths under test only exercise
    parse_tool_name, tool_arguments_payload, and memory_write_file; the rest are
    inert stubs (never reached by a denied call or a memory write)."""
    base = dict(
        compact_step_detail=lambda v: None,
        titleize_direct_step_token=lambda v: str(v or ""),
        run_async_tool_call=lambda coro: coro,
        parse_tool_name=_parse_tool_name,
        tool_arguments_payload=lambda a: dict(a) if isinstance(a, dict) else {},
        parse_json_object_loose=lambda s: {},
        safe_positive_int=lambda v, d: d,
        normalize_reasoning_effort=lambda s: None,
        build_direct_local_tool_config=lambda c, a, args: ("", {}),
        format_direct_local_tool_result=lambda r: str(r),
        build_direct_tool_config=lambda c, a, i: {},
        format_direct_tool_result=lambda r: str(r),
        llm_task=_noop,
        web_search=lambda q: [],
        web_fetch=lambda u: "",
        search_memory_notebook=_noop,
        get_memory_notebook_excerpt=_noop,
    )
    base.update(overrides)
    return dtx.DirectToolExecutionCallbacks(**base)


# ── 1. Per-install tool whitelist ───────────────────────────────────────────


class SpecialistToolsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_toolset_resolves_from_phase2_bindings(self):
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[{"key": "slack"}]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={"tool_toggles": {"custom_tool": True, "off_tool": False}}),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        self.assertEqual(ts["connectors"], {"slack"})
        self.assertIn("custom_tool", ts["tools"])
        self.assertNotIn("off_tool", ts["tools"])
        self.assertIn("memory_write", ts["core"])  # core always present

        # allowed logic: core, bound connector, explicit toggle — yes; unbound — no
        self.assertTrue(sage._specialist_tool_allowed("memory_write", ts))
        self.assertTrue(sage._specialist_tool_allowed("slack__post", ts))
        self.assertTrue(sage._specialist_tool_allowed("custom_tool", ts))
        self.assertFalse(sage._specialist_tool_allowed("discord_bot__send", ts))

    async def test_toolset_fail_safe_is_core_only(self):
        # If the binding lookups blow up, the specialist is restricted to
        # core + the always-on judgment tools (2026-08-14: neither depends
        # on external data, so neither needs to fail safe) — anything that
        # DOES depend on external data (a real connector binding) still
        # fails safe to denied.
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        self.assertEqual(ts["connectors"], set())
        self.assertEqual(ts["tools"], sage._UNGATED_JUDGMENT_TOOL_NAMES)
        self.assertFalse(sage._specialist_tool_allowed("slack__post", ts))
        self.assertTrue(sage._specialist_tool_allowed("memory_write", ts))  # core survives
        # No-integration judgment tools survive a total lookup failure too —
        # they never depended on the failed lookups in the first place.
        self.assertTrue(sage._specialist_tool_allowed("shell__exec", ts))
        self.assertTrue(sage._specialist_tool_allowed("browser__navigate", ts))
        # google_workspace-gated tools DO depend on the (failed) connector
        # lookup, so they fail safe to denied, same as any other connector
        # tool.
        self.assertFalse(sage._specialist_tool_allowed("email-access", ts))

    async def test_no_per_agent_tools_checklist_judgment_tools_always_allowed(self):
        """2026-08-14 (CLAUDE.md, founder decision): shell/browser/file/
        memory-manager/inventory have no per-agent enable switch left at
        all — a specialist gets them regardless of tool_toggles, including
        an explicit False (there is no more "disable this" for these five)."""
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={"tool_toggles": {
                    "shell__exec": False, "browser__navigate": False,
                    "file__read": False, "memory_update": False,
                    "inventory-tool": False,
                }}),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        for name in sage._UNGATED_JUDGMENT_TOOL_NAMES:
            self.assertTrue(sage._specialist_tool_allowed(name, ts), name)

    async def test_google_workspace_gated_tools_follow_the_real_connector_binding(self):
        """email-access/calendar-access/task-runner/crm-notes have no "__"
        in their enforcement id, so the generic connector-prefix check can't
        reach them — _resolve_specialist_toolset grants them explicitly, on
        the SAME connectors set every other connector tool uses. No toggle
        involved either way."""
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[{"key": "google_workspace"}]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={"tool_toggles": {}}),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        for name in ("email-access", "calendar-access", "task-runner", "crm-notes"):
            self.assertTrue(sage._specialist_tool_allowed(name, ts), name)

    async def test_google_workspace_gated_tools_denied_without_the_connector(self):
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),  # no google_workspace binding
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={"tool_toggles": {
                    # An explicit True no longer matters — only a real
                    # binding does.
                    "email-access": True,
                }}),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        self.assertFalse(sage._specialist_tool_allowed("email-access", ts))

    async def test_core_tool_explicit_false_no_longer_disables_it(self):
        """Pre-2026-08-14 an explicit False in tool_toggles for a core tool
        (Web Search, Memory read/write/update) was authoritative and denied
        it. That switch is gone — the value is now ignored entirely."""
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={"tool_toggles": {"web__search": False, "memory_write": False}}),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        self.assertTrue(sage._specialist_tool_allowed("web__search", ts))
        self.assertTrue(sage._specialist_tool_allowed("memory_write", ts))

    async def test_sage_path_returns_none(self):
        self.assertIsNone(
            await sage._resolve_specialist_toolset(workspace_id="ws-1", tenant_id="t1", agent_install_id="")
        )

    def test_registry_filter_keeps_only_bound_connectors(self):
        class _Entry:
            def __init__(self, name, connector):
                self.tool_name = name
                self.connector_id = connector

        registry = [
            _Entry("slack__post", "slack"),
            _Entry("discord_bot__send", "discord_bot"),
            _Entry("notion__create", "notion"),
        ]
        ts = {"core": {"memory_write"}, "connectors": {"slack"}, "tools": set()}
        kept = sage._filter_registry_for_specialist(registry, ts)
        self.assertEqual([e.tool_name for e in kept], ["slack__post"])


# ── fix/agent-task-tools-on-sdk-engine: project_task__* is intrinsic to
# project membership, not a bindable connector (it appears nowhere in
# runtime_config.CONNECTOR_CATALOG, has no ConnectorPicker entry, and
# nothing ever writes an agent_connector_bindings row for it) — the
# connector-membership scheme every other tool above is gated on has no
# path to ever grant it. These tests prove project_id (resolved from the
# SAME workspace_agent_installs bundle already fetched for tool_toggles/
# subagents_enabled — zero extra query) is the grant instead, at every
# layer: toolset resolution, the prompt-time allow-checks, AND the
# execution-time specialist_guard (a third, independent gate — see
# UnboundToolDenialTests below).


class ProjectTaskGrantTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_specialist_toolset_reads_project_id_from_the_bundle(self):
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={"tool_toggles": {}, "project_id": "proj-42"}),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        self.assertEqual(ts["project_id"], "proj-42")

    async def test_resolve_specialist_toolset_project_id_empty_when_bundle_has_none(self):
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value={"tool_toggles": {}}),  # no project_id key at all
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        self.assertEqual(ts["project_id"], "")

    async def test_resolve_specialist_toolset_project_id_fails_safe_on_lookup_error(self):
        # Same "deny-more, never allow-more" convention as every other field.
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(
                workspace_id="ws-1", tenant_id="t1", agent_install_id="install-support"
            )
        self.assertEqual(ts.get("project_id"), "")

    def test_specialist_tool_allowed_grants_project_task_by_membership_not_connector(self):
        ts_member = {
            "core": set(), "tools": set(), "connectors": set(),  # "project_task" never bound
            "raw_tool_toggles": {}, "capability_providers": frozenset(), "project_id": "proj-1",
        }
        self.assertTrue(sage._specialist_tool_allowed("project_task__list", ts_member))
        self.assertTrue(sage._specialist_tool_allowed("project_task__create", ts_member))

    def test_specialist_tool_allowed_denies_project_task_with_no_project(self):
        ts_no_project = {
            "core": set(), "tools": set(), "connectors": set(),
            "raw_tool_toggles": {}, "capability_providers": frozenset(), "project_id": "",
        }
        self.assertFalse(sage._specialist_tool_allowed("project_task__list", ts_no_project))

    def test_registry_filter_grants_project_task_by_membership(self):
        class _Entry:
            def __init__(self, name, connector):
                self.tool_name = name
                self.connector_id = connector

        registry = [_Entry("project_task__list", "project_task"), _Entry("slack__post", "slack")]
        ts_member = {"core": set(), "tools": set(), "connectors": set(), "project_id": "proj-1"}
        kept = sage._filter_registry_for_specialist(registry, ts_member)
        self.assertEqual([e.tool_name for e in kept], ["project_task__list"])

        ts_no_project = {"core": set(), "tools": set(), "connectors": set(), "project_id": ""}
        kept_none = sage._filter_registry_for_specialist(registry, ts_no_project)
        self.assertEqual(kept_none, [])


# ── Capability-gated tools (image_generation today) ─────────────────────────
# generate_image is decided SOLELY by whether its capability resolved a
# working provider for this agent — no tool_toggles/connector check applies
# to it at all (see agent_capability_service.py's "no separate enable
# toggle" contract, and skills_service.py's generate_image ToolDescriptor,
# which now carries capability_id="image_generation").


class CapabilityGatedToolsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_specialist_toolset_includes_capability_providers(self):
        bundle = {
            "tool_toggles": {},
            "install_metadata": {
                "capability_config": {"image_generation": {"mode": "byok_api", "provider": "openai"}},
                "capability_secrets": {},  # no key stored -> won't resolve
            },
        }
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=bundle),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(workspace_id="ws-1", tenant_id="t1", agent_install_id="install-x")
        self.assertIn("capability_providers", ts)
        # byok_api configured but no key stored -> does not resolve.
        self.assertNotIn("image_generation", ts["capability_providers"])

    async def test_generate_image_hidden_when_capability_toggle_is_on_but_provider_unresolved(self):
        """The tool_toggles switch is now IRRELEVANT to generate_image — even
        an explicit True does nothing without a resolved provider. Proves
        "no separate enable toggle" isn't just a slogan."""
        bundle = {
            "tool_toggles": {"generate_image": True},
            "install_metadata": {},
        }
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=bundle),
            ),
            patch.dict("os.environ", {}, clear=False),
        ):
            import os
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("STABILITY_API_KEY", None)
            ts = await sage._resolve_specialist_toolset(workspace_id="ws-1", tenant_id="t1", agent_install_id="install-x")
        self.assertFalse(sage._specialist_tool_allowed("generate_image", ts))

    async def test_generate_image_visible_when_capability_resolves_with_no_toggle_at_all(self):
        """The flip side: a resolved provider makes the tool available even
        though tool_toggles never mentions it — no separate toggle needed."""
        bundle = {
            "tool_toggles": {},  # generate_image never toggled on
            "install_metadata": {},
        }
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=bundle),
            ),
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-platform"}, clear=False),
            patch(
                "server_modules.entitlements_service.hosted_sage_ai_access_state_for_workspace_id",
                return_value={"allowed": True},
            ),
        ):
            ts = await sage._resolve_specialist_toolset(workspace_id="ws-1", tenant_id="t1", agent_install_id="install-x")
        self.assertIn("image_generation", ts["capability_providers"])
        self.assertTrue(sage._specialist_tool_allowed("generate_image", ts))

    def test_capability_gate_for_tool_identifies_generate_image_only(self):
        self.assertEqual(sage._capability_gate_for_tool("generate_image"), "image_generation")
        self.assertEqual(sage._capability_gate_for_tool("web__search"), "")
        self.assertEqual(sage._capability_gate_for_tool("http_request"), "")  # capability_id set, but not tool-gated
        self.assertEqual(sage._capability_gate_for_tool(""), "")

    def test_specialist_tool_allowed_ignores_toggles_and_connectors_for_capability_gated_tools(self):
        # Even with generate_image explicitly toggled on AND its
        # "connector" bound, an unresolved capability still blocks it —
        # capability resolution is the ONLY signal consulted.
        ts = {
            "core": set(), "tools": {"generate_image"}, "connectors": {"image"},
            "raw_tool_toggles": {"generate_image": True}, "capability_providers": frozenset(),
        }
        self.assertFalse(sage._specialist_tool_allowed("generate_image", ts))

    def test_registry_filter_gates_generate_image_by_capability_not_bindings(self):
        class _Entry:
            def __init__(self, name, connector):
                self.tool_name = name
                self.connector_id = connector

        registry = [_Entry("generate_image", "image"), _Entry("slack__post", "slack")]
        ts_unresolved = {"core": set(), "tools": set(), "connectors": {"image", "slack"}, "capability_providers": frozenset()}
        kept_unresolved = sage._filter_registry_for_specialist(registry, ts_unresolved)
        # "image" connector bound is irrelevant — generate_image needs a
        # resolved capability, not a connector binding.
        self.assertEqual([e.tool_name for e in kept_unresolved], ["slack__post"])

        ts_resolved = {"core": set(), "tools": set(), "connectors": set(), "capability_providers": frozenset({"image_generation"})}
        kept_resolved = sage._filter_registry_for_specialist(registry, ts_resolved)
        self.assertEqual([e.tool_name for e in kept_resolved], ["generate_image"])

    async def test_toolset_fail_safe_leaves_capability_providers_empty(self):
        """Extends test_toolset_fail_safe_is_core_only above: when the bundle
        lookup blows up, capability-gated tools must fail closed too, not
        just core/connector/tools."""
        with (
            patch(
                "server_modules.agent_bindings_repository.list_agent_connector_bindings",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            ts = await sage._resolve_specialist_toolset(workspace_id="ws-1", tenant_id="t1", agent_install_id="install-x")
        self.assertEqual(ts.get("capability_providers"), frozenset())
        self.assertFalse(sage._specialist_tool_allowed("generate_image", ts))


# ── 2. Mid-turn memory-write namespace isolation ────────────────────────────


class MidTurnMemoryIsolationTests(unittest.TestCase):
    """The Phase 4 isolation proof, extended to a write DONE BY A TOOL CALL."""

    def _write_via_tool_call(self, active_install_id: str):
        captured: dict = {}

        def _spy_memory_write_file(workspace_id, filename, content, *, mode="replace",
                                   agent_install_id=None, actor=None, **kwargs):
            captured["namespace"] = agent_install_id
            captured["workspace_id"] = workspace_id
            captured["content"] = content
            return {"filename": filename, "workspace_id": workspace_id, "new_hash": "hash"}

        callbacks = _make_callbacks(memory_write_file=_spy_memory_write_file)
        session_ctx = {"workspace_id": "default", "tenant_id": "t1", "authority_tier": "owner"}
        if active_install_id:
            session_ctx["active_agent_install_id"] = active_install_id
        skills_service.execute_single_direct_tool_call(
            tool_call={"name": "memory_write", "arguments": {"file": "NOTES.md", "content": "hello"}},
            workspace_id="default", thread_id="thr-1", index=1,
            provider="p", model="m", credentials={}, reasoning_effort="",
            session_ctx=session_ctx, callbacks=callbacks,
        )
        return captured

    def test_write_lands_in_acting_install_both_directions(self):
        a = self._write_via_tool_call("install-a")
        self.assertEqual(a["namespace"], "install-a")

        b = self._write_via_tool_call("install-b")
        self.assertEqual(b["namespace"], "install-b")

        # No acting install (Sage/master) → default (None) namespace, never a
        # specialist's. This is the byte-for-byte-unchanged path.
        sage_write = self._write_via_tool_call("")
        self.assertIsNone(sage_write["namespace"])


# ── 3. Unbound tool → clean denial + escalation event ───────────────────────


class UnboundToolDenialTests(unittest.TestCase):
    def _run(self, tool_name: str, session_ctx: dict):
        return dtx.execute_single_direct_tool_call(
            tool_call={"name": tool_name, "arguments": {}},
            workspace_id="default", thread_id="thr-1", index=1,
            provider="p", model="m", credentials={}, reasoning_effort="",
            session_ctx=session_ctx, callbacks=_make_callbacks(),
        )

    def test_unbound_tool_denied_and_escalated(self):
        events: list[dict] = []
        session_ctx = {
            "workspace_id": "default",
            "tenant_id": "t1",
            "active_agent_install_id": "install-support",
            "specialist_guard": {
                "agent_install_id": "install-support",
                "core": ["memory_write", "web__search"],
                "connectors": ["slack"],
                "tools": [],
            },
        }
        with patch.object(
            dtx.security_audit_service, "emit_security_audit_event",
            side_effect=lambda **kw: events.append(kw),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self._run("discord_bot__send", session_ctx)

        self.assertIn("not enabled for this specialist", str(ctx.exception))
        denied = [e for e in events if e.get("action") == "specialist.tool_denied"]
        self.assertEqual(len(denied), 1, "exactly one escalation event")
        self.assertTrue(denied[0]["metadata"]["escalation"])
        self.assertEqual(denied[0]["metadata"]["acting_agent_install_id"], "install-support")
        self.assertEqual(denied[0]["metadata"]["tool_name"], "discord_bot__send")
        self.assertEqual(denied[0]["status"], "escalated")

    def test_bound_connector_tool_passes_the_guard(self):
        # A bound connector tool must NOT be denied by the specialist guard.
        # (It may fail later at the broker/execution, but never with the guard
        # message — that is what we assert.)
        session_ctx = {
            "workspace_id": "default",
            "tenant_id": "t1",
            "active_agent_install_id": "install-support",
            "specialist_guard": {
                "agent_install_id": "install-support",
                "core": ["memory_write"],
                "connectors": ["slack"],
                "tools": [],
            },
        }
        with patch.object(dtx.security_audit_service, "emit_security_audit_event", side_effect=lambda **kw: None):
            try:
                self._run("slack__post", session_ctx)
            except Exception as exc:  # noqa: BLE001
                self.assertNotIn("not enabled for this specialist", str(exc))

    def test_no_guard_means_no_specialist_denial(self):
        # Sage / master path: no specialist_guard → the guard is inert.
        session_ctx = {"workspace_id": "default", "tenant_id": "t1"}
        with patch.object(dtx.security_audit_service, "emit_security_audit_event", side_effect=lambda **kw: None):
            try:
                self._run("discord_bot__send", session_ctx)
            except Exception as exc:  # noqa: BLE001
                self.assertNotIn("not enabled for this specialist", str(exc))

    # ── fix/agent-task-tools-on-sdk-engine: THIRD, independent gate ────────
    # _direct_tool_bundle (Tier 1) and _specialist_tool_allowed /
    # _filter_registry_for_specialist (Tier 2 registry) are prompt-time
    # concerns -- what the model SEES. This guard is the runtime-side
    # backstop that actually executes the call, and it re-checks
    # independently (never just trusts the prompt-time list) -- so
    # project_task__* needs the SAME project-membership grant here too, or
    # a project-member specialist would see the tool, call it, and still
    # get denied with "not enabled for this specialist agent".

    def test_project_member_specialist_passes_the_guard_for_project_task(self):
        session_ctx = {
            "workspace_id": "default",
            "tenant_id": "t1",
            "active_agent_install_id": "install-support",
            "specialist_guard": {
                "agent_install_id": "install-support",
                "core": ["memory_write"],
                "connectors": [],  # "project_task" never bound -- no path to bind it
                "tools": [],
                "project_id": "proj-1",
            },
        }
        with patch.object(dtx.security_audit_service, "emit_security_audit_event", side_effect=lambda **kw: None):
            try:
                self._run("project_task__list", session_ctx)
            except Exception as exc:  # noqa: BLE001
                self.assertNotIn("not enabled for this specialist", str(exc))

    def test_non_member_specialist_denied_for_project_task(self):
        events: list[dict] = []
        session_ctx = {
            "workspace_id": "default",
            "tenant_id": "t1",
            "active_agent_install_id": "install-support",
            "specialist_guard": {
                "agent_install_id": "install-support",
                "core": ["memory_write"],
                "connectors": [],
                "tools": [],
                "project_id": "",  # no project -- fail-safe default
            },
        }
        with patch.object(
            dtx.security_audit_service, "emit_security_audit_event",
            side_effect=lambda **kw: events.append(kw),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self._run("project_task__list", session_ctx)
        self.assertIn("not enabled for this specialist", str(ctx.exception))
        denied = [e for e in events if e.get("action") == "specialist.tool_denied"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["metadata"]["tool_name"], "project_task__list")

    def test_guard_missing_project_id_key_denies_project_task(self):
        # Back-compat / fail-safe: a specialist_guard built before this fix
        # (no "project_id" key at all) must still deny, not silently allow.
        session_ctx = {
            "workspace_id": "default",
            "tenant_id": "t1",
            "active_agent_install_id": "install-support",
            "specialist_guard": {
                "agent_install_id": "install-support",
                "core": ["memory_write"],
                "connectors": [],
                "tools": [],
            },
        }
        with patch.object(dtx.security_audit_service, "emit_security_audit_event", side_effect=lambda **kw: None):
            with self.assertRaises(RuntimeError) as ctx:
                self._run("project_task__list", session_ctx)
        self.assertIn("not enabled for this specialist", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

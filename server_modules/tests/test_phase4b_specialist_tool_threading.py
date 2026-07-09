"""Phase 4B — acting-agent threaded into the tool executor.

Proves the four guarantees of threading agent_install_id through the Sage
action loop into the tool-execution context:

  1. A specialist's toolset = core tools + exactly its bound connectors/tools
     (Phase 2 bindings); Sage (no acting install) is unrestricted.
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
        # If the binding lookups blow up, the specialist is restricted to core.
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
        self.assertEqual(ts["tools"], set())
        self.assertFalse(sage._specialist_tool_allowed("slack__post", ts))
        self.assertTrue(sage._specialist_tool_allowed("memory_write", ts))  # core survives

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


if __name__ == "__main__":
    unittest.main()

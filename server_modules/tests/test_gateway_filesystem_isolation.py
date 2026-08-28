"""Proves the Gateway filesystem/shell cross-agent isolation fix.

Background (see docs/design/memory-placement-scope.md's "gateway seam"
section): GatewayShellRuntime.filesystem.
read_write and shell.execute (empyralis-gateway/src/shell/runtime.ts) both
key their on-box directory off (mount, workspace_id) alone
(`mounts/<mount>/<workspace_id>/`). Neither dimension was ever derived from
`agent_install_id`, so any two agent installs sharing a workspace + Gateway
box landed on the identical directory through the model-facing file/shell
connectors — the same leak class Part 27.8 rated CRITICAL for connector
credentials, here for on-box files instead.

The fix has three layers, tested here as three layers:

  1. gateway_adapter.execute_gateway_action / _resolve_file_mount_for_gateway_action
     / _agent_scoped_mount — folds a server-supplied agent_install_id into
     the final mount name for BOTH filesystem.* and shell.execute, and
     never trusts a caller-supplied `mount` argument once an identity is
     present. This is the actual isolation boundary.
  2. hardware_action_broker_service.execute_hardware_action — threads the
     new agent_install_id kwarg through to layer 1 unchanged.
  3. skills_service.execute_single_direct_tool_call_async (the model-facing
     `file`/`shell` connector dispatch) — resolves agent_install_id from
     verified session identity ONLY (never from the tool call's own
     arguments) and threads it through unchanged.

NOTE on "fail closed": commit 8cc8d69dd's per-agent memory tool executors
error outright when no agent identity resolves. This fix deliberately does
NOT copy that for the file/shell connector: agent_turn_runtime_service.py's
_run_sage_action_loop_v3 (3 call sites, each commented "empty for Sage")
proves the owner-facing agent's OWN turn — the primary, highest-volume
caller of this exact connector when a box is paired — never has
active_agent_install_id/agent_install_id set in session_ctx at all. A hard
fail on empty identity would break Sage's own file/shell tool use outright,
not just a specialist edge case. Empty is instead treated the same way
memory already treats it: a stable,
server-controlled signal for "this is the owner-facing agent's own turn"
(never model-forgeable), which keeps today's existing, workspace-level
mount — while any SPECIALIST identity, which agent_turn_runtime_service.py
always stamps as a real, non-empty, server-controlled id when one is
active, gets properly isolated. That's the property these tests prove.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import hardware_action_broker_service as broker
from server_modules import agent_turn_runtime_service
from server_modules import skills_service
from server_modules import direct_tool_execution_service
from server_modules import direct_chat_operator_binding_service
from server_modules.hardware_runtime_adapters import gateway_adapter
from server_modules.specialist_runtime_context import SpecialistRuntimeContext


def _run(coro):
    return asyncio.run(coro)


def _registration(**overrides) -> dict:
    base = {
        "gateway_id": "gw-1",
        "device_id": "device-1",
        "workspace_id": "ws-1",
        "status": "active",
        "device_trust_state": "trusted",
    }
    base.update(overrides)
    return base


def _runtime_session() -> dict:
    return {
        "session_id": "session-1",
        "state": "ready",
        "canonical_runtime_target": "user_device_gateway",
    }


# ─────────────────────────────────────────────────────────────────────────
# Layer 1: gateway_adapter.execute_gateway_action — the actual mount/root
# derivation. Everything below execute_tool_via_gateway runs for real
# (file_mount_security grant checks, traversal rejection, agent scoping);
# only the Gateway WSS round trip itself is mocked, mirroring
# test_hardware_gateway_adapter.py's own
# test_execute_gateway_action_defaults_to_agent_computer_timeout pattern.
# ─────────────────────────────────────────────────────────────────────────


class GatewayAdapterMountIsolationTests(unittest.TestCase):
    async def _dispatch(
        self,
        *,
        capability_id: str,
        arguments: dict,
        agent_install_id: str | None = None,
        file_mount_grants=None,
    ):
        execute_mock = AsyncMock(
            return_value={
                "request_id": "req-1",
                "capability_id": capability_id,
                "run_id": "run-1",
                "result": {"summary": "ok", "path": arguments.get("path"), "mode": arguments.get("mode")},
            }
        )
        with (
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.find_gateway_registration",
                return_value=_registration(),
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.registration_is_usable",
                return_value=(True, ""),
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_execution_service.gateway_registration_execution_readiness",
                return_value=(True, ""),
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_runtime_session_service.update_runtime_session",
                side_effect=lambda session, **kwargs: {**session, **({"state": kwargs["state"]} if "state" in kwargs else {})},
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_access_policy_service.hardware_action_requires_software_approval",
                return_value=False,
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.gateway_action_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "operation": "tool_execute",
                    "next_action": "dispatch_tool_invoke",
                },
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.rust_runtime_kernel_client.enforce_kernel_decision",
                side_effect=lambda _command, decision, **_kwargs: decision,
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.gateway_execution_service.execute_tool_via_gateway",
                execute_mock,
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_result_correlator_service.emit_artifacts",
                new_callable=AsyncMock,
            ),
            patch(
                "server_modules.hardware_runtime_adapters.gateway_adapter.hardware_result_correlator_service.emit_tool_result",
                new_callable=AsyncMock,
            ),
        ):
            result = await gateway_adapter.execute_gateway_action(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                user_id="user-1",
                gateway_id="gw-1",
                device_id="device-1",
                action_id=capability_id,
                capability_id=capability_id,
                arguments=arguments,
                runtime_session=_runtime_session(),
                run_id="run-1",
                trace_id="trace-1",
                thread_id=None,
                request_id="req-1",
                trace_context=None,
                require_approval=None,
                runtime_access_mode="guarded",
                timeout_seconds=None,
                tool_call_id="tool-1",
                agent_install_id=agent_install_id,
                file_mount_grants=file_mount_grants,
            )
        return result, execute_mock

    def test_two_agents_writing_identical_path_get_isolated_mounts(self) -> None:
        """The actual security property: agent A and agent B both trying to
        write to the exact same workspace-relative path ('project/notes.txt')
        must land on two different on-box directories — agent A can never
        read/write agent B's file through this connector."""

        async def run_test() -> None:
            grants = [{"mount": "project", "grant": "read_write"}]
            result_a, mock_a = await self._dispatch(
                capability_id="filesystem.write",
                arguments={"path": "project/notes.txt", "content": "agent A's secret"},
                agent_install_id="agent-a",
                file_mount_grants=grants,
            )
            result_b, mock_b = await self._dispatch(
                capability_id="filesystem.write",
                arguments={"path": "project/notes.txt", "content": "agent B's secret"},
                agent_install_id="agent-b",
                file_mount_grants=grants,
            )
            self.assertEqual(result_a["status"], "completed")
            self.assertEqual(result_b["status"], "completed")
            mount_a = mock_a.await_args.kwargs["arguments"]["mount"]
            mount_b = mock_b.await_args.kwargs["arguments"]["mount"]
            self.assertNotEqual(mount_a, mount_b)
            # Neither agent's mount is the bare, workspace-shared bucket name
            # a pre-fix caller (or another agent) could also reach.
            self.assertNotEqual(mount_a, "project")
            self.assertNotEqual(mount_b, "project")

        asyncio.run(run_test())

    def test_same_agent_reuses_the_same_mount_across_calls(self) -> None:
        """Legitimate same-agent access still works: two separate calls (a
        write, then a read) from the SAME agent resolve to the SAME on-box
        mount, so the agent can read back what it just wrote."""

        async def run_test() -> None:
            grants = [{"mount": "project", "grant": "read_write"}]
            _, write_mock = await self._dispatch(
                capability_id="filesystem.write",
                arguments={"path": "project/notes.txt", "content": "hello"},
                agent_install_id="agent-a",
                file_mount_grants=grants,
            )
            _, read_mock = await self._dispatch(
                capability_id="filesystem.read",
                arguments={"path": "project/notes.txt"},
                agent_install_id="agent-a",
                file_mount_grants=grants,
            )
            self.assertEqual(
                write_mock.await_args.kwargs["arguments"]["mount"],
                read_mock.await_args.kwargs["arguments"]["mount"],
            )

        asyncio.run(run_test())

    def test_shell_execute_is_also_agent_scoped_and_ignores_caller_supplied_mount(self) -> None:
        """shell.execute shares the exact same on-box workspaceHostPath as
        filesystem.* in the Gateway (runtime.ts's ensureWorkspaceDir) — if
        it weren't scoped too, an agent could route around the filesystem
        fix entirely by dropping/reading files via `shell`. Also proves a
        caller-crafted `mount` argument (an attacker trying to name another
        agent's mount directly) is discarded once a real identity resolves."""

        async def run_test() -> None:
            result, execute_mock = await self._dispatch(
                capability_id="shell.execute",
                arguments={"command": "pwd", "mount": "agent-victim__project"},
                agent_install_id="agent-attacker",
            )
            self.assertEqual(result["status"], "completed")
            dispatched_mount = execute_mock.await_args.kwargs["arguments"]["mount"]
            self.assertNotEqual(dispatched_mount, "agent-victim__project")
            self.assertEqual(dispatched_mount, gateway_adapter._agent_scoped_mount("default", "agent-attacker"))

        asyncio.run(run_test())

    def test_no_agent_identity_keeps_legacy_unscoped_mount(self) -> None:
        """Backward-compat / regression guard: callers with no per-agent
        concept at all (agent_install_id=None — e.g. the human-operator
        /runtime/hardware/actions/execute REST route) see byte-for-byte the
        same mount name as before this fix. Mirrors
        test_hardware_action_broker_service.py's
        test_file_mount_grants_real_per_agent_read_write_grant_allows_write,
        which asserts dispatched mount == 'project' with no agent scoping."""

        async def run_test() -> None:
            _, execute_mock = await self._dispatch(
                capability_id="filesystem.write",
                arguments={"path": "project/notes.txt", "content": "hi"},
                agent_install_id=None,
                file_mount_grants=[{"mount": "project", "grant": "read_write"}],
            )
            self.assertEqual(execute_mock.await_args.kwargs["arguments"]["mount"], "project")

        asyncio.run(run_test())

    def test_path_traversal_still_rejected_with_agent_scope_active(self) -> None:
        async def run_test() -> None:
            result, execute_mock = await self._dispatch(
                capability_id="filesystem.write",
                arguments={"path": "../../etc/passwd", "content": "x"},
                agent_install_id="agent-a",
                file_mount_grants=[{"mount": "project", "grant": "read_write"}],
            )
            self.assertEqual(result["status"], "failed")
            execute_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_absolute_path_still_rejected_by_default_grants_with_agent_scope_active(self) -> None:
        async def run_test() -> None:
            result, execute_mock = await self._dispatch(
                capability_id="filesystem.write",
                arguments={"path": "/etc/passwd", "content": "x"},
                agent_install_id="agent-a",
                file_mount_grants=None,
            )
            self.assertEqual(result["status"], "failed")
            execute_mock.assert_not_awaited()

        asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────
# Layer 2: hardware_action_broker_service.execute_hardware_action — proves
# the new agent_install_id kwarg is threaded through to gateway_adapter
# unchanged, rather than dropped along the way.
# ─────────────────────────────────────────────────────────────────────────


class BrokerThreadsAgentInstallIdTests(unittest.TestCase):
    def test_broker_threads_agent_install_id_through_to_gateway_adapter(self) -> None:
        async def run_test() -> None:
            captured: dict = {}

            async def fake_execute_gateway_action(**kwargs):
                captured.update(kwargs)
                return {"status": "completed", "runtime_session": _runtime_session(), "trace_id": "trace-1"}

            with (
                patch(
                    "server_modules.hardware_action_broker_service.gateway_adapter.execute_gateway_action",
                    side_effect=fake_execute_gateway_action,
                ),
                # Bypasses the (unrelated, already independently tested)
                # runtime-session lifecycle/Rust-kernel machinery entirely —
                # this test's only job is proving the new agent_install_id
                # kwarg survives the broker -> adapter hop unchanged.
                patch(
                    "server_modules.hardware_action_broker_service._create_runtime_session",
                    new=AsyncMock(return_value=_runtime_session()),
                ),
                patch(
                    "server_modules.hardware_action_broker_service._resolve_trace_context",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "server_modules.hardware_action_broker_service._emit_tool_started",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await broker.execute_hardware_action(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    action_id="filesystem.write",
                    capability_id="filesystem.write",
                    arguments={"path": "project/notes.txt", "content": "hi"},
                    runtime_target="user_device_gateway",
                    gateway_id="gw-1",
                    run_id="run-1",
                    trace_id="trace-1",
                    request_id="req-1",
                    agent_install_id="agent-a",
                )

            self.assertEqual(captured.get("agent_install_id"), "agent-a")

        asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────
# Layer 3: skills_service.execute_single_direct_tool_call_async — the
# model-facing `file`/`shell` connector dispatch. Proves identity is
# resolved ONLY from verified session_ctx (never from the tool call's own
# arguments), that a real specialist identity is threaded through so it
# gets isolated at layer 1, and that the owner-facing agent's own turn
# (session_ctx with no agent_install_id at all — verified as the real
# shape agent_turn_runtime_service.py produces for Sage's own turn) is left
# on today's unchanged, working behavior rather than broken by a hard
# fail-closed gate.
# ─────────────────────────────────────────────────────────────────────────


class DirectToolConnectorIdentityGateTests(unittest.TestCase):
    def _callbacks(self) -> direct_tool_execution_service.DirectToolExecutionCallbacks:
        return direct_tool_execution_service.DirectToolExecutionCallbacks(
            compact_step_detail=lambda value: value,
            titleize_direct_step_token=lambda value: value,
            run_async_tool_call=lambda awaitable: awaitable,
            parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
            tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
            parse_json_object_loose=lambda value: {},
            safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
            normalize_reasoning_effort=lambda value: str(value or "").strip().lower() or None,
            build_direct_local_tool_config=skills_service.build_direct_local_tool_config,
            format_direct_local_tool_result=lambda result: str(result),
            build_direct_tool_config=lambda connector_id, action_id, tool_input: {
                "connector": connector_id,
                "action": action_id,
                "input": tool_input,
            },
            format_direct_tool_result=lambda result: str(result),
            llm_task=lambda *args, **kwargs: {"ok": True},
            web_search=lambda query: [],
            web_fetch=lambda url: f"Fetched {url}",
            search_memory_notebook=lambda **kwargs: [],
            get_memory_notebook_excerpt=lambda **kwargs: {},
            update_memory_context_file=lambda **kwargs: {},
            memory_append_daily_note=lambda **kwargs: {},
            create_memory_consolidation_staging_file=lambda **kwargs: {},
            consolidate_daily_memory_notes=lambda **kwargs: {},
        )

    def _gateway_reachable_patches(self, execute_via_gateway_mock):
        return (
            patch(
                "server_modules.skills_service._resolve_direct_tool_gateway_id",
                return_value="gw-1",
            ),
            patch(
                "server_modules.skills_service._execute_direct_tool_via_gateway_async",
                execute_via_gateway_mock,
            ),
        )

    def test_owner_facing_agent_own_turn_keeps_working_with_no_identity(self) -> None:
        """The verified real shape of Sage's own turn
        (agent_turn_runtime_service.py's _run_sage_action_loop_v3 passes
        agent_install_id=_spec_install_id, explicitly commented "empty for
        Sage", at all 3 of its call sites — session_ctx never gets
        active_agent_install_id/agent_install_id set for this case). This
        must NOT raise or otherwise regress: the file/shell connector is
        Sage's own primary, highest-volume use of this seam whenever a box
        is paired, and it must keep working exactly as it does today."""
        execute_mock = AsyncMock(
            return_value={"gateway_id": "gw-1", "result": {"mode": "read", "path": "project/notes.txt", "content": "hi"}}
        )
        gw_patch, exec_patch = self._gateway_reachable_patches(execute_mock)

        async def run_test():
            with gw_patch, exec_patch:
                return await skills_service.execute_single_direct_tool_call_async(
                    tool_call={"name": "file__read", "arguments": {"path": "project/notes.txt"}},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    index=1,
                    # No agent_install_id / active_agent_install_id at all —
                    # exactly what agent_turn_runtime_service.py produces
                    # for Sage's own turn.
                    session_ctx={"authority_tier": "owner"},
                    callbacks=self._callbacks(),
                )

        asyncio.run(run_test())
        execute_mock.assert_awaited_once()
        self.assertIsNone(execute_mock.await_args.kwargs["agent_install_id"])

    def test_shell_connector_also_keeps_working_for_the_owner_facing_agent(self) -> None:
        execute_mock = AsyncMock(
            return_value={"gateway_id": "gw-1", "result": {"command": "echo hi", "exit_code": 0, "stdout": "hi"}}
        )
        gw_patch, exec_patch = self._gateway_reachable_patches(execute_mock)

        async def run_test():
            with gw_patch, exec_patch:
                return await skills_service.execute_single_direct_tool_call_async(
                    tool_call={"name": "shell__exec", "arguments": {"command": "echo hi"}},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    index=1,
                    session_ctx={"authority_tier": "owner"},
                    callbacks=self._callbacks(),
                )

        asyncio.run(run_test())
        execute_mock.assert_awaited_once()
        self.assertIsNone(execute_mock.await_args.kwargs["agent_install_id"])

    def test_file_connector_resolves_and_threads_agent_install_id(self) -> None:
        """Legitimate same-agent access still works end to end: with a real
        agent_install_id in session_ctx, dispatch succeeds and that exact id
        (not something derived from the tool call's own arguments) is
        threaded into the Gateway dispatch call."""
        execute_mock = AsyncMock(
            return_value={"gateway_id": "gw-1", "result": {"mode": "read", "path": "project/notes.txt", "content": "hi"}}
        )
        gw_patch, exec_patch = self._gateway_reachable_patches(execute_mock)

        async def run_test():
            with gw_patch, exec_patch:
                return await skills_service.execute_single_direct_tool_call_async(
                    tool_call={"name": "file__read", "arguments": {"path": "project/notes.txt"}},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    index=1,
                    session_ctx={"authority_tier": "owner", "agent_install_id": "agent-a"},
                    callbacks=self._callbacks(),
                )

        asyncio.run(run_test())
        execute_mock.assert_awaited_once()
        self.assertEqual(execute_mock.await_args.kwargs["agent_install_id"], "agent-a")

    def test_active_agent_install_id_fallback_satisfies_the_gate(self) -> None:
        """Matches the exact fallback convention every memory__* tool
        dispatch in this file already uses: active_agent_install_id is
        honored when agent_install_id itself is absent."""
        execute_mock = AsyncMock(
            return_value={"gateway_id": "gw-1", "result": {"command": "echo hi", "exit_code": 0, "stdout": "hi"}}
        )
        gw_patch, exec_patch = self._gateway_reachable_patches(execute_mock)

        async def run_test():
            with gw_patch, exec_patch:
                return await skills_service.execute_single_direct_tool_call_async(
                    tool_call={"name": "shell__exec", "arguments": {"command": "echo hi"}},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    index=1,
                    session_ctx={"authority_tier": "owner", "active_agent_install_id": "agent-b"},
                    callbacks=self._callbacks(),
                )

        asyncio.run(run_test())
        execute_mock.assert_awaited_once()
        self.assertEqual(execute_mock.await_args.kwargs["agent_install_id"], "agent-b")

    def test_crafted_agent_install_id_in_tool_arguments_is_not_honored(self) -> None:
        """A model/caller cannot spoof another agent's identity by including
        agent_install_id inside the tool call's own arguments — identity is
        resolved exclusively from verified session_ctx. Here session_ctx
        carries the REAL identity 'agent-real' while the arguments claim to
        be 'agent-victim'; only the session-verified id may reach the
        Gateway dispatch."""
        execute_mock = AsyncMock(
            return_value={"gateway_id": "gw-1", "result": {"mode": "read", "path": "project/notes.txt", "content": "hi"}}
        )
        gw_patch, exec_patch = self._gateway_reachable_patches(execute_mock)

        async def run_test():
            with gw_patch, exec_patch:
                return await skills_service.execute_single_direct_tool_call_async(
                    tool_call={
                        "name": "file__read",
                        "arguments": {"path": "project/notes.txt", "agent_install_id": "agent-victim"},
                    },
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    index=1,
                    session_ctx={"authority_tier": "owner", "agent_install_id": "agent-real"},
                    callbacks=self._callbacks(),
                )

        asyncio.run(run_test())
        execute_mock.assert_awaited_once()
        self.assertEqual(execute_mock.await_args.kwargs["agent_install_id"], "agent-real")


# ─────────────────────────────────────────────────────────────────────────
# Layer 0: agent_turn_runtime_service.handle_sage_chat / _run_sage_action_
# loop_v3 — the STAMPING side of the invariant Layer 3 above assumes but
# never itself proves. Every DirectToolConnectorIdentityGateTests case
# constructs session_ctx BY HAND (e.g.
# session_ctx={"authority_tier": "owner", "agent_install_id": "agent-a"}) —
# that proves identity is threaded correctly ONCE it's already sitting in
# session_ctx, but says nothing about whether the real turn-dispatch code
# ever puts it there. A test that mocks the identity in like that proves
# nothing about the stamping step itself: a future refactor that silently
# dropped `session_ctx["active_agent_install_id"] = _acting_install_id`
# inside _run_sage_action_loop_v3, or stopped passing
# agent_install_id=_spec_install_id from handle_sage_chat into it (3 call
# sites, verified above at agent_turn_runtime_service.py ~4607/4686), would
# leave every Layer 3 test green while every real specialist turn silently
# fell back to Sage's workspace-wide mount — exactly the residual risk this
# class closes.
#
# Drives the REAL dispatch seam end to end, no mocked identity anywhere:
# handle_sage_chat(specialist_context=...) -> _run_sage_action_loop_v3's own
# session_ctx construction -> direct_chat_generation_service.
# stream_provider_backed_direct_chat's session_ctx kwarg — the exact dict
# instance skills_service.execute_single_direct_tool_call{,_async} reads via
# _agent_install_id_from_direct_tool_context when a tool call actually
# fires mid-turn. Only the LLM call itself (stream_provider_backed_direct_
# chat) and unrelated I/O (profile/context-file/memory loads, cloud
# provider/credential resolution, activity/audit logging) are mocked — the
# specialist-context resolution and session_ctx stamping under test run for
# real, unmocked. Mirrors the harness in
# test_agent_turn_runtime_service.py's
# SageAgentRuntimeSpecialistProviderResolutionTests, which proves the same
# seam is a real, working path for a different invariant (credential
# isolation).
# ─────────────────────────────────────────────────────────────────────────


class SpecialistTurnStampsAgentInstallIdOntoSessionCtxTests(unittest.TestCase):
    @staticmethod
    def _spec(**overrides) -> SpecialistRuntimeContext:
        base = dict(
            agent_install_id="agent-specialist-1",
            agent_label="Support Agent",
            agent_kind="specialist",
            persona="You are a support specialist.",
        )
        base.update(overrides)
        return SpecialistRuntimeContext(**base)

    @staticmethod
    def _run_turn(*, specialist_context):
        """Drives handle_sage_chat for real. Only the LLM call and unrelated
        I/O are mocked — session_ctx construction inside
        _run_sage_action_loop_v3 (the code under test) is never touched or
        stood in for."""
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Reply", "actions": [], "error": None},
        }]
        with (
            patch(
                "server_modules.agent_turn_runtime_service.assistant_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.agent_turn_runtime_service.assistant_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.agent_turn_runtime_service.assistant_health_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[]),
            patch(
                "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
                new=AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"})),
            ),
            patch(
                "server_modules.agent_turn_runtime_service._resolve_agent_cloud_provider",
                new=AsyncMock(return_value=("deepseek", {"api_key": "sk-workspace-default"}, "platform_credits")),
            ),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch(
                "server_modules.agent_turn_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.agent_turn_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
            ) as mock_stream,
            patch("server_modules.agent_turn_runtime_service.persist_interaction"),
            patch("server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.agent_turn_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_stream.return_value = iter(stream_events)
            _run(agent_turn_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello",
                specialist_context=specialist_context,
            ))
        return mock_stream

    def test_specialist_turn_stamps_non_empty_identity_reaching_the_dispatch_seam(self):
        """THE invariant: a customer-facing (specialist) agent's turn must
        reach skills_service's direct-tool dispatch with a non-empty
        agent_install_id resolvable from session_ctx. Reads the call
        with skills_service's OWN resolver
        (_agent_install_id_from_direct_tool_context — the exact function
        execute_single_direct_tool_call{,_async} calls at the real Gateway
        dispatch site) against the SAME session_ctx dict instance
        _run_sage_action_loop_v3 threads into the generation call, not a
        hand-built stand-in."""
        mock_stream = self._run_turn(specialist_context=self._spec(agent_install_id="agent-specialist-1"))

        mock_stream.assert_called_once()
        session_ctx = mock_stream.call_args.kwargs["session_ctx"]
        self.assertIsInstance(session_ctx, dict)
        resolved_identity = skills_service._agent_install_id_from_direct_tool_context(session_ctx)
        self.assertEqual(resolved_identity, "agent-specialist-1")
        self.assertEqual(session_ctx.get("active_agent_install_id"), "agent-specialist-1")

    def test_a_second_specialist_stamps_its_own_distinct_identity(self):
        """Not a fluke of one hardcoded id — a DIFFERENT specialist install
        gets its OWN id stamped, proving the value flows end-to-end from the
        SpecialistRuntimeContext actually passed in, rather than some
        constant a broken refactor might hardcode to make the first test
        alone pass."""
        mock_stream = self._run_turn(specialist_context=self._spec(agent_install_id="agent-specialist-2"))

        session_ctx = mock_stream.call_args.kwargs["session_ctx"]
        self.assertEqual(
            skills_service._agent_install_id_from_direct_tool_context(session_ctx),
            "agent-specialist-2",
        )

    def test_owner_facing_agent_turn_keeps_the_empty_identity_that_is_valid_for_it(self):
        """The other half of the invariant, asserted through the SAME real
        seam: Sage's own turn (specialist_context=None) must NOT get an
        agent_install_id stamped — an empty identity is deliberate and
        correct here (gateway_adapter._agent_scoped_mount leaves the
        pre-existing workspace-shared mount unchanged for it; see that
        function's docstring and commit da6b36242's message). This must
        stay true through the exact same code path the specialist tests
        above drive, so a regression that starts stamping a real id onto
        Sage's own turn — which would silently narrow Sage's own file/shell
        access to a fresh, empty per-agent mount instead of its existing
        workspace-wide one — is caught here too, not just the specialist
        widening case."""
        mock_stream = self._run_turn(specialist_context=None)

        session_ctx = mock_stream.call_args.kwargs["session_ctx"]
        resolved_identity = skills_service._agent_install_id_from_direct_tool_context(session_ctx)
        self.assertEqual(resolved_identity, "")
        self.assertNotIn("active_agent_install_id", session_ctx)


if __name__ == "__main__":
    unittest.main()

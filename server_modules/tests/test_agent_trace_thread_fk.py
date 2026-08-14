"""agent_traces.thread_id is a FOREIGN KEY, so a trace can only name a thread
that actually has a row.

OBSERVED LIVE, not hypothesised. A real agent turn produced:

    asyncpg.exceptions.ForeignKeyViolationError: insert or update on table
    "agent_traces" violates foreign key constraint "agent_traces_thread_id_fkey"
    DETAIL:  Key (thread_id)=(sage-main) is not present in table "agent_threads".

The turn itself SUCCEEDED (200, model replied, reply persisted and rendered).
Only the TRACE was lost, silently, inside ``start_trace``'s own except — and the
trace is the transparency record the product shows the customer (the agent
reading, writing, running a command, planning). A lost trace is a product gap,
not log noise.

Root cause: ``sage_agent_runtime_service._run_sage_action_loop_v3`` passed the
MODULE CONSTANT ``SAGE_THREAD_ID`` ("sage-main") to ``start_trace`` instead of
the caller's real thread id — while ``_handle_sage_chat_unguarded`` had already
called ``thread_service.ensure_master_thread`` on that real id one frame up.
Real workspaces hold per-agent thread rows (``thread_agent_ainstall_*``); no
literal ``sage-main`` row exists, so EVERY trace on that path died. Compare
``agent_turn.py``, which ensures its thread immediately before tracing it and
whose traces insert fine.

This file pins the invariant on all three ``start_trace`` call sites: the id a
trace names is an id whose row the caller guaranteed, or it is NULL (the column
is nullable — an unlinked trace is honest; a dangling reference costs the whole
record).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import agent_trace_service, run_service, sage_agent_runtime_service


_SERVER_MODULES = pathlib.Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


def _source(module_name: str) -> str:
    return (_SERVER_MODULES / f"{module_name}.py").read_text(encoding="utf-8")


def _start_trace_call_nodes(module_name: str) -> list[ast.Call]:
    """Every ``agent_trace_service.start_trace(...)`` call in a module."""
    tree = ast.parse(_source(module_name))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "start_trace":
            calls.append(node)
    return calls


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


class AgentTracesThreadIdIsAForeignKeyTests(unittest.TestCase):
    """Grounds every other assertion in this file: the constraint is real, and
    NULL is a legal value for it. Without both facts, "pass None rather than an
    id with no row" would be a guess rather than the schema's own answer."""

    def test_thread_id_references_agent_threads_and_is_nullable(self):
        ddl = _source("control_plane_repository")
        start = ddl.index("CREATE TABLE IF NOT EXISTS agent_traces (")
        body = ddl[start : ddl.index(");", start)]
        self.assertIn("REFERENCES agent_threads(id)", body)
        thread_line = next(
            line.strip() for line in body.splitlines() if line.strip().startswith("thread_id")
        )
        # NULL, not NOT NULL -- an unlinked trace is representable.
        self.assertIn("NULL", thread_line)
        self.assertNotIn("NOT NULL", thread_line)


class SageActionLoopTracesTheRealThreadTests(unittest.TestCase):
    """The live bug, at the seam that produced it.

    Drives the REAL ``_run_sage_action_loop_v3`` down its SDK-engine branch (the
    same fake-thread-row technique ``SdkEngineSessionContinuityIntegrationTests``
    already uses, so this needs no database and no compiled Rust kernel) and
    reads what it actually handed ``start_trace``."""

    def _decision(self):
        from server_modules import kill_switch_gate

        return kill_switch_gate.KillSwitchDecision(blocked=False, reason="", scope="")

    def _start_trace_kwargs(self, *, conversation_thread_id: str) -> dict:
        start_trace_mock = AsyncMock(return_value=None)
        collect_mock = MagicMock(
            return_value=[{"type": "final", "payload": {"reply": "ok", "session_id": "sess-1"}}]
        )
        with (
            patch("server_modules.kill_switch_gate.evaluate_kill_switch", return_value=self._decision()),
            patch(
                "server_modules.sage_agent_runtime_service.thread_service.get_thread",
                new=AsyncMock(return_value={"metadata": {}}),
            ),
            patch(
                "server_modules.control_plane_repository.merge_agent_thread_metadata",
                new=AsyncMock(return_value=None),
            ),
            patch.object(agent_trace_service, "start_trace", new=start_trace_mock),
            patch.object(
                sage_agent_runtime_service.claude_agent_sdk_bridge,
                "collect_events_via_claude_agent_sdk",
                new=collect_mock,
            ),
        ):
            _run(
                sage_agent_runtime_service._run_sage_action_loop_v3(
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                    message="hello",
                    provider="anthropic",
                    model="claude",
                    credentials={},
                    trace_id="trace-1",
                    actor_user_id="user-1",
                    system_prompt="",
                    prior_messages=[],
                    agent_install_id="",
                    engine_options={"engine": "claude_agent_sdk"},
                    conversation_thread_id=conversation_thread_id,
                )
            )
        self.assertTrue(
            start_trace_mock.await_args_list,
            "start_trace was never called -- the test no longer reaches the seam it exists to check.",
        )
        return dict(start_trace_mock.await_args.kwargs)

    def test_trace_names_the_thread_the_caller_already_ensured(self):
        # The exact thread shape from the live incident: an agent turn whose
        # thread row DID exist (agent_turn.py's trace on the same thread
        # inserted fine) while this path traced "sage-main" instead.
        kwargs = self._start_trace_kwargs(
            conversation_thread_id="thread_agent_ainstall_32048436b4e14ec5"
        )
        self.assertEqual(kwargs["thread_id"], "thread_agent_ainstall_32048436b4e14ec5")

    def test_never_substitutes_the_sage_main_constant(self):
        kwargs = self._start_trace_kwargs(
            conversation_thread_id="thread_agent_ainstall_32048436b4e14ec5"
        )
        self.assertNotEqual(kwargs["thread_id"], sage_agent_runtime_service.SAGE_THREAD_ID)

    def test_no_thread_traces_as_null_rather_than_an_id_with_no_row(self):
        kwargs = self._start_trace_kwargs(conversation_thread_id="")
        self.assertIsNone(kwargs["thread_id"])


class SageActionLoopThreadIdIsRequiredTests(unittest.TestCase):
    """CLAUDE.md: "a scope column with a default is a loaded gun -- make it
    required on any function that both reads and writes the scoped row." The
    thread id used to default to "" while the trace call quietly substituted a
    module constant, which is the same failure in a different costume: the
    caller never named a scope and the code invented one."""

    def test_conversation_thread_id_has_no_default(self):
        signature = inspect.signature(sage_agent_runtime_service._run_sage_action_loop_v3)
        parameter = signature.parameters["conversation_thread_id"]
        self.assertIs(
            parameter.default,
            inspect.Parameter.empty,
            "conversation_thread_id must be required so a forgetful caller fails loudly "
            "instead of silently tracing against a thread nobody named.",
        )

    def test_the_trace_call_does_not_reference_the_module_constant(self):
        """Structural, because a behavioural test only covers today's callers.
        Re-substituting the constant here type-checks, runs, and is silent."""
        calls = _start_trace_call_nodes("sage_agent_runtime_service")
        self.assertEqual(len(calls), 1)
        thread_id_arg = _keyword(calls[0], "thread_id")
        self.assertIsNotNone(thread_id_arg)
        names = {node.id for node in ast.walk(thread_id_arg) if isinstance(node, ast.Name)}
        self.assertNotIn("SAGE_THREAD_ID", names)
        self.assertIn("conversation_thread_id", names)


class DurableTurnTraceThreadTests(unittest.TestCase):
    """The third call site, ``run_service.execute_durable_turn_request``.

    Reached from POST /runs/start (turn_ingress_service.start_run_start), which
    -- unlike agent_turn.py -- had no ensure of its own, and whose thread id fell
    back to ``turn_request.session_id``. A session id is not a thread id and
    never has an agent_threads row, so that branch could only ever violate the
    FK and lose the trace."""

    def _turn_request(self, *, thread_id: str, session_id: str):
        from server_modules.agent_turn import AgentTurnRequest, TurnActor

        return AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            thread_id=thread_id,
            session_id=session_id,
            channel="web",
            actor=TurnActor(id="user-1", type="user"),
            message="hello",
        )

    def _capture(self, *, thread_id: str, session_id: str, ensure_fails: bool = False):
        class _Sentinel(Exception):
            pass

        start_trace_mock = AsyncMock(side_effect=_Sentinel())
        ensure_mock = AsyncMock(
            side_effect=RuntimeError("kernel unavailable") if ensure_fails else None
        )
        services = MagicMock()
        with (
            patch.object(agent_trace_service, "start_trace", new=start_trace_mock),
            patch("server_modules.thread_service.ensure_master_thread", new=ensure_mock),
        ):
            with self.assertRaises(_Sentinel):
                _run(
                    run_service.execute_durable_turn_request(
                        turn_request=self._turn_request(
                            thread_id=thread_id, session_id=session_id
                        ),
                        current_user={"user_id": "user-1"},
                        services=services,
                    )
                )
        return dict(start_trace_mock.await_args.kwargs), ensure_mock

    def test_a_session_id_is_never_passed_off_as_a_thread_id(self):
        kwargs, ensure_mock = self._capture(thread_id="", session_id="sess-abc")
        self.assertIsNone(kwargs["thread_id"])
        ensure_mock.assert_not_awaited()

    def test_a_real_thread_is_ensured_before_it_is_traced(self):
        kwargs, ensure_mock = self._capture(thread_id="thread-1", session_id="sess-abc")
        self.assertEqual(kwargs["thread_id"], "thread-1")
        ensure_mock.assert_awaited_once()
        self.assertEqual(ensure_mock.await_args.kwargs["thread_id"], "thread-1")

    def test_an_unensurable_thread_degrades_to_null_not_to_a_dangling_id(self):
        """Losing the LINK costs a field. Handing start_trace an id with no row
        costs the entire trace, because the FK violation is swallowed."""
        kwargs, _ = self._capture(thread_id="thread-1", session_id="", ensure_fails=True)
        self.assertIsNone(kwargs["thread_id"])


class StartTraceFailureIsAudibleTests(unittest.TestCase):
    """This stayed broken for as long as it did because the failure logged a
    generic "start_trace failed" and named nothing -- indistinguishable from
    transient database noise. It must still never RAISE: a lost trace may not
    kill a working turn."""

    def _failing_start_trace(self):
        logged: dict = {}

        def _capture(**kwargs):
            logged.update(kwargs)
            return {}

        with (
            patch(
                "server_modules.control_plane_repository.create_agent_trace",
                new=AsyncMock(side_effect=RuntimeError("agent_traces_thread_id_fkey")),
            ),
            patch(
                "server_modules.failure_policy_service.log_degraded_operation",
                side_effect=_capture,
            ),
        ):
            result = _run(
                agent_trace_service.start_trace(
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                    thread_id="sage-main",
                    run_id=None,
                    surface="sage",
                    runtime_target=None,
                    provider="anthropic",
                    model="claude",
                    root_agent_id="agent-1",
                )
            )
        return result, logged

    def test_a_lost_trace_does_not_raise(self):
        result, _ = self._failing_start_trace()
        self.assertIsNone(result)

    def test_the_log_names_what_was_lost(self):
        from server_modules.error_contracts import SEVERITY_ERROR

        _, logged = self._failing_start_trace()
        self.assertEqual(logged.get("severity"), SEVERITY_ERROR)
        metadata = dict(logged.get("metadata") or {})
        self.assertEqual(metadata.get("thread_id"), "sage-main")
        self.assertEqual(metadata.get("workspace_id"), "ws-1")
        self.assertEqual(metadata.get("surface"), "sage")


if __name__ == "__main__":
    unittest.main()

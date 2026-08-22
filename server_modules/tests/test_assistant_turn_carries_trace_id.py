"""The assistant turn a trace belongs to must carry that trace's id.

THE BUG THIS FILE EXISTS FOR (fixed 2026-08-15)
-----------------------------------------------
The agent Work tab renders

    "Showing message history — detailed step tracking isn't available for
     this conversation."

for EVERY conversation, and falls back to a flat message ledger. Its own
header comment states the contract it depends on: the assistant turn that
closes out a trace gets that trace's id written into ``metadata.trace_id``,
and the tab resolves ``GET /api/agent-traces/{trace_id}`` from it. With that
field null, ``hasResolvedTrace`` is false and the fallback copy renders --
so the customer never sees the agent read, write, run a command, or plan,
even though every one of those steps was recorded.

OBSERVED LIVE, at the database, on a real DeepSeek turn through the real
``POST /api/turn``:

    agent_traces        surface=web   provider=NULL      2 events (started, routed)
                        surface=sage  provider=deepseek  the REAL steps
                                        (tool.started, browser.action,
                                         tool.result, plan.item.updated)
    agent_turns         role=assistant   metadata->>'trace_id'  ->  NULL

TWO traces per turn, and the assistant turn carried neither.

WHY. A web chat is ``execution_mode=sync`` + ``response_mode=stream``, so
``turn_ingress_service.start_turn`` hands it to the STREAM builder. The
trace ``agent_turn.py`` opens is bound onto the outer stream-handle dict
(``{"kind": "direct_chat_stream", "producer": ...}``) by
``_bind_trace_id_to_turn_result`` -- a dict ``_should_persist_direct_chat_
result`` correctly declines to persist, and which nothing consults again.
``execute_direct_chat_turn_request`` even ACCEPTS that ``trace_context`` and
never uses it (grep the body: one mention, the parameter itself), which is
why that trace only ever holds trace.started + trace.routed. Meanwhile the
real work runs through ``handle_sage_chat`` -> ``_run_sage_action_loop_v3``,
which opens its OWN trace and emits every step into it. So the binder and
the trace that actually holds the steps were on different paths, and the
assistant-turn writers were on a third.

THE FIX, and the trap inside it. ``_run_sage_action_loop_v3`` now returns
``agent_trace_id`` -- the ``agent_traces.id`` it opened -- and the writer in
``_handle_sage_chat_unguarded`` stamps it into the assistant turn's
metadata. It is deliberately NOT called ``trace_id``: in that module
``trace_id`` is a per-call correlation uuid (``str(uuid.uuid4())``) that has
never been a row in ``agent_traces``. Writing THAT into
``metadata.trace_id`` would point the Work tab at a trace which does not
exist -- strictly worse than the empty value it had. Two different ids, two
different names, and the AST test below pins which one reaches the column.
"""

from __future__ import annotations

import ast
import asyncio
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import agent_trace_service, claude_agent_sdk_bridge
from server_modules import agent_turn_runtime_service
from server_modules.tests.test_default_engine_credit_debit import _TurnHarness


SAGE_RUNTIME_SOURCE = Path(agent_turn_runtime_service.__file__)


def _run(coro):
    return asyncio.run(coro)


def _agent_traces_columns() -> set[str]:
    """The real ``agent_traces`` column names, read out of the repository's own
    DDL -- so the stand-in row below is answerable to the table it stands in
    for instead of to itself."""
    ddl = (Path(agent_turn_runtime_service.__file__).parent / "control_plane_repository.py").read_text(
        encoding="utf-8"
    )
    start = ddl.index("CREATE TABLE IF NOT EXISTS agent_traces (")
    body = ddl[start : ddl.index(");", start)]
    return {
        line.strip().split()[0]
        for line in body.splitlines()[1:]
        if line.strip() and not line.strip().startswith(("PRIMARY", "FOREIGN", "CONSTRAINT", ")"))
    }


def _real_trace_context(trace_id: str) -> agent_trace_service.TraceContext:
    """The context a REAL ``start_trace`` hands back.

    Produced by calling the real ``start_trace`` with only its database WRITE
    faked, never by hand-constructing a TraceContext here -- a fixture that
    invents its own shape cannot notice the producer's shape changed
    (CLAUDE.md's private-memory ``session_metadata`` incident, same family).
    The stand-in row's keys are checked against the live DDL for the same
    reason.
    """
    row = {
        "id": trace_id,
        "tenant_id": "tenant-trace",
        "workspace_id": "ws-trace",
        "thread_id": "thread_agent_ainstall_32048436b4e14ec5",
        "run_id": None,
        "root_agent_id": "sage-main",
        "surface": "sage",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
    }
    unknown = set(row) - _agent_traces_columns()
    assert not unknown, f"stand-in agent_traces row has columns the table does not: {sorted(unknown)}"
    with patch(
        "server_modules.control_plane_repository.create_agent_trace",
        new=AsyncMock(return_value=row),
    ):
        context = _run(
            agent_trace_service.start_trace(
                workspace_id="ws-trace",
                tenant_id="tenant-trace",
                thread_id="thread_agent_ainstall_32048436b4e14ec5",
                run_id=None,
                surface="sage",
                runtime_target=None,
                provider="deepseek",
                model="deepseek-v4-pro",
                root_agent_id="sage-main",
            )
        )
    assert context is not None, "start_trace returned nothing -- the harness no longer reaches it."
    assert context.trace_id == trace_id
    return context


class _TracedTurn:
    """Drives the REAL ``handle_sage_chat`` on the production default engine
    (the same harness ``test_default_engine_credit_debit`` uses, so the turn
    path is production code end to end) and captures BOTH ends: what
    ``start_trace`` handed the action loop, and what actually reached
    ``thread_service.record_assistant_turn``."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.test = test

    def run(self, *, trace_id: str | None = "trace_fixture0000000000000000", **chat_kwargs):
        context = _real_trace_context(trace_id) if trace_id else None
        start_trace = AsyncMock(return_value=context)
        assistant_writes: list[dict] = []

        async def _record_assistant_turn(**kwargs):
            assistant_writes.append(kwargs)
            return None

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(agent_trace_service, "start_trace", new=start_trace)
            )
            stack.enter_context(
                patch(
                    "server_modules.agent_turn_runtime_service.agent_trace_service.start_trace",
                    new=start_trace,
                )
            )
            stack.enter_context(
                patch(
                    "server_modules.thread_service.record_assistant_turn",
                    new=_record_assistant_turn,
                )
            )
            stack.enter_context(
                patch("server_modules.thread_service.record_user_turn", new=AsyncMock(return_value=None))
            )
            result = _TurnHarness(self.test).run(
                engine=claude_agent_sdk_bridge.ENGINE_ID,
                **chat_kwargs,
            )

        self.test.assertTrue(
            assistant_writes,
            "no assistant turn was persisted -- this test no longer reaches the writer it exists to check.",
        )
        return {
            "trace_context": context,
            "start_trace": start_trace,
            "assistant_writes": assistant_writes,
            "metadata": dict(assistant_writes[-1].get("metadata") or {}),
            "result": result["result"],
        }


class AssistantTurnCarriesItsTraceIdTests(unittest.TestCase):
    """The live gap, asserted at the value the Work tab actually reads."""

    def test_the_persisted_assistant_turn_carries_the_trace_the_turn_produced(self):
        run = _TracedTurn(self).run(
            workspace_id="ws-trace",
            message="run the thing",
            request_id="turn-trace-1",
        )
        # Not "some trace id" -- THE trace this turn opened. A different id
        # here would resolve to somebody else's steps, or to nothing.
        self.assertEqual(run["metadata"].get("trace_id"), run["trace_context"].trace_id)

    def test_the_id_written_is_a_trace_row_id_not_the_per_call_correlation_uuid(self):
        """``handle_sage_chat``'s own ``trace_id`` local is a fresh uuid4 that
        is not, and has never been, a row in agent_traces. Writing it would
        look exactly like a fix and leave the Work tab resolving a 404."""
        run = _TracedTurn(self).run(
            workspace_id="ws-trace",
            message="run the thing",
            request_id="turn-trace-2",
        )
        written = run["metadata"].get("trace_id")
        self.assertEqual(written, run["trace_context"].trace_id)
        # The correlation uuid rides on the same row as run_id; the two must
        # never be the same value.
        self.assertNotEqual(written, run["assistant_writes"][-1].get("run_id"))

    def test_a_turn_with_no_trace_omits_the_key_rather_than_writing_an_empty_one(self):
        """``start_trace`` never raises -- it returns None and the turn ships.
        "this turn produced no trace" and "here is a trace id" are different
        facts, so the absent trace must be an ABSENT key, never "" (which the
        tab would treat as a resolvable value on the next reader that stops
        truthiness-checking it)."""
        run = _TracedTurn(self).run(
            trace_id=None,
            workspace_id="ws-trace",
            message="run the thing",
            request_id="turn-trace-3",
        )
        self.assertIsNone(run["start_trace"].return_value)
        self.assertNotIn("trace_id", run["metadata"])

    def test_the_reply_is_unaffected(self):
        """Stamping a field must not change what the customer is told."""
        run = _TracedTurn(self).run(
            workspace_id="ws-trace",
            message="run the thing",
            request_id="turn-trace-4",
        )
        self.assertEqual(run["result"]["message"], "Here is your answer.")


class ActionLoopPublishesTheTraceItOpenedTests(unittest.TestCase):
    """The producer half, driven directly: the loop that CREATES the trace is
    the only thing that can honestly report its id."""

    def _decision(self):
        from server_modules import kill_switch_gate

        return kill_switch_gate.KillSwitchDecision(blocked=False, reason="", scope="")

    def _loop_result(self, *, context, engine: str = "claude_agent_sdk", reply: str = "ok", record=None) -> dict:
        collect_mock = MagicMock(
            return_value=[{"type": "final", "payload": {"reply": reply, "session_id": "sess-1"}}]
        )
        if record is None:
            record = {}
        record["finish"] = AsyncMock(return_value=None)
        record["completed"] = AsyncMock(return_value=None)
        record["legacy_stream"] = MagicMock(
            side_effect=lambda *a, **k: iter([{"type": "final", "payload": {"reply": reply}}])
        )
        with (
            patch.object(agent_trace_service, "finish_trace", new=record["finish"]),
            patch.object(agent_trace_service, "emit_trace_completed", new=record["completed"]),
            patch(
                "server_modules.direct_chat_generation_service.stream_provider_backed_direct_chat",
                new=record["legacy_stream"],
            ),
            patch("server_modules.kill_switch_gate.evaluate_kill_switch", return_value=self._decision()),
            patch(
                "server_modules.agent_turn_runtime_service.thread_service.get_thread",
                new=AsyncMock(return_value={"metadata": {}}),
            ),
            patch(
                "server_modules.control_plane_repository.merge_agent_thread_metadata",
                new=AsyncMock(return_value=None),
            ),
            patch.object(agent_trace_service, "start_trace", new=AsyncMock(return_value=context)),
            patch.object(
                agent_turn_runtime_service.claude_agent_sdk_bridge,
                "collect_events_via_claude_agent_sdk",
                new=collect_mock,
            ),
        ):
            return _run(
                agent_turn_runtime_service._run_sage_action_loop_v3(
                    workspace_id="ws-1",
                    tenant_id="tenant-1",
                    message="hello",
                    provider="deepseek",
                    model="deepseek-v4-pro",
                    credentials={},
                    trace_id="correlation-uuid-not-a-trace-row",
                    actor_user_id="user-1",
                    system_prompt="",
                    prior_messages=[],
                    agent_install_id="",
                    engine_options={"engine": engine},
                    conversation_thread_id="thread_agent_ainstall_32048436b4e14ec5",
                )
            )

    def test_the_loop_returns_the_trace_row_id_it_opened(self):
        context = _real_trace_context("trace_loopfixture000000000000")
        result = self._loop_result(context=context)
        self.assertEqual(result.get("agent_trace_id"), context.trace_id)

    def test_the_loop_never_publishes_the_correlation_uuid_as_a_trace_row_id(self):
        context = _real_trace_context("trace_loopfixture000000000000")
        result = self._loop_result(context=context)
        self.assertNotEqual(result.get("agent_trace_id"), "correlation-uuid-not-a-trace-row")

    def test_a_lost_trace_publishes_empty_not_a_fabricated_id(self):
        result = self._loop_result(context=None)
        self.assertEqual(result.get("agent_trace_id"), "")


class TheTraceIsClosedWhenTheTurnEndsTests(unittest.TestCase):
    """A resolved trace that never finishes is a NEW lie, created by the fix
    above rather than by the original bug.

    ``WorkTab.tsx`` reads ``!trace.finished_at`` as "still running": it shows
    a "Working" pill on a turn that ended minutes ago, and it opens an SSE
    stream that only closes on a terminal event (``_terminal_trace_event`` in
    routes_agent_traces.py), so the connection polls the database forever.
    Every trace in the live database had ``finished_at = NULL`` because the
    SDK engine -- the production default -- has no finisher; only the legacy
    engine's callee did. Before this fix that was invisible, because nothing
    ever resolved the trace to look at it.
    """

    def _decision(self):
        from server_modules import kill_switch_gate

        return kill_switch_gate.KillSwitchDecision(blocked=False, reason="", scope="")

    def _run_loop(self, **kwargs):
        record: dict = {}
        loop = ActionLoopPublishesTheTraceItOpenedTests()
        loop._decision = self._decision  # type: ignore[method-assign]
        result = loop._loop_result(record=record, **kwargs)
        return result, record

    def test_a_finished_sdk_turn_closes_its_own_trace(self):
        context = _real_trace_context("trace_finishfixture0000000000")
        _, record = self._run_loop(context=context)
        self.assertEqual(record["finish"].await_count, 1)
        self.assertEqual(record["finish"].await_args.args[0], context)
        self.assertEqual(record["finish"].await_args.kwargs["outcome"], "success")
        # trace.completed is what makes the SSE generator return, so the
        # stream closes instead of polling forever.
        self.assertEqual(record["completed"].await_count, 1)

    def test_the_legacy_engine_trace_is_left_to_its_own_finisher(self):
        """direct_chat_generation_service finishes the SAME trace_context with
        a more precise outcome (success / partial / needs_input). Finishing it
        here as well would overwrite that verdict with a coarser one."""
        context = _real_trace_context("trace_legacyfixture0000000000")
        _, record = self._run_loop(context=context, engine="legacy")
        self.assertEqual(record["legacy_stream"].call_count, 1)
        self.assertEqual(record["finish"].await_count, 0)
        self.assertEqual(record["completed"].await_count, 0)

    def test_a_turn_that_produced_nothing_still_closes_its_trace(self):
        """The loop returns None here and the caller regenerates on a path
        that never sees this trace — so an unclosed trace would stay
        "Working" with nobody left to close it."""
        context = _real_trace_context("trace_emptyfixture00000000000")
        result, record = self._run_loop(context=context, reply="")
        self.assertIsNone(result)
        self.assertEqual(record["finish"].await_count, 1)
        self.assertEqual(record["finish"].await_args.kwargs["outcome"], "partial")

    def test_no_trace_means_nothing_to_close(self):
        _, record = self._run_loop(context=None)
        self.assertEqual(record["finish"].await_count, 0)
        self.assertEqual(record["completed"].await_count, 0)


class AssistantTurnTraceIdSourceIsStructurallyPinnedTests(unittest.TestCase):
    """A behavioural test only covers today's writers, and the failure mode
    here is silent by construction: swapping ``_agent_trace_id`` for the
    module's own ``trace_id`` local type-checks, runs, persists a plausible
    id, and breaks the Work tab with no error anywhere."""

    def _action_loop_assistant_write(self) -> ast.Call:
        tree = ast.parse(SAGE_RUNTIME_SOURCE.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "record_assistant_turn"
            and any(
                kw.arg == "metadata"
                and isinstance(kw.value, ast.Dict)
                and any(
                    isinstance(key, ast.Constant) and key.value == "model"
                    for key in kw.value.keys
                    if key is not None
                )
                for kw in node.keywords
            )
        ]
        self.assertEqual(
            len(calls),
            1,
            "expected exactly one action-loop assistant-turn writer -- if a second "
            "one appeared, it needs the same trace stamp and this test needs updating.",
        )
        return calls[0]

    def _metadata_dict(self, call: ast.Call) -> ast.Dict:
        for kw in call.keywords:
            if kw.arg == "metadata" and isinstance(kw.value, ast.Dict):
                return kw.value
        self.fail("the action-loop assistant write no longer passes a literal metadata dict")

    def test_the_stamped_value_comes_from_the_action_loop_not_the_correlation_uuid(self):
        metadata = self._metadata_dict(self._action_loop_assistant_write())
        names = {
            node.id
            for value in metadata.values
            for node in ast.walk(value)
            if isinstance(node, ast.Name)
        }
        self.assertIn("_agent_trace_id", names)
        self.assertNotIn("trace_id", names)

    def test_the_loop_publishes_the_trace_context_it_created(self):
        tree = ast.parse(SAGE_RUNTIME_SOURCE.read_text(encoding="utf-8"))
        published = [
            value
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and key.value == "agent_trace_id"
        ]
        self.assertEqual(len(published), 1)
        names = {n.id for n in ast.walk(published[0]) if isinstance(n, ast.Name)}
        self.assertIn("trace_context", names)


if __name__ == "__main__":
    unittest.main()

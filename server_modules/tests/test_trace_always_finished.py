"""An agent_traces row is never left open.

Two defects, one law between them: `agent_traces` needs exactly one finisher
per row, and every path must reach it.

1. `_run_sage_action_loop_v3` opened a trace and finished it only at its two
   normal exits. Anything that raised in between -- a provider error out of
   `asyncio.to_thread(_collect_stream_events)` on the SDK engine, which is the
   PRODUCTION DEFAULT, or a client disconnect cancelling the turn -- left
   `finished_at = NULL` forever. `prune_finished_agent_traces` gates on
   `finished_at IS NOT NULL` by design, so those rows are never reaped; the
   Work tab reads `!finished_at` as "still running" and shows a "Working" pill
   on a turn that died minutes ago; and `trace.completed` is what makes the
   SSE generator return, so an unfinished trace is a connection that never
   closes.

2. `finish_agent_trace`'s UPDATE had no `finished_at IS NULL` guard, so a
   second finisher silently overwrote the first, more precise outcome. That is
   also what made defect 1 unsafe to fix: a blanket crash-handler finisher
   could stomp a verdict a callee had already resolved correctly.

Fixing 2 is what makes 1 safe -- the guard lives on the one statement every
finisher goes through rather than as a rule each caller has to remember.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import agent_trace_service
from server_modules import agent_turn_runtime_service
from server_modules import control_plane_repository


def _run(coro):
    return asyncio.run(coro)


def _trace_context() -> agent_trace_service.TraceContext:
    return agent_trace_service.TraceContext(
        trace_id="trace-row-1",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        thread_id="thread-xyz",
        run_id=None,
        root_agent_id="sage",
    )


class TraceIsFinishedOnEveryExitTests(unittest.TestCase):
    """Drives the REAL `_run_sage_action_loop_v3` on the SDK engine (the
    production default) and asserts the trace it opens is closed on every
    exit -- success, crash, and cancellation.

    `start_trace` is patched to hand back a real TraceContext because with no
    database it otherwise returns None, and a None context makes every
    finisher a no-op -- the test would pass while proving nothing. The mock
    harness mirrors SdkEngineSessionContinuityIntegrationTests in
    test_agent_turn_runtime_service.py.
    """

    def _decision(self):
        from server_modules import kill_switch_gate

        return kill_switch_gate.KillSwitchDecision(blocked=False, reason="", scope="")

    def _call(self, collect_mock, *, finish_mock=None, completed_mock=None):
        """Run one SDK-engine turn with the given stream collector.

        finish_mock/completed_mock are passed IN rather than returned, because
        the cases that matter here are the ones where the loop raises -- a
        return value never arrives, so the assertions have to hold a reference
        the call could not hand back.
        """
        thread_row: dict = {"metadata": {}}
        finish_mock = finish_mock if finish_mock is not None else AsyncMock(return_value=None)
        completed_mock = completed_mock if completed_mock is not None else AsyncMock(return_value=None)

        async def _fake_merge(*, thread_id, tenant_id, workspace_id, metadata_patch):
            thread_row.setdefault("metadata", {}).update(metadata_patch)

        with (
            patch("server_modules.kill_switch_gate.evaluate_kill_switch", return_value=self._decision()),
            patch(
                "server_modules.agent_turn_runtime_service.thread_service.get_thread",
                new=AsyncMock(return_value={"metadata": {}}),
            ),
            patch(
                "server_modules.control_plane_repository.merge_agent_thread_metadata",
                new=AsyncMock(side_effect=_fake_merge),
            ),
            patch.object(
                agent_turn_runtime_service.agent_trace_service,
                "start_trace",
                new=AsyncMock(return_value=_trace_context()),
            ),
            patch.object(
                agent_turn_runtime_service.agent_trace_service,
                "finish_trace",
                new=finish_mock,
            ),
            patch.object(
                agent_turn_runtime_service.agent_trace_service,
                "emit_trace_completed",
                new=completed_mock,
            ),
            patch.object(
                agent_turn_runtime_service.claude_agent_sdk_bridge,
                "collect_events_via_claude_agent_sdk",
                new=collect_mock,
            ),
        ):
            _run(agent_turn_runtime_service._run_sage_action_loop_v3(
                workspace_id="ws-1", tenant_id="tenant-1", message="hello",
                provider="anthropic", model="claude", credentials={},
                trace_id="trace-1", actor_user_id="user-1", system_prompt="",
                prior_messages=[], agent_install_id="",
                engine_options={"engine": "claude_agent_sdk"},
                conversation_thread_id="thread-xyz",
            ))

    def _outcomes(self, finish_mock):
        return [c.kwargs.get("outcome") for c in finish_mock.await_args_list]

    def test_provider_crash_finishes_the_trace_as_failed(self):
        """THE BUG. `collect_events_via_claude_agent_sdk` raising is an
        ordinary provider/SDK failure on the default engine. Before the fix
        the exception unwound straight past both finishers and the row stayed
        `finished_at = NULL` forever.

        The count assertion is as load-bearing as the outcome: without it this
        could not tell "closed once, correctly" from "closed twice, the second
        overwriting the first".
        """
        boom = MagicMock(side_effect=RuntimeError("provider exploded mid-stream"))
        finish_mock = AsyncMock(return_value=None)
        completed_mock = AsyncMock(return_value=None)

        with self.assertRaises(RuntimeError) as ctx:
            self._call(boom, finish_mock=finish_mock, completed_mock=completed_mock)

        # The original error reaches the caller untouched -- the handler closes
        # the row, it never swallows or reinterprets the failure.
        self.assertIn("provider exploded mid-stream", str(ctx.exception))
        self.assertEqual(len(finish_mock.await_args_list), 1)
        self.assertEqual(self._outcomes(finish_mock), [agent_trace_service.TRACE_OUTCOME_FAILED])
        # trace.completed is what makes the SSE generator return; without it
        # the row closes but the customer's connection still hangs open.
        self.assertEqual(len(completed_mock.await_args_list), 1)

    def test_cancelled_turn_is_partial_not_failed(self):
        """A client disconnect (Cloudflare cuts a silent SSE stream at ~125s)
        cancels the task. That is "it stopped", not "it broke" -- work may well
        have happened first, and calling it failed would report a failure that
        did not occur."""
        cancelled = MagicMock(side_effect=asyncio.CancelledError())
        finish_mock = AsyncMock(return_value=None)

        with self.assertRaises(asyncio.CancelledError):
            self._call(cancelled, finish_mock=finish_mock)

        self.assertEqual(self._outcomes(finish_mock), [agent_trace_service.TRACE_OUTCOME_PARTIAL])

    def test_success_path_finishes_exactly_once(self):
        """The fix must not double-finish the normal path: the crash handler is
        reached only by a raise, so a clean turn still closes its row once,
        with the precise verdict its own exit resolved."""
        ok = MagicMock(return_value=[
            {"type": "final", "payload": {"reply": "all done", "session_id": "sess-1"}},
        ])
        finish_mock = AsyncMock(return_value=None)
        self._call(ok, finish_mock=finish_mock)

        self.assertEqual(len(finish_mock.await_args_list), 1)
        self.assertEqual(self._outcomes(finish_mock), [agent_trace_service.TRACE_OUTCOME_SUCCESS])


class FirstFinisherWinsTests(unittest.IsolatedAsyncioTestCase):
    """`finish_agent_trace` is the narrow waist every finisher goes through.
    First writer wins, and "already finished" must stay distinguishable from
    "no such trace" -- two different facts may never share one signal."""

    @staticmethod
    def _scoped(connection):
        class _Ctx:
            async def __aenter__(self):
                return connection

            async def __aexit__(self, *args):
                return False

        def _factory(*args, **kwargs):
            return _Ctx()

        return _factory

    async def test_update_refuses_to_overwrite_a_finished_trace(self):
        connection = MagicMock()
        connection.fetchrow = AsyncMock(return_value=None)

        with patch("server_modules.control_plane_repository._scoped_connection", new=self._scoped(connection)):
            await control_plane_repository.finish_agent_trace(
                "trace_1", tenant_id="tenant-1", workspace_id="ws-1", outcome="failed",
            )

        update_sql = connection.fetchrow.await_args_list[0].args[0]
        self.assertIn("UPDATE agent_traces", update_sql)
        self.assertIn("finished_at IS NULL", update_sql)

    async def test_already_finished_returns_the_verdict_that_stands(self):
        """A losing finisher gets back the row as it actually is, so the caller
        can see the outcome that won rather than a bare None."""
        standing = {
            "id": "trace_1", "tenant_id": "tenant-1", "workspace_id": "ws-1",
            "thread_id": "thread-1", "run_id": None, "root_agent_id": "sage",
            "surface": "sage", "runtime_target": None, "provider": "anthropic",
            "model": "claude", "started_at": "2026-08-31T12:00:00Z",
            "finished_at": "2026-08-31T12:00:09Z", "outcome": "success",
            "final_message_id": "msg-1",
        }
        connection = MagicMock()
        # UPDATE matches nothing (already finished), then the SELECT read-back.
        connection.fetchrow = AsyncMock(side_effect=[None, standing])

        with patch("server_modules.control_plane_repository._scoped_connection", new=self._scoped(connection)):
            row = await control_plane_repository.finish_agent_trace(
                "trace_1", tenant_id="tenant-1", workspace_id="ws-1", outcome="failed",
            )

        self.assertIsNotNone(row)
        # The precise verdict survives; the coarse late one was discarded.
        self.assertEqual(row["outcome"], "success")
        self.assertEqual(len(connection.fetchrow.await_args_list), 2)
        self.assertIn("SELECT", connection.fetchrow.await_args_list[1].args[0])

    async def test_no_such_trace_still_returns_none(self):
        """The other half of the same law: None keeps meaning exactly what it
        means today -- there is no such row -- and never doubles as "already
        finished"."""
        connection = MagicMock()
        connection.fetchrow = AsyncMock(side_effect=[None, None])

        with patch("server_modules.control_plane_repository._scoped_connection", new=self._scoped(connection)):
            row = await control_plane_repository.finish_agent_trace(
                "trace_1", tenant_id="tenant-1", workspace_id="ws-1", outcome="failed",
            )

        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()

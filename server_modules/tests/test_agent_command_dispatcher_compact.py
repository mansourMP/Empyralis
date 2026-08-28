"""Tests for agent_command_dispatcher._handle_compact — the manual `/sage
compact` command handler.

2026-07-24 compaction end-to-end fix pass:
  - BUG 2: find_cut_point can return 0 ("nothing to cut") even when
    should_compact() just said the turn is over threshold. The old handler
    reported SAGE_COMPACTED regardless — a "returns ok while doing
    nothing" bug the owner law explicitly prohibits. Now honest: only
    claims success when compact_turns actually ran and produced a summary.
  - BUG 5: should_compact/find_cut_point_with_fallback now take provider/
    model so the real per-model threshold formula applies here too.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_command_dispatcher


def _run(coro):
    return asyncio.run(coro)


def _thread_record(turns):
    return {"turns": turns}


class HandleCompactHonestyTests(unittest.TestCase):
    """The core BUG 2 fix: never report SAGE_COMPACTED without having
    actually run compact_turns and gotten a real summary back."""

    def _patched(self, *, should_compact_result, cut_idx_and_forced, compact_turns_result):
        return (
            patch(
                "server_modules.compaction_service.resolve_context_window",
                return_value=200_000,
            ),
            patch(
                "server_modules.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"metadata": {}}),
            ),
            patch(
                "server_modules.thread_service.ensure_master_thread",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.thread_service.get_thread",
                new=AsyncMock(return_value=_thread_record([
                    {"role": "user", "content": "hi " * 200},
                    {"role": "assistant", "content": "hello " * 200},
                ])),
            ),
            patch(
                "server_modules.compaction_service.should_compact",
                return_value=should_compact_result,
            ),
            patch(
                "server_modules.compaction_service.find_cut_point_with_fallback",
                return_value=cut_idx_and_forced,
            ),
            patch(
                "server_modules.compaction_service.load_previous_summary",
                new=AsyncMock(return_value=""),
            ),
            patch(
                "server_modules.compaction_service.compact_turns",
                new=AsyncMock(return_value=compact_turns_result),
            ),
        )

    def test_reports_compacted_only_when_a_real_summary_was_produced(self):
        patches = self._patched(
            should_compact_result=True,
            cut_idx_and_forced=(1, False),
            compact_turns_result="A real summary.",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7] as compact_mock:
            reply = _run(agent_command_dispatcher._handle_compact("ws-1", "thread-1"))
        self.assertEqual(reply, agent_command_dispatcher.SAGE_COMPACTED)
        compact_mock.assert_awaited_once()

    def test_never_reports_compacted_when_cut_idx_is_zero_even_with_forced_fallback(self):
        # BUG 2's exact regression: should_compact() says "over threshold"
        # but find_cut_point_with_fallback found nothing cuttable even with
        # the forced floor (forced=True, cut_idx=0). The OLD code still
        # returned SAGE_COMPACTED here — never having called compact_turns
        # at all.
        patches = self._patched(
            should_compact_result=True,
            cut_idx_and_forced=(0, True),
            compact_turns_result="unused",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7] as compact_mock:
            reply = _run(agent_command_dispatcher._handle_compact("ws-1", "thread-1"))
        self.assertEqual(reply, agent_command_dispatcher.SAGE_COMPACT_NOT_NEEDED)
        compact_mock.assert_not_awaited()

    def test_reports_not_needed_when_compact_turns_itself_produces_no_summary(self):
        # compact_turns already logs/traces WHY (no provider, empty
        # summary, overflow) — the command handler must not compound that
        # with a false "compacted" claim.
        patches = self._patched(
            should_compact_result=True,
            cut_idx_and_forced=(1, False),
            compact_turns_result="",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7] as compact_mock:
            reply = _run(agent_command_dispatcher._handle_compact("ws-1", "thread-1"))
        self.assertEqual(reply, agent_command_dispatcher.SAGE_COMPACT_NOT_NEEDED)
        compact_mock.assert_awaited_once()

    def test_should_compact_false_skips_everything(self):
        patches = self._patched(
            should_compact_result=False,
            cut_idx_and_forced=(1, False),
            compact_turns_result="unused",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7] as compact_mock:
            reply = _run(agent_command_dispatcher._handle_compact("ws-1", "thread-1"))
        self.assertEqual(reply, agent_command_dispatcher.SAGE_COMPACT_NOT_NEEDED)
        compact_mock.assert_not_awaited()

    def test_should_compact_receives_provider_and_model(self):
        # BUG 5: the real per-model threshold formula needs provider/model,
        # not just context_window.
        patches = self._patched(
            should_compact_result=False,
            cut_idx_and_forced=(1, False),
            compact_turns_result="unused",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4] as should_compact_mock, patches[5], patches[6], patches[7]:
            _run(agent_command_dispatcher._handle_compact("ws-1", "thread-1"))
        should_compact_mock.assert_called_once()
        call_kwargs = should_compact_mock.call_args.kwargs
        self.assertIn("provider", call_kwargs)
        self.assertIn("model", call_kwargs)


if __name__ == "__main__":
    unittest.main()

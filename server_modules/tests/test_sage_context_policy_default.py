"""STEP 2 — sane default for max_context_tokens when no per-agent
context_policy is configured.

Runs the full execute_sage_turn -> handle_sage_chat path (same harness shape
as test_sage_governance_gate_e2e.py's TelegramHostedE2ETests) with a large
model context window and enough estimated tokens to sit between the sane
default (128K) and the model's raw window (1M) — the exact gap this default
closes. Proves the OBSERVABLE effect (proactive compaction fires) rather
than reading the internal _ctx_policy_max variable directly.

_run_sage_action_loop_v3 is mocked to return None (not a reply dict): traced
this while building the test — a successful action-loop result returns at
line ~4534, *before* the B2 proactive-compaction block is ever reached, so
that path can't observe this fix either way. B2 (and the Phase 5C context
policy it reads) is wired to the action_result-is-None fallback branch only
— a pre-existing characteristic of this function, not something this PR
changes (see the STEP 0 report for why the fix stops at the default value
and doesn't touch the compaction mechanism's wiring).
"""

from __future__ import annotations

import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from server_modules.agent_turn_adapter import execute_sage_turn


def _run(coro):
    return asyncio.run(coro)


# Simulates a big-window model (e.g. a 1M-token-class model) so an unclamped
# window would never trigger compaction at the estimated size used below.
_BIG_MODEL_WINDOW = 1_000_000

# COMPACTION_RESERVE_TOKENS is 16384; this pushes _proactive_estimated to
# ~166K — above the 128K sane default, comfortably below the 1M raw window.
_ESTIMATED_INPUT_TOKENS = 150_000

# Larger than the sane default — proves an explicit per-agent value still
# wins rather than being clamped down to 128K.
_EXPLICIT_LARGER_CAP = 500_000


class ContextPolicyDefaultE2ETests(unittest.TestCase):
    def _base_patches(self, *, get_master_install=None):
        """The same I/O-boundary mocks test_sage_governance_gate_e2e.py uses
        to run the full handle_sage_chat path, plus the compaction-window
        mocks this test needs. agent_trace_service.start_trace returns None
        (a real, handled case — see start_trace's own `if not trace: return
        None`) rather than the existing tests' dict, which doesn't satisfy
        TraceContext's actual attribute contract."""
        patches = [
            patch("server_modules.agent_turn_runtime_service.assistant_profile_service.list_sage_profile",
                  return_value={"profile": {"user_name": "Test"}}),
            patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.agent_turn_runtime_service.assistant_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.agent_turn_runtime_service.assistant_health_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.agent_turn_runtime_service._resolve_cloud_provider",
                  return_value=("deepseek", {"api_key": "test-key"})),
            patch("server_modules.agent_turn_runtime_service.generate_chat_reply_with_provider_fallback",
                  return_value=("OK", {"model": "deepseek-chat"}, "deepseek", "")),
            patch("server_modules.agent_turn_runtime_service.persist_interaction"),
            patch("server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.agent_turn_runtime_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.agent_turn_runtime_service.thread_service.ensure_master_thread",
                  new=AsyncMock()),
            patch("server_modules.agent_turn_runtime_service.thread_service.get_thread",
                  new=AsyncMock(return_value={"turns": []})),
            patch("server_modules.agent_turn_runtime_service.agent_trace_service.start_trace",
                  new=AsyncMock(return_value=None)),
            # Routes into the action_result-is-None fallback branch, where B2
            # (and the Phase 5C context policy this test exercises) actually
            # lives — see the module docstring.
            patch("server_modules.agent_turn_runtime_service._run_sage_action_loop_v3",
                  new=AsyncMock(return_value=None)),
            # Force a big raw model window so only the policy clamp (or lack
            # of one) decides whether compaction triggers at ~166K estimated.
            patch("server_modules.compaction_service.resolve_context_window",
                  return_value=_BIG_MODEL_WINDOW),
            patch("server_modules.compaction_service.estimate_tokens",
                  return_value=_ESTIMATED_INPUT_TOKENS),
        ]
        if get_master_install is not None:
            patches.append(patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value=get_master_install),
            ))
        return patches

    def _run_with_flush_probe(self, get_master_install):
        """Runs a turn and returns whether the proactive-compaction flush
        step was reached — the observable signal that _proactive_ctx_window
        got clamped below the ~166K estimated size."""
        with ExitStack() as stack:
            flush_mock = stack.enter_context(patch(
                "server_modules.agent_turn_runtime_service._run_memory_flush_before_compaction",
                new=AsyncMock(return_value=False),
            ))
            for p in self._base_patches(get_master_install=get_master_install):
                stack.enter_context(p)
            _run(execute_sage_turn(
                workspace_id="ws-1",
                message="hello",
                channel_origin="telegram_hosted",
                channel_sender_id="user-1",
            ))
        return flush_mock.await_count > 0

    def test_unset_context_policy_uses_sane_default_and_triggers_compaction(self):
        """No context_policy on the master install at all — today this left
        _ctx_policy_max at 0 (no clamp), so a 1M-window model would never
        proactively compact at ~166K estimated tokens. With the sane
        128K default, it must."""
        master_install = {"id": "ainstall_master", "project_id": "", "metadata": {}}
        compaction_triggered = self._run_with_flush_probe(master_install)
        self.assertTrue(
            compaction_triggered,
            "expected the 128K sane default to clamp the window and trigger "
            "proactive compaction at ~166K estimated tokens",
        )

    def test_explicit_larger_cap_still_wins_over_the_default(self):
        """An agent that explicitly configures a cap larger than the sane
        default must not be clamped down to it."""
        master_install = {
            "id": "ainstall_master",
            "project_id": "",
            "metadata": {"context_policy": {"max_context_tokens": _EXPLICIT_LARGER_CAP}},
        }
        compaction_triggered = self._run_with_flush_probe(master_install)
        self.assertFalse(
            compaction_triggered,
            "an explicit max_context_tokens larger than the sane default "
            "must win — compaction should not trigger below it",
        )

    def test_explicit_smaller_cap_still_wins_over_the_default(self):
        """An agent that explicitly configures a cap smaller than the sane
        default must compact at its own, tighter threshold."""
        master_install = {
            "id": "ainstall_master",
            "project_id": "",
            "metadata": {"context_policy": {"max_context_tokens": 50_000}},
        }
        compaction_triggered = self._run_with_flush_probe(master_install)
        self.assertTrue(
            compaction_triggered,
            "an explicit max_context_tokens smaller than the sane default "
            "must still trigger compaction at its own threshold",
        )


if __name__ == "__main__":
    unittest.main()

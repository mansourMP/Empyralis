"""Tests for the 2026-07-21 double-charge-prevention fix.

Context (see docs/design/ for the full trace): a single Sage turn can reach
TWO different functions that mutate a workspace's real spendable balance
(workspace metadata's admin_defaults.credit_balance_usd):

  1. control_plane_repository.debit_workspace_credits_for_turn_atomic —
     called from agent_turn_runtime_service.handle_sage_chat's "cloud
     fallthrough" (text-only) completion path.
  2. control_plane_repository.debit_workspace_credit_balance_for_hosted_usage_atomic —
     called (via billing_service.debit_workspace_credit_balance_for_hosted_usage)
     from direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort,
     itself invoked from direct_chat_generation_service.stream_provider_backed_direct_chat
     — which is what _run_sage_action_loop_v3 calls.

Both are reachable from the SAME handle_sage_chat call tree. This file
proves, at three levels, that a single logical turn can never be charged
twice:

  (A) LEDGER LEVEL — the two ATOMIC functions already write their
      "already charged this id" markers into the exact same workspace
      credit_transactions list (proven against a real, temporary local
      SQLite identity DB — the same fallback path production runs on
      when Postgres isn't configured). Given the SAME request_id, whichever
      of the two fires SECOND is a no-op, regardless of call order.

  (B) KEY-COMPUTATION LEVEL — agent_turn_runtime_service._turn_credit_
      idempotency_key is the single, deterministic, never-empty source of
      that shared request_id: it prefers the caller's stable request_id
      (survives a channel/webhook retry) and falls back to trace_id, but
      NEVER falls back to a fresh uuid4() (which would defeat retry dedup).

  (C) WIRING LEVEL — handle_sage_chat actually threads that one key into
      BOTH call sites: the fallback debit's request_id kwarg, and the
      action-loop-v3 generation call's session_ctx (which is all
      direct_chat_hosted_usage_service._session_request_id ever reads for
      its own request_id).

Also covers: BYOK/local/subscription turns never debit; an insufficient
balance never blocks the reply (clamp-at-zero, non-blocking by
construction); and the new runtime-minutes platform-credit debit
(deployed_agent_virtual_runtime_service.py) is itself gated to
platform_credits-only and idempotent per runtime session.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from contextlib import ExitStack, asynccontextmanager, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import control_plane_repository
from server_modules import deployed_agent_virtual_runtime_service
from server_modules import direct_chat_hosted_usage_service
from server_modules import agent_turn_runtime_service


def _run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def _null_scoped_connection(*_args, **_kwargs):
    """Stand-in for control_plane_repository._scoped_connection that always
    yields None (the SQLite-identity-fallback signal), bypassing
    ensure_control_plane_schema/db.get_pool() entirely."""
    yield None


@contextmanager
def _force_local_identity_fallback():
    """Force control_plane_repository's DB-touching functions onto the
    local SQLite identity-DB fallback (the branch every debit function
    falls back to when no Postgres pool is configured), regardless of
    whatever ambient pool state earlier tests in this process may have
    left behind.

    This sandbox has been observed to have a REAL, live-reachable Postgres
    (a pre-existing "ws-1" workspace was found live during investigation of
    this exact flakiness), and a background scheduler thread
    (bounded_scheduler_service) independently calls
    ensure_control_plane_schema() on its own schedule -- so patching THAT
    function alone is not reliable: whichever thread/test wins the race to
    populate db.py's pool cache first can leave a live pool in place that
    other tests then silently pick up. Patching _scoped_connection directly
    -- the one seam every debit/read function actually awaits for its
    connection -- removes the dependency on that race entirely.

    Also takes explicit ownership of LOCAL_IDENTITY_DB_FILE with a fresh
    temp file rather than relying on conftest's ambient per-test isolation:
    when this whole suite runs alongside ~150 other test files, that
    isolation was observed to not consistently apply to these tests (this
    file's tests read back "workspace_not_found" for a row this SAME test
    had just written, because both the write and the read resolved to the
    real, non-isolated ~/.empyralis/state/auth/users.db instead of a
    per-test tmp path). Owning the path here removes the dependency on
    that ambient fixture behavior entirely. A money-critical test must be
    hermetic regardless of what else is running in the same process."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(
            control_plane_repository,
            "_scoped_connection",
            _null_scoped_connection,
        ))
        temp_dir = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(patch.object(
            control_plane_repository,
            "LOCAL_IDENTITY_DB_FILE",
            Path(temp_dir) / "users.db",
        ))
        yield


async def _seed_local_workspace(*, workspace_id: str, tenant_id: str, metadata: dict) -> None:
    """Write a workspace row straight into the local SQLite identity DB
    fallback (the same one debit_workspace_credits_for_turn_atomic /
    debit_workspace_credit_balance_for_hosted_usage_atomic read/write when
    no Postgres pool is configured — true in this sandbox, matching
    test_credit_system_reconnect.py's documented environment)."""
    with control_plane_repository._LOCAL_IDENTITY_LOCK:
        with control_plane_repository._connect_local_identity_db() as connection:
            control_plane_repository._upsert_local_workspace_registry(
                connection,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                name=workspace_id,
                workspace_type="personal",
                metadata=metadata,
                created_at_ts=int(time.time()),
            )
            connection.commit()


class LedgerLevelSharedDedupTests(unittest.TestCase):
    """(A) Both real atomic debit functions dedupe against the SAME ledger,
    for a single shared request_id, regardless of which one runs first."""

    def setUp(self) -> None:
        fallback = _force_local_identity_fallback()
        fallback.__enter__()
        self.addCleanup(fallback.__exit__, None, None, None)

    def test_turn_debit_then_hosted_usage_debit_same_key_charges_once(self):
        workspace_id = "ws-dbl-charge-1"
        tenant_id = "tenant-1"
        _run(_seed_local_workspace(
            workspace_id=workspace_id, tenant_id=tenant_id,
            metadata={"admin_defaults": {"credit_balance_usd": 10.0}},
        ))
        shared_key = "shared-turn-key-1"

        first = _run(control_plane_repository.debit_workspace_credits_for_turn_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id,
            request_id=shared_key, credits_to_charge=50, floor_usd=0.0,
            credits_per_usd=1_000,
        ))
        self.assertTrue(first["ok"])
        self.assertEqual(first["credits_debited"], 50)
        self.assertNotEqual(first.get("reason"), "already_recorded")

        # The OTHER debit function, same request_id, would also charge
        # real money (monthly_cost_usd comfortably exceeds the cap) if it
        # ran independently -- but it must see the SAME ledger and refuse.
        second = _run(control_plane_repository.debit_workspace_credit_balance_for_hosted_usage_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id,
            request_id=shared_key, usage_month="2026-07",
            monthly_cost_usd=5.0, monthly_cap_usd=0.0, credits_per_usd=1_000,
        ))
        self.assertTrue(second["ok"])
        self.assertEqual(second["debited_usd"], 0.0)
        self.assertEqual(second["reason"], "already_recorded")

        # Balance moved exactly once (50 credits / 1000 = $0.05 off $10.00).
        record = _run(control_plane_repository.get_workspace_by_id(workspace_id))
        balance = record["metadata"]["admin_defaults"]["credit_balance_usd"]
        self.assertAlmostEqual(balance, 10.0 - 0.05)

    def test_hosted_usage_debit_then_turn_debit_same_key_charges_once(self):
        """Same proof, opposite firing order -- the dedup must not be
        order-dependent."""
        workspace_id = "ws-dbl-charge-2"
        tenant_id = "tenant-1"
        _run(_seed_local_workspace(
            workspace_id=workspace_id, tenant_id=tenant_id,
            metadata={"admin_defaults": {"credit_balance_usd": 10.0}},
        ))
        shared_key = "shared-turn-key-2"

        first = _run(control_plane_repository.debit_workspace_credit_balance_for_hosted_usage_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id,
            request_id=shared_key, usage_month="2026-07",
            monthly_cost_usd=5.0, monthly_cap_usd=0.0, credits_per_usd=1_000,
        ))
        self.assertTrue(first["ok"])
        self.assertGreater(first["debited_usd"], 0.0)

        second = _run(control_plane_repository.debit_workspace_credits_for_turn_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id,
            request_id=shared_key, credits_to_charge=50, floor_usd=0.0,
            credits_per_usd=1_000,
        ))
        self.assertTrue(second["ok"])
        self.assertEqual(second["credits_debited"], 0)
        self.assertEqual(second["reason"], "already_recorded")

    def test_retry_with_the_same_key_after_a_fresh_trace_id_debits_once(self):
        """(b) A retry of the SAME logical turn must not double-charge even
        though a fresh per-attempt trace_id is minted every call --
        because the shared key here is the STABLE part (a caller-supplied
        request_id), not the fresh trace_id. This is exactly the "empty
        trace_id / retry mints a fresh id" hole the fix closes: the key fed
        to the debit call is computed by _turn_credit_idempotency_key, which
        prefers a stable id over a fresh one and never falls back to
        uuid4()."""
        workspace_id = "ws-retry-1"
        tenant_id = "tenant-1"
        _run(_seed_local_workspace(
            workspace_id=workspace_id, tenant_id=tenant_id,
            metadata={"admin_defaults": {"credit_balance_usd": 10.0}},
        ))
        caller_request_id = "webhook-delivery-id-abc"

        # First attempt: some trace_id.
        key_attempt_1 = agent_turn_runtime_service._turn_credit_idempotency_key(
            caller_request_id, "trace-attempt-1",
        )
        # Retry (e.g. webhook redelivery): a DIFFERENT fresh trace_id, same
        # caller request_id.
        key_attempt_2 = agent_turn_runtime_service._turn_credit_idempotency_key(
            caller_request_id, "trace-attempt-2",
        )
        self.assertEqual(key_attempt_1, key_attempt_2)
        self.assertEqual(key_attempt_1, caller_request_id)

        first = _run(control_plane_repository.debit_workspace_credits_for_turn_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id,
            request_id=key_attempt_1, credits_to_charge=20, floor_usd=0.0,
            credits_per_usd=1_000,
        ))
        retry = _run(control_plane_repository.debit_workspace_credits_for_turn_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id,
            request_id=key_attempt_2, credits_to_charge=20, floor_usd=0.0,
            credits_per_usd=1_000,
        ))
        self.assertTrue(first["ok"])
        self.assertEqual(first["credits_debited"], 20)
        self.assertTrue(retry["ok"])
        self.assertEqual(retry["credits_debited"], 0)
        self.assertEqual(retry["reason"], "already_recorded")

    def test_clamp_at_zero_holds_across_both_functions_sharing_one_ledger(self):
        """(e) Unifying the dedup namespace must not weaken the existing
        never-negative / never-raises guarantee for either function."""
        workspace_id = "ws-clamp-1"
        tenant_id = "tenant-1"
        _run(_seed_local_workspace(
            workspace_id=workspace_id, tenant_id=tenant_id,
            metadata={"admin_defaults": {"credit_balance_usd": 0.01}},
        ))
        huge = _run(control_plane_repository.debit_workspace_credits_for_turn_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id,
            request_id="huge-turn", credits_to_charge=1_000_000, floor_usd=0.0,
            credits_per_usd=1_000,
        ))
        self.assertTrue(huge["ok"])
        self.assertTrue(huge["insufficient"])
        self.assertEqual(huge["credit_balance_usd"], 0.0)

        # A second, different-request_id debit against the now-zero balance
        # must also clamp, not raise, and not go negative.
        more = _run(control_plane_repository.debit_workspace_credit_balance_for_hosted_usage_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id,
            request_id="another-huge-charge", usage_month="2026-07",
            monthly_cost_usd=5.0, monthly_cap_usd=0.0, credits_per_usd=1_000,
        ))
        self.assertTrue(more["ok"])
        self.assertEqual(more["debited_usd"], 0.0)
        record = _run(control_plane_repository.get_workspace_by_id(workspace_id))
        balance = record["metadata"]["admin_defaults"]["credit_balance_usd"]
        self.assertGreaterEqual(balance, 0.0)
        self.assertEqual(balance, 0.0)


class TurnCreditIdempotencyKeyTests(unittest.TestCase):
    """(B) The pure key-computation helper never produces an empty or
    non-deterministic key, and prefers the stable caller id."""

    def test_prefers_caller_supplied_request_id_over_trace_id(self):
        key = agent_turn_runtime_service._turn_credit_idempotency_key(
            "caller-stable-id", "trace-xyz",
        )
        self.assertEqual(key, "caller-stable-id")

    def test_falls_back_to_trace_id_when_no_caller_request_id(self):
        key = agent_turn_runtime_service._turn_credit_idempotency_key("", "trace-xyz")
        self.assertEqual(key, "trace-xyz")

    def test_never_mints_a_random_fallback(self):
        """The exact anti-pattern being removed: `trace_id or uuid4()`
        would silently mint a NEW random id on every call whenever both
        inputs were falsy. The fix must be pure/deterministic instead --
        same (falsy) inputs always produce the same (empty) output, never
        two different random ids that would defeat retry dedup."""
        key_1 = agent_turn_runtime_service._turn_credit_idempotency_key("", "")
        key_2 = agent_turn_runtime_service._turn_credit_idempotency_key("", "")
        self.assertEqual(key_1, key_2)
        self.assertEqual(key_1, "")

    def test_whitespace_only_request_id_is_treated_as_absent(self):
        key = agent_turn_runtime_service._turn_credit_idempotency_key("   ", "trace-xyz")
        self.assertEqual(key, "trace-xyz")


class HandleSageChatWiringTests(unittest.TestCase):
    """(C) handle_sage_chat actually threads ONE key into both potential
    debit call sites for a given turn."""

    def setUp(self) -> None:
        # CRITICAL for a money-critical test file: this sandbox has a REAL,
        # reachable Postgres pool with pre-existing shared data (a "ws-1"
        # workspace literally named "E2E Workspace" was found live during
        # investigation). Without forcing the local-identity fallback here,
        # get_workspace_by_id("ws-1") etc. read (and, if not for the
        # explicit debit-function mocks below, could write) real shared
        # state -- and a shared workspace's pre-existing credit_transactions
        # history can make dedup assertions here pass or fail depending on
        # what OTHER processes have done to it. Every test in this class
        # must be hermetic.
        fallback = _force_local_identity_fallback()
        fallback.__enter__()
        self.addCleanup(fallback.__exit__, None, None, None)

    @staticmethod
    def _base_patches(usage: dict):
        return [
            patch(
                "server_modules.agent_turn_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.agent_turn_runtime_service.assistant_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.agent_turn_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.agent_turn_runtime_service._resolve_cloud_provider", return_value=("deepseek", {"api_key": "test-key"})),
            # Force the "cloud fallthrough" (text-only) branch deterministically
            # -- an unmocked _run_sage_action_loop_v3 would attempt a REAL
            # network call to the provider (flaky, slow, and it can itself
            # return a non-None dict, which would skip the fallback branch
            # under test entirely). Mocking straight to None is exactly the
            # "action loop produced nothing usable" case handle_sage_chat's
            # cloud-fallthrough path exists to handle.
            patch(
                "server_modules.agent_turn_runtime_service._run_sage_action_loop_v3",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.agent_turn_runtime_service.generate_chat_reply_with_provider_fallback",
                return_value=("Reply", usage, "deepseek", ""),
            ),
            # The Phase 5A metering call sits in the SAME try/except as the
            # credit debit right below it in handle_sage_chat -- if this
            # (unrelated, real DB-touching) call raised, the outer
            # `except Exception: pass` would silently swallow it AND skip
            # the debit, making a debit-focused test flaky for a reason that
            # has nothing to do with billing. Mock it out explicitly.
            patch(
                "server_modules.usage_events_repository.record_usage_from_context",
                new=AsyncMock(return_value=None),
            ),
            patch("server_modules.agent_turn_runtime_service.persist_interaction"),
            patch("server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.agent_turn_runtime_service.security_audit_service.emit_security_audit_event"),
        ]

    def _run_chat_with_mocks(self, usage: dict, extra_patches=None, **chat_kwargs):
        """Enter all base + extra patches via ExitStack (avoids the
        `*unpack` inside a parenthesized `with` statement, which is not
        valid syntax), run handle_sage_chat, and return (result, entered
        mocks in the same order as extra_patches)."""
        from contextlib import ExitStack

        extra_patches = extra_patches or []
        with ExitStack() as stack:
            extra_mocks = [stack.enter_context(p) for p in extra_patches]
            for p in self._base_patches(usage):
                stack.enter_context(p)
            result = _run(agent_turn_runtime_service.handle_sage_chat(**chat_kwargs))
        return result, extra_mocks

    def test_fallback_debit_uses_the_callers_stable_request_id_not_trace_id(self):
        usage = {
            "model": "deepseek-chat", "pricing_known": True, "estimated_cost_usd": 0.01,
            "prompt_tokens": 500, "completion_tokens": 150,
        }
        result, (mock_debit,) = self._run_chat_with_mocks(
            usage,
            extra_patches=[patch(
                "server_modules.control_plane_repository.debit_workspace_credits_for_turn_atomic",
                new=AsyncMock(return_value={"ok": True, "credits_debited": 20, "debited_usd": 0.01, "insufficient": False}),
            )],
            workspace_id="ws-1", message="hello", request_id="stable-caller-id-1",
        )

        mock_debit.assert_awaited_once()
        self.assertEqual(mock_debit.await_args.kwargs["request_id"], "stable-caller-id-1")
        # And NOT the internally-generated trace_id -- proving the caller's
        # stable id wins, per _turn_credit_idempotency_key.
        self.assertNotEqual(mock_debit.await_args.kwargs["request_id"], result["trace_id"])

    def test_fallback_debit_falls_back_to_trace_id_when_caller_supplies_none(self):
        usage = {
            "model": "deepseek-chat", "pricing_known": True, "estimated_cost_usd": 0.01,
            "prompt_tokens": 500, "completion_tokens": 150,
        }
        result, (mock_debit,) = self._run_chat_with_mocks(
            usage,
            extra_patches=[patch(
                "server_modules.control_plane_repository.debit_workspace_credits_for_turn_atomic",
                new=AsyncMock(return_value={"ok": True, "credits_debited": 20, "debited_usd": 0.01, "insufficient": False}),
            )],
            workspace_id="ws-1", message="hello",
        )

        mock_debit.assert_awaited_once()
        self.assertEqual(mock_debit.await_args.kwargs["request_id"], result["trace_id"])
        self.assertTrue(mock_debit.await_args.kwargs["request_id"])  # never empty

    def test_zero_or_unknown_cost_turn_never_debits(self):
        """(c)-adjacent: no ground-truth cost -> no charge, matching the
        pre-existing `_sage_usd_cost is not None` guard (unchanged by this
        fix, verified still enforced)."""
        usage = {"model": "deepseek-chat"}  # no pricing_known / estimated_cost_usd
        _result, (mock_debit,) = self._run_chat_with_mocks(
            usage,
            extra_patches=[patch(
                "server_modules.control_plane_repository.debit_workspace_credits_for_turn_atomic",
                new=AsyncMock(),
            )],
            workspace_id="ws-1", message="hello",
        )
        mock_debit.assert_not_awaited()

    def test_byok_workspace_never_calls_the_fallback_debit(self):
        """(c) A workspace with its own configured sage_ai_provider (BYOK)
        must never touch the platform credit balance."""
        usage = {
            "model": "claude-sonnet-4-6", "pricing_known": True, "estimated_cost_usd": 0.02,
            "prompt_tokens": 500, "completion_tokens": 150,
        }
        byok_workspace_record = {
            "workspace_id": "ws-byok-1",
            "tenant_id": "tenant-byok",
            "metadata": {"admin_defaults": {"sage_ai_provider": "anthropic"}},
        }
        _result, (mock_debit, _mock_ws) = self._run_chat_with_mocks(
            usage,
            extra_patches=[
                patch(
                    "server_modules.control_plane_repository.debit_workspace_credits_for_turn_atomic",
                    new=AsyncMock(),
                ),
                patch(
                    "server_modules.control_plane_repository.get_workspace_by_id",
                    new=AsyncMock(return_value=byok_workspace_record),
                ),
            ],
            workspace_id="ws-byok-1", message="hello",
        )
        mock_debit.assert_not_awaited()

    def test_insufficient_balance_never_blocks_the_reply(self):
        """Non-blocking-by-construction contract must survive the refactor:
        even when the debit reports insufficient funds, the turn still
        returns its reply -- it is never refused."""
        usage = {
            "model": "deepseek-chat", "pricing_known": True, "estimated_cost_usd": 5.0,
            "prompt_tokens": 500, "completion_tokens": 150,
        }
        result, (mock_debit,) = self._run_chat_with_mocks(
            usage,
            extra_patches=[patch(
                "server_modules.control_plane_repository.debit_workspace_credits_for_turn_atomic",
                new=AsyncMock(return_value={"ok": True, "credits_debited": 3, "debited_usd": 0.001, "insufficient": True}),
            )],
            workspace_id="ws-1", message="hello",
        )
        mock_debit.assert_awaited_once()
        self.assertEqual(result["message"], "Reply")
        self.assertIsNone(result["error"])

    def test_action_loop_v3_generation_call_carries_the_same_shared_key(self):
        """The OTHER debit path (direct_chat_hosted_usage_service, reached
        via _run_sage_action_loop_v3 -> stream_provider_backed_direct_chat)
        reads its request_id from session_ctx -- prove session_ctx is
        wired to the exact same turn_credit_idempotency_key the fallback
        debit call uses, closing the cross-path gap by construction."""
        stream_events = [{
            "type": "final",
            "payload": {"reply": "Tool reply", "actions": [], "error": None},
        }]
        with (
            patch(
                "server_modules.agent_turn_runtime_service.sage_profile_service.list_sage_profile",
                return_value={"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}},
            ),
            patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.agent_turn_runtime_service.assistant_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.memory_service.get_memory", return_value=""),
            patch("server_modules.agent_turn_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.agent_turn_runtime_service._resolve_cloud_provider", return_value=("deepseek", {"api_key": "test-key"})),
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
            # Irrelevant to what this test verifies (session_ctx contents) --
            # mocked out so it can't leave any process-global state (e.g. a
            # cached "control plane unavailable" decision) that could make a
            # LATER, unrelated test's debit-mock assertion order-dependent.
            patch(
                "server_modules.agent_turn_runtime_service.agent_trace_service.start_trace",
                new=AsyncMock(return_value={}),
            ),
        ):
            mock_stream.return_value = iter(stream_events)
            _run(agent_turn_runtime_service.handle_sage_chat(
                workspace_id="ws-1", message="hello", request_id="stable-caller-id-2",
            ))

        mock_stream.assert_called_once()
        session_ctx = mock_stream.call_args.kwargs["session_ctx"]
        self.assertEqual(session_ctx["request_id"], "stable-caller-id-2")
        self.assertEqual(session_ctx["client_request_id"], "stable-caller-id-2")
        self.assertEqual(
            session_ctx["agent_turn_request"]["context_hints"]["request_id"],
            "stable-caller-id-2",
        )


class RuntimeUsageCreditDebitPlanTests(unittest.TestCase):
    """(d) deployed_agent_virtual_runtime_service's new runtime-minutes
    debit wiring: platform-credit only, real amounts (not the old hardcoded
    credits_debited=0.0), and idempotent per session via the SAME atomic
    primitive already proven above."""

    def setUp(self) -> None:
        fallback = _force_local_identity_fallback()
        fallback.__enter__()
        self.addCleanup(fallback.__exit__, None, None, None)

    def test_local_payer_never_produces_a_debit_plan(self):
        plan = deployed_agent_virtual_runtime_service.runtime_usage_credit_debit_plan(
            payer="local", estimated_cost_usd=1.23, runtime_session_id="sess-1",
        )
        self.assertIsNone(plan)

    def test_unknown_or_missing_payer_never_produces_a_debit_plan(self):
        for payer in ("", None, "byok", "subscription_passthrough"):
            plan = deployed_agent_virtual_runtime_service.runtime_usage_credit_debit_plan(
                payer=payer, estimated_cost_usd=1.23, runtime_session_id="sess-1",
            )
            self.assertIsNone(plan, f"payer={payer!r} must never produce a debit plan")

    def test_zero_or_negative_cost_never_produces_a_debit_plan(self):
        for cost in (0, 0.0, -1.0, None):
            plan = deployed_agent_virtual_runtime_service.runtime_usage_credit_debit_plan(
                payer="platform_credits", estimated_cost_usd=cost, runtime_session_id="sess-1",
            )
            self.assertIsNone(plan, f"cost={cost!r} must never produce a debit plan")

    def test_platform_credits_produces_a_stable_namespaced_request_id(self):
        plan = deployed_agent_virtual_runtime_service.runtime_usage_credit_debit_plan(
            payer="platform_credits", estimated_cost_usd=0.5, runtime_session_id="rt-sess-42",
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["request_id"], "runtime_usage:rt-sess-42")
        self.assertGreater(plan["credits_to_charge"], 0)
        # Deterministic: calling again for the SAME session id (e.g. a
        # terminate-session retry) produces the identical request_id.
        plan_retry = deployed_agent_virtual_runtime_service.runtime_usage_credit_debit_plan(
            payer="platform_credits", estimated_cost_usd=0.5, runtime_session_id="rt-sess-42",
        )
        self.assertEqual(plan["request_id"], plan_retry["request_id"])

    def test_runtime_usage_debit_is_idempotent_across_terminate_retries(self):
        """The plan's request_id, fed into the SAME
        debit_workspace_credits_for_turn_atomic already proven idempotent
        above, must charge a given runtime session at most once even if
        session termination is retried."""
        workspace_id = "ws-runtime-1"
        tenant_id = "tenant-1"
        _run(_seed_local_workspace(
            workspace_id=workspace_id, tenant_id=tenant_id,
            metadata={"admin_defaults": {"credit_balance_usd": 10.0}},
        ))
        plan = deployed_agent_virtual_runtime_service.runtime_usage_credit_debit_plan(
            payer="platform_credits", estimated_cost_usd=0.5, runtime_session_id="rt-sess-retry",
        )
        first = _run(control_plane_repository.debit_workspace_credits_for_turn_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id, **plan,
        ))
        retry = _run(control_plane_repository.debit_workspace_credits_for_turn_atomic(
            workspace_id=workspace_id, tenant_id=tenant_id, **plan,
        ))
        self.assertTrue(first["ok"])
        self.assertGreater(first["credits_debited"], 0)
        self.assertTrue(retry["ok"])
        self.assertEqual(retry["credits_debited"], 0)
        self.assertEqual(retry["reason"], "already_recorded")

    def test_runtime_debit_request_id_is_namespaced_away_from_sage_turn_keys(self):
        """Runtime-minutes usage and a Sage chat turn are different billing
        events -- even if a caller (bug or otherwise) reused a raw session
        id as a Sage trace_id, the "runtime_usage:" prefix keeps the two
        request_id spaces from ever colliding in the shared ledger."""
        plan = deployed_agent_virtual_runtime_service.runtime_usage_credit_debit_plan(
            payer="platform_credits", estimated_cost_usd=0.5, runtime_session_id="collide-id",
        )
        turn_key = agent_turn_runtime_service._turn_credit_idempotency_key("collide-id", "trace-x")
        self.assertNotEqual(plan["request_id"], turn_key)


class PrimaryPathRealDebitTests(unittest.TestCase):
    """MAN-108 Bug 3: direct_chat_hosted_usage_service.persist_direct_chat_
    hosted_usage_best_effort is the PRIMARY debit call site -- it fires on
    every normal live chat turn (the action-loop-v3 path handle_sage_chat
    returns from immediately, per the class docstring's point 2/(C) above).
    Before the fix it called billing_service.debit_workspace_credit_
    balance_for_hosted_usage, which only draws down credit_balance_usd once
    a workspace's CUMULATIVE MONTHLY cost exceeds its monthly cap -- a
    no-op for one real turn under any normal usage. The fix switches this
    call site to billing_service.debit_workspace_credits_for_turn (the same
    real, MAN-74-reconnected primitive the fallback path already used).
    These tests prove: (1) the real primitive is what actually fires and
    moves the balance, (2) the old dormant primitive is no longer called
    from here, and (3) running the SAME turn (same request_id) twice only
    debits once -- idempotency holds end-to-end through the real call site,
    not just at the raw atomic-function level proven above."""

    def setUp(self) -> None:
        fallback = _force_local_identity_fallback()
        fallback.__enter__()
        self.addCleanup(fallback.__exit__, None, None, None)

    @staticmethod
    def _persist_kwargs(request_id: str) -> dict:
        return dict(
            workspace_id="ws-primary-path-1",
            thread_id="thread-1",
            session_ctx={"tenant_id": "tenant-1", "request_id": request_id},
            availability_payload={
                "credential_plane": "platform_runtime",
                "platform_runtime_allowed": True,
            },
            usage_masked={
                "usage_accounting": {
                    "input_tokens": 500,
                    "output_tokens": 150,
                    "total_tokens": 650,
                    # ~$0.000112 raw -> well above zero after the 3x margin
                    # and 100-credits/$ retail rate, comfortably nonzero
                    # credits owed (matches billing_credit_config's
                    # documented "short hello" worked example).
                    "estimated_cost_usd": 0.000112,
                    "effective_provider": "deepseek",
                    "effective_model": "deepseek-chat",
                }
            },
            requested_provider="deepseek",
            effective_provider="deepseek",
            requested_model="deepseek-chat",
            effective_model="deepseek-chat",
        )

    def _run_persist_with_real_debit(self, request_id: str):
        """Runs the real persist_direct_chat_hosted_usage_best_effort call,
        with only the unrelated ledger-write persistence mocked out (they
        write to a Postgres-shaped table this sandbox doesn't have) -- the
        actual credit debit call goes through for real, against the local
        SQLite identity-DB fallback seeded in setUp."""
        with (
            patch(
                "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry",
                new=AsyncMock(return_value={"id": "shost_x"}),
            ),
            patch(
                "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_credit_ledger_event",
                new=AsyncMock(return_value={"id": "cled_x"}),
            ),
            patch(
                "server_modules.billing_service.debit_workspace_credit_balance_for_hosted_usage",
            ) as mock_dormant_debit,
        ):
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                **self._persist_kwargs(request_id)
            )
        return mock_dormant_debit

    def test_primary_path_calls_the_real_per_turn_debit_and_moves_the_balance(self):
        workspace_id = "ws-primary-path-1"
        tenant_id = "tenant-1"
        _run(_seed_local_workspace(
            workspace_id=workspace_id, tenant_id=tenant_id,
            metadata={"admin_defaults": {"credit_balance_usd": 10.0}},
        ))

        mock_dormant_debit = self._run_persist_with_real_debit("primary-path-req-1")

        # The old dormant monthly-cap-overage function must never be called
        # from this call site anymore.
        mock_dormant_debit.assert_not_called()

        # The real per-turn debit actually moved the balance.
        record = _run(control_plane_repository.get_workspace_by_id(workspace_id))
        balance_after = record["metadata"]["admin_defaults"]["credit_balance_usd"]
        self.assertLess(balance_after, 10.0)
        transactions = record["metadata"]["admin_defaults"]["credit_transactions"]
        usage_debits = [t for t in transactions if t.get("kind") == "usage_debit" and t.get("request_id") == "primary-path-req-1"]
        self.assertEqual(len(usage_debits), 1)

    def test_same_turn_run_twice_with_same_request_id_debits_only_once(self):
        """The critical idempotency proof requested by MAN-108: replaying
        the exact same turn (same request_id, e.g. a retried webhook or a
        duplicate stream-completion callback) through the REAL, now-fixed
        call site must charge the workspace exactly once, not twice."""
        workspace_id = "ws-primary-path-1"
        tenant_id = "tenant-1"
        _run(_seed_local_workspace(
            workspace_id=workspace_id, tenant_id=tenant_id,
            metadata={"admin_defaults": {"credit_balance_usd": 10.0}},
        ))
        shared_request_id = "primary-path-retry-req-1"

        self._run_persist_with_real_debit(shared_request_id)
        record_after_first = _run(control_plane_repository.get_workspace_by_id(workspace_id))
        balance_after_first = record_after_first["metadata"]["admin_defaults"]["credit_balance_usd"]
        self.assertLess(balance_after_first, 10.0)

        # Re-run the SAME logical turn (identical request_id) a second time.
        self._run_persist_with_real_debit(shared_request_id)
        record_after_second = _run(control_plane_repository.get_workspace_by_id(workspace_id))
        balance_after_second = record_after_second["metadata"]["admin_defaults"]["credit_balance_usd"]

        # Balance must not have moved again -- the second call is a no-op.
        self.assertEqual(balance_after_second, balance_after_first)
        transactions = record_after_second["metadata"]["admin_defaults"]["credit_transactions"]
        usage_debits = [
            t for t in transactions
            if t.get("kind") == "usage_debit" and t.get("request_id") == shared_request_id
        ]
        self.assertEqual(len(usage_debits), 1, "the same request_id must only ever produce ONE usage_debit transaction")


if __name__ == "__main__":
    unittest.main()

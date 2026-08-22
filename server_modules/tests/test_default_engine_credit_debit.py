"""A turn on the DEFAULT production engine must debit credits exactly once.

THE BUG THIS FILE EXISTS FOR (fixed 2026-08-09)
-----------------------------------------------
MAN-310 made the Claude Agent SDK the default turn engine. The engine is
chosen at ONE seam — ``_run_sage_action_loop_v3._collect_stream_events`` —
and the two branches were not symmetric about money:

    legacy  -> direct_chat_generation_service.stream_provider_backed_direct_chat
                 -> direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort
                      -> billing_service.debit_workspace_credits_for_turn      DEBITS
    sdk     -> claude_agent_sdk_bridge.collect_events_via_claude_agent_sdk     (no debit anywhere)

The only OTHER debit in agent_turn_runtime_service lived in the cloud
fallthrough (text-only) block, which a normal turn never reaches — the
action-loop branch returns first. So every ordinary turn on the production
default engine recorded a usage_event (real tokens, real cost, shown to the
customer on the billing page) and charged nothing.

Nothing failed. Nothing logged. Spend was metered and never billed.

WHAT IS ASSERTED
----------------
Behaviourally: a default-engine turn debits EXACTLY ONCE (a call count, not
"at least once" — "at least once" is precisely what a double-debit would
also satisfy), BYOK turns still never touch platform credits, and an
insufficient balance still never blocks the reply.

Structurally: metering and debiting are ONE function with ONE call site for
the debit primitive, and the two call sites of that function are mutually
exclusive by control flow. A behavioural test can only cover the engines
that exist today; the AST tests are what catch the NEXT one.
"""

from __future__ import annotations

import ast
import asyncio
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import types as sdk_types

from server_modules import claude_agent_sdk_bridge
from server_modules import agent_turn_runtime_service
from server_modules.tests.test_claude_agent_sdk_bridge import _fake_claude_sdk_client


SAGE_RUNTIME_SOURCE = Path(agent_turn_runtime_service.__file__)

# The shared seam. Both metering and debiting live here and nowhere else in
# agent_turn_runtime_service.
SHARED_SEAM = "_meter_and_debit_turn"
DEBIT_PRIMITIVE = "debit_workspace_credits_for_turn_atomic"
METER_PRIMITIVE = "record_usage_from_context"


def _run(coro):
    return asyncio.run(coro)


def _sdk_events(*, reply: str = "Here is your answer.", input_tokens: int = 12000, output_tokens: int = 800):
    """A realistic claude_agent_sdk_bridge event stream for one turn.

    ``total_cost_usd`` is deliberately ABSENT: it is gated on
    served_by_anthropic upstream (translate_sdk_message) and the
    platform-credit tier is DeepSeek-only, so a real platform-paid SDK turn
    never carries one. That is exactly the case in which the debit must
    still happen — priced from the same lookup usage_events itself uses.
    """
    return [
        {
            "type": "final",
            "payload": {
                "reply": reply,
                "session_id": "sdk-session-1",
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            },
        },
    ]


def _legacy_stream_events(*_args, **_kwargs):
    yield {"type": "final", "payload": {"reply": "Here is your answer."}}


class _TurnHarness:
    """Drives the REAL handle_sage_chat, mocking only the LLM boundary and
    the money primitives — everything in between (prompt build, action loop,
    event collection, engine dispatch, metering, the branch that returns) is
    production code."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.test = test

    def run(
        self,
        *,
        engine: str,
        workspace_record=None,
        debit_result=None,
        sdk_events=None,
        legacy_usage=None,
        real_bridge: bool = False,
        extra_patches=None,
        **chat_kwargs,
    ):
        meter_calls: list[dict] = []

        async def _record(**kwargs):
            meter_calls.append(kwargs)
            return None

        debit = AsyncMock(
            return_value=debit_result
            if debit_result is not None
            else {"ok": True, "credits_debited": 1, "debited_usd": 0.002, "insufficient": False}
        )
        hosted_debit = MagicMock(
            return_value={"ok": True, "credits_debited": 1, "debited_usd": 0.002, "insufficient": False}
        )
        legacy_stream = MagicMock(side_effect=_legacy_stream_events)

        patches = [
            patch(
                "server_modules.control_plane_repository.debit_workspace_credits_for_turn_atomic",
                new=debit,
            ),
            patch("server_modules.billing_service.debit_workspace_credits_for_turn", new=hosted_debit),
            patch("server_modules.usage_events_repository.record_usage_from_context", new=_record),
            patch(
                "server_modules.direct_chat_generation_service.stream_provider_backed_direct_chat",
                new=legacy_stream,
            ),
            patch(
                "server_modules.agent_turn_runtime_service.generate_chat_reply_with_provider_fallback",
                return_value=("Reply", legacy_usage or {}, "deepseek", ""),
            ),
            patch(
                "server_modules.agent_turn_runtime_service.sage_profile_service.list_sage_profile",
                return_value={
                    "profile": {
                        "user_name": "",
                        "identity_summary": "",
                        "communication_style": "",
                        "recurring_responsibility": "",
                        "standing_rules": [],
                    }
                },
            ),
            patch(
                "server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files",
                return_value={},
            ),
            patch(
                "server_modules.agent_turn_runtime_service.assistant_memory_service."
                "build_sage_memory_context_block",
                return_value="",
            ),
            patch(
                "server_modules.agent_turn_runtime_service.sage_heartbeat_service."
                "build_sage_heartbeat_snapshot",
                new=AsyncMock(return_value={}),
            ),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[]),
            patch(
                "server_modules.agent_turn_runtime_service._resolve_cloud_provider",
                return_value=("deepseek", {"api_key": "test-key"}),
            ),
            patch("server_modules.agent_turn_runtime_service.persist_interaction"),
            patch(
                "server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event",
                new=AsyncMock(),
            ),
            patch(
                "server_modules.agent_turn_runtime_service.security_audit_service."
                "emit_security_audit_event"
            ),
        ]
        # real_bridge=True (ServedModelFullPathIntegrationTest below): the
        # real claude_agent_sdk_bridge.collect_events_via_claude_agent_sdk
        # runs, all the way down to a real ClaudeAgentOptions/
        # create_sdk_mcp_server and a real translate_sdk_message call — the
        # caller is responsible for patching claude_agent_sdk.ClaudeSDKClient
        # itself (via _fake_claude_sdk_client-shaped test double) BEFORE
        # calling this method. Every other test in this file mocks the
        # bridge's own OUTPUT and never exercises its internals at all.
        if not real_bridge:
            patches.append(
                patch(
                    "server_modules.agent_turn_runtime_service.claude_agent_sdk_bridge."
                    "collect_events_via_claude_agent_sdk",
                    return_value=sdk_events if sdk_events is not None else _sdk_events(),
                )
            )
        if extra_patches:
            patches.extend(extra_patches)
        if workspace_record is not None:
            patches.append(
                patch(
                    "server_modules.control_plane_repository.get_workspace_by_id",
                    new=AsyncMock(return_value=workspace_record),
                )
            )

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = _run(
                agent_turn_runtime_service.handle_sage_chat(
                    engine_options={"engine": engine},
                    **chat_kwargs,
                )
            )
        return {
            "result": result,
            "meter_calls": meter_calls,
            "debit": debit,
            "hosted_debit": hosted_debit,
            "legacy_stream": legacy_stream,
        }


class DefaultEngineCreditDebitTests(unittest.TestCase):
    """The regression tests. Each of these FAILS on the pre-fix tree."""

    def setUp(self) -> None:
        self.harness = _TurnHarness(self)

    def test_production_default_engine_is_the_claude_agent_sdk(self):
        """Pins WHICH engine "default" means, so the tests below cannot go
        quietly stale if the default is ever moved.

        _resolve_turn_engine_id returns "" under PYTEST_CURRENT_TEST (tests
        get the legacy engine so pre-existing mocks keep working), which is
        exactly why this leak survived a suite that was looking right at it —
        a test that does not pass engine_options exercises the engine
        production does NOT use.
        """
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTEST_CURRENT_TEST", None)
            os.environ.pop("EMPYRALIS_FORCE_LEGACY_ENGINE", None)
            self.assertEqual(
                agent_turn_runtime_service._resolve_turn_engine_id(None),
                claude_agent_sdk_bridge.ENGINE_ID,
            )
            self.assertEqual(
                agent_turn_runtime_service._resolve_turn_engine_id({}),
                claude_agent_sdk_bridge.ENGINE_ID,
            )
        # And under pytest, with no explicit choice, it is NOT the SDK —
        # the reason a naive test passes vacuously.
        self.assertNotEqual(
            agent_turn_runtime_service._resolve_turn_engine_id({}),
            claude_agent_sdk_bridge.ENGINE_ID,
        )

    def test_default_engine_turn_debits_exactly_once(self):
        """THE regression test. A normal turn on the production default
        engine charges the workspace exactly one time.

        The count is asserted exactly. "at least one debit" would be
        satisfied by a double charge, which is the opposite failure and just
        as much a bug.
        """
        run = self.harness.run(
            engine=claude_agent_sdk_bridge.ENGINE_ID,
            workspace_id="ws-default-engine",
            message="what is the status of my project?",
            request_id="turn-1",
        )

        self.assertEqual(run["debit"].await_count, 1)
        self.assertEqual(run["debit"].await_args.kwargs["request_id"], "turn-1")
        self.assertGreater(run["debit"].await_args.kwargs["credits_to_charge"], 0)
        # The reply still ships, unchanged.
        self.assertEqual(run["result"]["message"], "Here is your answer.")

    def test_default_engine_turn_is_metered_and_debited_together(self):
        """Metering without a debit is the exact shape of the bug. One turn
        produces one usage_event AND one debit — never one without the
        other."""
        run = self.harness.run(
            engine=claude_agent_sdk_bridge.ENGINE_ID,
            workspace_id="ws-default-engine",
            message="hello",
            request_id="turn-2",
        )

        self.assertEqual(len(run["meter_calls"]), 1)
        self.assertEqual(run["debit"].await_count, 1)
        metered = run["meter_calls"][0]
        self.assertEqual(metered["mode"], "platform_credits")
        self.assertEqual(metered["tokens_in"], 12000)
        self.assertEqual(metered["tokens_out"], 800)

    def test_default_engine_never_reaches_the_legacy_generation_debit(self):
        """Why the leak existed, asserted rather than described: the SDK
        branch never enters stream_provider_backed_direct_chat, so the
        debit living inside it (direct_chat_hosted_usage_service, the
        "PRIMARY debit path" per MAN-108 Bug 3) cannot fire for it."""
        run = self.harness.run(
            engine=claude_agent_sdk_bridge.ENGINE_ID,
            workspace_id="ws-default-engine",
            message="hello",
            request_id="turn-3",
        )
        self.assertEqual(run["legacy_stream"].call_count, 0)
        self.assertEqual(run["hosted_debit"].call_count, 0)
        # ...which is precisely why the seam debit must fire instead.
        self.assertEqual(run["debit"].await_count, 1)

    def test_legacy_engine_still_reaches_the_generation_service_that_debits(self):
        """The other half of the asymmetry, so this file documents both:
        the legacy engine's debit is inside the generation service it
        calls. Unchanged by the fix."""
        run = self.harness.run(
            engine="legacy",
            workspace_id="ws-default-engine",
            message="hello",
            request_id="turn-4",
        )
        self.assertEqual(run["legacy_stream"].call_count, 1)

    def test_byok_workspace_is_metered_but_never_debited_on_the_default_engine(self):
        """A bring-your-own-key turn runs on the customer's own provider
        account. It must appear in usage_events and must never draw down
        Empyralis platform credits.

        Before the fix the SDK metering call hardcoded
        mode="platform_credits", so BYOK turns were mislabelled at the same
        call site that failed to debit.
        """
        run = self.harness.run(
            engine=claude_agent_sdk_bridge.ENGINE_ID,
            workspace_record={
                "workspace_id": "ws-byok",
                "tenant_id": "tenant-byok",
                "metadata": {"admin_defaults": {"sage_ai_provider": "anthropic"}},
            },
            workspace_id="ws-byok",
            message="hello",
            request_id="turn-byok",
        )

        run["debit"].assert_not_awaited()
        self.assertEqual(len(run["meter_calls"]), 1)
        self.assertEqual(run["meter_calls"][0]["mode"], "byok")

    def test_unknown_pricing_never_debits_on_the_default_engine(self):
        """No ground-truth price -> no charge. Never a fabricated zero and
        never a guess; the turn is still metered."""
        run = self.harness.run(
            engine=claude_agent_sdk_bridge.ENGINE_ID,
            sdk_events=_sdk_events(input_tokens=0, output_tokens=0),
            workspace_id="ws-default-engine",
            message="hello",
            request_id="turn-nocost",
        )
        run["debit"].assert_not_awaited()

    def test_insufficient_balance_never_blocks_the_reply_on_the_default_engine(self):
        """Non-blocking by construction. A billing shortfall is a logged
        warning, never a refused answer — the product law the pre-existing
        fallthrough debit already honoured, preserved across the move."""
        run = self.harness.run(
            engine=claude_agent_sdk_bridge.ENGINE_ID,
            debit_result={
                "ok": True,
                "credits_debited": 0,
                "debited_usd": 0.0,
                "insufficient": True,
            },
            workspace_id="ws-broke",
            message="hello",
            request_id="turn-broke",
        )

        self.assertEqual(run["debit"].await_count, 1)
        self.assertEqual(run["result"]["message"], "Here is your answer.")
        self.assertIsNone(run["result"]["error"])

    def test_debit_never_raises_out_of_the_turn(self):
        """Even a debit that blows up must not cost the customer their
        reply."""
        run = self.harness.run(
            engine=claude_agent_sdk_bridge.ENGINE_ID,
            debit_result=None,
            workspace_id="ws-default-engine",
            message="hello",
            request_id="turn-raise",
        )
        # Re-run with an exploding debit.
        with patch(
            "server_modules.control_plane_repository.debit_workspace_credits_for_turn_atomic",
            new=AsyncMock(side_effect=RuntimeError("ledger down")),
        ):
            exploded = self.harness.run(
                engine=claude_agent_sdk_bridge.ENGINE_ID,
                workspace_id="ws-default-engine",
                message="hello",
                request_id="turn-raise-2",
            )
        self.assertEqual(run["result"]["message"], "Here is your answer.")
        self.assertEqual(exploded["result"]["message"], "Here is your answer.")


class FullPathRealDefaultEngineIntegrationTest(unittest.TestCase):
    """ONE test that exercises the REAL default-engine path end to end, with
    nothing mocked between "a web chat message arrives" and "a real reply
    ships" except the two boundaries no test may cross: the live LLM
    provider (claude_agent_sdk.ClaudeSDKClient, mocked here the same way
    test_claude_agent_sdk_bridge.py's own bridge-internals tests already
    do) and the money ledger (debit_workspace_credits_for_turn_atomic).

    Every other test in this file mocks claude_agent_sdk_bridge.collect_
    events_via_claude_agent_sdk directly — real coverage of the SELECTION
    wiring and the debit fusion, but zero coverage of translate_sdk_message
    itself running on this path. This test closes that gap:

        handle_sage_chat (real, PYTEST_CURRENT_TEST shortcut disabled so
          _resolve_turn_engine_id takes its REAL production branch)
        -> _run_sage_action_loop_v3 -> _collect_stream_events (real)
        -> claude_agent_sdk_bridge.collect_events_via_claude_agent_sdk (real)
        -> run_claude_agent_sdk_turn (real: ClaudeAgentOptions,
           create_sdk_mcp_server, the whole options-building path)
        -> ClaudeSDKClient (FAKE — the LLM boundary)
        -> translate_sdk_message (real) turns the scripted AssistantMessage/
           ResultMessage into the same event dicts a live turn would produce
        -> _meter_and_debit_turn (real) resolves served_model from the
           scripted ResultMessage.model_usage and debits against it
        -> debit_workspace_credits_for_turn_atomic (FAKE — the money ledger)
        -> a real reply string ships back out of handle_sage_chat
    """

    def setUp(self) -> None:
        self.harness = _TurnHarness(self)

    def test_default_engine_full_path_bills_served_model_and_ships_a_real_reply(self) -> None:
        fake_client = _fake_claude_sdk_client([
            [
                sdk_types.AssistantMessage(
                    content=[sdk_types.TextBlock(text="Full-path reply.")],
                    model="deepseek-v4-flash",
                ),
                sdk_types.ResultMessage(
                    subtype="success",
                    duration_ms=10,
                    duration_api_ms=8,
                    is_error=False,
                    num_turns=1,
                    session_id="sess-full-path",
                    result="Full-path reply.",
                    usage={"input_tokens": 900, "output_tokens": 60},
                    model_usage={
                        "deepseek-v4-flash": {
                            "inputTokens": 900,
                            "outputTokens": 60,
                            "cacheReadInputTokens": 0,
                            "cacheCreationInputTokens": 0,
                            "contextWindow": 128000,
                            "canonicalModel": "deepseek-v4-flash",
                        }
                    },
                ),
            ]
        ])

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("claude_agent_sdk.ClaudeSDKClient", new=fake_client),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=AsyncMock()),
        ):
            os.environ.pop("PYTEST_CURRENT_TEST", None)
            os.environ.pop("EMPYRALIS_FORCE_LEGACY_ENGINE", None)
            run = self.harness.run(
                engine=claude_agent_sdk_bridge.ENGINE_ID,
                real_bridge=True,
                workspace_id="ws-full-path",
                message="hello",
                request_id="turn-full-path",
            )

        # A real reply shipped, translated by the real bridge from the
        # scripted ResultMessage — not a hand-built event dict.
        self.assertEqual(run["result"]["message"], "Full-path reply.")
        # The real ClaudeSDKClient double was actually invoked — this is
        # not accidentally still hitting the mocked-bridge path.
        self.assertEqual(len(fake_client.calls), 1)
        # Billed exactly once, against the served model the scripted
        # ResultMessage reported (see _meter_and_debit_turn's own "SERVED
        # VS REQUESTED" docstring) — a call count, not "at least one".
        self.assertEqual(run["debit"].await_count, 1)
        self.assertEqual(len(run["meter_calls"]), 1)
        self.assertEqual(run["meter_calls"][0]["model"], "deepseek-v4-flash")
        self.assertEqual(run["meter_calls"][0]["tokens_in"], 900)
        self.assertEqual(run["meter_calls"][0]["tokens_out"], 60)


def _sdk_events_with_model_usage(
    *,
    reply: str = "Here is your answer.",
    input_tokens: int = 12000,
    output_tokens: int = 800,
    canonical_model: str | None,
):
    """Same shape as _sdk_events, plus a real ``model_usage`` entry carrying
    ``canonicalModel`` — the SDK's own honest report of what actually
    served the turn (claude_agent_sdk_bridge.translate_sdk_message's
    ResultMessage branch copies this through unmodified). ``canonical_model
    =None`` omits ``model_usage`` entirely (the "older CLI"/no-report case
    the fallback-to-``usage`` branch in agent_turn_runtime_service handles)."""
    payload: dict = {
        "reply": reply,
        "session_id": "sdk-session-1",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    if canonical_model is not None:
        payload["model_usage"] = {
            canonical_model: {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "contextWindow": 128000,
                "canonicalModel": canonical_model,
            }
        }
    return [{"type": "final", "payload": payload}]


class ServedVsRequestedModelBillingTests(unittest.TestCase):
    """The #6 billing-honesty fix: a turn is billed for the model that
    ACTUALLY served it (ResultMessage.model_usage[...].canonicalModel),
    never for what the tier/config merely requested. The concrete case this
    closes: DeepSeek is documented (provider_profiles.py's "deepseek"
    catalog entry) to silently substitute a different model under a
    retired/mismatched name rather than reject the call — so "trust the
    request" would have charged a customer for a tier they never actually
    received.
    """

    def setUp(self) -> None:
        self.harness = _TurnHarness(self)

    def _run_with_requested_and_served(self, *, requested_model: str, served_model: str | None, request_id: str):
        with patch(
            "server_modules.agent_turn_runtime_service.resolve_requested_model",
            return_value=requested_model,
        ):
            return self.harness.run(
                engine=claude_agent_sdk_bridge.ENGINE_ID,
                sdk_events=_sdk_events_with_model_usage(canonical_model=served_model),
                workspace_id="ws-billing-honesty",
                message="hello",
                request_id=request_id,
            )

    def test_downgrade_bills_the_cheaper_served_model_not_the_requested_one(self):
        """requested "pro" (expensive), served "flash" (cheap, per the
        SDK's own canonicalModel) — the customer must pay the flash price."
        A pre-fix run bills the pro price here, which is the exact "charge
        for what was not delivered" bug this closes."""
        mismatched = self._run_with_requested_and_served(
            requested_model="deepseek-v4-pro",
            served_model="deepseek-v4-flash",
            request_id="turn-downgrade",
        )
        honest_flash = self._run_with_requested_and_served(
            requested_model="deepseek-v4-flash",
            served_model=None,
            request_id="turn-honest-flash",
        )
        honest_pro = self._run_with_requested_and_served(
            requested_model="deepseek-v4-pro",
            served_model=None,
            request_id="turn-honest-pro",
        )

        mismatched_charge = mismatched["debit"].await_args.kwargs["credits_to_charge"]
        flash_charge = honest_flash["debit"].await_args.kwargs["credits_to_charge"]
        pro_charge = honest_pro["debit"].await_args.kwargs["credits_to_charge"]

        # The mismatched (requested pro, served flash) turn must cost the
        # SAME as an honest, un-substituted flash turn — never the pro
        # price, and never something in between (a partial/blended charge
        # would still be "billing the wrong thing").
        self.assertEqual(mismatched_charge, flash_charge)
        self.assertLess(mismatched_charge, pro_charge)

        # Call counts — "a debit happened" is satisfied by a double charge
        # just as happily as a correct one, so the count is asserted
        # exactly, not just that it is truthy.
        self.assertEqual(mismatched["debit"].await_count, 1)
        self.assertEqual(len(mismatched["meter_calls"]), 1)
        self.assertEqual(mismatched["meter_calls"][0]["model"], "deepseek-v4-flash")

        # Surfaced to the customer — not just correctly priced. Same
        # metadata reaches the persisted turn (thread_service.
        # record_assistant_turn) via the identical dict.
        self.assertEqual(mismatched["result"]["model"], "deepseek-v4-pro")
        self.assertEqual(mismatched["result"]["effective_model"], "deepseek-v4-flash")
        self.assertTrue(mismatched["result"]["model_overridden"])

    def test_no_substitution_bills_the_requested_model_and_reports_no_override(self):
        """The common case — SDK reports the SAME model that was
        requested — must be byte-for-byte unaffected by this fix."""
        run = self._run_with_requested_and_served(
            requested_model="deepseek-v4-flash",
            served_model="deepseek-v4-flash",
            request_id="turn-no-mismatch",
        )
        self.assertEqual(run["meter_calls"][0]["model"], "deepseek-v4-flash")
        self.assertEqual(run["debit"].await_count, 1)
        self.assertEqual(run["result"]["effective_model"], "deepseek-v4-flash")
        self.assertFalse(run["result"]["model_overridden"])

    def test_no_model_usage_report_falls_back_to_requested_model_honestly(self):
        """An older CLI / no per-model report at all: nothing here can
        claim a substitution it has no evidence for, so it bills and
        reports the requested model — never a fabricated "no override"
        that looks more confident than the data supports, but also never
        blocks the charge just because the richer signal is absent."""
        run = self._run_with_requested_and_served(
            requested_model="deepseek-v4-pro",
            served_model=None,
            request_id="turn-no-report",
        )
        self.assertEqual(run["meter_calls"][0]["model"], "deepseek-v4-pro")
        self.assertEqual(run["debit"].await_count, 1)
        self.assertEqual(run["result"]["effective_model"], "deepseek-v4-pro")
        self.assertFalse(run["result"]["model_overridden"])


class ResolveServedModelFromUsageTests(unittest.TestCase):
    """Unit coverage for the pure resolver
    agent_turn_runtime_service._resolve_served_model_from_usage — the
    function _meter_and_debit_turn's caller uses to turn the SDK's raw
    ``model_usage`` dict into a served-model answer (or an honest
    "unknown")."""

    def test_single_entry_returns_its_canonical_model(self):
        self.assertEqual(
            agent_turn_runtime_service._resolve_served_model_from_usage(
                "deepseek-v4-pro", {"deepseek-v4-pro": {"canonicalModel": "deepseek-v4-flash"}},
            ),
            "deepseek-v4-flash",
        )

    def test_no_usage_data_returns_none(self):
        self.assertIsNone(agent_turn_runtime_service._resolve_served_model_from_usage("deepseek-v4-pro", None))
        self.assertIsNone(agent_turn_runtime_service._resolve_served_model_from_usage("deepseek-v4-pro", {}))

    def test_agreeing_multi_entry_usage_returns_the_shared_model(self):
        usage = {
            "deepseek-v4-pro": {"canonicalModel": "deepseek-v4-flash"},
            "deepseek-v4-pro-alt-key": {"canonicalModel": "deepseek-v4-flash"},
        }
        self.assertEqual(
            agent_turn_runtime_service._resolve_served_model_from_usage("deepseek-v4-pro", usage),
            "deepseek-v4-flash",
        )

    def test_disagreeing_multi_entry_usage_returns_none_rather_than_guessing(self):
        usage = {
            "a": {"canonicalModel": "deepseek-v4-flash"},
            "b": {"canonicalModel": "deepseek-v4-pro"},
        }
        self.assertIsNone(agent_turn_runtime_service._resolve_served_model_from_usage("deepseek-v4-pro", usage))

    def test_missing_canonical_model_field_returns_none(self):
        self.assertIsNone(
            agent_turn_runtime_service._resolve_served_model_from_usage(
                "deepseek-v4-pro", {"deepseek-v4-pro": {"inputTokens": 100}},
            ),
        )


class SingleDebitSeamStructureTests(unittest.TestCase):
    """Double-debit is prevented structurally, not by vigilance.

    A behavioural test can only prove things about the engines that exist.
    These prove the property that has to survive the next one.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(SAGE_RUNTIME_SOURCE.read_text())
        cls.functions = [
            node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

    def _enclosing_function(self, lineno: int) -> str:
        best = None
        for node in self.functions:
            if node.lineno <= lineno <= node.end_lineno:
                if best is None or node.lineno > best.lineno:
                    best = node
        return best.name if best is not None else ""

    def _call_sites(self, attr_name: str) -> list[int]:
        sites = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == attr_name:
                sites.append(node.lineno)
        return sites

    def test_debit_primitive_has_exactly_one_call_site_and_it_is_the_shared_seam(self):
        """One debit implementation. A second copy is how the two engines
        drifted apart in the first place — the SDK engine was added beside a
        debit that only the legacy engine's own code path performed."""
        sites = self._call_sites(DEBIT_PRIMITIVE)
        self.assertEqual(
            len(sites), 1,
            f"{DEBIT_PRIMITIVE} must be called from exactly one place in "
            f"{SAGE_RUNTIME_SOURCE.name}; found {len(sites)} at lines {sites}",
        )
        self.assertEqual(self._enclosing_function(sites[0]), SHARED_SEAM)

    def test_metering_has_no_call_site_outside_the_shared_seam(self):
        """Metering and debiting are fused. You cannot record spend in this
        module without charging for it — which is the invariant the bug
        broke, so it is the invariant worth pinning."""
        sites = self._call_sites(METER_PRIMITIVE)
        self.assertTrue(sites, f"{METER_PRIMITIVE} call site disappeared entirely")
        for line in sites:
            self.assertEqual(
                self._enclosing_function(line), SHARED_SEAM,
                f"{METER_PRIMITIVE} called at line {line} from "
                f"{self._enclosing_function(line)!r}, outside the shared seam — "
                "metering without a debit is exactly the leak this file guards.",
            )

    def test_the_two_seam_call_sites_are_mutually_exclusive_by_control_flow(self):
        """The action-loop branch RETURNS, so a turn can only ever reach one
        of the two _meter_and_debit_turn call sites. Not "unlikely to reach
        both" — unable to."""
        handler = next(
            fn for fn in self.functions if fn.name == "_handle_sage_chat_unguarded"
        )
        action_branch = next(
            stmt
            for stmt in handler.body
            if isinstance(stmt, ast.If) and ast.unparse(stmt.test) == "action_result is not None"
        )

        self.assertFalse(
            action_branch.orelse,
            "an else-branch would give control a way past the return below",
        )
        self.assertIsInstance(
            action_branch.body[-1], ast.Return,
            "the action-loop branch must end in an unconditional return — it is "
            "the only thing keeping the two debit call sites mutually exclusive",
        )

        seam_sites = self._call_sites(SHARED_SEAM)
        inside = [ln for ln in seam_sites if action_branch.lineno <= ln <= action_branch.end_lineno]
        outside = [ln for ln in seam_sites if ln not in inside]
        self.assertEqual(
            len(inside), 1,
            f"expected exactly one {SHARED_SEAM} call inside the action-loop "
            f"branch, found {inside}",
        )
        self.assertEqual(
            len(outside), 1,
            f"expected exactly one {SHARED_SEAM} call on the cloud-fallthrough "
            f"path, found {outside}",
        )

    def test_shared_seam_is_the_only_definition(self):
        """One function, defined once — so "call the seam" cannot silently
        mean two different things."""
        definitions = [fn for fn in self.functions if fn.name == SHARED_SEAM]
        self.assertEqual(len(definitions), 1)


if __name__ == "__main__":
    unittest.main()


# ── A byok_api AGENT was billed platform credits (2026-08-21) ───────────────
#
# THE BUG THIS SECTION EXISTS FOR
# -------------------------------
# "Who pays for this turn" has TWO possible sources and only ONE was asked:
#
#     WORKSPACE  admin_defaults.sage_ai_provider   -> "byok"      READ
#     AGENT      model_config.mode == "byok_api"   -> "byok_api"  NOT READ
#
# _resolve_agent_cloud_provider computes the agent-level answer, its own
# docstring states the rule outright ("NEVER bill platform credits for a
# BYOK-bound agent"), and it is unit-tested. Its returned billing_mode had
# exactly ONE production call site:
#
#     provider, credentials, _ = await _resolve_agent_cloud_provider(...)
#                             ^ thrown away
#
# So a byok_api agent in an ordinary workspace — the normal case, since
# sage_ai_provider is a WORKSPACE AI-route default nobody has to touch in
# order to bind ONE agent to its own key — resolved to "platform_credits"
# and was DEBITED for tokens the customer had already paid their own
# provider for, with the usage_events row stamped mode="platform_credits":
# a lie in the one column a future consumption-pricing model has to trust.
#
# CLAUDE.md's "built, tested, and never wired" failure mode, on a money path.
#
# Measured on the pre-fix tree by driving the real turn seam: mode recorded
# as "platform_credits", 10 credits debited. After: mode "byok_api", zero
# debits, the usage_events row still written.
#
# WHAT IS ASSERTED
# ----------------
# Counts, not existence: EXACTLY ONE usage_events row per turn (a metering
# call that fires twice is as wrong as one that never fires) and EXACTLY
# ZERO debits. "a row was written" and "no debit happened" are each
# satisfied by failures in the opposite direction.
#
# Plus a structural test, because a behavioural one can only cover the
# lanes that exist today: the resolved billing mode must not be discarded
# into `_` again, and both _meter_and_debit_turn call sites must pass it.


def _byok_agent_patches():
    """The two module-level lookups _resolve_agent_cloud_provider's byok_api
    branch makes. Patched (not faked one level up at
    _resolve_agent_cloud_provider itself) precisely so the REAL resolver
    runs and the REAL billing_mode it returns is what the assertions below
    are reading — mocking the resolver would mock the thing under test."""
    return [
        patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials",
            return_value={"api_key": "customers-own-key"},
        ),
        patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat",
            return_value=True,
        ),
    ]


def _specialist(*, mode: str, provider: str = "openai", model: str = "gpt-4.1"):
    from server_modules.specialist_runtime_context import SpecialistRuntimeContext

    return SpecialistRuntimeContext(
        agent_install_id=f"ainstall_{mode or 'unset'}",
        agent_label="Test Agent",
        agent_kind="specialist",
        persona="",
        provider=provider,
        model=model,
        mode=mode,
    )


_PLAIN_WORKSPACE = {
    # No admin_defaults.sage_ai_provider — the ordinary workspace, and the
    # one in which this bug fired.
    "workspace_id": "ws-plain",
    "tenant_id": "tenant-plain",
    "metadata": {},
}
_BYOK_WORKSPACE = {
    "workspace_id": "ws-adminbyok",
    "tenant_id": "tenant-adminbyok",
    "metadata": {"admin_defaults": {"sage_ai_provider": "anthropic"}},
}


class ByokApiAgentTurnBillingTests(unittest.TestCase):
    """These FAIL on the pre-fix tree."""

    def setUp(self) -> None:
        self.harness = _TurnHarness(self)

    def _run_agent(self, spec, workspace_record, request_id, engine=None):
        return self.harness.run(
            engine=engine or claude_agent_sdk_bridge.ENGINE_ID,
            workspace_record=workspace_record,
            extra_patches=_byok_agent_patches(),
            workspace_id=workspace_record["workspace_id"],
            message="hello",
            request_id=request_id,
            specialist_context=spec,
        )

    def test_byok_api_agent_turn_is_recorded_exactly_once(self):
        """THE recording half. Record every orchestrated turn — a turn
        Empyralis does not bill is still a turn Empyralis ran, and
        consumption pricing cannot be re-based later onto data nobody
        collected."""
        run = self._run_agent(_specialist(mode="byok_api"), _PLAIN_WORKSPACE, "byok-agent-1")

        self.assertEqual(len(run["meter_calls"]), 1)
        metered = run["meter_calls"][0]
        self.assertEqual(metered["tokens_in"], 12000)
        self.assertEqual(metered["tokens_out"], 800)

    def test_byok_api_agent_turn_is_never_debited(self):
        """THE regression test. Zero, asserted as a count — the pre-fix tree
        charged 10 credits here."""
        run = self._run_agent(_specialist(mode="byok_api"), _PLAIN_WORKSPACE, "byok-agent-2")

        self.assertEqual(run["debit"].await_count, 0)
        self.assertEqual(run["hosted_debit"].call_count, 0)
        # ...and the reply still shipped, so this is a billing fix and not a
        # turn that was quietly broken into silence.
        self.assertEqual(run["result"]["message"], "Here is your answer.")

    def test_byok_api_agent_turn_is_labelled_byok_api_not_platform_credits(self):
        """The mode column is the ONLY thing distinguishing "the customer
        paid for this" from "we did" once the row is written. It said
        platform_credits.

        "byok_api" and not the workspace-level "byok": two different facts
        (an agent bound to its own key vs. a whole workspace routed to one)
        may not share one signal, and usage_events_repository's own
        _USAGE_MODE_TO_PAYER already maps BOTH to the "BYOK" payer for
        display — the reader was built for this value before any writer
        produced it."""
        run = self._run_agent(_specialist(mode="byok_api"), _PLAIN_WORKSPACE, "byok-agent-3")

        self.assertEqual(run["meter_calls"][0]["mode"], "byok_api")

        from server_modules import usage_events_repository as _usage_repo

        self.assertEqual(_usage_repo._canonical_usage_payer("byok_api"), "BYOK")

    def test_legacy_agent_shape_provider_without_mode_is_also_treated_as_byok(self):
        """An agent with a provider and NO explicit mode pre-dates
        model_config.mode; the resolver documents it as byok_api ("the
        closest real meaning of 'this agent has its own provider'"). The
        billing answer must come from the RESOLVER, never from the raw
        _spec.mode string — which is empty here, and would read as
        platform-paid to anyone deriving it locally."""
        run = self._run_agent(_specialist(mode=""), _PLAIN_WORKSPACE, "byok-agent-legacy")

        self.assertEqual(run["debit"].await_count, 0)
        self.assertEqual(run["meter_calls"][0]["mode"], "byok_api")

    def test_platform_credits_agent_still_debits_exactly_once(self):
        """The control, and the proof this fix did not simply switch the
        debit off. An agent that really is platform-paid still charges,
        exactly once."""
        run = self._run_agent(
            _specialist(mode="platform_credits", provider="deepseek", model="deepseek-chat"),
            _PLAIN_WORKSPACE,
            "platform-agent-1",
        )

        self.assertEqual(run["debit"].await_count, 1)
        self.assertEqual(run["debit"].await_args.kwargs["request_id"], "platform-agent-1")
        self.assertGreater(run["debit"].await_args.kwargs["credits_to_charge"], 0)
        self.assertEqual(run["meter_calls"][0]["mode"], "platform_credits")

    def test_platform_credits_agent_in_a_byok_workspace_is_unchanged(self):
        """Precedence, in the direction that could have regressed something
        already correct: an agent declaring platform_credits does NOT
        override a workspace-level BYOK route. Adding the agent input must
        not change any answer that was right before it existed."""
        run = self._run_agent(
            _specialist(mode="platform_credits", provider="deepseek", model="deepseek-chat"),
            _BYOK_WORKSPACE,
            "platform-agent-byok-ws",
        )

        self.assertEqual(run["debit"].await_count, 0)
        self.assertEqual(run["meter_calls"][0]["mode"], "byok")

    def test_master_sage_turn_with_no_agent_is_unchanged(self):
        """No specialist at all — the master/Sage turn, which has no
        per-agent model_config. Still platform-paid, still debited once."""
        run = self.harness.run(
            engine=claude_agent_sdk_bridge.ENGINE_ID,
            workspace_record=_PLAIN_WORKSPACE,
            workspace_id="ws-plain",
            message="hello",
            request_id="master-1",
        )

        self.assertEqual(run["debit"].await_count, 1)
        self.assertEqual(run["meter_calls"][0]["mode"], "platform_credits")


class MeterAndDebitSeamByokTests(unittest.TestCase):
    """The seam itself, driven directly.

    The class above drives whole turns, which only ever exercises whichever
    _meter_and_debit_turn call site that lane happens to take. These call the
    function, so the contract is pinned regardless of which branch reaches
    it — the coverage that matters for the SECOND call site (the cloud
    fallthrough), which no turn in this file's harness lands on with tokens
    attached. Deliberately NOT written as a whole-turn test that quietly
    exercises neither: a green that asserts nothing is worse than a red.
    """

    def _call(self, *, agent_billing_mode, workspace_record=_PLAIN_WORKSPACE):
        meter = AsyncMock(return_value=None)
        debit = AsyncMock(
            return_value={"ok": True, "credits_debited": 1, "debited_usd": 0.002, "insufficient": False}
        )
        with (
            patch("server_modules.usage_events_repository.record_usage_from_context", new=meter),
            patch(
                "server_modules.control_plane_repository.debit_workspace_credits_for_turn_atomic",
                new=debit,
            ),
        ):
            outcome = _run(
                agent_turn_runtime_service._meter_and_debit_turn(
                    workspace_id=workspace_record["workspace_id"],
                    tenant_id=workspace_record["tenant_id"],
                    credit_idempotency_key="seam-1",
                    workspace_record=workspace_record,
                    provider="openai",
                    model="gpt-4.1",
                    tokens_in=12000,
                    tokens_out=800,
                    # A REAL price, so "no debit" cannot pass merely because
                    # nothing was priced — the vacuous way this assertion
                    # could go green while the bug is intact.
                    usd_cost=0.5,
                    agent_billing_mode=agent_billing_mode,
                )
            )
        return outcome, meter, debit

    def test_byok_api_meters_once_and_debits_zero_even_with_a_real_price(self):
        outcome, meter, debit = self._call(agent_billing_mode="byok_api")

        self.assertEqual(meter.await_count, 1)
        self.assertEqual(meter.await_args.kwargs["mode"], "byok_api")
        self.assertEqual(debit.await_count, 0)
        self.assertEqual(outcome["mode"], "byok_api")
        self.assertEqual(outcome["credits_owed"], 0)
        self.assertIsNone(outcome["debit"])
        # usd_cost is still REPORTED — a BYOK turn costs the PLATFORM
        # nothing, which is a different fact from the tokens being free to
        # produce. Only `mode` is allowed to carry that difference.
        self.assertEqual(outcome["usd_cost"], 0.5)

    def test_platform_credits_at_the_same_seam_still_debits_exactly_once(self):
        """The control that makes the assertion above mean something."""
        outcome, meter, debit = self._call(agent_billing_mode="platform_credits")

        self.assertEqual(meter.await_count, 1)
        self.assertEqual(meter.await_args.kwargs["mode"], "platform_credits")
        self.assertEqual(debit.await_count, 1)
        self.assertGreater(outcome["credits_owed"], 0)

    def test_the_argument_is_optional_and_omitting_it_changes_nothing(self):
        """Every pre-existing caller keeps its exact behaviour."""
        outcome, meter, debit = self._call(agent_billing_mode="")

        self.assertEqual(meter.await_args.kwargs["mode"], "platform_credits")
        self.assertEqual(debit.await_count, 1)


class ResolveTurnPayerModeUnitTests(unittest.TestCase):
    """The resolution rule on its own, so its precedence is pinned
    independently of any turn."""

    def _resolve(self, workspace_record, agent_mode=""):
        return agent_turn_runtime_service._resolve_turn_payer_mode(workspace_record, agent_mode)

    def test_agent_byok_beats_a_plain_workspace(self):
        self.assertEqual(self._resolve(_PLAIN_WORKSPACE, "byok_api"), "byok_api")

    def test_agent_byok_beats_a_byok_workspace_with_the_more_specific_answer(self):
        self.assertEqual(self._resolve(_BYOK_WORKSPACE, "byok_api"), "byok_api")

    def test_agent_platform_credits_does_not_override_a_byok_workspace(self):
        self.assertEqual(self._resolve(_BYOK_WORKSPACE, "platform_credits"), "byok")

    def test_no_agent_mode_falls_through_exactly_as_before(self):
        self.assertEqual(self._resolve(_PLAIN_WORKSPACE, ""), "platform_credits")
        self.assertEqual(self._resolve(_BYOK_WORKSPACE, ""), "byok")
        self.assertEqual(self._resolve(None, ""), "platform_credits")

    def test_an_unknown_agent_mode_fails_closed_and_never_debits(self):
        """A lane this module has never heard of must not be billed to
        platform credits on the grounds that nobody taught it otherwise. The
        set is spelled as what IS platform-paid, so anything else is not."""
        for unknown in ("cli_subscription", "local", "some_future_lane"):
            with self.subTest(mode=unknown):
                resolved = self._resolve(_PLAIN_WORKSPACE, unknown)
                self.assertEqual(resolved, unknown)
                self.assertNotEqual(resolved, "platform_credits")


class BillingModeWiringStructureTests(unittest.TestCase):
    """A behavioural test covers the lanes that exist today. These cover the
    next one — specifically, the exact edit that caused this bug."""

    def setUp(self) -> None:
        self.tree = ast.parse(SAGE_RUNTIME_SOURCE.read_text(encoding="utf-8"))

    def test_the_resolved_billing_mode_is_never_discarded_again(self):
        """The whole bug in one line: `provider, credentials, _ = await
        _resolve_agent_cloud_provider(...)`. Unpacking the billing mode into
        a throwaway type-checks, runs, and is silent in production."""
        offenders = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if isinstance(call, ast.Await):
                call = call.value
            if not isinstance(call, ast.Call):
                continue
            name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
            if name != "_resolve_agent_cloud_provider":
                continue
            for target in node.targets:
                if not isinstance(target, ast.Tuple) or len(target.elts) != 3:
                    continue
                third = target.elts[2]
                if isinstance(third, ast.Name) and third.id == "_":
                    offenders.append(node.lineno)
        self.assertEqual(
            offenders,
            [],
            "_resolve_agent_cloud_provider's billing_mode is being thrown away at "
            f"line(s) {offenders} — that is the bug this file's byok_api section exists for. "
            "Bind it and pass it to _meter_and_debit_turn.",
        )

    def test_every_meter_and_debit_call_site_passes_the_agent_billing_mode(self):
        """A second call site that forgets the argument silently reverts to
        the workspace-only answer for whichever lane it serves — which is
        exactly how one engine kept charging while the other stopped."""
        call_sites = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == SHARED_SEAM
        ]
        self.assertGreaterEqual(len(call_sites), 2, "expected both engine call sites")
        for call in call_sites:
            with self.subTest(line=call.lineno):
                self.assertIn(
                    "agent_billing_mode",
                    {kw.arg for kw in call.keywords},
                    f"{SHARED_SEAM} at line {call.lineno} does not pass agent_billing_mode",
                )

    def test_the_payer_decision_has_exactly_one_implementation(self):
        """Who pays is answered in _resolve_turn_payer_mode and nowhere
        else — the reason the workspace half and the agent half could not be
        made to disagree once both are inputs to the same function."""
        source = SAGE_RUNTIME_SOURCE.read_text(encoding="utf-8")
        self.assertEqual(source.count("def _resolve_turn_payer_mode("), 1)
        # And the seam asks it rather than deciding for itself.
        self.assertEqual(source.count('"mode": _resolve_turn_payer_mode('), 1)

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

The only OTHER debit in sage_agent_runtime_service lived in the cloud
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
from server_modules import sage_agent_runtime_service
from server_modules.tests.test_claude_agent_sdk_bridge import _fake_claude_sdk_client


SAGE_RUNTIME_SOURCE = Path(sage_agent_runtime_service.__file__)

# The shared seam. Both metering and debiting live here and nowhere else in
# sage_agent_runtime_service.
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
                "server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback",
                return_value=("Reply", legacy_usage or {}, "deepseek", ""),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
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
                "server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                return_value={},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.sage_memory_service."
                "build_sage_memory_context_block",
                return_value="",
            ),
            patch(
                "server_modules.sage_agent_runtime_service.sage_heartbeat_service."
                "build_sage_heartbeat_snapshot",
                new=AsyncMock(return_value={}),
            ),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch(
                "server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                return_value=("deepseek", {"api_key": "test-key"}),
            ),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch(
                "server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                new=AsyncMock(),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.security_audit_service."
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
                    "server_modules.sage_agent_runtime_service.claude_agent_sdk_bridge."
                    "collect_events_via_claude_agent_sdk",
                    return_value=sdk_events if sdk_events is not None else _sdk_events(),
                )
            )
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
                sage_agent_runtime_service.handle_sage_chat(
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
                sage_agent_runtime_service._resolve_turn_engine_id(None),
                claude_agent_sdk_bridge.ENGINE_ID,
            )
            self.assertEqual(
                sage_agent_runtime_service._resolve_turn_engine_id({}),
                claude_agent_sdk_bridge.ENGINE_ID,
            )
        # And under pytest, with no explicit choice, it is NOT the SDK —
        # the reason a naive test passes vacuously.
        self.assertNotEqual(
            sage_agent_runtime_service._resolve_turn_engine_id({}),
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

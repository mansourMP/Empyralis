"""Tests for docs/design/audit-context-currency.md fixes #1-#4: wiring the
existing LLM-summarized compaction (server_modules/compaction_service.py)
into the PRIMARY generation path instead of leaving it reachable only from
sage_agent_runtime_service.handle_sage_chat's narrow fallback branch.

Covers:
  - Fix #1 (proactive): stream_provider_backed_direct_chat compacts BEFORE
    dispatching to the provider when the assembled context estimate exceeds
    the model's window.
  - Fix #2 (reactive): a provider context-overflow ("failure" event) is
    caught, compacted, and retried ONCE — never more than once per turn.
  - Fix #3 (budget preflight): _action_loop_context_budget_preflight in
    sage_agent_runtime_service.py runs a token-based check ahead of the
    primary action loop (_run_sage_action_loop_v3), replacing the fixed
    SAGE_THREAD_MAX_TURNS=10 turn-count cap as the only guard there.
  - Fix #4 (catalog currency): provider_profiles.py resolves real context
    windows for claude-opus-4-8 / claude-sonnet-5 / claude-fable-5 /
    claude-haiku-4-5-20251001, and logs a warning on an unrecognized model
    instead of silently falling back to 128K.
  - The EMPYRALIS_PRIMARY_COMPACTION_ENABLED flag: off means every path
    above behaves byte-for-byte as it did before this feature existed.

Only the new module + the compaction/direct-chat suites this change touches
are expected to be run alongside this file (per the build brief) — this
file does not re-test unrelated direct_chat_generation_service behavior
already covered by test_direct_chat_generation_service.py.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import compaction_service
from server_modules import direct_chat_generation_service
from server_modules import provider_profiles
from server_modules import sage_agent_runtime_service


def _run(coro):
    return asyncio.run(coro)


# ── Fix #4: Anthropic model catalog currency ────────────────────────────────

class AnthropicModelCatalogCurrencyTests(unittest.TestCase):
    """Verified live against platform.claude.com/docs/en/about-claude/models/
    overview on 2026-07-22 (redirected from docs.claude.com): Opus 4.8,
    Sonnet 5, and Fable 5 are all 1,000,000-token context windows; Haiku
    4.5 (claude-haiku-4-5-20251001) is 200,000 (already correct in the
    catalog before this change — only the three newer ids were missing)."""

    def test_resolves_current_generation_windows(self) -> None:
        self.assertEqual(
            provider_profiles.context_window_for_model("anthropic", "claude-opus-4-8"), 1_000_000
        )
        self.assertEqual(
            provider_profiles.context_window_for_model("anthropic", "claude-sonnet-5"), 1_000_000
        )
        self.assertEqual(
            provider_profiles.context_window_for_model("anthropic", "claude-fable-5"), 1_000_000
        )
        self.assertEqual(
            provider_profiles.context_window_for_model("anthropic", "claude-haiku-4-5-20251001"), 200_000
        )

    def test_unknown_model_returns_none_and_logs_warning(self) -> None:
        with self.assertLogs("server_modules.provider_profiles", level="WARNING") as log_ctx:
            result = provider_profiles.context_window_for_model("anthropic", "claude-does-not-exist")
        self.assertIsNone(result)
        self.assertTrue(any("unrecognized model" in message for message in log_ctx.output))

    def test_compaction_service_resolves_real_window_not_the_128k_fallback(self) -> None:
        # This is the exact regression the audit flagged: an unrecognized
        # model used to silently compact against DEFAULT_CONTEXT_WINDOW
        # (128K) instead of the model's real (often much larger) window.
        self.assertEqual(
            compaction_service.resolve_context_window("anthropic", "claude-opus-4-8"), 1_000_000
        )
        self.assertEqual(
            compaction_service.resolve_context_window("anthropic", "claude-does-not-exist"),
            compaction_service.DEFAULT_CONTEXT_WINDOW,
        )


class OverflowErrorShapeTests(unittest.TestCase):
    """compaction_service.is_context_overflow_error's keyword list used to
    miss Anthropic's actual Messages API overflow wording entirely."""

    def test_detects_anthropic_prompt_too_long_message(self) -> None:
        self.assertTrue(
            compaction_service.is_context_overflow_error(
                "prompt is too long: 220000 tokens > 200000 maximum"
            )
        )

    def test_detects_openai_shaped_message(self) -> None:
        self.assertTrue(
            compaction_service.is_context_overflow_error(
                "This model's maximum context length is 8192 tokens."
            )
        )

    def test_does_not_flag_unrelated_errors(self) -> None:
        self.assertFalse(compaction_service.is_context_overflow_error("http_500: internal server error"))
        self.assertFalse(compaction_service.is_context_overflow_error(""))


# ── Fix #5 (flag): EMPYRALIS_PRIMARY_COMPACTION_ENABLED ────────────────────

class PrimaryCompactionFlagTests(unittest.TestCase):
    def test_defaults_to_enabled(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EMPYRALIS_PRIMARY_COMPACTION_ENABLED", None)
            self.assertTrue(direct_chat_generation_service._primary_compaction_enabled())

    def test_disabled_via_env_var(self) -> None:
        with patch.dict(os.environ, {"EMPYRALIS_PRIMARY_COMPACTION_ENABLED": "0"}):
            self.assertFalse(direct_chat_generation_service._primary_compaction_enabled())


# ── Fix #1/#2 helper: _compact_conversation_messages_in_place ──────────────

class CompactConversationMessagesInPlaceTests(unittest.TestCase):
    def _summary_side_effect(self, summary: str):
        def _inner(coro):
            # run_async_tool_call is mocked out entirely here, so the
            # coroutine compact_turns(...) built to pass to it is never
            # actually awaited — close it to avoid a "coroutine was never
            # awaited" warning, then hand back the canned summary as if it
            # had run.
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return summary
        return _inner

    def test_mutates_list_in_place_and_returns_true(self) -> None:
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "older reply"},
            {"role": "user", "content": "recent"},
        ]
        with patch.object(
            direct_chat_generation_service, "run_async_tool_call",
            side_effect=self._summary_side_effect("A concise summary."),
        ), patch("server_modules.compaction_service.find_cut_point", return_value=2):
            changed = direct_chat_generation_service._compact_conversation_messages_in_place(
                conversation_messages=messages,
                workspace_id="ws-1",
                thread_id="thread-1",
                provider="anthropic",
                model="claude-haiku-4-5-20251001",
            )

        self.assertTrue(changed)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("A concise summary.", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "user", "content": "recent"})

    def test_returns_false_when_nothing_to_cut(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        with patch("server_modules.compaction_service.find_cut_point", return_value=0):
            changed = direct_chat_generation_service._compact_conversation_messages_in_place(
                conversation_messages=messages,
                workspace_id="ws-1",
                thread_id="thread-1",
                provider="anthropic",
                model="claude-haiku-4-5-20251001",
            )
        self.assertFalse(changed)
        self.assertEqual(messages, [{"role": "user", "content": "hi"}])

    def test_returns_false_on_empty_summary(self) -> None:
        messages = [
            {"role": "user", "content": "old"},
            {"role": "user", "content": "recent"},
        ]
        with patch.object(
            direct_chat_generation_service, "run_async_tool_call",
            side_effect=self._summary_side_effect(""),
        ), patch("server_modules.compaction_service.find_cut_point", return_value=1):
            changed = direct_chat_generation_service._compact_conversation_messages_in_place(
                conversation_messages=messages,
                workspace_id="ws-1",
                thread_id="thread-1",
                provider="anthropic",
                model="claude-haiku-4-5-20251001",
            )
        self.assertFalse(changed)

    def test_threads_provider_and_model_into_compact_turns(self) -> None:
        # docs/design/audit-context-anatomy.md fix #3 half (a): this call
        # site already resolves the turn's real provider/model (used two
        # lines above it for resolve_context_window) but used to drop them
        # on the floor when calling compact_turns, which then silently
        # defaulted to a platform-wide "deepseek" key regardless of what
        # the user is actually paying for.
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "older reply"},
            {"role": "user", "content": "recent"},
        ]
        compact_turns_mock = MagicMock(return_value="unused-coro-placeholder")
        with patch.object(
            direct_chat_generation_service, "run_async_tool_call",
            side_effect=self._summary_side_effect("A concise summary."),
        ), patch("server_modules.compaction_service.find_cut_point", return_value=2), \
             patch.object(compaction_service, "compact_turns", compact_turns_mock):
            direct_chat_generation_service._compact_conversation_messages_in_place(
                conversation_messages=messages,
                workspace_id="ws-1",
                thread_id="thread-1",
                provider="anthropic",
                model="claude-haiku-4-5-20251001",
            )

        self.assertEqual(compact_turns_mock.call_count, 1)
        call_kwargs = compact_turns_mock.call_args.kwargs
        self.assertEqual(call_kwargs.get("provider"), "anthropic")
        self.assertEqual(call_kwargs.get("model"), "claude-haiku-4-5-20251001")


# ── Fix #1/#2 at the stream_provider_backed_direct_chat level ──────────────

def _services(*, stream_fn):
    return direct_chat_generation_service.DirectChatGenerationServices(
        thinking_step_payload=lambda iteration, status, detail=None: {
            "type": "step", "iteration": iteration, "status": status, "detail": detail,
        },
        build_context_used=lambda **kwargs: kwargs,
        build_direct_tool_approval_response=lambda **kwargs: None,
        parse_tool_name=lambda name: tuple(str(name).split("__", 1)) if "__" in str(name) else ("", ""),
        tool_arguments_payload=lambda value: value if isinstance(value, dict) else {},
        parse_page_state=lambda value: {},
        direct_tool_step_payload=lambda connector_id, action_id, arguments, **kwargs: {
            "type": "step", "connector": connector_id, "action": action_id, "arguments": arguments, **kwargs,
        },
        execute_single_direct_tool_call=lambda **kwargs: "tool result",
        direct_tool_followup_message=lambda tool_name, result_text: f"{tool_name}: {result_text}",
        suggest_actions=lambda _message, _availability: [],
        clear_direct_tool_loop_state=lambda _session_key: None,
        persist_direct_chat_memory_best_effort=lambda **kwargs: None,
        persist_direct_chat_transcript_best_effort=lambda **kwargs: None,
        persist_direct_chat_hosted_usage_best_effort=lambda **kwargs: None,
        record_direct_tool_signature=lambda _session_key, _tool_call: False,
        direct_chat_error_reply=lambda error: f"Chat failed: {error}",
        capture_exception=lambda exc: None,
        generate_chat_reply_stream_with_provider_fallback=stream_fn,
    )


def _base_kwargs(services, **overrides):
    kwargs = dict(
        services=services,
        context={"provider": "anthropic"},
        metadata={"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        system_prompt="System prompt",
        normalized_workspace_id="ws-1",
        normalized_requested_provider="anthropic",
        normalized_requested_model="claude-haiku-4-5-20251001",
        normalized_reasoning_effort=None,
        normalized_thread_id="thread-1",
        normalized_message="Hello",
        compacted_prior_messages=[],
        prior_messages_used=False,
        history_mode="none",
        connected_systems=[],
        tool_capabilities=[],
        availability_payload={"ai_ready": True},
        tools=[],
        direct_chat_credentials={},
        proactive_suggestions=[],
        tool_loop_session_key="session-1",
        fallback_reason=None,
        session_ctx=None,
        trace_context=None,
        resolved_chat_max_iterations=5,
        direct_tool_result_summary_system_message="Summarize tool results.",
    )
    kwargs.update(overrides)
    return kwargs


def _summary_side_effect(summary: str):
    def _inner(coro):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return summary
    return _inner


class PrimaryPathProactiveCompactionTests(unittest.TestCase):
    def test_proactive_compaction_triggers_and_provider_sees_compacted_context(self) -> None:
        calls: list[dict] = []

        def _stream_fn(**kwargs):
            # Snapshot prior_messages as a COPY at call time: the generator
            # passes the live conversation_messages list by reference, which
            # it keeps mutating (appending this turn's own user/assistant
            # messages) after this call returns — inspecting kwargs later
            # would otherwise see those later appends too.
            calls.append({**kwargs, "prior_messages": list(kwargs.get("prior_messages") or [])})
            return iter([
                {
                    "type": "result",
                    "reply": "Final answer.",
                    "usage_masked": {},
                    "provider": "anthropic",
                    "model": "claude-haiku-4-5-20251001",
                    "attempted_providers": "anthropic",
                    "error": "",
                    "tool_calls": [],
                },
            ])

        services = _services(stream_fn=_stream_fn)
        prior = [{"role": "user", "content": f"old turn {i}"} for i in range(10)] + [
            {"role": "assistant", "content": f"old reply {i}"} for i in range(10)
        ]

        with patch.object(
            direct_chat_generation_service, "run_async_tool_call",
            side_effect=_summary_side_effect("Summary of the older turns."),
        ), patch("server_modules.compaction_service.resolve_context_window", return_value=10):
            # window=10: COMPACTION_RESERVE_TOKENS alone (16384) already
            # exceeds it, so the proactive check is guaranteed to trip on
            # the very first iteration regardless of message content.
            events = list(
                direct_chat_generation_service.stream_provider_backed_direct_chat(
                    **_base_kwargs(services, compacted_prior_messages=prior)
                )
            )

        self.assertEqual(len(calls), 1)
        sent_prior_messages = calls[0]["prior_messages"] or []
        self.assertLess(len(sent_prior_messages), len(prior))
        self.assertTrue(
            any(
                isinstance(m, dict) and "[Compacted context" in str(m.get("content") or "")
                for m in sent_prior_messages
            ),
            f"expected a compacted summary message in {sent_prior_messages!r}",
        )
        self.assertTrue(any(e.get("type") == "final" for e in events))
        final_payloads = [e["payload"] for e in events if e.get("type") == "final"]
        self.assertEqual(final_payloads[-1]["reply"], "Final answer.")

    def test_flag_off_leaves_prior_messages_untouched(self) -> None:
        calls: list[dict] = []

        def _stream_fn(**kwargs):
            # Snapshot prior_messages as a COPY at call time: the generator
            # passes the live conversation_messages list by reference, which
            # it keeps mutating (appending this turn's own user/assistant
            # messages) after this call returns — inspecting kwargs later
            # would otherwise see those later appends too.
            calls.append({**kwargs, "prior_messages": list(kwargs.get("prior_messages") or [])})
            return iter([
                {
                    "type": "result",
                    "reply": "Final.",
                    "usage_masked": {},
                    "provider": "anthropic",
                    "model": "claude-haiku-4-5-20251001",
                    "attempted_providers": "anthropic",
                    "error": "",
                    "tool_calls": [],
                },
            ])

        services = _services(stream_fn=_stream_fn)
        prior = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "reply"}]

        with patch.dict(os.environ, {"EMPYRALIS_PRIMARY_COMPACTION_ENABLED": "0"}), \
             patch("server_modules.compaction_service.resolve_context_window", return_value=1):
            # window=1 would trip the proactive check every time if the flag
            # were on — with it off, this must be a complete no-op.
            events = list(
                direct_chat_generation_service.stream_provider_backed_direct_chat(
                    **_base_kwargs(services, compacted_prior_messages=prior)
                )
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["prior_messages"], prior)
        self.assertTrue(any(e.get("type") == "final" for e in events))


class PrimaryPathReactiveCompactionRetryTests(unittest.TestCase):
    def test_overflow_failure_triggers_one_compaction_retry_then_succeeds(self) -> None:
        rounds = iter([
            [
                {
                    "type": "failure",
                    "attempted_providers": "anthropic",
                    "error": "prompt is too long: 300000 tokens > 200000 maximum",
                },
            ],
            [
                {
                    "type": "result",
                    "reply": "Recovered answer.",
                    "usage_masked": {},
                    "provider": "anthropic",
                    "model": "claude-haiku-4-5-20251001",
                    "attempted_providers": "anthropic",
                    "error": "",
                    "tool_calls": [],
                },
            ],
        ])
        call_count = {"n": 0}

        def _stream_fn(**kwargs):
            call_count["n"] += 1
            return iter(next(rounds))

        services = _services(stream_fn=_stream_fn)
        prior = [{"role": "user", "content": "message one"}, {"role": "assistant", "content": "message two"}]

        with patch.object(
            direct_chat_generation_service, "run_async_tool_call",
            side_effect=_summary_side_effect("Summary of message one."),
        ), patch("server_modules.compaction_service.resolve_context_window", return_value=1_000_000), \
           patch("server_modules.compaction_service.find_cut_point", return_value=1):
            # A big window (proactive stays quiet) but a fake overflow
            # "failure" event anyway — simulates the estimate-vs-real-
            # tokenizer drift the reactive path exists to backstop.
            events = list(
                direct_chat_generation_service.stream_provider_backed_direct_chat(
                    **_base_kwargs(services, compacted_prior_messages=prior)
                )
            )

        self.assertEqual(call_count["n"], 2, "expected exactly one failed attempt plus one retry")
        final_payloads = [e["payload"] for e in events if e.get("type") == "final"]
        self.assertTrue(final_payloads)
        self.assertEqual(final_payloads[-1]["reply"], "Recovered answer.")
        self.assertEqual(final_payloads[-1]["error"], "")

    def test_overflow_failure_retries_at_most_once(self) -> None:
        call_count = {"n": 0}

        def _stream_fn(**kwargs):
            call_count["n"] += 1
            return iter([
                {
                    "type": "failure",
                    "attempted_providers": "anthropic",
                    "error": "prompt is too long: 300000 tokens > 200000 maximum",
                },
            ])

        services = _services(stream_fn=_stream_fn)
        prior = [{"role": "user", "content": "message one"}, {"role": "assistant", "content": "message two"}]

        with patch.object(
            direct_chat_generation_service, "run_async_tool_call",
            side_effect=_summary_side_effect("Summary of message one."),
        ), patch("server_modules.compaction_service.resolve_context_window", return_value=1_000_000), \
           patch("server_modules.compaction_service.find_cut_point", return_value=1):
            events = list(
                direct_chat_generation_service.stream_provider_backed_direct_chat(
                    **_base_kwargs(services, compacted_prior_messages=prior)
                )
            )

        # Exactly 2 calls: the original attempt + the ONE allowed retry. A
        # THIRD would mean the retry cap didn't hold — infinite-loop risk.
        self.assertEqual(call_count["n"], 2)
        final_payloads = [e["payload"] for e in events if e.get("type") == "final"]
        self.assertTrue(final_payloads)
        self.assertNotEqual(final_payloads[-1]["error"], "")

    def test_flag_off_skips_reactive_retry_entirely(self) -> None:
        call_count = {"n": 0}

        def _stream_fn(**kwargs):
            call_count["n"] += 1
            return iter([
                {
                    "type": "failure",
                    "attempted_providers": "anthropic",
                    "error": "prompt is too long: 300000 tokens > 200000 maximum",
                },
            ])

        services = _services(stream_fn=_stream_fn)
        prior = [{"role": "user", "content": "message one"}, {"role": "assistant", "content": "message two"}]

        with patch.dict(os.environ, {"EMPYRALIS_PRIMARY_COMPACTION_ENABLED": "0"}):
            events = list(
                direct_chat_generation_service.stream_provider_backed_direct_chat(
                    **_base_kwargs(services, compacted_prior_messages=prior)
                )
            )

        # Flag off: the first failure is terminal, exactly as before this
        # feature existed — never a second (retry) call.
        self.assertEqual(call_count["n"], 1)
        final_payloads = [e["payload"] for e in events if e.get("type") == "final"]
        self.assertTrue(final_payloads)
        self.assertNotEqual(final_payloads[-1]["error"], "")


# ── Fix #3: sage_agent_runtime_service._action_loop_context_budget_preflight ─

class ActionLoopContextBudgetPreflightTests(unittest.TestCase):
    def test_returns_unchanged_when_under_budget(self) -> None:
        prior = [{"role": "user", "content": "hi"}]
        with patch("server_modules.compaction_service.resolve_context_window", return_value=1_000_000):
            result = _run(
                sage_agent_runtime_service._action_loop_context_budget_preflight(
                    workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                    system_prompt="sys", user_message="hi",
                    prior_messages=prior, channel_prior_messages=None,
                    ctx_policy_max=0, ctx_policy_action="compact",
                    used_context=[],
                )
            )
        self.assertIs(result, prior)

    def test_flag_off_returns_prior_messages_unchanged(self) -> None:
        prior = [{"role": "user", "content": "x" * 10000}]
        with patch(
            "server_modules.direct_chat_generation_service._primary_compaction_enabled",
            return_value=False,
        ), patch("server_modules.compaction_service.resolve_context_window", return_value=1):
            result = _run(
                sage_agent_runtime_service._action_loop_context_budget_preflight(
                    workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                    system_prompt="sys", user_message="hi",
                    prior_messages=prior, channel_prior_messages=None,
                    ctx_policy_max=0, ctx_policy_action="compact",
                    used_context=[],
                )
            )
        self.assertIs(result, prior)

    def test_fresh_session_policy_skips_this_preflight(self) -> None:
        prior = [{"role": "user", "content": "x" * 10000}]
        with patch("server_modules.compaction_service.resolve_context_window", return_value=1):
            result = _run(
                sage_agent_runtime_service._action_loop_context_budget_preflight(
                    workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                    system_prompt="sys", user_message="hi",
                    prior_messages=prior, channel_prior_messages=None,
                    ctx_policy_max=0, ctx_policy_action="fresh_session",
                    used_context=[],
                )
            )
        self.assertIs(result, prior)

    def test_triggers_llm_compaction_for_non_channel_turn(self) -> None:
        prior = [
            {"role": "user", "content": "old one"},
            {"role": "assistant", "content": "old two"},
            {"role": "user", "content": "recent"},
        ]
        used_context: list[str] = []
        with patch("server_modules.compaction_service.resolve_context_window", return_value=1), \
             patch("server_modules.compaction_service.find_cut_point", return_value=2), \
             patch("server_modules.compaction_service.compact_turns", new=AsyncMock(return_value="Summary text.")), \
             patch.object(sage_agent_runtime_service, "_run_memory_flush_before_compaction", new=AsyncMock(return_value=True)):
            result = _run(
                sage_agent_runtime_service._action_loop_context_budget_preflight(
                    workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                    system_prompt="sys", user_message="hi",
                    prior_messages=prior, channel_prior_messages=None,
                    ctx_policy_max=0, ctx_policy_action="compact",
                    used_context=used_context,
                )
            )
        self.assertEqual(result[0]["role"], "system")
        self.assertIn("Summary text.", result[0]["content"])
        self.assertEqual(result[1], {"role": "user", "content": "recent"})
        self.assertIn("action_loop_prior_messages_compacted", used_context)

    def test_threads_provider_and_model_into_compact_turns(self) -> None:
        # docs/design/audit-context-anatomy.md fix #3 half (a): provider/
        # model are already parameters of this function (used above for
        # resolve_context_window) but used to be omitted from the
        # compact_turns call itself.
        prior = [
            {"role": "user", "content": "old one"},
            {"role": "assistant", "content": "old two"},
            {"role": "user", "content": "recent"},
        ]
        compact_turns_mock = AsyncMock(return_value="Summary text.")
        with patch("server_modules.compaction_service.resolve_context_window", return_value=1), \
             patch("server_modules.compaction_service.find_cut_point", return_value=2), \
             patch("server_modules.compaction_service.compact_turns", new=compact_turns_mock), \
             patch.object(sage_agent_runtime_service, "_run_memory_flush_before_compaction", new=AsyncMock(return_value=True)):
            _run(
                sage_agent_runtime_service._action_loop_context_budget_preflight(
                    workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                    system_prompt="sys", user_message="hi",
                    prior_messages=prior, channel_prior_messages=None,
                    ctx_policy_max=0, ctx_policy_action="compact",
                    used_context=[],
                )
            )
        self.assertEqual(compact_turns_mock.call_count, 1)
        call_kwargs = compact_turns_mock.call_args.kwargs
        self.assertEqual(call_kwargs.get("provider"), "anthropic")
        self.assertEqual(call_kwargs.get("model"), "claude-haiku-4-5-20251001")

    def test_channel_turn_gets_plain_truncation_not_llm_summary(self) -> None:
        prior = [
            {"role": "user", "content": "old one"},
            {"role": "assistant", "content": "old two"},
            {"role": "user", "content": "recent"},
        ]
        compact_turns_mock = AsyncMock(return_value="Should never be reached for a channel turn.")
        with patch("server_modules.compaction_service.resolve_context_window", return_value=1), \
             patch("server_modules.compaction_service.find_cut_point", return_value=2), \
             patch("server_modules.compaction_service.compact_turns", new=compact_turns_mock), \
             patch.object(sage_agent_runtime_service, "_run_memory_flush_before_compaction", new=AsyncMock(return_value=True)):
            result = _run(
                sage_agent_runtime_service._action_loop_context_budget_preflight(
                    workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                    system_prompt="sys", user_message="hi",
                    prior_messages=prior, channel_prior_messages=list(prior),
                    ctx_policy_max=0, ctx_policy_action="compact",
                    used_context=[],
                )
            )
        compact_turns_mock.assert_not_called()
        self.assertEqual(result, [{"role": "user", "content": "recent"}])

    def test_memory_flush_failure_skips_compaction(self) -> None:
        prior = [
            {"role": "user", "content": "old one"},
            {"role": "user", "content": "recent"},
        ]
        with patch("server_modules.compaction_service.resolve_context_window", return_value=1), \
             patch("server_modules.compaction_service.find_cut_point", return_value=1), \
             patch.object(sage_agent_runtime_service, "_run_memory_flush_before_compaction", new=AsyncMock(return_value=False)):
            result = _run(
                sage_agent_runtime_service._action_loop_context_budget_preflight(
                    workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                    system_prompt="sys", user_message="hi",
                    prior_messages=prior, channel_prior_messages=None,
                    ctx_policy_max=0, ctx_policy_action="compact",
                    used_context=[],
                )
            )
        self.assertIs(result, prior)


# ── Fix #3 half (b): compact_turns must never fail silently ────────────────

class CompactTurnsObservabilityTests(unittest.TestCase):
    """docs/design/audit-context-anatomy.md fix #3 half (b): compact_turns
    used to return "" completely silently whenever the resolved provider had
    no usable key/route (e.g. the platform-wide DeepSeek key it falls back
    to is unset on the server) — no log, no trace event, nothing. Both must
    now fire."""

    def test_emits_compaction_skipped_trace_event_when_trace_context_given(self) -> None:
        turns = [{"role": "user", "content": "hello"}]
        emit_mock = AsyncMock(return_value="tevent_123")
        with patch(
            "server_modules.compaction_service.openai_chat_text",
            return_value=("", None, "", "missing API key for provider deepseek"),
        ), patch("server_modules.agent_trace_service.emit_compaction_skipped", new=emit_mock):
            summary = _run(
                compaction_service.compact_turns(
                    turns=turns, workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="deepseek", model="deepseek-chat",
                    trace_context="fake-trace-context",
                )
            )
        self.assertEqual(summary, "")
        emit_mock.assert_called_once()
        call_args = emit_mock.call_args.args
        self.assertEqual(call_args[0], "fake-trace-context")
        self.assertIn("missing API key", call_args[1])
        self.assertEqual(call_args[2], "deepseek")
        self.assertEqual(call_args[3], "deepseek-chat")

    def test_no_trace_event_emitted_when_trace_context_is_none(self) -> None:
        # Every pre-existing call site omits trace_context (it defaults to
        # None) — must keep working exactly as before, with only the log
        # (asserted above) as the observability signal. Exercises the
        # no-provider-resolved skip path (see class below) since no
        # provider/model is passed here either.
        turns = [{"role": "user", "content": "hello"}]
        emit_mock = AsyncMock(return_value=None)
        with patch(
            "server_modules.compaction_service.openai_chat_text",
            return_value=("", None, "", "missing API key for provider deepseek"),
        ), patch("server_modules.agent_trace_service.emit_compaction_skipped", new=emit_mock):
            summary = _run(
                compaction_service.compact_turns(
                    turns=turns, workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                )
            )
        self.assertEqual(summary, "")
        emit_mock.assert_not_called()

    def test_succeeds_normally_when_summary_is_produced(self) -> None:
        # Regression guard: the observability additions above must not
        # change the happy path at all.
        turns = [{"role": "user", "content": "hello"}]
        with patch(
            "server_modules.compaction_service.openai_chat_text",
            return_value=("A real summary.", {"total_tokens": 10}, "deepseek-chat", ""),
        ), patch(
            "server_modules.control_plane_repository.upsert_agent_turn",
            new=AsyncMock(return_value=None),
        ):
            summary = _run(
                compaction_service.compact_turns(
                    turns=turns, workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                )
            )
        self.assertEqual(summary, "A real summary.")


# ── 2026-07-23 founder ruling: no model-fallback chains ────────────────────

class CompactTurnsNoPlatformFallbackTests(unittest.TestCase):
    """The platform-wide DeepSeek default (`provider or "deepseek"`) is
    removed. If the turn's own provider can't be resolved, compact_turns
    must skip outright — never silently run compaction on a model the user
    never chose. 'If the user's model fails, it fails visibly; the owner
    changes their model' — no substitute model, ever."""

    def test_no_provider_resolved_skips_without_calling_the_llm_at_all(self) -> None:
        # The key assertion: openai_chat_text must NEVER be invoked when no
        # provider was resolved for this turn — proves there is no hidden
        # default (not "deepseek", not openai_chat_text's own "openai"
        # keyword default either) sneaking the call through.
        turns = [{"role": "user", "content": "hello"}]
        with patch("server_modules.compaction_service.openai_chat_text") as llm_mock, \
             self.assertLogs("server_modules.compaction_service", level="WARNING") as log_ctx:
            summary = _run(
                compaction_service.compact_turns(
                    turns=turns, workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                )
            )
        self.assertEqual(summary, "")
        llm_mock.assert_not_called()
        self.assertTrue(any("no summary produced" in message for message in log_ctx.output))
        self.assertTrue(
            any("model_unavailable_no_fallback" in message for message in log_ctx.output)
        )

    def test_no_provider_resolved_still_emits_trace_event_with_new_reason(self) -> None:
        turns = [{"role": "user", "content": "hello"}]
        emit_mock = AsyncMock(return_value="tevent_456")
        with patch("server_modules.compaction_service.openai_chat_text") as llm_mock, \
             patch("server_modules.agent_trace_service.emit_compaction_skipped", new=emit_mock):
            summary = _run(
                compaction_service.compact_turns(
                    turns=turns, workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    trace_context="fake-trace-context",
                )
            )
        self.assertEqual(summary, "")
        llm_mock.assert_not_called()
        emit_mock.assert_called_once()
        call_args = emit_mock.call_args.args
        self.assertEqual(call_args[0], "fake-trace-context")
        self.assertEqual(call_args[1], "model_unavailable_no_fallback")

    def test_blank_provider_string_also_skips_not_just_missing_kwarg(self) -> None:
        # Callers pass `provider=_ws_provider or None` etc. — an empty
        # string must be treated identically to omitting the kwarg.
        turns = [{"role": "user", "content": "hello"}]
        with patch("server_modules.compaction_service.openai_chat_text") as llm_mock:
            summary = _run(
                compaction_service.compact_turns(
                    turns=turns, workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="", model="",
                )
            )
        self.assertEqual(summary, "")
        llm_mock.assert_not_called()

    def test_explicit_provider_that_fails_is_unaffected_by_this_change(self) -> None:
        # When the turn DOES have its own resolved provider/model, this
        # ruling does not change behavior at all — a genuine failure from
        # that provider still surfaces via the pre-existing reason/error
        # path, not the new "model_unavailable_no_fallback" reason (that
        # string is reserved for "no provider was ever resolved").
        turns = [{"role": "user", "content": "hello"}]
        with patch(
            "server_modules.compaction_service.openai_chat_text",
            return_value=("", None, "", "http_402: Payment Required"),
        ) as llm_mock, self.assertLogs(
            "server_modules.compaction_service", level="WARNING"
        ) as log_ctx:
            summary = _run(
                compaction_service.compact_turns(
                    turns=turns, workspace_id="ws-1", tenant_id="default", thread_id="thread-1",
                    provider="anthropic", model="claude-haiku-4-5-20251001",
                )
            )
        self.assertEqual(summary, "")
        llm_mock.assert_called_once()
        self.assertTrue(any("http_402" in message for message in log_ctx.output))
        self.assertFalse(
            any("model_unavailable_no_fallback" in message for message in log_ctx.output)
        )


if __name__ == "__main__":
    unittest.main()

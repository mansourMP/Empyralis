"""Regression tests for the provider-gated tool-strip fix in
direct_chat_generation_service.stream_provider_backed_direct_chat.

Context: the tool-calling loop used to strip ALL tool definitions from
`metadata["tools"]` / `context["tools"]` the moment ANY tool executed
(`executed_any_tools` True), for EVERY provider — capping every turn at one
round of tool calls followed by a synthesis-only pass. The strip exists for a
real reason (DeepSeek returns empty content when tool definitions and
tool-result messages coexist in the same payload — see
_TOOL_STRIP_REQUIRED_PROVIDERS and the strip site in
stream_provider_backed_direct_chat), but it fired unconditionally, so
mainstream providers (Anthropic, OpenAI, ...) that handle tools + tool_result
fine were silently blocked from a second round of tool use (call toolA ->
observe result -> call toolB) too.

The fix gates the strip on `_TOOL_STRIP_REQUIRED_PROVIDERS`. These tests
prove, against the real generator (only the provider-stream and tool
execution are mocked, following test_direct_chat_generation_service.py's
existing `_services` fixture pattern):

  (A) DeepSeek: the workaround is INTACT — tools are still stripped after
      the first tool-executing round, so DeepSeek never sees tools alongside
      tool_result messages.
  (B) Anthropic (a non-DeepSeek provider): tools are NOT stripped — the
      model can call a tool, observe the result, and call a SECOND tool in
      the same turn, proving real multi-round tool use now works.
"""

from __future__ import annotations

import unittest

from server_modules import direct_chat_generation_service


class _StreamRoundRecorder:
    """Stands in for services.generate_chat_reply_stream_with_provider_fallback.
    Returns one canned round of stream events per call and records the
    `tools` visible on `metadata`/`context` for that call, so tests can
    assert exactly what the strip site did before each provider round-trip."""

    def __init__(self, rounds: list[list[dict]]) -> None:
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        metadata = kwargs["metadata"]
        context = kwargs["context"]
        self.calls.append(
            {
                "metadata_tools": list(metadata.get("tools") or []),
                "context_tools": list(context.get("tools") or []),
            }
        )
        round_index = len(self.calls) - 1
        return iter(self._rounds[round_index])

    @property
    def call_count(self) -> int:
        return len(self.calls)


_SEARCH_TOOL = {
    "name": "web__search",
    "description": "Search the web.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
}
_MEMORY_TOOL = {
    "name": "memory_search",
    "description": "Search workspace memory.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
}


def _tool_call_round(*, provider: str, model: str, tool_name: str, call_id: str) -> list[dict]:
    return [
        {
            "type": "result",
            "reply": "",
            "usage_masked": {"provider": provider},
            "provider": provider,
            "model": model,
            "attempted_providers": provider,
            "error": "",
            "tool_calls": [
                {"id": call_id, "name": tool_name, "arguments": {"query": "hello"}}
            ],
        }
    ]


def _final_round(*, provider: str, model: str, reply: str) -> list[dict]:
    return [
        {
            "type": "result",
            "reply": reply,
            "usage_masked": {"provider": provider},
            "provider": provider,
            "model": model,
            "attempted_providers": provider,
            "error": "",
            "tool_calls": [],
        }
    ]


class ToolStripProviderGateTests(unittest.TestCase):
    def _services(self, *, recorder: _StreamRoundRecorder, tool_call_log: list[str]):
        def _execute_single_direct_tool_call(**kwargs):
            tool_call_log.append(str(kwargs.get("tool_call", {}).get("name") or ""))
            return "tool result payload"

        return direct_chat_generation_service.DirectChatGenerationServices(
            thinking_step_payload=lambda iteration, status, detail=None: {
                "type": "step",
                "iteration": iteration,
                "status": status,
                "detail": detail,
            },
            build_context_used=lambda **kwargs: kwargs,
            build_direct_tool_approval_response=lambda **kwargs: None,
            parse_tool_name=lambda name: tuple(str(name).split("__", 1)) if "__" in str(name) else ("", ""),
            tool_arguments_payload=lambda value: value if isinstance(value, dict) else {},
            parse_page_state=lambda value: {},
            direct_tool_step_payload=lambda connector_id, action_id, arguments, **kwargs: {
                "type": "step",
                "connector": connector_id,
                "action": action_id,
                "arguments": arguments,
                **kwargs,
            },
            execute_single_direct_tool_call=_execute_single_direct_tool_call,
            direct_tool_followup_message=lambda tool_name, result_text: f"{tool_name}: {result_text}",
            suggest_actions=lambda _message, _availability: [],
            clear_direct_tool_loop_state=lambda _session_key: None,
            persist_direct_chat_memory_best_effort=lambda **kwargs: None,
            persist_direct_chat_transcript_best_effort=lambda **kwargs: None,
            persist_direct_chat_hosted_usage_best_effort=lambda **kwargs: None,
            record_direct_tool_signature=lambda _session_key, _tool_call: False,
            direct_chat_error_reply=lambda error: f"Chat failed: {error}",
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=recorder,
        )

    def _run(self, *, provider: str, model: str, recorder: _StreamRoundRecorder, tool_call_log: list[str], max_iterations: int):
        return list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(recorder=recorder, tool_call_log=tool_call_log),
                context={"provider": provider, "tools": [_SEARCH_TOOL, _MEMORY_TOOL]},
                metadata={"provider": provider, "model": model, "tools": [_SEARCH_TOOL, _MEMORY_TOOL]},
                system_prompt="System prompt",
                normalized_workspace_id="default",
                normalized_requested_provider=provider,
                normalized_requested_model=model,
                normalized_reasoning_effort=None,
                normalized_thread_id="thread-1",
                normalized_message="Search for something, then follow up.",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[_SEARCH_TOOL, _MEMORY_TOOL],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key=f"session-{provider}",
                fallback_reason=None,
                session_ctx=None,
                trace_context=None,
                resolved_chat_max_iterations=max_iterations,
                direct_tool_result_summary_system_message="Summarize tool results.",
                assistant_plan_tools=[_SEARCH_TOOL, _MEMORY_TOOL],
            )
        )

    def test_deepseek_turn_keeps_the_workaround_tools_stripped_after_first_tool_round(self) -> None:
        """(a) DeepSeek workaround preserved: once a tool has executed, the
        NEXT provider round-trip must see NO tools — otherwise DeepSeek's
        confirmed empty-content bug (tools + tool_result coexisting) would
        regress."""
        recorder = _StreamRoundRecorder(
            [
                _tool_call_round(provider="deepseek", model="deepseek-chat", tool_name="web__search", call_id="call_1"),
                _final_round(provider="deepseek", model="deepseek-chat", reply="Here is the answer."),
            ]
        )
        tool_call_log: list[str] = []

        events = self._run(
            provider="deepseek",
            model="deepseek-chat",
            recorder=recorder,
            tool_call_log=tool_call_log,
            max_iterations=4,
        )

        self.assertEqual(recorder.call_count, 2)
        # Round 1 (before any tool executed): tools were offered.
        self.assertTrue(recorder.calls[0]["metadata_tools"])
        self.assertTrue(recorder.calls[0]["context_tools"])
        # Round 2 (after the first tool executed, still DeepSeek): the
        # workaround must still strip tools so DeepSeek never sees tool
        # definitions alongside a tool_result message.
        self.assertEqual(recorder.calls[1]["metadata_tools"], [])
        self.assertEqual(recorder.calls[1]["context_tools"], [])
        self.assertEqual(tool_call_log, ["web__search"])
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Here is the answer.")
        # NOTE: DeepSeek is a platform-runtime-metered provider (see
        # provider_profiles.PLATFORM_RUNTIME_AUTH_PROVIDERS), so the final
        # payload masks the raw provider id behind a tier label by design
        # (_mask_platform_paid_final_payload) -- that masking is unrelated
        # to this fix, so it's not asserted here. The recorder-based
        # assertions above are the load-bearing proof for this test.

    def test_anthropic_turn_keeps_tools_available_for_a_second_tool_round(self) -> None:
        """(b) Non-DeepSeek provider (anthropic): tools must NOT be
        stripped after the first tool round, and the model must be able to
        call a SECOND tool in the same turn (call toolA -> observe -> call
        toolB) before finally synthesizing an answer -- proving real
        multi-round tool use now works end to end, not just that the kwarg
        looks right."""
        recorder = _StreamRoundRecorder(
            [
                _tool_call_round(provider="anthropic", model="claude-sonnet-4-6", tool_name="web__search", call_id="call_1"),
                _tool_call_round(provider="anthropic", model="claude-sonnet-4-6", tool_name="memory_search", call_id="call_2"),
                _final_round(provider="anthropic", model="claude-sonnet-4-6", reply="Combined answer from both tools."),
            ]
        )
        tool_call_log: list[str] = []

        events = self._run(
            provider="anthropic",
            model="claude-sonnet-4-6",
            recorder=recorder,
            tool_call_log=tool_call_log,
            max_iterations=4,
        )

        # Three provider round-trips: tool round 1, tool round 2, final synthesis.
        self.assertEqual(recorder.call_count, 3)
        self.assertTrue(recorder.calls[0]["metadata_tools"])
        # Round 2, AFTER the first tool executed: tools must still be present
        # -- this is the exact case the old unconditional strip broke.
        self.assertTrue(recorder.calls[1]["metadata_tools"], "tools were wrongly stripped for anthropic after round 1")
        self.assertTrue(recorder.calls[1]["context_tools"], "tools were wrongly stripped for anthropic after round 1")
        # Round 3, after the SECOND tool executed: still not stripped.
        self.assertTrue(recorder.calls[2]["metadata_tools"])
        self.assertTrue(recorder.calls[2]["context_tools"])
        # Both tools actually ran -- real second-round tool use, not just an
        # unused kwarg.
        self.assertEqual(tool_call_log, ["web__search", "memory_search"])
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["payload"]["reply"], "Combined answer from both tools.")
        self.assertEqual(events[-1]["payload"]["provider"], "anthropic")

    def test_strip_gate_follows_the_actual_serving_provider_after_a_fallback(self) -> None:
        """The strip must gate on the provider that ACTUALLY served the
        turn (tracked via the `provider` field on each stream "result"
        event), not the originally requested one -- so a turn that started
        as anthropic but fell back to deepseek mid-turn still gets the
        DeepSeek workaround applied on the next round."""
        recorder = _StreamRoundRecorder(
            [
                # Requested anthropic, but the fallback-aware stream reports
                # the turn actually executed on deepseek.
                _tool_call_round(provider="deepseek", model="deepseek-chat", tool_name="web__search", call_id="call_1"),
                _final_round(provider="deepseek", model="deepseek-chat", reply="Answer after fallback."),
            ]
        )
        tool_call_log: list[str] = []

        events = self._run(
            provider="anthropic",
            model="claude-sonnet-4-6",
            recorder=recorder,
            tool_call_log=tool_call_log,
            max_iterations=4,
        )

        self.assertEqual(recorder.call_count, 2)
        self.assertEqual(recorder.calls[1]["metadata_tools"], [])
        self.assertEqual(recorder.calls[1]["context_tools"], [])
        self.assertEqual(events[-1]["payload"]["provider"], "deepseek")


if __name__ == "__main__":
    unittest.main()

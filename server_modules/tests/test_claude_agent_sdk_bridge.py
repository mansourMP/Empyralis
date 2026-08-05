"""MAN-310: unit tests for the Claude Agent SDK bridge's event translation.

Two things are exercised end-to-end against REAL objects, not stand-ins:

  1. translate_sdk_message() is fed real claude_agent_sdk.types dataclass
     instances (AssistantMessage/UserMessage/ResultMessage carrying
     TextBlock/ToolUseBlock/ToolResultBlock) — the exact classes
     claude_agent_sdk.query() yields (see
     spikes/man-310-claude-agent-sdk/inspect_event_shapes.py, which
     introspects the same package and confirms these constructor shapes).
  2. The events translate_sdk_message() produces are then fed through
     sage_agent_runtime_service._collect_sage_operator_loop_v3_events — the
     REAL, unmodified consumer this bridge exists to satisfy — proving the
     translation is not just shape-plausible but actually parses correctly
     end to end.

A separate class (TurnEngineSelectionFlagOffTests) pins the one decision
point _run_sage_action_loop_v3 branches on (_resolve_turn_engine_id): every
input an existing caller could produce (None, {}, a non-dict) must resolve
to something that is NOT claude_agent_sdk_bridge.ENGINE_ID, so the legacy
direct_chat_generation_service.stream_provider_backed_direct_chat path is
provably untouched when the flag is off.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import types as sdk_types

from server_modules import agent_trace_service
from server_modules import claude_agent_sdk_bridge
from server_modules import sage_agent_runtime_service


def _trace_context() -> agent_trace_service.TraceContext:
    # A plain in-memory dataclass — no DB, no network. Real production code
    # builds one via agent_trace_service.start_trace(); constructing it
    # directly here is equivalent for this module's purposes (only
    # next_seq()/trace_id/root_agent_id are read).
    return agent_trace_service.TraceContext(
        trace_id="trace-1",
        workspace_id="ws-1",
        tenant_id="default",
        thread_id="thread-1",
        run_id=None,
        root_agent_id="sage",
    )


class ResolveSdkProcessEnvTests(unittest.TestCase):
    def test_explicit_overrides_win_over_credentials(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "from-credentials", "base_url": "https://from-credentials.example"},
            anthropic_api_key="explicit-key",
            anthropic_base_url="https://api.deepseek.com/anthropic",
        )
        self.assertEqual(env["ANTHROPIC_API_KEY"], "explicit-key")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")

    def test_falls_back_to_credentials_when_no_explicit_override(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "sk-from-creds"},
        )
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-from-creds")
        self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_never_hardcodes_a_base_url(self):
        # No override, no credentials base_url — nothing about DeepSeek (or
        # any other non-Anthropic backend) appears anywhere unless the
        # caller supplied it for this turn.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(credentials=None)
        self.assertEqual(env, {})


class StripMcpToolPrefixTests(unittest.TestCase):
    def test_strips_registered_server_prefix(self):
        self.assertEqual(
            claude_agent_sdk_bridge.strip_mcp_tool_prefix("mcp__empyralis__web__search"),
            "web__search",
        )

    def test_passes_through_unprefixed_name(self):
        self.assertEqual(claude_agent_sdk_bridge.strip_mcp_tool_prefix("web__search"), "web__search")

    def test_passes_through_other_servers_prefix_unchanged(self):
        # Only ever expected to see our own server's prefix in practice, but
        # a different prefix must not be mangled.
        self.assertEqual(
            claude_agent_sdk_bridge.strip_mcp_tool_prefix("mcp__other_server__tool"),
            "mcp__other_server__tool",
        )


class TranslateAssistantMessageTests(unittest.TestCase):
    def test_text_block_accumulates_into_state_and_emits_no_events(self):
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.TextBlock(text="Hello there.")],
            model="claude-sonnet-4-5",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        self.assertEqual(events, [])
        self.assertEqual(state.reply_text_parts, ["Hello there."])

    def test_tool_use_block_emits_tool_started_and_tool_progress(self):
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(id="toolu_1", name="web__search", input={"query": "empyralis"})],
            model="claude-sonnet-4-5",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        types_seen = [e.get("type") for e in events]
        self.assertIn("tool_progress", types_seen)
        trace_events = [e for e in events if e.get("type") == "trace"]
        self.assertEqual(len(trace_events), 2)  # tool.started + search.query
        started = trace_events[0]["payload"]
        self.assertEqual(started["event_type"], "tool.started")
        self.assertEqual(started["tool_call_id"], "toolu_1")
        self.assertEqual(started["data"]["tool_name"], "web__search")
        self.assertEqual(started["data"]["connector_id"], "web")
        search_event = trace_events[1]["payload"]
        self.assertEqual(search_event["event_type"], "search.query")
        self.assertEqual(search_event["data"]["query"], "empyralis")
        # State remembers the tool call for the later ToolResultBlock match.
        self.assertEqual(state.tool_use_names["toolu_1"], "web__search")
        self.assertEqual(state.tool_use_inputs["toolu_1"], {"query": "empyralis"})

    def test_mcp_prefixed_tool_name_is_stripped_before_parsing(self):
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(
                id="toolu_2", name="mcp__empyralis__memory_search", input={"query": "x"},
            )],
            model="claude-sonnet-4-5",
        )
        claude_agent_sdk_bridge.translate_sdk_message(message, state=state, trace_context=_trace_context())
        self.assertEqual(state.tool_use_names["toolu_2"], "memory_search")

    def test_thinking_block_produces_no_events(self):
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.ThinkingBlock(thinking="reasoning...", signature="sig")],
            model="claude-sonnet-4-5",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        self.assertEqual(events, [])

    def test_unsupported_meta_tool_still_gets_a_tool_started_shape(self):
        # translate_sdk_message doesn't filter tool names -- filtering
        # happens earlier, in build_sdk_tools/run_claude_agent_sdk_turn,
        # which never registers task_complete/update_plan/query_tool_registry
        # with the SDK in the first place, so the model can't call them.
        # This test just documents that translate_sdk_message itself has no
        # opinion on tool identity.
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(id="toolu_3", name="memory_search", input={"query": "x"})],
            model="claude-sonnet-4-5",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        trace_events = [e for e in events if e.get("type") == "trace"]
        self.assertEqual(trace_events[0]["payload"]["event_type"], "tool.started")


class TranslateUserMessageToolResultTests(unittest.TestCase):
    def _seed_started(self, state: claude_agent_sdk_bridge.TranslationState, trace_context) -> None:
        started_message = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(id="toolu_1", name="web__search", input={"query": "empyralis"})],
            model="claude-sonnet-4-5",
        )
        claude_agent_sdk_bridge.translate_sdk_message(started_message, state=state, trace_context=trace_context)

    def test_successful_tool_result_emits_ok_status_and_done_plan_item(self):
        state = claude_agent_sdk_bridge.TranslationState()
        trace_context = _trace_context()
        self._seed_started(state, trace_context)
        result_message = sdk_types.UserMessage(
            content=[sdk_types.ToolResultBlock(
                tool_use_id="toolu_1",
                content=[{"type": "text", "text": "Found 3 results."}],
                is_error=False,
            )],
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            result_message, state=state, trace_context=trace_context,
        )
        trace_events = [e["payload"] for e in events if e.get("type") == "trace"]
        result_event = next(p for p in trace_events if p["event_type"] == "tool.result")
        self.assertEqual(result_event["tool_call_id"], "toolu_1")
        self.assertEqual(result_event["data"]["status"], "ok")
        self.assertIn("Found 3 results.", result_event["data"]["summary"])
        plan_event = next(p for p in trace_events if p["event_type"] == "plan.item.updated")
        self.assertEqual(plan_event["data"]["status"], "done")

    def test_failed_tool_result_emits_failed_status_and_failed_plan_item(self):
        state = claude_agent_sdk_bridge.TranslationState()
        trace_context = _trace_context()
        self._seed_started(state, trace_context)
        result_message = sdk_types.UserMessage(
            content=[sdk_types.ToolResultBlock(
                tool_use_id="toolu_1",
                content=[{"type": "text", "text": "Search API unavailable."}],
                is_error=True,
            )],
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            result_message, state=state, trace_context=trace_context,
        )
        trace_events = [e["payload"] for e in events if e.get("type") == "trace"]
        result_event = next(p for p in trace_events if p["event_type"] == "tool.result")
        self.assertEqual(result_event["data"]["status"], "failed")
        plan_event = next(p for p in trace_events if p["event_type"] == "plan.item.updated")
        self.assertEqual(plan_event["data"]["status"], "failed")

    def test_tool_result_status_classification_agrees_with_collector(self):
        # tool_result_status.classify_tool_result is the SAME structural
        # verdict sage_agent_runtime_service._collect_sage_operator_loop_v3_
        # events uses to bucket a tool call as "completed" vs "failed" --
        # this pins that the "ok"/"failed" tokens this module emits land on
        # the correct side of that classifier (see
        # CollectSageOperatorLoopV3EventsToolResultStatusTests in
        # test_sage_agent_runtime_service.py for the same classifier tested
        # against the legacy engine's own producer).
        from server_modules import tool_result_status

        self.assertTrue(tool_result_status.classify_tool_result({"status": "failed"}).failed)
        self.assertFalse(tool_result_status.classify_tool_result({"status": "ok"}).failed)


class TranslateResultMessageTests(unittest.TestCase):
    def test_successful_result_emits_final_with_reply_and_no_trace_failed(self):
        state = claude_agent_sdk_bridge.TranslationState()
        state.reply_text_parts.append("partial ")
        state.reply_text_parts.append("reply")
        message = sdk_types.ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            total_cost_usd=0.002,
            usage={"input_tokens": 10, "output_tokens": 5},
            result="The final answer.",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        self.assertEqual(len(events), 1)
        final_event = events[0]
        self.assertEqual(final_event["type"], "final")
        self.assertEqual(final_event["payload"]["reply"], "The final answer.")
        self.assertNotIn("error", final_event["payload"])
        self.assertEqual(final_event["payload"]["total_cost_usd"], 0.002)

    def test_result_message_without_result_text_falls_back_to_accumulated_text_blocks(self):
        state = claude_agent_sdk_bridge.TranslationState()
        state.reply_text_parts.extend(["Hello ", "world."])
        message = sdk_types.ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            result=None,
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        self.assertEqual(events[0]["payload"]["reply"], "Hello world.")

    def test_error_result_emits_trace_failed_and_final_with_error(self):
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.ResultMessage(
            subtype="error_max_turns",
            duration_ms=100,
            duration_api_ms=80,
            is_error=True,
            num_turns=5,
            session_id="sess-1",
            result=None,
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        types_seen = [(e["type"], e.get("payload", {}).get("event_type")) for e in events]
        self.assertIn(("trace", "trace.failed"), types_seen)
        final_event = next(e for e in events if e["type"] == "final")
        self.assertEqual(final_event["payload"]["error"], "error_max_turns")

    def test_final_payload_carries_session_id_for_later_resume(self):
        # MAN-310 Phase 2: every real ResultMessage carries a session_id
        # (required, non-Optional field) — this is how sage_agent_runtime_
        # service._run_sage_action_loop_v3 learns what to persist for the
        # NEXT turn's resume (see _sdk_engine_session_persist).
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.ResultMessage(
            subtype="success", duration_ms=100, duration_api_ms=80, is_error=False,
            num_turns=1, session_id="sess-continuity-1", result="Done.",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        final_event = next(e for e in events if e["type"] == "final")
        self.assertEqual(final_event["payload"]["session_id"], "sess-continuity-1")


class TranslationSurvivesMissingTraceContextTests(unittest.TestCase):
    """trace_context=None must degrade to dropping trace events, never
    raise -- mirrors agent_trace_service.build_ephemeral_envelope's own
    documented None-in/None-out contract, which the legacy path's
    _emit_trace_event already relies on the same way (`if x is not None:
    yield x`)."""

    def test_tool_use_with_no_trace_context_still_emits_tool_progress(self):
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(id="toolu_1", name="web__search", input={"query": "x"})],
            model="claude-sonnet-4-5",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(message, state=state, trace_context=None)
        self.assertEqual([e["type"] for e in events], ["tool_progress"])


class EndToEndTranslationThroughRealCollectorTests(unittest.TestCase):
    """The strongest proof this bridge does its job: run a full synthetic
    turn (tool call -> tool result -> text -> end of turn) through
    translate_sdk_message, then feed the RESULT into
    sage_agent_runtime_service._collect_sage_operator_loop_v3_events --
    the exact, unmodified function _run_sage_action_loop_v3 calls on
    whatever _collect_stream_events returns, on EITHER engine."""

    def test_full_turn_with_one_successful_tool_call(self):
        state = claude_agent_sdk_bridge.TranslationState()
        trace_context = _trace_context()
        all_events = []

        tool_use = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(id="toolu_1", name="web__search", input={"query": "empyralis"})],
            model="claude-sonnet-4-5",
        )
        all_events += claude_agent_sdk_bridge.translate_sdk_message(tool_use, state=state, trace_context=trace_context)

        tool_result = sdk_types.UserMessage(
            content=[sdk_types.ToolResultBlock(
                tool_use_id="toolu_1",
                content=[{"type": "text", "text": "3 results found."}],
                is_error=False,
            )],
        )
        all_events += claude_agent_sdk_bridge.translate_sdk_message(tool_result, state=state, trace_context=trace_context)

        final_text = sdk_types.AssistantMessage(
            content=[sdk_types.TextBlock(text="Here is what I found.")],
            model="claude-sonnet-4-5",
        )
        all_events += claude_agent_sdk_bridge.translate_sdk_message(final_text, state=state, trace_context=trace_context)

        result = sdk_types.ResultMessage(
            subtype="success",
            duration_ms=500,
            duration_api_ms=400,
            is_error=False,
            num_turns=2,
            session_id="sess-1",
            result="Here is what I found.",
        )
        all_events += claude_agent_sdk_bridge.translate_sdk_message(result, state=state, trace_context=trace_context)

        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(all_events)

        self.assertEqual(collected["final_payload"]["reply"], "Here is what I found.")
        self.assertEqual(len(collected["tool_calls"]), 1)
        tool_call = collected["tool_calls"][0]
        self.assertEqual(tool_call["name"], "web__search")
        self.assertEqual(tool_call["status"], "completed")
        self.assertEqual(tool_call["output"], "3 results found.")
        self.assertEqual(collected["action_execution_mode"], "tools_executed")
        self.assertEqual(collected["blocked_tools"], [])

    def test_full_turn_with_one_failed_tool_call(self):
        state = claude_agent_sdk_bridge.TranslationState()
        trace_context = _trace_context()
        all_events = []

        tool_use = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(id="toolu_9", name="shell__exec", input={"command": "false"})],
            model="claude-sonnet-4-5",
        )
        all_events += claude_agent_sdk_bridge.translate_sdk_message(tool_use, state=state, trace_context=trace_context)

        tool_result = sdk_types.UserMessage(
            content=[sdk_types.ToolResultBlock(
                tool_use_id="toolu_9",
                content=[{"type": "text", "text": "command not found"}],
                is_error=True,
            )],
        )
        all_events += claude_agent_sdk_bridge.translate_sdk_message(tool_result, state=state, trace_context=trace_context)

        result = sdk_types.ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            result="That command failed.",
        )
        all_events += claude_agent_sdk_bridge.translate_sdk_message(result, state=state, trace_context=trace_context)

        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(all_events)
        tool_call = collected["tool_calls"][0]
        self.assertEqual(tool_call["status"], "failed")
        self.assertEqual(tool_call["error"], "command not found")


class BuildSdkToolsTests(unittest.TestCase):
    def test_unsupported_meta_tools_are_not_registered(self):
        tool_defs = [
            {"name": "task_complete", "description": "d", "parameters": {}},
            {"name": "update_plan", "description": "d", "parameters": {}},
            {"name": "query_tool_registry", "description": "d", "parameters": {}},
            {"name": "web__search", "description": "d", "parameters": {"type": "object", "properties": {}}},
        ]
        generation_services = MagicMock()
        sdk_tools = claude_agent_sdk_bridge.build_sdk_tools(
            tool_defs=tool_defs,
            generation_services=generation_services,
            workspace_id="ws-1",
            thread_id="th-1",
            provider="anthropic",
            model="claude-sonnet-4-5",
            credentials={},
            reasoning_effort="",
            session_ctx={},
        )
        names = {t.name for t in sdk_tools}
        self.assertEqual(names, {"web__search"})

    def test_handler_dispatches_to_existing_executor_and_wraps_result(self):
        tool_defs = [{"name": "web__search", "description": "d", "parameters": {"type": "object", "properties": {}}}]
        generation_services = MagicMock()
        generation_services.execute_single_direct_tool_call = MagicMock(return_value="3 results.")
        sdk_tools = claude_agent_sdk_bridge.build_sdk_tools(
            tool_defs=tool_defs,
            generation_services=generation_services,
            workspace_id="ws-1",
            thread_id="th-1",
            provider="anthropic",
            model="claude-sonnet-4-5",
            credentials={"api_key": "sk-x"},
            reasoning_effort="",
            session_ctx={"foo": "bar"},
        )
        handler = sdk_tools[0].handler
        result = asyncio.run(handler({"query": "empyralis"}))
        self.assertEqual(result, {"content": [{"type": "text", "text": "3 results."}]})
        generation_services.execute_single_direct_tool_call.assert_called_once()
        _, kwargs = generation_services.execute_single_direct_tool_call.call_args
        self.assertEqual(kwargs["tool_call"], {"name": "web__search", "arguments": {"query": "empyralis"}})
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["credentials"], {"api_key": "sk-x"})

    def test_handler_surfaces_governance_exception_as_tool_error_not_a_crash(self):
        tool_defs = [{"name": "fleet__create_agent", "description": "d", "parameters": {"type": "object", "properties": {}}}]
        generation_services = MagicMock()
        generation_services.execute_single_direct_tool_call = MagicMock(
            side_effect=RuntimeError("Tool 'fleet__create_agent' is not enabled for this specialist agent."),
        )
        sdk_tools = claude_agent_sdk_bridge.build_sdk_tools(
            tool_defs=tool_defs,
            generation_services=generation_services,
            workspace_id="ws-1",
            thread_id="th-1",
            provider="anthropic",
            model="claude-sonnet-4-5",
            credentials={},
            reasoning_effort="",
            session_ctx={},
        )
        handler = sdk_tools[0].handler
        result = asyncio.run(handler({}))
        self.assertTrue(result["is_error"])
        self.assertIn("not enabled for this specialist", result["content"][0]["text"])


class RenderPromptTests(unittest.TestCase):
    def test_no_prior_messages_returns_bare_message(self):
        self.assertEqual(claude_agent_sdk_bridge.render_prompt("hi", None), "hi")
        self.assertEqual(claude_agent_sdk_bridge.render_prompt("hi", []), "hi")

    def test_prior_messages_are_folded_in_order(self):
        prior = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first reply"},
        ]
        rendered = claude_agent_sdk_bridge.render_prompt("second message", prior)
        self.assertIn("first message", rendered)
        self.assertIn("first reply", rendered)
        self.assertTrue(rendered.strip().endswith("user: second message"))
        self.assertLess(rendered.index("first message"), rendered.index("first reply"))
        self.assertLess(rendered.index("first reply"), rendered.index("second message"))


class _FakeQuery:
    """Stands in for claude_agent_sdk.query: an async-generator callable
    that records each call's (prompt, options) and, per call (consumed in
    order), either yields a scripted list of SDK message objects or raises
    a scripted exception — optionally after yielding some messages first
    (pass an (messages, exception) tuple), to simulate a failure partway
    through a turn."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[SimpleNamespace] = []

    async def __call__(self, *, prompt, options):
        self.calls.append(SimpleNamespace(prompt=prompt, options=options))
        item = self._responses.pop(0)
        if isinstance(item, tuple):
            messages, exc = item
            for message in messages:
                yield message
            raise exc
        for message in item:
            yield message


def _result_message(session_id: str = "sess-1", reply: str = "ok") -> sdk_types.ResultMessage:
    return sdk_types.ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=8, is_error=False,
        num_turns=1, session_id=session_id, result=reply,
    )


class RunClaudeAgentSdkTurnResumeTests(unittest.TestCase):
    """MAN-310 Phase 2: run_claude_agent_sdk_turn's resume_session_id branch
    — the actual mechanism that lets a turn skip re-folding history. Trace
    persistence (agent_trace_service.persist_ephemeral_envelope) is patched
    out in every test here so these stay focused on prompt/resume wiring;
    it has its own dedicated tests below."""

    def _run_turn(self, *, resume_session_id="", prior_messages=None, fake_query, message="second message"):
        with (
            patch("claude_agent_sdk.query", new=fake_query),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=AsyncMock()),
        ):
            events = asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message=message,
                system_prompt="Be terse.",
                prior_messages=prior_messages,
                tool_defs=[],
                generation_services=MagicMock(),
                workspace_id="ws-1",
                thread_id="trace-1",
                provider="anthropic",
                model="claude-sonnet-4-5",
                credentials={},
                trace_context=_trace_context(),
                resume_session_id=resume_session_id,
            ))
        return events

    def test_no_resume_folds_full_history_and_leaves_resume_option_unset(self):
        prior = [{"role": "user", "content": "first message"}, {"role": "assistant", "content": "first reply"}]
        fake_query = _FakeQuery([[_result_message()]])

        self._run_turn(resume_session_id="", prior_messages=prior, fake_query=fake_query)

        self.assertEqual(len(fake_query.calls), 1)
        sent_prompt = fake_query.calls[0].prompt
        self.assertIn("first message", sent_prompt)
        self.assertIn("first reply", sent_prompt)
        self.assertIsNone(fake_query.calls[0].options.resume)

    def test_resume_sends_bare_message_and_sets_resume_option(self):
        prior = [{"role": "user", "content": "first message"}, {"role": "assistant", "content": "first reply"}]
        fake_query = _FakeQuery([[_result_message()]])

        self._run_turn(resume_session_id="sess-prior", prior_messages=prior, fake_query=fake_query)

        self.assertEqual(len(fake_query.calls), 1)
        sent_prompt = fake_query.calls[0].prompt
        # The resumed session already has this history — must NOT be folded
        # in again (that would be two independent memories of the same
        # conversation at once).
        self.assertEqual(sent_prompt, "second message")
        self.assertNotIn("first message", sent_prompt)
        self.assertEqual(fake_query.calls[0].options.resume, "sess-prior")

    def test_resume_failure_before_any_message_retries_fresh_with_full_history(self):
        prior = [{"role": "user", "content": "first message"}, {"role": "assistant", "content": "first reply"}]
        # First call (resume attempted): raises before yielding anything.
        # Second call (the fallback retry): succeeds.
        fake_query = _FakeQuery([
            ([], RuntimeError("no such session")),
            [_result_message(session_id="sess-fresh")],
        ])

        events = self._run_turn(resume_session_id="sess-stale", prior_messages=prior, fake_query=fake_query)

        self.assertEqual(len(fake_query.calls), 2)
        first_call, second_call = fake_query.calls
        self.assertEqual(first_call.options.resume, "sess-stale")
        self.assertEqual(first_call.prompt, "second message")
        # The retry drops resume entirely and folds full history, exactly
        # like a turn that never had a session to resume.
        self.assertIsNone(second_call.options.resume)
        self.assertIn("first message", second_call.prompt)
        self.assertIn("first reply", second_call.prompt)
        final_event = next(e for e in events if e["type"] == "final")
        self.assertEqual(final_event["payload"]["session_id"], "sess-fresh")

    def test_resume_failure_after_a_message_does_not_retry(self):
        """A tool may already have executed a real side effect through the
        SAME in-process executor the legacy engine uses -- retrying the
        whole turn from scratch there could run it twice. Only a resume
        that fails before yielding ANYTHING is safe to retry."""
        prior = [{"role": "user", "content": "first message"}]
        partial_message = sdk_types.AssistantMessage(
            content=[sdk_types.TextBlock(text="partial...")], model="claude-sonnet-4-5",
        )
        fake_query = _FakeQuery([
            ([partial_message], RuntimeError("connection dropped mid-turn")),
        ])

        with self.assertRaises(RuntimeError):
            self._run_turn(resume_session_id="sess-stale", prior_messages=prior, fake_query=fake_query)

        self.assertEqual(len(fake_query.calls), 1)  # no retry attempted

    def test_failure_without_resume_attempt_propagates_without_retry(self):
        fake_query = _FakeQuery([([], RuntimeError("boom"))])

        with self.assertRaises(RuntimeError):
            self._run_turn(resume_session_id="", prior_messages=[], fake_query=fake_query)

        self.assertEqual(len(fake_query.calls), 1)  # nothing to fall back FROM


class RunClaudeAgentSdkTurnTracePersistenceTests(unittest.TestCase):
    """MAN-310 Phase 2: run_claude_agent_sdk_turn is the async context that
    makes each PERSISTED_TRACE_EVENT_TYPES envelope translate_sdk_message
    builds (ephemeral-only, by construction) durable, via agent_trace_
    service.persist_ephemeral_envelope."""

    def test_persists_trace_events_and_skips_tool_progress_and_final(self):
        tool_use = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(id="toolu_1", name="web__search", input={"query": "x"})],
            model="claude-sonnet-4-5",
        )
        tool_result = sdk_types.UserMessage(content=[sdk_types.ToolResultBlock(
            tool_use_id="toolu_1", content=[{"type": "text", "text": "found it"}], is_error=False,
        )])
        fake_query = _FakeQuery([[tool_use, tool_result, _result_message()]])
        persist_mock = AsyncMock()

        with (
            patch("claude_agent_sdk.query", new=fake_query),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=persist_mock),
        ):
            events = asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message="find something",
                system_prompt="",
                prior_messages=None,
                tool_defs=[],
                generation_services=MagicMock(),
                workspace_id="ws-1",
                thread_id="trace-1",
                provider="anthropic",
                model="claude-sonnet-4-5",
                credentials={},
                trace_context=_trace_context(),
            ))

        trace_events = [e for e in events if e.get("type") == "trace"]
        self.assertGreaterEqual(len(trace_events), 1)
        self.assertEqual(persist_mock.await_count, len(trace_events))
        persisted_envelopes = [call.args[1] for call in persist_mock.await_args_list]
        self.assertEqual(persisted_envelopes, [e["payload"] for e in trace_events])
        # tool_progress and final events are never routed to persistence.
        non_trace_types = {e["type"] for e in events} - {"trace"}
        self.assertTrue(non_trace_types <= {"tool_progress", "final"})

    def test_no_trace_context_persists_nothing(self):
        fake_query = _FakeQuery([[_result_message()]])
        persist_mock = AsyncMock()

        with (
            patch("claude_agent_sdk.query", new=fake_query),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=persist_mock),
        ):
            asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message="hi",
                system_prompt="",
                prior_messages=None,
                tool_defs=[],
                generation_services=MagicMock(),
                workspace_id="ws-1",
                thread_id="trace-1",
                provider="anthropic",
                model="claude-sonnet-4-5",
                credentials={},
                trace_context=None,
            ))

        persist_mock.assert_not_awaited()


class TurnEngineSelectionFlagOffTests(unittest.TestCase):
    """Pins that _run_sage_action_loop_v3's engine flag defaults to the
    legacy path for every input shape an existing caller (none of which
    pass engine_options today) could ever produce."""

    def test_none_resolves_to_legacy(self):
        self.assertNotEqual(
            sage_agent_runtime_service._resolve_turn_engine_id(None),
            claude_agent_sdk_bridge.ENGINE_ID,
        )

    def test_empty_dict_resolves_to_legacy(self):
        self.assertNotEqual(
            sage_agent_runtime_service._resolve_turn_engine_id({}),
            claude_agent_sdk_bridge.ENGINE_ID,
        )

    def test_non_dict_resolves_to_legacy(self):
        self.assertNotEqual(
            sage_agent_runtime_service._resolve_turn_engine_id("claude_agent_sdk"),  # type: ignore[arg-type]
            claude_agent_sdk_bridge.ENGINE_ID,
        )

    def test_other_engine_value_resolves_to_legacy(self):
        self.assertNotEqual(
            sage_agent_runtime_service._resolve_turn_engine_id({"engine": "legacy"}),
            claude_agent_sdk_bridge.ENGINE_ID,
        )

    def test_explicit_sdk_engine_is_selected(self):
        self.assertEqual(
            sage_agent_runtime_service._resolve_turn_engine_id({"engine": "claude_agent_sdk"}),
            claude_agent_sdk_bridge.ENGINE_ID,
        )

    def test_engine_value_is_case_insensitive(self):
        self.assertEqual(
            sage_agent_runtime_service._resolve_turn_engine_id({"engine": "Claude_Agent_SDK"}),
            claude_agent_sdk_bridge.ENGINE_ID,
        )


if __name__ == "__main__":
    unittest.main()

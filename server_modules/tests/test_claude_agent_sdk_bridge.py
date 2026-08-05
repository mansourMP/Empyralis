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
import os
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock, patch

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
    """Anthropic's documented auth precedence (code.claude.com/docs/en/
    authentication) ranks ANTHROPIC_AUTH_TOKEN above ANTHROPIC_API_KEY, and
    AUTH_TOKEN carries no interactive "approve this key?" consent gate — see
    resolve_sdk_process_env's own docstring for why that matters for a
    fresh-every-turn subprocess. A supplied credential therefore lands on
    ANTHROPIC_AUTH_TOKEN, not ANTHROPIC_API_KEY."""

    def test_explicit_overrides_win_over_credentials(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "from-credentials", "base_url": "https://from-credentials.example"},
            anthropic_api_key="explicit-key",
            anthropic_base_url="https://api.deepseek.com/anthropic",
        )
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "explicit-key")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        # The old (pre-fix) target key stays blanked, not merely absent —
        # see the ambient-leak test class below for why "blanked" and
        # "absent" are not the same thing for this function.
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")

    def test_falls_back_to_credentials_when_no_explicit_override(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "sk-from-creds"},
        )
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "sk-from-creds")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "")

    def test_never_hardcodes_a_base_url(self):
        # No override, no credentials base_url — nothing about DeepSeek (or
        # any other non-Anthropic backend) appears anywhere unless the
        # caller supplied it for this turn.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(credentials=None)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "")

    def test_config_dir_forwarded_to_both_linux_and_macos_keys(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(config_dir="/tmp/fresh-turn-dir")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/tmp/fresh-turn-dir")
        self.assertEqual(env["CLAUDE_SECURESTORAGE_CONFIG_DIR"], "/tmp/fresh-turn-dir")

    def test_no_config_dir_means_no_config_dir_keys(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env()
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertNotIn("CLAUDE_SECURESTORAGE_CONFIG_DIR", env)


class ResolveSdkProcessEnvAmbientLeakTests(unittest.TestCase):
    """claude_agent_sdk's Python Transport MERGES ClaudeAgentOptions(env=...)
    ON TOP OF the parent process's own environment rather than replacing it
    (see resolve_sdk_process_env's docstring) — so Empyralis's OWN backend
    process having any ANTHROPIC_*/CLAUDE_CODE_* variable set would
    otherwise leak into every spawned tenant subprocess: wrong billing
    attribution at best, cross-tenant credential use at worst. This pins
    that every credential-shaped key this module knows about is ALWAYS
    present in the returned dict — omitted is not safe, since an omitted
    key doesn't survive the merge-over-parent-env — and that patching
    os.environ with plausible ambient leakage never changes the result."""

    def test_every_credential_key_is_always_present_and_blank_by_default(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env()
        for key in claude_agent_sdk_bridge._CREDENTIAL_ENV_KEYS:
            self.assertIn(key, env)
            self.assertEqual(env[key], "")

    def test_ambient_environment_variables_never_reach_the_result(self):
        ambient = {
            "ANTHROPIC_API_KEY": "leaked-ambient-key",
            "ANTHROPIC_AUTH_TOKEN": "leaked-ambient-token",
            "ANTHROPIC_BASE_URL": "https://leaked.example.com",
            "CLAUDE_CODE_OAUTH_TOKEN": "leaked-oauth-token",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLAUDE_CODE_USE_FOUNDRY": "1",
            "CLAUDE_CODE_USE_ANTHROPIC_AWS": "1",
            "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD": "1",
            "CLAUDE_CODE_USE_MANTLE": "1",
        }
        with patch.dict(os.environ, ambient, clear=False):
            # No explicit per-turn credential supplied — this function must
            # not read the ambient values above out of os.environ (it never
            # touches os.environ at all) and must still explicitly blank
            # every one of them in its own returned dict.
            env = claude_agent_sdk_bridge.resolve_sdk_process_env()
        for key in ambient:
            self.assertEqual(env[key], "", f"{key} leaked from ambient os.environ")

    def test_explicit_credential_still_wins_over_ambient_noise(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "leaked-ambient-key"}, clear=False):
            env = claude_agent_sdk_bridge.resolve_sdk_process_env(anthropic_api_key="real-tenant-key")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "real-tenant-key")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")


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


class RunClaudeAgentSdkTurnIsolationTests(unittest.TestCase):
    """Each turn must get its own fresh, never-logged-in-to CLAUDE_CONFIG_DIR
    (see resolve_sdk_process_env's docstring for why) and that directory
    must not survive past the turn — a leftover directory per turn is an
    unbounded disk leak on a server handling many turns. Exercises the REAL
    run_claude_agent_sdk_turn with only claude_agent_sdk's own three
    entrypoints (ClaudeAgentOptions/create_sdk_mcp_server/query) faked out —
    everything this module does with them (building options, awaiting the
    async generator) runs for real."""

    def test_config_dir_is_fresh_for_the_turn_and_removed_after(self):
        import claude_agent_sdk as real_sdk

        captured: Dict[str, Any] = {}

        class _FakeOptions:
            def __init__(self, **kwargs):
                captured["options_kwargs"] = kwargs

        async def _fake_query(*, prompt, options):
            # The directory must exist WHILE the (fake) subprocess would be
            # running — the property this test exists to pin.
            env = captured["options_kwargs"]["env"]
            captured["dir_existed_during_call"] = os.path.isdir(env["CLAUDE_CONFIG_DIR"])
            captured["dir_path"] = env["CLAUDE_CONFIG_DIR"]
            return
            yield  # pragma: no cover - makes this an async generator function

        with (
            patch.object(real_sdk, "ClaudeAgentOptions", _FakeOptions),
            patch.object(real_sdk, "create_sdk_mcp_server", return_value=MagicMock()),
            patch.object(real_sdk, "query", _fake_query),
        ):
            events = asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message="hi",
                system_prompt="",
                prior_messages=None,
                tool_defs=[],
                generation_services=MagicMock(),
                workspace_id="ws-1",
                thread_id="thread-1",
                provider="anthropic",
                model="claude-x",
                credentials={"api_key": "sk-test"},
            ))

        self.assertEqual(events, [])
        env = captured["options_kwargs"]["env"]
        self.assertTrue(captured["dir_existed_during_call"], "config dir did not exist during the turn")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], env["CLAUDE_SECURESTORAGE_CONFIG_DIR"])
        self.assertFalse(os.path.isdir(captured["dir_path"]), "temp config dir was not cleaned up after the turn")

    def test_config_dir_is_removed_even_when_the_turn_raises(self):
        import claude_agent_sdk as real_sdk

        captured: Dict[str, Any] = {}

        class _FakeOptions:
            def __init__(self, **kwargs):
                captured["options_kwargs"] = kwargs

        async def _fake_query(*, prompt, options):
            raise RuntimeError("simulated CLI failure")
            yield  # pragma: no cover

        with (
            patch.object(real_sdk, "ClaudeAgentOptions", _FakeOptions),
            patch.object(real_sdk, "create_sdk_mcp_server", return_value=MagicMock()),
            patch.object(real_sdk, "query", _fake_query),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                    message="hi",
                    system_prompt="",
                    prior_messages=None,
                    tool_defs=[],
                    generation_services=MagicMock(),
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    provider="anthropic",
                    model="claude-x",
                    credentials={"api_key": "sk-test"},
                ))

        dir_path = captured["options_kwargs"]["env"]["CLAUDE_CONFIG_DIR"]
        self.assertFalse(os.path.isdir(dir_path), "temp config dir leaked after an exception")


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

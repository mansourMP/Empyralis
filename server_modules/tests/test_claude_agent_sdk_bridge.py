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
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import types as sdk_types

from server_modules import agent_trace_service
from server_modules import claude_agent_sdk_bridge
from server_modules import openai_compat_adapter
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

    def test_provider_with_a_native_anthropic_endpoint_supplies_the_base_url(self):
        # Found by driving a real product turn: model_config selects a
        # provider, and nothing else in the turn carries an Anthropic-shaped
        # URL — without this the CLI would talk to Anthropic using a
        # DeepSeek token.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "sk-deepseek"}, provider="deepseek",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "sk-deepseek")

    def test_provider_openai_shaped_profile_url_is_never_used_as_the_base_url(self):
        # provider_profiles' base_url for deepseek is .../v1 — the OpenAI
        # protocol. Handing that to an Anthropic-Messages client fails as an
        # opaque HTTP error, so it must never be the fallback.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "k", "base_url": "https://api.deepseek.com/v1"},
            provider="deepseek",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")

    def test_anthropic_itself_gets_no_base_url_override(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "sk-ant"}, provider="anthropic",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "")

    def test_adapter_routed_providers_get_the_loopback_adapters_own_base_url(self):
        # openai/gemini/xai ship no native Anthropic-Messages endpoint, but
        # they DO now route through openai_compat_adapter's own in-process
        # loopback translator (see resolve_sdk_process_env's adapter-routed
        # branch) — the base_url is that server's own address, never the
        # provider's real (public) endpoint and never empty.
        for provider in ("openai", "gemini", "xai"):
            env = claude_agent_sdk_bridge.resolve_sdk_process_env(
                credentials={"api_key": "k"}, provider=provider,
            )
            self.assertTrue(
                env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:"),
                f"provider={provider!r} got {env['ANTHROPIC_BASE_URL']!r}",
            )

    def test_a_genuinely_unrecognised_provider_gets_no_base_url(self):
        # Empty/unknown — not natively Anthropic-compatible, not adapter-
        # routed — still emits nothing, so the turn fails as an honest auth
        # error against Anthropic rather than silently talking the wrong
        # protocol to a made-up address.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "k"}, provider="",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "")

    def test_explicit_override_still_beats_the_provider_mapping(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "k"}, provider="deepseek",
            anthropic_base_url="https://gateway.internal/anthropic",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://gateway.internal/anthropic")

    def test_config_dir_forwarded_to_both_linux_and_macos_keys(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(config_dir="/tmp/fresh-turn-dir")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/tmp/fresh-turn-dir")
        self.assertEqual(env["CLAUDE_SECURESTORAGE_CONFIG_DIR"], "/tmp/fresh-turn-dir")

    def test_no_config_dir_means_no_config_dir_keys(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env()
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertNotIn("CLAUDE_SECURESTORAGE_CONFIG_DIR", env)

    # -- Ollama: a native Anthropic-compatible endpoint, but self-hosted
    # per-workspace rather than DeepSeek's one fixed public URL, so it is
    # resolved from the turn's own credentials instead of a map entry (see
    # resolve_ollama_anthropic_base_url) -- MAN-310 follow-up.

    def test_ollama_base_url_is_derived_from_the_turns_own_credential_and_v1_stripped(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"base_url": "http://localhost:11434/v1"}, provider="ollama",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://localhost:11434")

    def test_ollama_base_url_never_hardcoded_when_nothing_is_configured(self):
        # No base_url anywhere in this turn's credentials (the shape
        # secretless_provider_credentials("ollama", "none") actually
        # produces for the common case) -- no override is emitted. Guessing
        # localhost here would be worse than no override: it could point at
        # a wrong or nonexistent local service on whatever machine the
        # backend process happens to run on.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"auth_mode": "none"}, provider="ollama",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "")

    def test_ollama_auth_token_placeholder_is_non_empty_even_with_no_api_key(self):
        # provider_profiles.py's "ollama" entry has auth=["none"] -- no real
        # secret ever exists for it -- but Ollama's own setup docs require a
        # non-empty ANTHROPIC_AUTH_TOKEN (the value itself is ignored
        # server-side). Today's generic no-api-key behavior alone would
        # leave this blank; the explicit Ollama branch must fix that.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"auth_mode": "none"}, provider="ollama",
        )
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "ollama")

    def test_ollama_auth_token_placeholder_still_applies_with_no_configured_base_url(self):
        # The auth-token fix and the base-url fix are independent: even when
        # there is nothing to override ANTHROPIC_BASE_URL with, the token
        # must still never be blank for this provider.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials=None, provider="ollama",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "ollama")

    def test_ollama_real_api_key_still_beats_the_placeholder(self):
        # An unlikely but possible shape (a credential that does carry a
        # real key) must still win over the "ollama" placeholder -- the
        # placeholder only exists to fill a gap, never to override a real
        # credential.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "sk-real"}, provider="ollama",
        )
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "sk-real")

    def test_ollama_explicit_override_still_beats_the_derived_credential(self):
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"base_url": "http://localhost:11434/v1"}, provider="ollama",
            anthropic_base_url="https://gateway.internal/anthropic",
        )
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://gateway.internal/anthropic")

    def test_ollama_never_falls_through_to_the_openai_compat_adapter(self):
        # "ollama" has a native endpoint -- it must never be minted an
        # opaque adapter token or routed at the loopback adapter's address,
        # the way openai/gemini/xai are (see
        # test_adapter_routed_providers_get_the_loopback_adapters_own_base_url
        # above).
        with patch.object(
            openai_compat_adapter, "mint_turn_token_for_provider",
        ) as mock_mint:
            env = claude_agent_sdk_bridge.resolve_sdk_process_env(
                credentials={"base_url": "http://localhost:11434/v1"}, provider="ollama",
            )
        mock_mint.assert_not_called()
        self.assertFalse(env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:"))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://localhost:11434")

    def test_ollama_cloud_is_a_distinct_provider_and_is_unaffected(self):
        # "ollama_cloud" (the hosted/BYOK offering) is a different provider
        # id from local "ollama" and has no native Anthropic surface -- it
        # must still go through the loopback adapter, never this resolver.
        env = claude_agent_sdk_bridge.resolve_sdk_process_env(
            credentials={"api_key": "k", "base_url": "http://localhost:11434/v1"},
            provider="ollama_cloud",
        )
        self.assertTrue(env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:"))


class ResolveOllamaAnthropicBaseUrlTests(unittest.TestCase):
    """Direct unit tests of the resolver itself, independent of the wiring
    inside resolve_sdk_process_env -- a realistic range of shapes a
    workspace's configured Ollama base_url could actually take."""

    def test_localhost_default_with_v1_suffix_is_stripped_to_bare_host(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                "ollama", {"base_url": "http://localhost:11434/v1"},
            ),
            "http://localhost:11434",
        )

    def test_custom_port_is_preserved(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                "ollama", {"base_url": "http://localhost:8080/v1"},
            ),
            "http://localhost:8080",
        )

    def test_remote_host_is_preserved(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                "ollama", {"base_url": "http://192.168.1.50:11434/v1"},
            ),
            "http://192.168.1.50:11434",
        )

    def test_remote_domain_name_over_https_is_preserved(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                "ollama", {"base_url": "https://ollama.internal.example.com:11434/v1"},
            ),
            "https://ollama.internal.example.com:11434",
        )

    def test_value_with_no_v1_suffix_is_left_as_is(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                "ollama", {"base_url": "http://localhost:11434"},
            ),
            "http://localhost:11434",
        )

    def test_trailing_slash_with_no_path_is_stripped(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                "ollama", {"base_url": "http://localhost:11434/"},
            ),
            "http://localhost:11434",
        )

    def test_trailing_slash_after_v1_is_stripped(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                "ollama", {"base_url": "http://localhost:11434/v1/"},
            ),
            "http://localhost:11434",
        )

    def test_no_base_url_configured_returns_no_override(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url("ollama", {"auth_mode": "none"}),
            "",
        )

    def test_none_credentials_returns_no_override(self):
        self.assertEqual(claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url("ollama", None), "")

    def test_blank_base_url_string_returns_no_override(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url("ollama", {"base_url": "   "}),
            "",
        )

    def test_non_ollama_provider_is_always_a_no_op_even_with_a_base_url_present(self):
        # A profile-configured base_url on some OTHER provider's credentials
        # must never leak through this resolver -- it is gated on provider,
        # not just on the shape of the credentials dict.
        for provider in ("", "deepseek", "anthropic", "ollama_cloud", "openai"):
            self.assertEqual(
                claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                    provider, {"base_url": "http://localhost:11434/v1"},
                ),
                "",
                f"provider={provider!r} should be a no-op",
            )

    def test_provider_id_is_case_and_whitespace_insensitive(self):
        self.assertEqual(
            claude_agent_sdk_bridge.resolve_ollama_anthropic_base_url(
                " Ollama ", {"base_url": "http://localhost:11434/v1"},
            ),
            "http://localhost:11434",
        )


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
        # With known_tool_names left unset ("not configured" — the pure-
        # translation mode these unit tests use), translate_sdk_message has
        # no opinion on tool identity: filtering happens earlier, in
        # build_sdk_tools/run_claude_agent_sdk_turn, which never registers
        # task_complete/update_plan/query_tool_registry with the SDK in the
        # first place, so the model can't call them.
        #
        # Every REAL turn does configure known_tool_names (see
        # RunClaudeAgentSdkTurnForeignToolTests), and there the opposite is
        # asserted — an unregistered name never becomes a tool.started. See
        # TranslateForeignToolTests.
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
        # served_by_anthropic is now a PRECONDITION of reporting a cost, not
        # an incidental detail: total_cost_usd is a client-side number the
        # CLI computes from Anthropic's price table no matter where it was
        # pointed, so it may only be reported for a turn Anthropic actually
        # served. This test used to assert the cost came through with no
        # attribution established at all — the buggy behaviour. It now pins
        # the honest half of the rule (attributed -> reported); the
        # unattributed and non-Anthropic halves are pinned in
        # TotalCostAttributionTests below.
        state.served_by_anthropic = True
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
        # A subtype that really does name a failure is still passed through
        # verbatim as the persisted trace.failed code — the "success"
        # fallback below must not have flattened the honest cases too.
        failed = next(
            e["payload"] for e in events
            if e["type"] == "trace" and e["payload"].get("event_type") == "trace.failed"
        )
        self.assertEqual(failed["data"]["code"], "error_max_turns")


class ResultMessageErrorCodeTruthfulnessTests(unittest.TestCase):
    """A failure must never file itself under "success".

    Driving the real Claude Code CLI, an API/upstream failure arrives as a
    ResultMessage with is_error=True and subtype STILL SET TO "success" —
    the installed SDK documents this itself on ResultMessage.api_error_status
    ("HTTP status code ... of the failing API call when ``is_error`` is True
    and ``subtype`` is 'success'"). The bridge copied subtype straight into
    the persisted trace.failed `code`, and _collect_sage_operator_loop_v3_
    events renders that code verbatim as the blocked entry's NAME — so the
    customer's Work tab showed a blocked entry called "success" for a turn
    that had failed outright.
    """

    def _translate(self, message):
        return claude_agent_sdk_bridge.translate_sdk_message(
            message,
            state=claude_agent_sdk_bridge.TranslationState(),
            trace_context=_trace_context(),
        )

    @staticmethod
    def _failed_data(events):
        return next(
            e["payload"]["data"] for e in events
            if e["type"] == "trace" and e["payload"].get("event_type") == "trace.failed"
        )

    def test_api_failure_reported_with_subtype_success_is_not_coded_success(self):
        message = sdk_types.ResultMessage(
            subtype="success",  # measured: the CLI does NOT change this on API failure
            duration_ms=1200,
            duration_api_ms=1100,
            is_error=True,
            num_turns=1,
            session_id="sess-1",
            result=None,
            api_error_status=500,
        )
        events = self._translate(message)

        code = self._failed_data(events)["code"]
        self.assertNotEqual(code, "success")
        self.assertEqual(code, "provider_generation_failed")
        final_event = next(e for e in events if e["type"] == "final")
        self.assertNotEqual(final_event["payload"]["error"], "success")
        self.assertEqual(final_event["payload"]["error"], "provider_generation_failed")

    def test_the_http_status_is_carried_as_detail_not_as_a_new_code(self):
        message = sdk_types.ResultMessage(
            subtype="success", duration_ms=10, duration_api_ms=8, is_error=True,
            num_turns=1, session_id="sess-1", result=None, api_error_status=529,
        )
        events = self._translate(message)

        data = self._failed_data(events)
        self.assertEqual(data["code"], "provider_generation_failed")
        self.assertIn("529", data["message"])

    def test_blank_subtype_also_falls_back_instead_of_emitting_an_empty_code(self):
        message = sdk_types.ResultMessage(
            subtype="", duration_ms=10, duration_api_ms=8, is_error=True,
            num_turns=1, session_id="sess-1", result=None,
        )
        events = self._translate(message)
        self.assertEqual(self._failed_data(events)["code"], "provider_generation_failed")

    def test_the_failure_reaches_the_real_collector_without_the_word_success(self):
        # The end the bug was actually visible at: the collector turns a
        # trace.failed `code` into a blocked entry's name, which is what the
        # Work tab renders.
        message = sdk_types.ResultMessage(
            subtype="success", duration_ms=10, duration_api_ms=8, is_error=True,
            num_turns=1, session_id="sess-1", result=None, api_error_status=500,
        )
        events = self._translate(message)
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)

        names = [entry.get("name") for entry in collected["blocked_tools"]]
        self.assertNotIn("success", names)
        self.assertIn("provider_generation_failed", names)
        self.assertEqual(collected["tool_calls"], [])


class SyntheticAssistantMessageTests(unittest.TestCase):
    """API-error prose must never ship as the agent's answer.

    claude_agent_sdk.types.AssistantMessage carries a typed `error` field
    (AssistantMessageError: "authentication_failed" | "billing_error" |
    "rate_limit" | "invalid_request" | "server_error" | "unknown") and the
    CLI stamps model="<synthetic>" on messages it fabricated rather than
    received from a model. Both arrive carrying a TextBlock of API-error
    prose. The bridge read neither field, so that prose was appended to
    state.reply_text_parts like ordinary model output and became the reply
    the customer saw — the provider's error text, in the agent's voice, on a
    turn presented as having answered.
    """

    _PROSE = 'API Error: 500 {"type":"error","error":{"type":"api_error"}}'

    def _synthetic(self, *, error="server_error", model="<synthetic>"):
        return sdk_types.AssistantMessage(
            content=[sdk_types.TextBlock(text=self._PROSE)],
            model=model,
            error=error,
        )

    def test_errored_assistant_message_never_enters_the_reply(self):
        state = claude_agent_sdk_bridge.TranslationState()
        events = claude_agent_sdk_bridge.translate_sdk_message(
            self._synthetic(), state=state, trace_context=_trace_context(),
        )

        self.assertEqual(state.reply_text_parts, [])
        self.assertTrue(state.saw_provider_error)
        failed = [
            e["payload"] for e in events
            if e["type"] == "trace" and e["payload"].get("event_type") == "trace.failed"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["data"]["code"], "provider_generation_failed")
        self.assertIn("server_error", failed[0]["data"]["message"])

    def test_synthetic_model_alone_is_enough_to_reject_the_text(self):
        state = claude_agent_sdk_bridge.TranslationState()
        claude_agent_sdk_bridge.translate_sdk_message(
            self._synthetic(error=None), state=state, trace_context=_trace_context(),
        )
        self.assertEqual(state.reply_text_parts, [])
        self.assertTrue(state.saw_provider_error)

    def test_an_ordinary_assistant_message_is_untouched(self):
        state = claude_agent_sdk_bridge.TranslationState()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.TextBlock(text="Here is the answer.")],
            model="claude-sonnet-4-5",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        self.assertEqual(events, [])
        self.assertEqual(state.reply_text_parts, ["Here is the answer."])
        self.assertFalse(state.saw_provider_error)

    def test_the_same_prose_does_not_return_through_the_result_message(self):
        # The other door: ResultMessage.result on such a turn is that same
        # API-error text, and it lands directly on payload["reply"].
        state = claude_agent_sdk_bridge.TranslationState()
        events = claude_agent_sdk_bridge.translate_sdk_message(
            self._synthetic(), state=state, trace_context=_trace_context(),
        )
        result_message = sdk_types.ResultMessage(
            subtype="success", duration_ms=10, duration_api_ms=8, is_error=True,
            num_turns=1, session_id="sess-1", result=self._PROSE, api_error_status=500,
        )
        events += claude_agent_sdk_bridge.translate_sdk_message(
            result_message, state=state, trace_context=_trace_context(),
        )

        final_event = next(e for e in events if e["type"] == "final")
        self.assertEqual(final_event["payload"]["reply"], "")
        collected = sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)
        self.assertEqual(collected["final_payload"]["reply"], "")
        self.assertEqual(collected["tool_calls"], [])
        self.assertTrue(collected["blocked_tools"])

    def test_a_genuine_partial_reply_on_a_failed_turn_still_survives(self):
        # Narrowness check: reply suppression above needs BOTH a seen
        # provider-error message AND is_error. An ordinary error_max_turns
        # turn carries real model output and must keep it.
        state = claude_agent_sdk_bridge.TranslationState()
        state.reply_text_parts.append("I got halfway through.")
        message = sdk_types.ResultMessage(
            subtype="error_max_turns", duration_ms=10, duration_api_ms=8, is_error=True,
            num_turns=5, session_id="sess-1", result=None,
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        final_event = next(e for e in events if e["type"] == "final")
        self.assertEqual(final_event["payload"]["reply"], "I got halfway through.")


class TotalCostAttributionTests(unittest.TestCase):
    """ResultMessage.total_cost_usd is computed CLIENT-SIDE by the `claude`
    CLI from ANTHROPIC's price table, whatever endpoint it was actually
    pointed at. Copied through unconditionally it reported an Anthropic
    price for tokens Anthropic never served: a canned local response on a
    DeepSeek-routed turn was billed at $0.0033. A missing number is honest;
    a wrong one is not, so the key is omitted rather than zeroed."""

    def _final_payload(self, *, served_by_anthropic, total_cost_usd=0.0033):
        state = claude_agent_sdk_bridge.TranslationState(served_by_anthropic=served_by_anthropic)
        message = sdk_types.ResultMessage(
            subtype="success", duration_ms=10, duration_api_ms=8, is_error=False,
            num_turns=1, session_id="sess-1", result="Done.", total_cost_usd=total_cost_usd,
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        return next(e for e in events if e["type"] == "final")["payload"]

    def test_non_anthropic_turn_reports_no_cost_at_all(self):
        payload = self._final_payload(served_by_anthropic=False)
        self.assertNotIn("total_cost_usd", payload)
        # Not a zero and not an estimate — absent.
        self.assertIsNone(payload.get("total_cost_usd"))

    def test_unestablished_attribution_reports_no_cost(self):
        # The default. Fail-safe: nobody said where this turn went, so no
        # number is emitted.
        self.assertNotIn("total_cost_usd", self._final_payload(served_by_anthropic=None))

    def test_anthropic_turn_still_reports_the_real_cost(self):
        payload = self._final_payload(served_by_anthropic=True, total_cost_usd=0.002)
        self.assertEqual(payload["total_cost_usd"], 0.002)

    def test_provider_with_its_own_anthropic_compatible_endpoint_is_not_anthropic(self):
        self.assertFalse(claude_agent_sdk_bridge.turn_is_served_by_anthropic(provider="deepseek"))
        # Guard the premise: this provider really is routed elsewhere.
        self.assertTrue(claude_agent_sdk_bridge.resolve_anthropic_compatible_base_url("deepseek"))

    def test_anthropic_provider_with_no_override_is_anthropic(self):
        self.assertTrue(claude_agent_sdk_bridge.turn_is_served_by_anthropic(provider="anthropic"))

    def test_an_explicit_base_url_override_disqualifies_the_cost(self):
        self.assertFalse(claude_agent_sdk_bridge.turn_is_served_by_anthropic(
            provider="anthropic", anthropic_base_url="https://api.deepseek.com/anthropic",
        ))

    def test_an_unrecognised_provider_is_not_assumed_to_be_anthropic(self):
        self.assertFalse(claude_agent_sdk_bridge.turn_is_served_by_anthropic(provider="openai"))


class RunTurnCostAttributionWiringTests(unittest.TestCase):
    """Pins that production actually populates the signal — the unit tests
    above would all pass against a bridge that never set it."""

    def _run_turn(self, *, provider, total_cost_usd=0.0033):
        result_message = sdk_types.ResultMessage(
            subtype="success", duration_ms=10, duration_api_ms=8, is_error=False,
            num_turns=1, session_id="sess-1", result="Done.", total_cost_usd=total_cost_usd,
        )
        fake_client = _fake_claude_sdk_client([[result_message]])
        with (
            patch("claude_agent_sdk.ClaudeSDKClient", new=fake_client),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=AsyncMock()),
        ):
            events = asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message="hello",
                system_prompt="Be terse.",
                prior_messages=None,
                tool_defs=[],
                generation_services=MagicMock(),
                workspace_id="ws-1",
                thread_id="trace-1",
                provider=provider,
                model="claude-sonnet-4-5",
                credentials={},
                trace_context=_trace_context(),
            ))
        return next(e for e in events if e["type"] == "final")["payload"]

    def test_a_deepseek_routed_turn_emits_no_cost(self):
        self.assertNotIn("total_cost_usd", self._run_turn(provider="deepseek"))

    def test_an_anthropic_turn_emits_the_cost(self):
        self.assertEqual(self._run_turn(provider="anthropic", total_cost_usd=0.002)["total_cost_usd"], 0.002)

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


_UNSET_CONTEXT_USAGE = object()


def _fake_claude_sdk_client(responses, *, context_usage=_UNSET_CONTEXT_USAGE, context_usage_error=None):
    """Build a fake CLASS standing in for claude_agent_sdk.ClaudeSDKClient —
    patched in as the class itself (`patch("claude_agent_sdk.ClaudeSDKClient",
    new=...)`), never as one pre-built instance, because run_claude_agent_sdk_
    turn instantiates a fresh client per attempt (the primary call, and — on
    a safe resume failure — the fallback retry), the same way it called
    claude_agent_sdk.query() fresh per attempt before this module's engine
    swap from query() to ClaudeSDKClient.

    `responses` is popped once per INSTANTIATION (there is exactly one
    .query()/.receive_response() pair per instance in production, mirroring
    one query() call per attempt before). Each item is either a plain list
    of scripted SDK message objects (yielded in order from
    receive_response()), or an (messages, exception) tuple to simulate a
    failure partway through the stream — the same two shapes the prior
    query()-mocking harness (formerly _FakeQuery) used, so every resume/
    fallback test below ports over with the same responses= scripts.

    `.calls` lives on the returned CLASS (not an instance): a list of
    SimpleNamespace(prompt=, options=), one entry per .query(prompt) call
    across every instance this factory produced — the direct analogue of
    the old _FakeQuery.calls.

    `context_usage` / `context_usage_error` script get_context_usage()
    identically for every instance this factory produces. Neither
    supplied -> raises AttributeError, standing in for "this build of the
    SDK/CLI has no such method" — the realistic default for a test that
    isn't specifically exercising context-usage attachment.
    """
    responses_queue = list(responses)
    calls: list[SimpleNamespace] = []

    class _FakeClaudeSDKClient:
        def __init__(self, *, options=None, transport=None):
            self.options = options
            self._transport = transport
            self._item = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def query(self, prompt, session_id="default"):
            calls.append(SimpleNamespace(prompt=prompt, options=self.options))
            self._item = responses_queue.pop(0)

        async def receive_response(self):
            item = self._item
            if isinstance(item, tuple):
                messages, exc = item
                for message in messages:
                    yield message
                raise exc
            for message in item:
                yield message

        async def get_context_usage(self):
            if context_usage_error is not None:
                raise context_usage_error
            if context_usage is _UNSET_CONTEXT_USAGE:
                raise AttributeError("get_context_usage is not scripted on this fake")
            return context_usage

    _FakeClaudeSDKClient.calls = calls
    return _FakeClaudeSDKClient


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

    def _run_turn(self, *, resume_session_id="", prior_messages=None, fake_client, message="second message"):
        with (
            patch("claude_agent_sdk.ClaudeSDKClient", new=fake_client),
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
        fake_client = _fake_claude_sdk_client([[_result_message()]])

        self._run_turn(resume_session_id="", prior_messages=prior, fake_client=fake_client)

        self.assertEqual(len(fake_client.calls), 1)
        sent_prompt = fake_client.calls[0].prompt
        self.assertIn("first message", sent_prompt)
        self.assertIn("first reply", sent_prompt)
        self.assertIsNone(fake_client.calls[0].options.resume)

    def test_resume_sends_bare_message_and_sets_resume_option(self):
        prior = [{"role": "user", "content": "first message"}, {"role": "assistant", "content": "first reply"}]
        fake_client = _fake_claude_sdk_client([[_result_message()]])

        self._run_turn(resume_session_id="sess-prior", prior_messages=prior, fake_client=fake_client)

        self.assertEqual(len(fake_client.calls), 1)
        sent_prompt = fake_client.calls[0].prompt
        # The resumed session already has this history — must NOT be folded
        # in again (that would be two independent memories of the same
        # conversation at once).
        self.assertEqual(sent_prompt, "second message")
        self.assertNotIn("first message", sent_prompt)
        self.assertEqual(fake_client.calls[0].options.resume, "sess-prior")

    def test_resume_failure_before_any_message_retries_fresh_with_full_history(self):
        prior = [{"role": "user", "content": "first message"}, {"role": "assistant", "content": "first reply"}]
        # First call (resume attempted): raises before yielding anything.
        # Second call (the fallback retry): succeeds.
        fake_client = _fake_claude_sdk_client([
            ([], RuntimeError("no such session")),
            [_result_message(session_id="sess-fresh")],
        ])

        events = self._run_turn(resume_session_id="sess-stale", prior_messages=prior, fake_client=fake_client)

        self.assertEqual(len(fake_client.calls), 2)
        first_call, second_call = fake_client.calls
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
        fake_client = _fake_claude_sdk_client([
            ([partial_message], RuntimeError("connection dropped mid-turn")),
        ])

        with self.assertRaises(RuntimeError):
            self._run_turn(resume_session_id="sess-stale", prior_messages=prior, fake_client=fake_client)

        self.assertEqual(len(fake_client.calls), 1)  # no retry attempted

    def test_failure_without_resume_attempt_propagates_without_retry(self):
        fake_client = _fake_claude_sdk_client([([], RuntimeError("boom"))])

        with self.assertRaises(RuntimeError):
            self._run_turn(resume_session_id="", prior_messages=[], fake_client=fake_client)

        self.assertEqual(len(fake_client.calls), 1)  # nothing to fall back FROM


class RunClaudeAgentSdkTurnTracePersistenceTests(unittest.TestCase):
    """MAN-310 Phase 2: run_claude_agent_sdk_turn is the async context that
    makes each PERSISTED_TRACE_EVENT_TYPES envelope translate_sdk_message
    builds (ephemeral-only, by construction) durable, via agent_trace_
    service.persist_ephemeral_envelope."""

    def test_persists_trace_events_and_skips_tool_progress_and_final(self):
        # The tool must be one this turn actually REGISTERS, named the way
        # the CLI really presents an SDK MCP tool (mcp__empyralis__*), or
        # translate_sdk_message's foreign-tool guard would (correctly)
        # reject it and this would silently stop exercising the
        # tool.started/tool.result persistence path it exists to cover.
        tool_use = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(
                id="toolu_1", name="mcp__empyralis__web__search", input={"query": "x"},
            )],
            model="claude-sonnet-4-5",
        )
        tool_result = sdk_types.UserMessage(content=[sdk_types.ToolResultBlock(
            tool_use_id="toolu_1", content=[{"type": "text", "text": "found it"}], is_error=False,
        )])
        fake_client = _fake_claude_sdk_client([[tool_use, tool_result, _result_message()]])
        persist_mock = AsyncMock()

        with (
            patch("claude_agent_sdk.ClaudeSDKClient", new=fake_client),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=persist_mock),
        ):
            events = asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message="find something",
                system_prompt="",
                prior_messages=None,
                tool_defs=[{
                    "name": "web__search", "description": "d",
                    "parameters": {"type": "object", "properties": {}},
                }],
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
        persisted_types = [e["payload"]["event_type"] for e in trace_events]
        self.assertIn("tool.started", persisted_types)
        self.assertIn("tool.result", persisted_types)
        self.assertEqual(persist_mock.await_count, len(trace_events))
        persisted_envelopes = [call.args[1] for call in persist_mock.await_args_list]
        self.assertEqual(persisted_envelopes, [e["payload"] for e in trace_events])
        # tool_progress and final events are never routed to persistence.
        non_trace_types = {e["type"] for e in events} - {"trace"}
        self.assertTrue(non_trace_types <= {"tool_progress", "final"})

    def test_no_trace_context_persists_nothing(self):
        fake_client = _fake_claude_sdk_client([[_result_message()]])
        persist_mock = AsyncMock()

        with (
            patch("claude_agent_sdk.ClaudeSDKClient", new=fake_client),
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


class RunClaudeAgentSdkTurnIsolationTests(unittest.TestCase):
    """Each turn must get its own fresh, never-logged-in-to CLAUDE_CONFIG_DIR
    (see resolve_sdk_process_env's docstring for why) and that directory
    must not survive past the turn — a leftover directory per turn is an
    unbounded disk leak on a server handling many turns. Exercises the REAL
    run_claude_agent_sdk_turn with only claude_agent_sdk's own three
    entrypoints (ClaudeAgentOptions/create_sdk_mcp_server/ClaudeSDKClient)
    faked out — everything this module does with them (building options,
    connecting, awaiting receive_response()) runs for real."""

    def test_config_dir_is_fresh_for_the_turn_and_removed_after(self):
        import claude_agent_sdk as real_sdk

        captured: Dict[str, Any] = {}

        class _FakeOptions:
            def __init__(self, **kwargs):
                captured["options_kwargs"] = kwargs

        class _FakeClient:
            def __init__(self, *, options=None, transport=None):
                self.options = options

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def query(self, prompt, session_id="default"):
                # The directory must exist WHILE the (fake) subprocess would
                # be running — the property this test exists to pin.
                env = captured["options_kwargs"]["env"]
                captured["dir_existed_during_call"] = os.path.isdir(env["CLAUDE_CONFIG_DIR"])
                captured["dir_path"] = env["CLAUDE_CONFIG_DIR"]

            async def receive_response(self):
                return
                yield  # pragma: no cover - makes this an async generator function

            async def get_context_usage(self):
                raise AttributeError("not scripted on this fake")

        with (
            patch.object(real_sdk, "ClaudeAgentOptions", _FakeOptions),
            patch.object(real_sdk, "create_sdk_mcp_server", return_value=MagicMock()),
            patch.object(real_sdk, "ClaudeSDKClient", _FakeClient),
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

        class _FakeClient:
            def __init__(self, *, options=None, transport=None):
                self.options = options

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def query(self, prompt, session_id="default"):
                return None

            async def receive_response(self):
                raise RuntimeError("simulated CLI failure")
                yield  # pragma: no cover

        with (
            patch.object(real_sdk, "ClaudeAgentOptions", _FakeOptions),
            patch.object(real_sdk, "create_sdk_mcp_server", return_value=MagicMock()),
            patch.object(real_sdk, "ClaudeSDKClient", _FakeClient),
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


class RunClaudeAgentSdkTurnBuiltInToolLockdownTests(unittest.TestCase):
    """MAN-310 regression pin. ClaudeAgentOptions.tools defaults to the full
    `claude_code` preset, which leaves the CLI's OWN built-ins (TaskCreate,
    TodoWrite, Read, Write, Edit, Bash, WebFetch, Task) callable by the
    model. allowed_tools does NOT restrict them — it governs APPROVAL, not
    AVAILABILITY, and only ever lists this bridge's mcp__empyralis__* tools.

    Live consequence, observed before the fix: asked to create a task, the
    model called the CLI's built-in TaskCreate. It really ran and really
    returned "Task #1 created successfully"; translate_sdk_message faithfully
    turned that into a tool.started/tool.result pair persisted to
    agent_trace_events; the customer was told the task existed; project_tasks
    was empty. tool_honesty_guard structurally cannot catch that — the trace
    corroborated the claim. Work that never happened must never be reportable
    as done, so this asserts the option is set, on EVERY options object the
    turn builds (including the resume-fallback retry's).

    Same faking pattern as RunClaudeAgentSdkTurnIsolationTests: only
    claude_agent_sdk's three entrypoints are stubbed, so the real
    run_claude_agent_sdk_turn builds the real kwargs. No network, no
    subprocess, no paid call."""

    def _capture_options_kwargs(self, *, responses, tool_defs=(), resume_session_id=""):
        import claude_agent_sdk as real_sdk

        captured: list[Dict[str, Any]] = []

        class _FakeOptions:
            def __init__(self, **kwargs):
                captured.append(kwargs)
                for key, value in kwargs.items():
                    setattr(self, key, value)

        fake_client = _fake_claude_sdk_client(responses)
        with (
            patch.object(real_sdk, "ClaudeAgentOptions", _FakeOptions),
            patch.object(real_sdk, "create_sdk_mcp_server", return_value=MagicMock()),
            patch.object(real_sdk, "ClaudeSDKClient", new=fake_client),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=AsyncMock()),
        ):
            asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message="create a task called ship it",
                system_prompt="",
                prior_messages=[{"role": "user", "content": "earlier"}],
                tool_defs=list(tool_defs),
                generation_services=MagicMock(),
                workspace_id="ws-1",
                thread_id="thread-1",
                provider="anthropic",
                model="claude-x",
                credentials={"api_key": "sk-test"},
                trace_context=_trace_context(),
                resume_session_id=resume_session_id,
            ))
        return captured

    def test_built_in_cli_toolset_is_disabled(self):
        captured = self._capture_options_kwargs(responses=[[_result_message()]])

        self.assertEqual(len(captured), 1)
        kwargs = captured[0]
        # Present AND empty. An absent key is the bug: it means the SDK
        # default (the full claude_code preset) applies.
        self.assertIn("tools", kwargs)
        self.assertEqual(kwargs["tools"], [])

    def test_every_options_object_including_the_resume_retry_disables_built_ins(self):
        # The resume-failure fallback builds a SECOND options object. A fix
        # applied to only one construction site would leave the CLI's
        # built-ins live on exactly the retry path.
        captured = self._capture_options_kwargs(
            responses=[([], RuntimeError("no such session")), [_result_message()]],
            resume_session_id="sess-stale",
        )

        self.assertEqual(len(captured), 2)
        for kwargs in captured:
            self.assertIn("tools", kwargs)
            self.assertEqual(kwargs["tools"], [])

    def test_only_empyralis_mcp_tools_are_ever_allowed(self):
        captured = self._capture_options_kwargs(
            responses=[[_result_message()]],
            tool_defs=[
                {"name": "web__search", "description": "d", "parameters": {}},
                {"name": "project_task__create", "description": "d", "parameters": {}},
            ],
        )

        allowed = captured[0]["allowed_tools"]
        self.assertEqual(
            allowed, ["mcp__empyralis__web__search", "mcp__empyralis__project_task__create"],
        )
        # No CLI built-in name may ever appear here either.
        for builtin in ("TaskCreate", "TodoWrite", "Read", "Write", "Edit", "Bash", "WebFetch", "Task"):
            self.assertNotIn(builtin, allowed)

    def test_ambient_mcp_and_filesystem_settings_are_not_loaded(self):
        """Siblings of the tools=[] hole, both reached through the CLI's own
        defaults rather than through anything this module passes.

        strict_mcp_config defaults to False, so the CLI ALSO loads MCP
        servers it finds ambiently (a project .mcp.json beside the backend
        process's cwd, user/global settings, plugin-provided servers). Those
        arrive as ordinary mcp__*__* tool_use blocks that tools=[] does not
        touch — same failure class, different door.

        setting_sources defaults to None, which per the SDK's own docstring
        means "all sources are loaded (matches CLI defaults)": user settings,
        project .claude/settings.json, .claude/settings.local.json and —
        because "project" is among them — CLAUDE.md files, all resolved from
        the inherited backend cwd. That put the HOST repo's instructions,
        permission rules and slash commands inside a tenant's turn."""
        kwargs = self._capture_options_kwargs(responses=[[_result_message()]])[0]

        self.assertIs(kwargs["strict_mcp_config"], True)
        self.assertEqual(kwargs["setting_sources"], [])


class IsRegisteredEmpyralisToolTests(unittest.TestCase):
    """The predicate translate_sdk_message's foreign-tool guard is built on.
    Note the strip_mcp_tool_prefix interplay: that helper only ever strips
    THIS server's prefix, so a foreign MCP tool name survives whole and can
    never collide with a registered bare Empyralis tool name."""

    KNOWN = frozenset({"web__search", "project_task__create"})

    def test_registered_tool_under_our_prefix_is_accepted(self):
        self.assertTrue(claude_agent_sdk_bridge.is_registered_empyralis_tool(
            "mcp__empyralis__project_task__create", self.KNOWN,
        ))

    def test_cli_built_in_is_rejected(self):
        for builtin in ("TaskCreate", "TodoWrite", "Read", "Write", "Edit", "Bash", "WebFetch", "Task"):
            self.assertFalse(
                claude_agent_sdk_bridge.is_registered_empyralis_tool(builtin, self.KNOWN),
                f"{builtin} must not be treated as Empyralis work",
            )

    def test_foreign_mcp_server_tool_is_rejected(self):
        self.assertFalse(claude_agent_sdk_bridge.is_registered_empyralis_tool(
            "mcp__github__create_issue", self.KNOWN,
        ))

    def test_foreign_server_cannot_impersonate_a_registered_name(self):
        # strip_mcp_tool_prefix leaves a non-empyralis prefix intact, so this
        # never reduces to the registered "web__search".
        self.assertFalse(claude_agent_sdk_bridge.is_registered_empyralis_tool(
            "mcp__evil__web__search", self.KNOWN,
        ))

    def test_unprefixed_name_is_rejected_even_when_it_matches_a_registered_name(self):
        # The CLI always presents SDK MCP tools prefixed (allowed_tools is
        # built on that same convention), so a bare name that happens to
        # match a registered tool came from somewhere else.
        self.assertFalse(claude_agent_sdk_bridge.is_registered_empyralis_tool(
            "web__search", self.KNOWN,
        ))

    def test_unregistered_name_under_our_prefix_is_rejected(self):
        self.assertFalse(claude_agent_sdk_bridge.is_registered_empyralis_tool(
            "mcp__empyralis__not_registered_this_turn", self.KNOWN,
        ))

    def test_empty_name_is_rejected_even_when_unconfigured(self):
        self.assertFalse(claude_agent_sdk_bridge.is_registered_empyralis_tool("", None))
        self.assertFalse(claude_agent_sdk_bridge.is_registered_empyralis_tool("   ", None))

    def test_unconfigured_allowlist_has_no_opinion(self):
        self.assertTrue(claude_agent_sdk_bridge.is_registered_empyralis_tool("web__search", None))

    def test_empty_allowlist_rejects_everything(self):
        # A turn that registered no tools at all: nothing is Empyralis work.
        self.assertFalse(claude_agent_sdk_bridge.is_registered_empyralis_tool(
            "mcp__empyralis__web__search", frozenset(),
        ))


class TranslateForeignToolTests(unittest.TestCase):
    """Defence in depth behind tools=[]. Even if a non-Empyralis tool becomes
    reachable again — an options regression, an ambient MCP server, a CLI
    update — its call must never be translated into the tool.started/
    tool.result pair that IS the product's record of work done."""

    KNOWN = frozenset({"web__search"})

    def _state(self):
        return claude_agent_sdk_bridge.TranslationState(known_tool_names=self.KNOWN)

    def test_cli_built_in_emits_an_anomaly_not_a_tool_started(self):
        state = self._state()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(
                id="toolu_1", name="TaskCreate", input={"title": "ship it"},
            )],
            model="claude-sonnet-4-5",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        trace_types = [e["payload"]["event_type"] for e in events if e["type"] == "trace"]
        self.assertEqual(trace_types, ["trace.failed"])
        self.assertNotIn("tool.started", trace_types)
        # No tool_progress either: nothing in the product is in progress.
        self.assertEqual([e["type"] for e in events], ["trace"])
        payload = events[0]["payload"]
        self.assertEqual(payload["data"]["code"], "foreign_tool_call")
        self.assertIn("TaskCreate", payload["data"]["message"])
        self.assertIn("toolu_1", state.foreign_tool_use_ids)
        # And it is NOT remembered as a real call, so no later result can
        # be correlated back onto it.
        self.assertNotIn("toolu_1", state.tool_use_names)

    def test_result_of_a_foreign_tool_emits_nothing(self):
        state = self._state()
        claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.AssistantMessage(
                content=[sdk_types.ToolUseBlock(id="toolu_1", name="TaskCreate", input={})],
                model="claude-sonnet-4-5",
            ),
            state=state, trace_context=_trace_context(),
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.UserMessage(content=[sdk_types.ToolResultBlock(
                tool_use_id="toolu_1",
                content=[{"type": "text", "text": "Task #1 created successfully"}],
                is_error=False,
            )]),
            state=state, trace_context=_trace_context(),
        )
        # The exact string that got a customer told a task existed. It must
        # produce no tool.result and no completed plan item.
        self.assertEqual(events, [])

    def test_registered_tool_is_unaffected(self):
        state = self._state()
        message = sdk_types.AssistantMessage(
            content=[sdk_types.ToolUseBlock(
                id="toolu_2", name="mcp__empyralis__web__search", input={"query": "x"},
            )],
            model="claude-sonnet-4-5",
        )
        events = claude_agent_sdk_bridge.translate_sdk_message(
            message, state=state, trace_context=_trace_context(),
        )
        trace_types = [e["payload"]["event_type"] for e in events if e["type"] == "trace"]
        self.assertIn("tool.started", trace_types)
        self.assertIn("tool_progress", [e["type"] for e in events])
        self.assertEqual(state.tool_use_names["toolu_2"], "web__search")
        self.assertEqual(state.foreign_tool_use_ids, set())

    def test_orphan_tool_result_is_not_recorded_as_a_completed_call(self):
        # No ToolUseBlock ever announced toolu_ghost. Without this guard the
        # collector invents an entry named "direct_tool" and marks it
        # completed — a green row in the work ledger for a call nothing in
        # this turn can name.
        state = self._state()
        events = claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.UserMessage(content=[sdk_types.ToolResultBlock(
                tool_use_id="toolu_ghost", content=[{"type": "text", "text": "done"}], is_error=False,
            )]),
            state=state, trace_context=_trace_context(),
        )
        trace_types = [e["payload"]["event_type"] for e in events if e["type"] == "trace"]
        self.assertEqual(trace_types, ["trace.failed"])
        self.assertEqual(events[0]["payload"]["data"]["code"], "orphan_tool_result")

    def test_anomalies_survive_a_missing_trace_context(self):
        # Same None-in/None-out contract the rest of this module honours:
        # degrade to emitting nothing, never raise.
        state = claude_agent_sdk_bridge.TranslationState(known_tool_names=self.KNOWN)
        events = claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.AssistantMessage(
                content=[sdk_types.ToolUseBlock(id="toolu_1", name="Bash", input={"command": "ls"})],
                model="claude-sonnet-4-5",
            ),
            state=state, trace_context=None,
        )
        self.assertEqual(events, [])
        self.assertIn("toolu_1", state.foreign_tool_use_ids)


class ForeignToolThroughRealCollectorAndHonestyGuardTests(unittest.TestCase):
    """The end-to-end proof, through the two REAL, unmodified consumers:
    sage_agent_runtime_service._collect_sage_operator_loop_v3_events (which
    builds the customer-visible work ledger) and tool_honesty_guard (which
    checks a reply's claims against that ledger)."""

    def _run_turn_events(self, *, tool_name, known_tool_names, reply):
        state = claude_agent_sdk_bridge.TranslationState(known_tool_names=known_tool_names)
        trace_context = _trace_context()
        events = []
        events += claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.AssistantMessage(
                content=[sdk_types.ToolUseBlock(id="toolu_1", name=tool_name, input={"query": "x"})],
                model="claude-sonnet-4-5",
            ),
            state=state, trace_context=trace_context,
        )
        events += claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.UserMessage(content=[sdk_types.ToolResultBlock(
                tool_use_id="toolu_1", content=[{"type": "text", "text": "3 results found."}], is_error=False,
            )]),
            state=state, trace_context=trace_context,
        )
        events += claude_agent_sdk_bridge.translate_sdk_message(
            sdk_types.ResultMessage(
                subtype="success", duration_ms=10, duration_api_ms=8, is_error=False,
                num_turns=2, session_id="sess-1", result=reply,
            ),
            state=state, trace_context=trace_context,
        )
        return sage_agent_runtime_service._collect_sage_operator_loop_v3_events(events)

    def test_foreign_tool_never_enters_the_work_ledger(self):
        collected = self._run_turn_events(
            tool_name="WebFetch",
            known_tool_names=frozenset({"web__search"}),
            reply="Here's what I found.",
        )
        # Nothing completed. The whole point: the Work tab and every
        # downstream consumer of tool_calls see no work, because none was
        # done in this product.
        self.assertEqual(collected["tool_calls"], [])
        self.assertEqual(collected["action_execution_mode"], "tool_blocked")
        self.assertEqual(len(collected["blocked_tools"]), 1)
        self.assertEqual(collected["blocked_tools"][0]["name"], "foreign_tool_call")
        self.assertEqual(collected["blocked_tools"][0]["status"], "blocked")

    def test_removing_the_foreign_trace_restores_tool_honesty_guard(self):
        """Why this class of bug is worse than a normal one, and what the
        guard buys back. tool_honesty_guard compares a reply's claims
        against the turn's tool_calls. A foreign tool that really ran and
        really succeeded puts a genuine-looking SUCCESS in that list, and
        check_tool_reply_consistency's `if successful:` branch then treats
        the claim as corroborated — the guard is blinded by the very
        evidence that should have condemned the reply. Keeping the foreign
        call OUT of tool_calls hands the guard back its ability to see."""
        from server_modules import tool_honesty_guard

        reply = "Here's what I found."

        # What the collector produced before this guard existed: the foreign
        # call translated into a completed tool_calls entry.
        masked = tool_honesty_guard.check_tool_reply_consistency(
            reply, [{"name": "WebFetch", "status": "completed", "output": "3 results found."}],
        )
        self.assertTrue(masked["consistent"], "sanity: a fake success masks the guard")

        collected = self._run_turn_events(
            tool_name="WebFetch",
            known_tool_names=frozenset({"web__search"}),
            reply=reply,
        )
        unmasked = tool_honesty_guard.check_tool_reply_consistency(
            reply, list(collected["tool_calls"]),
        )
        self.assertFalse(unmasked["consistent"])
        self.assertEqual(unmasked["mismatch_type"], "claims_without_run")

    def test_a_registered_tool_still_produces_a_completed_entry(self):
        collected = self._run_turn_events(
            tool_name="mcp__empyralis__web__search",
            known_tool_names=frozenset({"web__search"}),
            reply="Here's what I found.",
        )
        self.assertEqual(len(collected["tool_calls"]), 1)
        self.assertEqual(collected["tool_calls"][0]["name"], "web__search")
        self.assertEqual(collected["tool_calls"][0]["status"], "completed")
        self.assertEqual(collected["blocked_tools"], [])
        self.assertEqual(collected["action_execution_mode"], "tools_executed")


class RunClaudeAgentSdkTurnForeignToolTests(unittest.TestCase):
    """The wiring: translate_sdk_message's guard is only armed if the turn
    tells it which tools it registered. Pins that run_claude_agent_sdk_turn
    always does, on both the normal and the resume-fallback path."""

    def _run(self, *, responses, tool_defs, resume_session_id=""):
        fake_client = _fake_claude_sdk_client(responses)
        with (
            patch("claude_agent_sdk.ClaudeSDKClient", new=fake_client),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=AsyncMock()),
        ):
            return asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message="create a task called ship it",
                system_prompt="",
                prior_messages=[{"role": "user", "content": "earlier"}],
                tool_defs=tool_defs,
                generation_services=MagicMock(),
                workspace_id="ws-1",
                thread_id="trace-1",
                provider="anthropic",
                model="claude-sonnet-4-5",
                credentials={},
                trace_context=_trace_context(),
                resume_session_id=resume_session_id,
            ))

    @staticmethod
    def _task_create_turn():
        return [
            sdk_types.AssistantMessage(
                content=[sdk_types.ToolUseBlock(
                    id="toolu_1", name="TaskCreate", input={"title": "ship it"},
                )],
                model="claude-sonnet-4-5",
            ),
            sdk_types.UserMessage(content=[sdk_types.ToolResultBlock(
                tool_use_id="toolu_1",
                content=[{"type": "text", "text": "Task #1 created successfully"}],
                is_error=False,
            )]),
            _result_message(reply="Task created. The task id is 1"),
        ]

    def test_the_live_incident_turn_records_no_tool_work(self):
        # Replays the exact message sequence observed in production before
        # the fix, through the real run_claude_agent_sdk_turn.
        events = self._run(
            responses=[self._task_create_turn()],
            tool_defs=[{
                "name": "project_task__create", "description": "d",
                "parameters": {"type": "object", "properties": {}},
            }],
        )
        trace_types = [e["payload"]["event_type"] for e in events if e["type"] == "trace"]
        self.assertEqual(trace_types, ["trace.failed"])
        self.assertNotIn("tool.started", trace_types)
        self.assertNotIn("tool.result", trace_types)
        self.assertNotIn("tool_progress", [e["type"] for e in events])

    def test_the_resume_fallback_path_is_armed_too(self):
        events = self._run(
            responses=[([], RuntimeError("no such session")), self._task_create_turn()],
            tool_defs=[{
                "name": "project_task__create", "description": "d",
                "parameters": {"type": "object", "properties": {}},
            }],
            resume_session_id="sess-stale",
        )
        trace_types = [e["payload"]["event_type"] for e in events if e["type"] == "trace"]
        self.assertEqual(trace_types, ["trace.failed"])
        self.assertNotIn("tool.started", trace_types)

    def test_registered_tool_still_flows_through_the_real_turn(self):
        events = self._run(
            responses=[[
                sdk_types.AssistantMessage(
                    content=[sdk_types.ToolUseBlock(
                        id="toolu_1", name="mcp__empyralis__project_task__create",
                        input={"title": "ship it"},
                    )],
                    model="claude-sonnet-4-5",
                ),
                sdk_types.UserMessage(content=[sdk_types.ToolResultBlock(
                    tool_use_id="toolu_1",
                    content=[{"type": "text", "text": "Created task MAN-1."}],
                    is_error=False,
                )]),
                _result_message(reply="Created it."),
            ]],
            tool_defs=[{
                "name": "project_task__create", "description": "d",
                "parameters": {"type": "object", "properties": {}},
            }],
        )
        trace_types = [e["payload"]["event_type"] for e in events if e["type"] == "trace"]
        self.assertIn("tool.started", trace_types)
        self.assertIn("tool.result", trace_types)
        self.assertNotIn("trace.failed", trace_types)


class RunClaudeAgentSdkTurnContextUsageTests(unittest.TestCase):
    """The whole reason this module swapped from claude_agent_sdk.query() to
    ClaudeSDKClient: get_context_usage() is only reachable on the stateful
    client. Success-path-only, best-effort — see _run_via_client's own
    docstring in claude_agent_sdk_bridge.py for the full reasoning; these
    tests pin the observable behavior."""

    _USAGE = {
        "categories": [{"name": "System prompt", "tokens": 1200, "color": "#888"}],
        "totalTokens": 15000,
        "maxTokens": 200000,
        "rawMaxTokens": 200000,
        "percentage": 7.5,
        "model": "claude-sonnet-4-5",
        "isAutoCompactEnabled": True,
        "memoryFiles": [],
        "mcpTools": [],
        "agents": [],
        "gridRows": [],
    }

    def _run_turn(self, *, fake_client, resume_session_id=""):
        with (
            patch("claude_agent_sdk.ClaudeSDKClient", new=fake_client),
            patch.object(claude_agent_sdk_bridge.agent_trace_service, "persist_ephemeral_envelope", new=AsyncMock()),
        ):
            return asyncio.run(claude_agent_sdk_bridge.run_claude_agent_sdk_turn(
                message="hello",
                system_prompt="Be terse.",
                prior_messages=None,
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

    def test_successful_turn_attaches_context_usage_to_final_payload(self):
        fake_client = _fake_claude_sdk_client([[_result_message()]], context_usage=self._USAGE)
        events = self._run_turn(fake_client=fake_client)
        final_event = next(e for e in events if e["type"] == "final")
        self.assertEqual(final_event["payload"]["context_usage"], self._USAGE)

    def test_get_context_usage_failure_is_best_effort_and_omitted(self):
        fake_client = _fake_claude_sdk_client(
            [[_result_message()]],
            context_usage_error=RuntimeError("control-protocol round trip failed"),
        )
        events = self._run_turn(fake_client=fake_client)
        final_event = next(e for e in events if e["type"] == "final")
        self.assertNotIn("context_usage", final_event["payload"])
        # The rest of the turn is unaffected — a failed best-effort extra
        # must never take the real reply down with it.
        self.assertEqual(final_event["payload"]["reply"], "ok")

    def test_missing_get_context_usage_method_is_best_effort_and_omitted(self):
        # The fake's default (neither context_usage nor context_usage_error
        # passed) raises AttributeError — standing in for an older SDK/CLI
        # build that has no such method at all.
        fake_client = _fake_claude_sdk_client([[_result_message()]])
        events = self._run_turn(fake_client=fake_client)
        final_event = next(e for e in events if e["type"] == "final")
        self.assertNotIn("context_usage", final_event["payload"])

    def test_non_dict_context_usage_is_omitted_not_fabricated(self):
        fake_client = _fake_claude_sdk_client([[_result_message()]], context_usage="not-a-dict")
        events = self._run_turn(fake_client=fake_client)
        final_event = next(e for e in events if e["type"] == "final")
        self.assertNotIn("context_usage", final_event["payload"])

    def test_errored_turn_still_gets_context_usage_since_the_client_completed(self):
        # A provider-level error (ResultMessage.is_error=True) still lets the
        # SDK message loop finish normally — the client/subprocess is still
        # alive and answering control-protocol requests, so this still
        # counts as "the turn's message loop completed" (as opposed to a
        # raised exception, which is what actually withholds the call — see
        # the next test). Context usage is still real, useful information
        # about a turn that ran, even though its own answer failed.
        error_result = sdk_types.ResultMessage(
            subtype="error_max_turns", duration_ms=10, duration_api_ms=8, is_error=True,
            num_turns=5, session_id="sess-1", result=None,
        )
        fake_client = _fake_claude_sdk_client([[error_result]], context_usage=self._USAGE)
        events = self._run_turn(fake_client=fake_client)
        final_event = next(e for e in events if e["type"] == "final")
        self.assertEqual(final_event["payload"]["context_usage"], self._USAGE)

    def test_raised_exception_never_attempts_context_usage(self):
        # No resume attempted -> the exception propagates without a fallback
        # retry, and this fake never scripts get_context_usage. If
        # run_claude_agent_sdk_turn tried to call it anyway on this path, it
        # would surface as the fake's AttributeError instead of the
        # RuntimeError asserted here.
        fake_client = _fake_claude_sdk_client([([], RuntimeError("boom"))])
        with self.assertRaises(RuntimeError):
            self._run_turn(fake_client=fake_client)

    def test_resume_fallback_success_still_attaches_context_usage(self):
        fake_client = _fake_claude_sdk_client(
            [
                ([], RuntimeError("no such session")),
                [_result_message(session_id="sess-fresh")],
            ],
            context_usage=self._USAGE,
        )
        events = self._run_turn(fake_client=fake_client, resume_session_id="sess-stale")
        final_event = next(e for e in events if e["type"] == "final")
        self.assertEqual(final_event["payload"]["context_usage"], self._USAGE)


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

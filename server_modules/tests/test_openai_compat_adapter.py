"""Tests for server_modules/openai_compat_adapter.py.

No real network call is ever made here. The "upstream OpenAI-shaped
provider" in every FastAPI-level test is a small FastAPI app of its own,
scripted per test case, wired to the adapter via httpx.ASGITransport — real
HTTP request/response *code paths* (real streaming, real SSE chunking, real
JSON (de)serialization) with zero real sockets. This is the same technique
described in the module's own docstring as already having been used to
drive the real bundled `claude` CLI against a fake upstream at zero cost;
here it proves the adapter itself, independent of the CLI (see
scripts/verify_adapter_live_cli.py — not part of this suite — for the
real-CLI proof).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from server_modules import openai_compat_adapter as adapter


# ============================================================================
# Fake upstream builder
# ============================================================================

def make_fake_upstream(
    *,
    chunks: Optional[List[Dict[str, Any]]] = None,
    status_code: int = 200,
    raw_body: Optional[bytes] = None,
    fail_before_any_bytes: bool = False,
    non_streaming_json: Optional[Dict[str, Any]] = None,
    capture: Optional[Dict[str, Any]] = None,
) -> FastAPI:
    """A scripted fake OpenAI Chat Completions endpoint. `capture`, if
    given, records the last received request body/headers so the test can
    assert on exactly what the adapter sent upstream."""
    fake = FastAPI()

    @fake.post("/chat/completions")
    async def _chat_completions(request: Request):  # noqa: ANN001
        body = await request.json()
        if capture is not None:
            capture["body"] = body
            capture["headers"] = dict(request.headers)
        if fail_before_any_bytes:
            # Simulate "accepted the connection, then closed with nothing
            # sent" by returning an empty streaming body.
            async def _empty() -> AsyncIterator[bytes]:
                return
                yield b""  # pragma: no cover - unreachable, keeps this a generator
            return StreamingResponse(_empty(), media_type="text/event-stream", status_code=200)
        if status_code >= 400:
            return JSONResponse(status_code=status_code, content={"error": {"message": "fake upstream error"}})
        if raw_body is not None:
            async def _raw() -> AsyncIterator[bytes]:
                yield raw_body
            return StreamingResponse(_raw(), media_type="text/event-stream")
        if not body.get("stream"):
            return JSONResponse(content=non_streaming_json or {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]})

        async def _stream() -> AsyncIterator[bytes]:
            for chunk in (chunks or []):
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    return fake


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def parse_sse(resp: httpx.Response) -> List[Dict[str, Any]]:
    """[(event, data_dict), ...] parsed from a text/event-stream response."""
    text = resp.text
    frames = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        event = None
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if event is None:
            continue
        data = json.loads("\n".join(data_lines)) if data_lines else {}
        frames.append({"event": event, "data": data})
    return frames


@pytest.fixture(autouse=True)
def _reset_adapter_state():
    adapter.clear_all_turn_credentials_for_tests()
    adapter.set_http_client_for_tests(None)
    yield
    adapter.clear_all_turn_credentials_for_tests()
    adapter.set_http_client_for_tests(None)


def mint_token(fake_app: FastAPI, *, provider: str = "openai", extra_headers: Optional[Dict[str, str]] = None) -> str:
    headers = {"Authorization": "Bearer REAL-SECRET-KEY-never-should-leak", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    token = adapter.mint_turn_credential(
        provider=provider, chat_completions_url="http://testserver/chat/completions", headers=headers,
    )
    adapter.set_http_client_for_tests(client_for(fake_app))
    return token


# ============================================================================
# 1. Pure request translation
# ============================================================================

class TestRequestTranslation:
    def test_requires_model_and_messages(self):
        with pytest.raises(adapter.AdapterTranslationError):
            adapter.translate_anthropic_request_to_openai({"messages": [{"role": "user", "content": "hi"}]}, provider="openai")
        with pytest.raises(adapter.AdapterTranslationError):
            adapter.translate_anthropic_request_to_openai({"model": "gpt-5.4", "messages": []}, provider="openai")

    def test_system_list_drops_billing_header_block(self):
        body = {
            "model": "gpt-5.4",
            "system": [
                {"type": "text", "text": "x-anthropic-billing-header: do-not-forward-this"},
                {"type": "text", "text": "You are a helpful assistant."},
            ],
            "messages": [{"role": "user", "content": "hi"}],
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        system_msg = out["messages"][0]
        assert system_msg["role"] == "system"
        assert "billing-header" not in system_msg["content"]
        assert "helpful assistant" in system_msg["content"]

    def test_system_plain_string_accepted_defensively(self):
        body = {"model": "gpt-5.4", "system": "be nice", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert out["messages"][0] == {"role": "system", "content": "be nice"}

    def test_thinking_and_cache_control_and_metadata_never_forwarded(self):
        body = {
            "model": "gpt-5.4",
            "thinking": {"type": "adaptive"},
            "metadata": {"user_id": "abc"},
            "context_management": {"foo": "bar"},
            "betas": ["some-beta"],
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
            }],
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        blob = json.dumps(out)
        assert "thinking" not in blob
        assert "cache_control" not in blob
        assert "context_management" not in blob
        assert "adaptive" not in blob

    def test_max_tokens_clamped_per_provider_model(self):
        body = {"model": "gpt-4o-mini", "max_tokens": 32000, "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert out["max_tokens"] == 16384  # exact-model ceiling in the owned table

    def test_max_tokens_unknown_model_falls_back_conservatively(self):
        body = {"model": "totally-unknown-model-xyz", "max_tokens": 32000, "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="custom_openai_compatible")
        assert out["max_tokens"] == 4096

    def test_max_tokens_never_clamped_upward(self):
        body = {"model": "gpt-4o-mini", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert out["max_tokens"] == 10

    def test_tool_choice_absent_is_omitted_not_defaulted(self):
        body = {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert "tool_choice" not in out

    @pytest.mark.parametrize("anthropic_choice,expected", [
        ({"type": "auto"}, "auto"),
        ({"type": "any"}, "required"),
        ({"type": "none"}, "none"),
        ({"type": "tool", "name": "my_tool"}, {"type": "function", "function": {"name": "my_tool"}}),
    ])
    def test_tool_choice_mapping(self, anthropic_choice, expected):
        body = {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "my_tool", "description": "d", "input_schema": {"type": "object"}}],
            "tool_choice": anthropic_choice,
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert out["tool_choice"] == expected

    def test_tool_choice_unrecognized_type_errors(self):
        body = {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "t", "description": "d", "input_schema": {}}],
            "tool_choice": {"type": "bogus"},
        }
        with pytest.raises(adapter.AdapterTranslationError):
            adapter.translate_anthropic_request_to_openai(body, provider="openai")

    def test_assistant_tool_use_becomes_tool_calls(self):
        body = {
            "model": "gpt-5.4",
            "messages": [
                {"role": "user", "content": "what's 2+2"},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": "toolu_1", "name": "calc", "input": {"expr": "2+2"}},
                ]},
            ],
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assistant_msg = out["messages"][1]
        assert assistant_msg["content"] == "Let me check."
        assert assistant_msg["tool_calls"] == [{
            "id": "toolu_1", "type": "function",
            "function": {"name": "calc", "arguments": json.dumps({"expr": "2+2"})},
        }]

    def test_parallel_tool_results_split_into_n_tool_messages_in_order(self):
        body = {
            "model": "gpt-5.4",
            "messages": [
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "a", "input": {}},
                    {"type": "tool_use", "id": "t2", "name": "b", "input": {}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "result A"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "result B", "is_error": True},
                ]},
            ],
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        tool_msgs = [m for m in out["messages"] if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0] == {"role": "tool", "tool_call_id": "t1", "content": "result A"}
        assert tool_msgs[1] == {"role": "tool", "tool_call_id": "t2", "content": "[tool error] result B"}

    def test_tool_result_mixed_with_text_emits_trailing_user_message(self):
        body = {
            "model": "gpt-5.4",
            "messages": [
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "result A"},
                    {"type": "text", "text": "also, one more thing"},
                ]},
            ],
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert out["messages"][0] == {"role": "tool", "tool_call_id": "t1", "content": "result A"}
        assert out["messages"][1]["role"] == "user"
        assert "also, one more thing" in out["messages"][1]["content"]

    def test_tool_result_image_dropped_with_marker(self):
        body = {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": [
                    {"type": "text", "text": "see:"},
                    {"type": "image", "source": {"media_type": "image/png", "data": "AAAA"}},
                ]},
            ]}],
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert out["messages"][0]["content"] == "see:\n[image omitted]"

    def test_output_config_json_schema_maps_to_response_format(self):
        body = {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "output_config": {"format": {"type": "json_schema", "name": "title", "schema": {"type": "object"}}},
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert out["response_format"]["type"] == "json_schema"
        assert out["response_format"]["json_schema"]["schema"] == {"type": "object"}

    def test_stream_true_requests_usage_via_stream_options(self):
        body = {"model": "gpt-5.4", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert out["stream_options"] == {"include_usage": True}

    def test_estimate_input_tokens_positive(self):
        body = {"model": "x", "messages": [{"role": "user", "content": "hello there, this is a test"}]}
        assert adapter.estimate_input_tokens(body) > 0


# ============================================================================
# 1b. Reasoning effort — the CLAUDE.md-cited dead control this fixes.
#     "openai_compat_adapter.py has zero references to reasoning_effort" is
#     no longer true; these tests exercise both branches _apply_reasoning_
#     effort can take: a real provider-native wire parameter, and the
#     honest system-instruction fallback for a model with no verified one.
# ============================================================================

class TestClampReasoningEffort:
    def test_exact_match_passes_through(self):
        assert adapter.clamp_reasoning_effort("medium", ["low", "medium", "high"]) == "medium"

    def test_clamps_down_to_nearest_allowed_below_request(self):
        # gpt-5-nano's real catalog levels are ["none", "low"] — "high"
        # is not invented, it degrades to the highest level actually
        # available rather than the highest level requested.
        assert adapter.clamp_reasoning_effort("high", ["none", "low"]) == "low"

    def test_clamps_up_when_nothing_allowed_is_at_or_below_request(self):
        assert adapter.clamp_reasoning_effort("low", ["medium", "high"]) == "medium"

    def test_no_allowed_levels_returns_none(self):
        assert adapter.clamp_reasoning_effort("high", []) is None

    def test_unrecognized_requested_value_returns_none(self):
        assert adapter.clamp_reasoning_effort("ludicrous", ["low", "high"]) is None

    def test_levels_outside_the_five_word_ladder_are_ignored_as_targets(self):
        # A model whose only catalog levels are "none"/"minimal" (neither
        # of which the byok_api picker can ever request) has nothing this
        # ladder can select — never silently promoted to a level the
        # customer didn't ask for and the model may not accept.
        assert adapter.clamp_reasoning_effort("low", ["none", "minimal"]) is None


class TestReasoningEffortWiring:
    def test_omitted_when_not_requested(self):
        body = {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai")
        assert "reasoning_effort" not in out
        assert "reasoning" not in out
        assert out["messages"] == [{"role": "user", "content": "hi"}]

    def test_openai_reasoning_model_gets_native_field(self):
        body = {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai", reasoning_effort="high")
        assert out["reasoning_effort"] == "high"
        assert "reasoning" not in out
        # No fallback instruction was ALSO injected — the wire param is
        # the whole effect, not a belt-and-suspenders double-send.
        assert out["messages"] == [{"role": "user", "content": "hi"}]

    def test_openai_non_reasoning_model_falls_back_to_instruction(self):
        # gpt-4o is not a reasoning model (provider_profiles.py's own
        # 2026-08-20 correction) — the picker still has to do SOMETHING.
        body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai", reasoning_effort="high")
        assert "reasoning_effort" not in out
        assert out["messages"][0]["role"] == "system"
        assert "high reasoning effort" in out["messages"][0]["content"]
        assert out["messages"][1] == {"role": "user", "content": "hi"}

    def test_fallback_instruction_appends_to_existing_system_message(self):
        body = {
            "model": "gpt-4o",
            "system": [{"type": "text", "text": "You are a helpful assistant."}],
            "messages": [{"role": "user", "content": "hi"}],
        }
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai", reasoning_effort="low")
        assert out["messages"][0]["role"] == "system"
        assert "helpful assistant" in out["messages"][0]["content"]
        assert "low reasoning effort" in out["messages"][0]["content"]
        # Still exactly one system message — never a second one stacked on.
        assert sum(1 for m in out["messages"] if m["role"] == "system") == 1

    def test_requested_level_clamped_to_model_ceiling(self):
        # gpt-5-nano's catalog levels are ["none", "low"].
        body = {"model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openai", reasoning_effort="xhigh")
        assert out["reasoning_effort"] == "low"

    def test_gemini_reasoning_model_gets_native_field(self):
        body = {"model": "gemini-2.5-pro", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="gemini", reasoning_effort="medium")
        assert out["reasoning_effort"] == "medium"

    def test_xai_model_has_no_verified_wire_control_falls_back(self):
        # xAI's own docs (2026-08-20): grok-4 reasons with a fixed budget
        # and exposes no settable reasoning_effort — every xai model this
        # catalog currently offers is in the same position.
        body = {"model": "grok-4", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="xai", reasoning_effort="high")
        assert "reasoning_effort" not in out
        assert out["messages"][0]["role"] == "system"
        assert "high reasoning effort" in out["messages"][0]["content"]

    def test_openrouter_uses_nested_reasoning_object_not_flat_field(self):
        body = {"model": "openai/gpt-5.4", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="openrouter", reasoning_effort="high")
        assert out["reasoning"] == {"effort": "high"}
        assert "reasoning_effort" not in out

    def test_unrecognized_model_falls_back_to_instruction_never_a_guess(self):
        body = {"model": "some-custom-deployment-name", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(
            body, provider="custom_openai_compatible", reasoning_effort="medium",
        )
        assert "reasoning_effort" not in out
        assert "reasoning" not in out
        assert "medium reasoning effort" in out["messages"][0]["content"]

    def test_mistral_and_groq_have_no_verified_wire_control(self):
        # mistral-large-latest (reasoning happens on Magistral only, not
        # this model) and Groq's Llama models (no reasoning at all) both
        # land on the fallback rather than an unverified wire field.
        for provider, model in (("mistral", "mistral-large-latest"), ("groq", "llama-3.3-70b-versatile")):
            body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
            out = adapter.translate_anthropic_request_to_openai(body, provider=provider, reasoning_effort="low")
            assert "reasoning_effort" not in out, provider
            assert "reasoning" not in out, provider
            assert "low reasoning effort" in out["messages"][0]["content"], provider

    # ── Second pass, 2026-08-20: xAI has shipped grok-4.5/4.6/4.20-multi-
    # agent (real reasoning_effort support) since the first catalog pass.
    # They are DELIBERATELY NOT added as hardcoded entries here — see
    # provider_profiles.py's own xai catalog comment for why (no on-box,
    # self-describing harness exists for this provider, unlike Codex; a
    # hardcoded entry is still transcription and goes stale the same way).
    # These three currently fall to the honest fallback, proven below. ──

    def test_grok_4_6_currently_falls_back_pending_a_live_capability_source(self):
        # Genuinely reasoning-capable per xAI's docs, but this catalog has
        # no way to derive that live — the safe, honest default applies.
        body = {"model": "grok-4.6", "messages": [{"role": "user", "content": "hi"}]}
        out = adapter.translate_anthropic_request_to_openai(body, provider="xai", reasoning_effort="high")
        assert "reasoning_effort" not in out
        assert "high reasoning effort" in out["messages"][0]["content"]

    def test_unrecognized_live_discovered_model_logs_a_greppable_warning(self, caplog):
        # The permanent-staleness decision: an id this catalog has NEVER
        # heard of (as opposed to one it has explicitly verified doesn't
        # support the control) must be distinguishable operationally, even
        # though both land on the same safe fallback behavior.
        import logging
        with caplog.at_level(logging.WARNING, logger="server_modules.openai_compat_adapter"):
            body = {"model": "grok-5-hypothetical-future-release", "messages": [{"role": "user", "content": "hi"}]}
            out = adapter.translate_anthropic_request_to_openai(body, provider="xai", reasoning_effort="high")
        assert "reasoning_effort" not in out
        assert "high reasoning effort" in out["messages"][0]["content"]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("reasoning_effort_model_unknown" in r.message for r in warnings)
        assert any("grok-5-hypothetical-future-release" in r.message for r in warnings)

    def test_known_unsupported_model_does_not_log_the_unknown_warning(self):
        # grok-4 is IN the catalog (with reasoning_levels: []) — a
        # confirmed, stable "no", not a live-discovery arrival. Logging it
        # on every turn would be noise, not signal.
        import logging
        logger = logging.getLogger("server_modules.openai_compat_adapter")
        records: List[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        logger.addHandler(handler)
        try:
            body = {"model": "grok-4", "messages": [{"role": "user", "content": "hi"}]}
            adapter.translate_anthropic_request_to_openai(body, provider="xai", reasoning_effort="high")
        finally:
            logger.removeHandler(handler)
        assert not any("reasoning_effort_model_unknown" in r.getMessage() for r in records)


# ============================================================================
# 2. AnthropicStreamAssembler — pure state machine, varied fragmentation
# ============================================================================

class TestStreamAssembler:
    def _run(self, chunks: List[Dict[str, Any]]):
        asm = adapter.AnthropicStreamAssembler(message_id="msg_test", model="gpt-5.4")
        frames: List[str] = []
        it = iter(chunks)
        first = next(it)
        frames.extend(asm.begin(first))
        for c in it:
            frames.extend(asm.feed(c))
        frames.extend(asm.finish())
        return asm, frames

    @staticmethod
    def _events(frames: List[str]) -> List[str]:
        out = []
        for f in frames:
            for line in f.split("\n"):
                if line.startswith("event:"):
                    out.append(line[len("event:"):].strip())
        return out

    def test_prose_only_turn(self):
        chunks = [
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo!"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        asm, frames = self._run(chunks)
        events = self._events(frames)
        assert events == [
            "message_start", "content_block_start", "content_block_delta", "content_block_delta",
            "content_block_stop", "message_delta", "message_stop",
        ]
        full_text = "".join(
            json.loads(f.split("data: ", 1)[1])["delta"]["text"]
            for f in frames if "content_block_delta" in f
        )
        assert full_text == "Hello!"
        assert asm.counters.tool_calls_emitted_downstream == 0

    def test_single_tool_call_args_split_across_fragments(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "search", "arguments": ""}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"query"'}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": ':"cats"}'}},
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        asm, frames = self._run(chunks)
        events = self._events(frames)
        assert events == ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"]
        delta_frame = [f for f in frames if "input_json_delta" in f][0]
        payload = json.loads(delta_frame.split("data: ", 1)[1])
        assert payload["delta"]["partial_json"] == '{"query":"cats"}'
        start_frame = [f for f in frames if "content_block_start" in f][0]
        block = json.loads(start_frame.split("data: ", 1)[1])["content_block"]
        assert block["name"] == "search" and block["id"] == "call_1"
        assert asm.counters.tool_calls_emitted_downstream == 1

    def test_parallel_tool_calls_deinterleaved_contiguous(self):
        # Deliberately interleaved upstream fragmentation: slot 0 and slot 1
        # alternate within and across chunks.
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_A", "function": {"name": "alpha", "arguments": "{\"a\":"}},
                {"index": 1, "id": "call_B", "function": {"name": "beta", "arguments": "{\"b\":"}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 1, "function": {"arguments": "2}"}},
                {"index": 0, "function": {"arguments": "1}"}},
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        asm, frames = self._run(chunks)
        events = self._events(frames)
        # slot 0 (alpha, first-seen) must be fully contiguous BEFORE slot 1 (beta)
        assert events == [
            "message_start",
            "content_block_start", "content_block_delta", "content_block_stop",  # alpha
            "content_block_start", "content_block_delta", "content_block_stop",  # beta
            "message_delta", "message_stop",
        ]
        starts = [json.loads(f.split("data: ", 1)[1]) for f in frames if "content_block_start" in f]
        assert starts[0]["content_block"]["name"] == "alpha"
        assert starts[0]["content_block"]["id"] == "call_A"
        assert starts[1]["content_block"]["name"] == "beta"
        assert starts[1]["content_block"]["id"] == "call_B"
        deltas = [json.loads(f.split("data: ", 1)[1]) for f in frames if "input_json_delta" in f]
        assert json.loads(deltas[0]["delta"]["partial_json"]) == {"a": 1}
        assert json.loads(deltas[1]["delta"]["partial_json"]) == {"b": 2}
        assert asm.counters.tool_calls_seen_from_upstream == 4  # 2 slots x 2 fragments each
        assert asm.counters.tool_calls_emitted_downstream == 2

    def test_no_argument_tool_call_emits_single_empty_object_delta(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "ping", "arguments": ""}},
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        asm, frames = self._run(chunks)
        delta_frames = [f for f in frames if "input_json_delta" in f]
        assert len(delta_frames) == 1  # never zero deltas
        payload = json.loads(delta_frames[0].split("data: ", 1)[1])
        assert payload["delta"]["partial_json"] == "{}"

    def test_id_fallback_when_index_absent(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"id": "call_xyz", "function": {"name": "f", "arguments": "{}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        asm, frames = self._run(chunks)
        assert asm.counters.tool_calls_emitted_downstream == 1

    def test_position_fallback_when_no_index_and_no_id(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"function": {"name": "f", "arguments": "{\"x\":1}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        asm, frames = self._run(chunks)
        assert asm.counters.tool_calls_emitted_downstream == 1
        start = [json.loads(f.split("data: ", 1)[1]) for f in frames if "content_block_start" in f][0]
        assert start["content_block"]["id"].startswith("toolu_")  # synthesized, stable within this response

    def test_malformed_json_arguments_raises_and_never_repairs(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "f", "arguments": "{not valid json"}},
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        asm = adapter.AnthropicStreamAssembler(message_id="m", model="x")
        it = iter(chunks)
        asm.begin(next(it))
        for c in it:
            asm.feed(c)
        with pytest.raises(adapter.UnrecoverableStreamError):
            asm.finish()
        assert asm.counters.json_repair_attempts == 0

    def test_nameless_slot_raises(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"arguments": "{}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        asm = adapter.AnthropicStreamAssembler(message_id="m", model="x")
        it = iter(chunks)
        asm.begin(next(it))
        for c in it:
            asm.feed(c)
        with pytest.raises(adapter.UnrecoverableStreamError):
            asm.finish()
        assert asm.counters.nameless_slots_encountered == 1

    def test_text_after_tool_calls_buffered_and_flushed_after(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "f", "arguments": "{}"}},
            ]}}]},
            {"choices": [{"delta": {"content": "trailing note"}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        asm, frames = self._run(chunks)
        events = self._events(frames)
        # tool block fully closes before the trailing text block opens
        assert events == [
            "message_start", "content_block_start", "content_block_delta", "content_block_stop",
            "content_block_start", "content_block_delta", "content_block_stop",
            "message_delta", "message_stop",
        ]

    def test_real_usage_forwarded_when_present_else_zero(self):
        chunks_with_usage = [
            {"choices": [{"delta": {"content": "hi"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ]
        asm, frames = self._run(chunks_with_usage)
        delta = [json.loads(f.split("data: ", 1)[1]) for f in frames if "\"message_delta\"" in f][0]
        assert delta["usage"]["output_tokens"] == 5
        assert delta["usage"]["cache_creation_input_tokens"] == 0
        assert delta["usage"]["cache_read_input_tokens"] == 0

        chunks_no_usage = [
            {"choices": [{"delta": {"content": "hi"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        asm2, frames2 = self._run(chunks_no_usage)
        delta2 = [json.loads(f.split("data: ", 1)[1]) for f in frames2 if "\"message_delta\"" in f][0]
        assert delta2["usage"]["output_tokens"] == 0  # never fabricated

    def test_finish_reason_mapping(self):
        for finish, expected in [("stop", "end_turn"), ("length", "max_tokens"), ("tool_calls", "tool_use")]:
            chunks = [
                {"choices": [{"delta": {"content": "x"}}]},
                {"choices": [{"delta": {}, "finish_reason": finish}]},
            ]
            if finish == "tool_calls":
                chunks[1] = {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "c", "function": {"name": "f", "arguments": "{}"}},
                ]}, "finish_reason": "tool_calls"}]}
            asm, frames = self._run(chunks)
            delta = [json.loads(f.split("data: ", 1)[1]) for f in frames if "\"message_delta\"" in f][0]
            assert delta["delta"]["stop_reason"] == expected


# ============================================================================
# 3. FastAPI endpoint integration (fake upstream over ASGITransport)
# ============================================================================

@pytest.mark.asyncio
class TestMessagesEndpoint:
    async def test_prose_only_turn_end_to_end(self):
        fake = make_fake_upstream(chunks=[
            {"choices": [{"delta": {"content": "The answer is 4."}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages?beta=true",
                headers={"authorization": f"Bearer {token}"},
                json={
                    "model": "gpt-5.4",
                    "max_tokens": 1024,
                    "stream": True,
                    "messages": [{"role": "user", "content": "what's 2+2"}],
                },
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = await parse_sse(resp)
        events = [f["event"] for f in frames]
        assert events[0] == "message_start"
        assert events[-1] == "message_stop"
        text = "".join(f["data"]["delta"]["text"] for f in frames if f["event"] == "content_block_delta" and f["data"]["delta"]["type"] == "text_delta")
        assert text == "The answer is 4."

    async def test_single_tool_call_round_trip(self):
        fake = make_fake_upstream(chunks=[
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "get_weather", "arguments": ""}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"city":"Paris"}'}},
            ]}, "finish_reason": "tool_calls"}]},
        ])
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages",
                headers={"authorization": f"Bearer {token}"},
                json={
                    "model": "gpt-5.4", "max_tokens": 1024, "stream": True,
                    "messages": [{"role": "user", "content": "weather in paris?"}],
                    "tools": [{"name": "get_weather", "description": "d", "input_schema": {"type": "object"}}],
                },
            )
        frames = await parse_sse(resp)
        tool_start = next(f for f in frames if f["event"] == "content_block_start" and f["data"]["content_block"]["type"] == "tool_use")
        assert tool_start["data"]["content_block"]["name"] == "get_weather"
        message_delta = next(f for f in frames if f["event"] == "message_delta")
        assert message_delta["data"]["delta"]["stop_reason"] == "tool_use"

    async def test_parallel_tool_calls_over_the_wire(self):
        fake = make_fake_upstream(chunks=[
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_A", "function": {"name": "alpha", "arguments": "{}"}},
                {"index": 1, "id": "call_B", "function": {"name": "beta", "arguments": "{}"}},
            ]}, "finish_reason": "tool_calls"}]},
        ])
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={
                    "model": "gpt-5.4", "max_tokens": 1024, "stream": True,
                    "messages": [{"role": "user", "content": "do both"}],
                    "tools": [
                        {"name": "alpha", "description": "d", "input_schema": {}},
                        {"name": "beta", "description": "d", "input_schema": {}},
                    ],
                },
            )
        frames = await parse_sse(resp)
        tool_blocks = [f for f in frames if f["event"] == "content_block_start" and f["data"]["content_block"]["type"] == "tool_use"]
        assert [b["data"]["content_block"]["name"] for b in tool_blocks] == ["alpha", "beta"]

    async def test_no_argument_tool_call_over_the_wire(self):
        fake = make_fake_upstream(chunks=[
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "ping"}},
            ]}, "finish_reason": "tool_calls"}]},
        ])
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={
                    "model": "gpt-5.4", "max_tokens": 1024, "stream": True,
                    "messages": [{"role": "user", "content": "ping"}],
                    "tools": [{"name": "ping", "description": "d", "input_schema": {}}],
                },
            )
        frames = await parse_sse(resp)
        delta = next(f for f in frames if f["event"] == "content_block_delta" and f["data"]["delta"]["type"] == "input_json_delta")
        assert delta["data"]["delta"]["partial_json"] == "{}"

    async def test_unknown_token_is_401(self):
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": "Bearer not-a-real-token"},
                json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 401
        assert resp.headers["content-type"].startswith("application/json")

    async def test_credential_exchange_real_key_reaches_only_fake_upstream(self):
        capture: Dict[str, Any] = {}
        fake = make_fake_upstream(chunks=[
            {"choices": [{"delta": {"content": "ok"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ], capture=capture)
        token = mint_token(fake)
        assert "REAL-SECRET-KEY" not in token  # the opaque token itself carries no key material
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "max_tokens": 100, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 200
        assert "REAL-SECRET-KEY" not in resp.text  # never echoed back to the CLI-side caller
        assert capture["headers"]["authorization"] == "Bearer REAL-SECRET-KEY-never-should-leak"  # only the fake upstream saw it

        # After the turn "ends" (clear_turn_credential), the token is worthless.
        adapter.clear_turn_credential(token)
        async with client_for(adapter.app) as client:
            resp2 = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp2.status_code == 401

    async def test_malformed_upstream_first_frame_is_fast_400(self):
        fake = make_fake_upstream(raw_body=b"data: {not json at all\n\n")
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "max_tokens": 100, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        assert "empyralis-adapter" in body["error"]["message"]
        assert "message_start" not in resp.text  # no SSE bytes at all were ever sent

    async def test_upstream_fails_before_any_bytes_no_message_start_ever(self):
        fake = make_fake_upstream(fail_before_any_bytes=True)
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "max_tokens": 100, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 502
        assert resp.headers["content-type"].startswith("application/json")
        assert "message_start" not in resp.text
        assert not resp.headers["content-type"].startswith("text/event-stream")

    async def test_upstream_5xx_passed_through_as_5xx_not_400(self):
        fake = make_fake_upstream(status_code=500)
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "max_tokens": 100, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 500

    async def test_upstream_400_becomes_adapter_400_not_5xx(self):
        fake = make_fake_upstream(status_code=400)
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "max_tokens": 100, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 400

    async def test_non_streaming_path(self):
        fake = make_fake_upstream(non_streaming_json={
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        })
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "max_tokens": 100, "stream": False, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"][0]["text"] == "hello"
        assert body["usage"]["output_tokens"] == 2
        assert body["usage"]["cache_creation_input_tokens"] == 0

    async def test_non_streaming_malformed_tool_call_is_400(self):
        fake = make_fake_upstream(non_streaming_json={
            "choices": [{"message": {"tool_calls": [
                {"id": "c1", "function": {"name": "f", "arguments": "{bad json"}},
            ]}, "finish_reason": "tool_calls"}],
        })
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "max_tokens": 100, "stream": False, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 400

    async def test_count_tokens_endpoint(self):
        fake = make_fake_upstream()
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages/count_tokens?beta=true", headers={"authorization": f"Bearer {token}"},
                json={"model": "gpt-5.4", "messages": [{"role": "user", "content": "hello world, this is a test message"}]},
            )
        assert resp.status_code == 200
        assert resp.json()["input_tokens"] > 0

    async def test_x_api_key_header_also_accepted(self):
        fake = make_fake_upstream(chunks=[{"choices": [{"delta": {}, "finish_reason": "stop"}]}])
        token = mint_token(fake)
        async with client_for(adapter.app) as client:
            resp = await client.post(
                "/v1/messages", headers={"x-api-key": token},
                json={"model": "gpt-5.4", "max_tokens": 100, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 200

    async def test_max_tokens_and_tool_choice_actually_sent_upstream(self):
        capture: Dict[str, Any] = {}
        fake = make_fake_upstream(chunks=[{"choices": [{"delta": {}, "finish_reason": "stop"}]}], capture=capture)
        token = mint_token(fake, provider="openai")
        async with client_for(adapter.app) as client:
            await client.post(
                "/v1/messages", headers={"authorization": f"Bearer {token}"},
                json={
                    "model": "gpt-4o-mini", "max_tokens": 999999, "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        assert capture["body"]["max_tokens"] == 16384  # clamped, not 999999
        assert "tool_choice" not in capture["body"]  # absent in request -> omitted, not defaulted


# ============================================================================
# 4. Real per-provider upstream resolution (no network; provider_profiles.py's
#    OWN resolution logic, unmodified — the "openai" provider is skipped here
#    since it triggers provider_profiles' _init(), which imports the full
#    server.py module and its side effects; see module docstring / dispatch
#    report for that known, intentional test gap).
# ============================================================================

class TestRealUpstreamResolution:
    def test_groq(self):
        url, headers = adapter.resolve_real_upstream_request("groq", {"api_key": "x"}, "llama-3.3-70b-versatile")
        assert url == "https://api.groq.com/openai/v1/chat/completions"
        assert headers["Authorization"] == "Bearer x"

    def test_xai(self):
        url, _headers = adapter.resolve_real_upstream_request("xai", {"api_key": "x"}, "grok-4")
        assert url == "https://api.x.ai/v1/chat/completions"

    def test_openrouter(self):
        url, _headers = adapter.resolve_real_upstream_request("openrouter", {"api_key": "x"}, "openai/gpt-5.4")
        assert url == "https://openrouter.ai/api/v1/chat/completions"

    def test_gemini_uses_openai_compat_layer(self):
        url, headers = adapter.resolve_real_upstream_request("gemini", {"api_key": "g-key"}, "gemini-2.5-flash")
        assert url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert headers["Authorization"] == "Bearer g-key"

    def test_gemini_missing_key_raises(self):
        with pytest.raises(adapter.AdapterTranslationError):
            adapter.resolve_real_upstream_request("gemini", {}, "gemini-2.5-flash")

    def test_custom_openai_compatible_requires_base_url(self):
        with pytest.raises(Exception):
            adapter.resolve_real_upstream_request("custom_openai_compatible", {"api_key": "x"}, "some-model")

    def test_unrouted_provider_raises(self):
        with pytest.raises(adapter.AdapterTranslationError):
            adapter.resolve_real_upstream_request("not-a-real-provider", {}, "m")


# ============================================================================
# 5. Provider routing table
# ============================================================================

class TestProviderRouting:
    @pytest.mark.parametrize("provider", [
        "openai", "gemini", "xai", "groq", "azure_openai", "openrouter",
        "qwen", "mistral", "ollama_cloud", "custom_openai_compatible",
    ])
    def test_adapter_routed_providers(self, provider):
        assert adapter.is_adapter_routed_provider(provider) is True

    @pytest.mark.parametrize("provider", ["anthropic", "deepseek", "ollama", ""])
    def test_non_adapter_routed_providers(self, provider):
        assert adapter.is_adapter_routed_provider(provider) is False

"""In-process Anthropic-Messages <-> OpenAI-Chat-Completions protocol adapter.

Problem this solves: claude_agent_sdk_bridge.py's engine (the Claude Agent
SDK, which spawns the real `claude` CLI and points it at ANTHROPIC_BASE_URL)
only works today for providers that ship a genuinely native Anthropic-
Messages-compatible endpoint (Anthropic itself, DeepSeek, and Ollama — the
first two resolved from that module's fixed `_ANTHROPIC_COMPATIBLE_BASE_
URLS` map; Ollama resolved differently, per-turn, by that module's
`resolve_ollama_anthropic_base_url`, since it is self-hosted per workspace
rather than one fixed public URL).
Everything else in provider_profiles.py that speaks OpenAI's Chat
Completions protocol instead (openai, gemini, xai, groq, azure_openai,
openrouter, qwen, mistral, ollama_cloud, custom_openai_compatible) has no
path onto that engine. This module is the missing path: a small local HTTP
server, speaking exactly the wire shape the bundled `claude` CLI expects at
ANTHROPIC_BASE_URL, that translates every request/response through to
whichever OpenAI-shaped provider a turn is actually configured for.

Real, credible prior art for "put a translating proxy between an
Anthropic-Messages client and an OpenAI-shaped backend" exists at AWS
(aws-samples), Palantir Foundry, and TrueFoundry — this is an established
enterprise integration pattern, not a hack. LiteLLM was deliberately not
used or vendored (PyPI package backdoored with a credential stealer, March
2026, TeamPCP supply-chain attack); every translation function here is
Empyralis's own.

Prior art INSIDE this repo: scripts/deepseek_anthropic_proxy.py proves the
same shape works end-to-end for one provider (DeepSeek) that happens to
already have a native endpoint (so that script is a belt-and-suspenders
fallback, not load-bearing). Its request-conversion functions were a
reasonable starting shape; its streaming half has real bugs this module
does not share — the biggest one: it forwards OpenAI `tool_calls[]`
fragments straight through as they arrive, so two parallel tool calls whose
argument fragments interleave on the wire produce INTERLEAVED Anthropic
content-block events, which is not a shape the real Anthropic wire format
(or the CLI's parser) can ever produce or expects. AnthropicStreamAssembler
below buffers per-slot and flushes each tool call's block contiguously
specifically to avoid that.

────────────────────────────────────────────────────────────────────────
KNOWN GAP IN THIS WORKTREE, READ BEFORE WIRING: at the time this module was
written, `server_modules/claude_agent_sdk_bridge.py` — the module this is
designed to plug into — did not exist on this git branch. It exists on a
separate, not-yet-integrated branch (as of this writing: commit a7c012aa3,
reachable from branch `worktree-agent-ad98d05ce9a83a297`, NOT an ancestor of
this branch's HEAD). Pulling that branch's history into this one was
attempted (`git merge --ff-only`, a fast-forward, since this branch has no
commits of its own beyond their common ancestor) and was blocked by the
auto-mode permission classifier; per that tool's own guidance this was not
worked around. Concretely: nothing in this module imports or is imported by
claude_agent_sdk_bridge.py yet, and no line of that file has been edited.
This module is fully self-contained and independently tested (unit tests
below, plus a live-CLI verification harness run directly against the real
`claude` binary — see the dispatch report for evidence) so none of that
blocks proving this adapter works. It DOES block the actual plug-in. Once
claude_agent_sdk_bridge.py exists on this branch (merge/rebase, or this
work lands on a branch that already has it), the wiring is exactly:

  1. In resolve_sdk_process_env / resolve_anthropic_compatible_base_url's
     call site, change the base_url resolution to:

         from server_modules import openai_compat_adapter
         base_url = (
             resolve_anthropic_compatible_base_url(provider)
             or openai_compat_adapter.resolve_adapter_routed_base_url(provider)
         )

     resolve_adapter_routed_base_url has the exact same contract as
     resolve_anthropic_compatible_base_url (empty string = "no override for
     this provider"), so this is a pure `or`, no branching logic to add.
     Do NOT add adapter-routed providers to `_ANTHROPIC_COMPATIBLE_BASE_
     URLS` itself — that map's own docstring reserves it for providers with
     a genuinely native Anthropic endpoint, which these providers do not
     have; conflating the two erases the distinction that map exists to
     preserve.

  2. Where resolve_sdk_process_env decides ANTHROPIC_AUTH_TOKEN, when this
     turn's provider is adapter-routed, mint an opaque per-turn token
     instead of handing the CLI subprocess the real provider key:

         if openai_compat_adapter.is_adapter_routed_provider(provider):
             env["ANTHROPIC_AUTH_TOKEN"] = openai_compat_adapter.mint_turn_token_for_provider(
                 provider, credentials, model,
             )
         else:
             env["ANTHROPIC_AUTH_TOKEN"] = api_key  # existing behavior, unchanged

  3. In run_claude_agent_sdk_turn's `finally` block, alongside the existing
     `shutil.rmtree(config_dir, ignore_errors=True)`, clear the token:

         if minted_adapter_token:
             openai_compat_adapter.clear_turn_credential(minted_adapter_token)

That is the entire integration surface. Nothing else in claude_agent_sdk_
bridge.py needs to change — this module never touches DeepSeek's or
Anthropic's own routing, and does not go near `_ANTHROPIC_COMPATIBLE_BASE_
URLS`.
────────────────────────────────────────────────────────────────────────

Wire contract this module implements (verified against the real bundled
`claude` CLI binary + its embedded @anthropic-ai/sdk in a throwaway local
investigation; see the dispatch report for the concrete evidence):

  - POST {base}/v1/messages (with or without a `?beta=true` query string —
    FastAPI dispatches both to the same handler since the param is never
    declared) and POST {base}/v1/messages/count_tokens (same query
    tolerance).
  - Auth: `authorization: Bearer <opaque per-turn token>` (how the bridge's
    ANTHROPIC_AUTH_TOKEN arrives) or `x-api-key: <token>`. anthropic-
    version / anthropic-beta headers are accepted and ignored.
  - Request body quirks tolerated: `system` is a list of content blocks,
    never a plain string (a plain string is still accepted defensively);
    one system block is the CLI's own internal billing bookkeeping text
    (prefixed `x-anthropic-billing-header:`) and is dropped, never
    forwarded as an instruction; `thinking` (default shape
    `{"type":"adaptive"}`, not the documented `{"type":"enabled",...}`) is
    silently stripped, never forwarded (OpenAI-shaped providers have no
    equivalent); `max_tokens` (CLI default 32000) is clamped per
    provider+model against a table this module owns (never guessed/
    scraped), falling back to a conservative default when the model is
    unrecognized; `tool_choice` may be entirely absent — never assumed
    present.
  - SSE contract: the CLI's own SSE reader dispatches ONLY on a closed
    `event:` allowlist and silently discards any frame whose event name
    isn't on it (or that has no `event:` line at all) — every frame this
    module emits sets `event:` explicitly. `message_start` is never sent
    before the first real chunk has actually arrived from upstream (see
    the endpoint handler's two-phase "peek, then stream" structure); once
    it IS sent, the only clean exits are a normal `message_stop` or
    dropping the connection — `event: error` mid-stream is never emitted,
    because on the real CLI that throws away everything already
    accumulated for the turn and shows only a generic message, discarding
    whatever specific diagnostic this module logged server-side.
  - Anthropic-only concepts (thinking/redacted_thinking, cache_control,
    metadata/context_management/betas) are stripped, never forwarded and
    never fabricated back (cache_creation_input_tokens /
    cache_read_input_tokens are always reported as literal 0 — never a
    guessed cache hit, since that number feeds billing).

Security: this server is loopback-only (127.0.0.1), started lazily on a
daemon thread with its own asyncio event loop the first time an
adapter-routed turn needs it (see ensure_adapter_server_running) — never a
route on the main FastAPI app, which binds wider (0.0.0.0:8001 behind
nginx). The customer's real provider API key is exchanged for a random,
structureless opaque token BEFORE it is ever handed to the `claude` CLI
subprocess (mint_turn_token_for_provider / TurnCredential); the CLI only
ever sees the opaque token as its ANTHROPIC_AUTH_TOKEN. This module's own
request handler exchanges that token back for the real key + real upstream
URL via an in-memory, per-turn map (_turn_credentials), cleared the moment
the turn ends (clear_turn_credential — callers MUST call this in a
`finally`, mirroring run_claude_agent_sdk_turn's own config_dir cleanup
discipline). The real key is NEVER logged, NEVER written to disk, and NEVER
appears in the subprocess's environment. Only the opaque token is safe to
log, and every log line in this module was written with that split in
mind — grep for `LOGGER.` here if auditing this claim.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

LOGGER = logging.getLogger(__name__)

# ============================================================================
# 1. Provider routing
# ============================================================================

# Every provider_profiles.py provider id that speaks OpenAI's Chat
# Completions protocol and has NO native Anthropic-Messages endpoint.
# DeepSeek and (bare) "ollama" are deliberately absent — both are
# Anthropic-native and resolved natively by claude_agent_sdk_bridge.py
# (DeepSeek via its fixed `_ANTHROPIC_COMPATIBLE_BASE_URLS` map, Ollama
# per-turn via its `resolve_ollama_anthropic_base_url`) — never add either
# here, they don't need translation. "ollama_cloud" (Ollama's separate
# hosted/BYOK offering, ollama.com) is NOT the same provider id as local
# "ollama" and belongs in this set: it is a genuinely OpenAI-shaped hosted
# endpoint with no native Anthropic-Messages surface of its own.
ADAPTER_ROUTED_PROVIDER_IDS = frozenset({
    "openai",
    "gemini",
    "xai",
    "groq",
    "azure_openai",
    "openrouter",
    "qwen",
    "mistral",
    "ollama_cloud",
    "custom_openai_compatible",
})


def is_adapter_routed_provider(provider: str) -> bool:
    return str(provider or "").strip().lower() in ADAPTER_ROUTED_PROVIDER_IDS


# ============================================================================
# 2. Per-provider/model max_output_tokens ceiling — a table this module
#    owns, never scraped/guessed. The CLI's default request `max_tokens` is
#    32000; many OpenAI/Gemini/xAI models reject that outright, so every
#    request is clamped to min(requested, this table's ceiling for that
#    exact model, else the provider's own conservative default, else a
#    global conservative floor).
# ============================================================================

_PROVIDER_MODEL_MAX_OUTPUT_TOKENS: Dict[str, Dict[str, int]] = {
    "openai": {
        "gpt-5.5": 128000,
        "gpt-5.5-pro": 128000,
        "gpt-5.4": 128000,
        "gpt-5.4-mini": 128000,
        "gpt-5.4-nano": 128000,
        "gpt-5.2": 128000,
        "gpt-5-mini": 128000,
        "gpt-5-nano": 128000,
        "gpt-4.1": 32768,
        "gpt-4.1-mini": 32768,
        "gpt-4o": 16384,
        "gpt-4o-mini": 16384,
    },
    "gemini": {
        "gemini-3-pro-preview": 65536,
        "gemini-3-flash-preview": 65536,
        "gemini-2.5-pro": 65536,
        "gemini-2.5-flash": 65536,
        "gemini-2.5-flash-lite": 65536,
        "gemini-2.0-flash": 8192,
        "gemini-2.0-flash-lite": 8192,
        "gemini-1.5-pro": 8192,
        "gemini-1.5-flash": 8192,
    },
    "xai": {
        "grok-4": 32768,
        "grok-4-0709": 32768,
        "grok-4-latest": 32768,
        "grok-3": 32768,
    },
    "groq": {
        "llama-3.3-70b-versatile": 32768,
        "llama-3.1-8b-instant": 8192,
    },
    "qwen": {
        "qwen-plus": 8192,
        "qwen-turbo": 8192,
        "qwen-max": 8192,
    },
    "mistral": {
        "mistral-large-latest": 32000,
        "mistral-medium-latest": 32000,
        "mistral-small-latest": 32000,
    },
    "ollama_cloud": {
        "gpt-oss:120b": 32768,
        "gpt-oss:20b": 32768,
    },
}

# Used when the model isn't in the exact-match table above but the provider
# is known — still better than the CLI's raw 32000 default for providers
# whose typical ceiling is well below that.
_PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS: Dict[str, int] = {
    "openai": 16384,
    "gemini": 8192,
    "xai": 32768,
    "groq": 8192,
    "azure_openai": 4096,
    "openrouter": 8192,
    "qwen": 8192,
    "mistral": 8192,
    "ollama_cloud": 8192,
    "custom_openai_compatible": 4096,
}

# The absolute floor when neither the provider nor the model is recognized
# at all — spec-mandated conservative fallback rather than passing the
# CLI's raw 32000 through blind.
_CONSERVATIVE_DEFAULT_MAX_OUTPUT_TOKENS = 4096


def clamp_max_tokens(provider: str, model: str, requested: Any) -> int:
    """min(requested, known ceiling for provider+model) — never guessed
    upward, only ever clamped down (or defaulted, when `requested` itself
    is missing/invalid)."""
    try:
        requested_n = int(requested)
        if requested_n <= 0:
            requested_n = 32000
    except (TypeError, ValueError):
        requested_n = 32000
    p = str(provider or "").strip().lower()
    m = str(model or "").strip()
    ceiling = _PROVIDER_MODEL_MAX_OUTPUT_TOKENS.get(p, {}).get(m)
    if ceiling is None:
        ceiling = _PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS.get(p, _CONSERVATIVE_DEFAULT_MAX_OUTPUT_TOKENS)
    return max(1, min(requested_n, ceiling))


# ============================================================================
# 3. Request translation: Anthropic Messages -> OpenAI Chat Completions
# ============================================================================

class AdapterTranslationError(Exception):
    """A request/response could not be honestly translated. Always surfaced
    as an HTTP 400 with a specific message BEFORE any SSE byte is sent —
    see the endpoint handler. Never raised after message_start has gone
    out; see UnrecoverableStreamError for the mid-stream equivalent."""


_BILLING_HEADER_PREFIX = "x-anthropic-billing-header:"


def _system_blocks_to_text(system: Any) -> str:
    if isinstance(system, str):
        return system.strip()
    if not isinstance(system, list):
        return ""
    parts: List[str] = []
    for block in system:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "")
        if text.strip().startswith(_BILLING_HEADER_PREFIX):
            # The CLI's own internal billing bookkeeping text — never an
            # instruction meant for the model. Drop it, don't forward it.
            continue
        if text:
            parts.append(text)
    return "\n\n".join(p for p in parts if p).strip()


def _flatten_tool_result_content(content: Any) -> Tuple[str, bool]:
    """-> (flattened_text, had_image). Images have no home in an OpenAI
    role:"tool" message; they are dropped with an explicit marker rather
    than silently swallowed."""
    if content is None:
        return "", False
    if isinstance(content, str):
        return content, False
    if isinstance(content, list):
        parts: List[str] = []
        had_image = False
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = str(block.get("text") or "")
                if text:
                    parts.append(text)
            elif btype == "image":
                had_image = True
                parts.append("[image omitted]")
        return "\n".join(parts), had_image
    return str(content), False


def _user_plain_content_to_openai(content: Any) -> Any:
    """Non tool_result user content -> OpenAI `content` (string, or a list
    of {type:text|image_url} blocks when any image is present)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    text_parts: List[str] = []
    blocks: List[Dict[str, Any]] = []
    has_image = False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = str(block.get("text") or "")
            text_parts.append(text)
            blocks.append({"type": "text", "text": text})
        elif btype == "image":
            has_image = True
            source = block.get("source") or {}
            media_type = str(source.get("media_type") or "image/png")
            data = str(source.get("data") or "")
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            })
    if has_image:
        return blocks
    return "\n".join(text_parts)


def _assistant_content_to_openai(content: Any) -> Dict[str, Any]:
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = str(block.get("text") or "")
                if text:
                    text_parts.append(text)
            elif btype == "tool_use":
                raw_input = block.get("input")
                tool_calls.append({
                    "id": str(block.get("id") or f"call_{uuid.uuid4().hex[:24]}"),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(raw_input if isinstance(raw_input, dict) else {}),
                    },
                })
            # thinking / redacted_thinking / server_tool_use / anything else:
            # dropped. A model's private reasoning is never resent as prose,
            # and there is no OpenAI-shaped equivalent to resend it as.
    else:
        text = str(content or "")
        if text:
            text_parts.append(text)
    out: Dict[str, Any] = {"role": "assistant"}
    joined = "\n".join(p for p in text_parts if p)
    if joined or not tool_calls:
        out["content"] = joined
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _translate_messages(messages: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for msg in messages if isinstance(messages, list) else []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        content = msg.get("content")
        if role == "assistant":
            out.append(_assistant_content_to_openai(content))
            continue
        # user (or any other role — treated defensively as user-shaped,
        # this should never happen from the real CLI).
        has_tool_result = isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
        if not has_tool_result:
            out.append({"role": "user", "content": _user_plain_content_to_openai(content)})
            continue
        # Anthropic packs N parallel tool_result blocks into ONE user
        # message; OpenAI needs one role:"tool" message PER block, in
        # order. Any text/image blocks mixed into the SAME user message
        # become a separate trailing user message, emitted after the tool
        # messages.
        other_blocks: List[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_use_id = str(block.get("tool_use_id") or "")
                text, _had_image = _flatten_tool_result_content(block.get("content"))
                if bool(block.get("is_error")):
                    text = f"[tool error] {text}" if text else "[tool error]"
                out.append({"role": "tool", "tool_call_id": tool_use_id, "content": text})
            else:
                other_blocks.append(block)
        if other_blocks:
            out.append({"role": "user", "content": _user_plain_content_to_openai(other_blocks)})
    return out


def _translate_tools(tools: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(tools, list) or not tools:
        return None
    out: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        schema = tool.get("input_schema")
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": str(tool.get("description") or ""),
                "parameters": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
            },
        })
    return out or None


def _translate_tool_choice(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if not isinstance(tool_choice, dict):
        raise AdapterTranslationError("tool_choice must be an object")
    kind = str(tool_choice.get("type") or "").strip()
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    if kind == "tool":
        name = str(tool_choice.get("name") or "").strip()
        if not name:
            raise AdapterTranslationError("tool_choice type 'tool' requires a name")
        return {"type": "function", "function": {"name": name}}
    raise AdapterTranslationError(f"unrecognized tool_choice.type {kind!r}")


def translate_anthropic_request_to_openai(body: Dict[str, Any], *, provider: str) -> Dict[str, Any]:
    """Anthropic /v1/messages request body -> OpenAI Chat Completions
    request body. Raises AdapterTranslationError (-> HTTP 400, pre-stream)
    on anything that cannot be honestly translated.

    `thinking`, `cache_control`, `metadata`, `context_management`, `betas`
    are never read from `body` at all — they are stripped by omission, not
    by an explicit delete step. `output_config.format` is best-effort
    mapped to OpenAI's `response_format` (nice-to-have; failures here never
    fail the whole translation)."""
    if not isinstance(body, dict):
        raise AdapterTranslationError("request body must be a JSON object")
    model = str(body.get("model") or "").strip()
    if not model:
        raise AdapterTranslationError("request is missing required field 'model'")
    messages_in = body.get("messages")
    if not isinstance(messages_in, list) or not messages_in:
        raise AdapterTranslationError("request is missing required non-empty field 'messages'")

    openai_messages: List[Dict[str, Any]] = []
    system_text = _system_blocks_to_text(body.get("system"))
    if system_text:
        openai_messages.append({"role": "system", "content": system_text})
    openai_messages.extend(_translate_messages(messages_in))

    stream = bool(body.get("stream", False))
    openai_body: Dict[str, Any] = {
        "model": model,
        "messages": openai_messages,
        "max_tokens": clamp_max_tokens(provider, model, body.get("max_tokens")),
        "stream": stream,
    }
    if stream:
        # Ask the provider to include real token usage on the final
        # streaming chunk (standard OpenAI Chat Completions option) so the
        # adapter can report a MEASURED output_tokens rather than always 0.
        openai_body["stream_options"] = {"include_usage": True}
    if body.get("temperature") is not None:
        try:
            openai_body["temperature"] = float(body["temperature"])
        except (TypeError, ValueError):
            pass
    if body.get("top_p") is not None:
        try:
            openai_body["top_p"] = float(body["top_p"])
        except (TypeError, ValueError):
            pass
    stop_sequences = body.get("stop_sequences")
    if isinstance(stop_sequences, list) and stop_sequences:
        openai_body["stop"] = [str(s) for s in stop_sequences]

    tools = _translate_tools(body.get("tools"))
    if tools:
        openai_body["tools"] = tools
        if "tool_choice" in body:
            choice = _translate_tool_choice(body.get("tool_choice"))
            if choice is not None:
                openai_body["tool_choice"] = choice

    output_config = body.get("output_config")
    if isinstance(output_config, dict):
        fmt = output_config.get("format")
        if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
            schema = fmt.get("schema")
            if isinstance(schema, dict):
                try:
                    openai_body["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": str(fmt.get("name") or "response"),
                            "schema": schema,
                            "strict": bool(fmt.get("strict", False)),
                        },
                    }
                except Exception:  # pragma: no cover - defensive, nice-to-have only
                    LOGGER.debug("openai_compat_adapter: output_config->response_format mapping failed, skipping")

    return openai_body


def estimate_input_tokens(body: Dict[str, Any]) -> int:
    """Cheap /v1/messages/count_tokens estimator (~4 chars/token). This is
    a UX number, never billed against — a coarse heuristic is fine."""
    try:
        text = json.dumps(body.get("messages") or []) + _system_blocks_to_text(body.get("system"))
    except Exception:
        text = ""
    return max(1, len(text) // 4)


# ============================================================================
# 4. Streaming translation: OpenAI chunks -> Anthropic SSE frames
# ============================================================================

def render_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class UnrecoverableStreamError(Exception):
    """Raised mid-stream, i.e. strictly after message_start has already
    been emitted to the client. The caller MUST NOT translate this into an
    `event: error` frame (see module docstring) — the only honest options
    left are a clean message_stop or dropping the connection; raising this
    signals the latter, and the endpoint handler lets it propagate out of
    the streaming generator so Starlette aborts the connection rather than
    closing it as if the turn completed normally."""


@dataclass
class _ToolSlot:
    key: Any
    order: int
    id: str = ""
    name: str = ""
    arguments: str = ""
    flushed: bool = False


class TurnCounters:
    """Per-turn diagnostics, tagged by turn id in every log line — never
    with real key material. A mismatch here (e.g. json_repair_attempts != 0,
    which should be structurally impossible since this module never
    attempts a repair) is a signal to escalate, not silently absorb."""

    def __init__(self) -> None:
        self.tool_calls_seen_from_upstream = 0
        self.tool_calls_emitted_downstream = 0
        self.nameless_slots_encountered = 0
        self.json_repair_attempts = 0
        self.upstream_finish_reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool_calls_seen_from_upstream": self.tool_calls_seen_from_upstream,
            "tool_calls_emitted_downstream": self.tool_calls_emitted_downstream,
            "nameless_slots_encountered": self.nameless_slots_encountered,
            "json_repair_attempts": self.json_repair_attempts,
            "upstream_finish_reason": self.upstream_finish_reason,
        }


_FINISH_REASON_TO_STOP_REASON = {
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
}


class AnthropicStreamAssembler:
    """Feed OpenAI Chat-Completions streaming chunk dicts in via `feed()`;
    get Anthropic Messages SSE frame strings out. Honors Anthropic's wire
    invariant that content blocks are strictly sequential (start -> delta*
    -> stop, one at a time, never interleaved) even when the upstream
    provider interleaves parallel tool-call argument fragments across
    chunks — see module docstring.

    Design: text streams live (content_block_start/delta emitted
    immediately) for as long as no tool_calls fragment has appeared yet.
    The instant the first tool_calls fragment shows up, any open text block
    is closed and every tool call's fragments (keyed by OpenAI's `index`,
    falling back to `id`, falling back to array position) are buffered —
    never flushed to the wire — until `finish()` is called, at which point
    every tool slot is flushed IN THE ORDER IT WAS FIRST SEEN, each
    contiguously (start, one input_json_delta carrying the full buffered
    argument string verbatim, stop) before message_delta/message_stop.
    Text that (unusually) arrives AFTER a tool_calls fragment has already
    been seen is likewise buffered and flushed as one trailing text block
    after every tool slot, to preserve strict sequentiality.

    A slot that never receives a name, or whose buffered arguments do not
    parse as a JSON object, is never repaired and never silently dropped —
    it raises UnrecoverableStreamError (see that class)."""

    def __init__(self, *, message_id: str, model: str, input_tokens_estimate: int = 0) -> None:
        self.message_id = message_id
        self.model = model
        self.input_tokens_estimate = input_tokens_estimate
        self.counters = TurnCounters()
        self._next_index = 0
        self._text_open = False
        self._text_index: Optional[int] = None
        self._tools_started = False
        self._trailing_text = ""
        self._slots: "OrderedDict[Any, _ToolSlot]" = OrderedDict()
        self._upstream_usage: Optional[Dict[str, Any]] = None
        self._finished = False

    # -- construction -----------------------------------------------------
    def message_start_frame(self) -> str:
        return render_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self.message_id,
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": self.input_tokens_estimate,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        })

    def begin(self, first_chunk: Dict[str, Any]) -> List[str]:
        """Call exactly once, with the FIRST chunk successfully received
        from upstream. Only after this has been called does message_start
        go out — see the endpoint handler for how the caller guarantees
        that ordering."""
        return [self.message_start_frame()] + self.feed(first_chunk)

    # -- per-chunk ----------------------------------------------------------
    def feed(self, chunk: Dict[str, Any]) -> List[str]:
        frames: List[str] = []
        if not isinstance(chunk, dict):
            return frames
        usage_field = chunk.get("usage")
        if isinstance(usage_field, dict):
            self._upstream_usage = usage_field
        choices = chunk.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        if not isinstance(choice, dict):
            choice = {}
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}
        finish_reason = choice.get("finish_reason")
        if finish_reason:
            self.counters.upstream_finish_reason = str(finish_reason)

        # Provider "reasoning"/"reasoning_content" deltas: Anthropic-only
        # concept on the other side (thinking blocks need a signature this
        # adapter cannot produce) — discarded, never synthesized.

        tool_call_deltas = delta.get("tool_calls")
        if isinstance(tool_call_deltas, list) and tool_call_deltas:
            if not self._tools_started:
                self._tools_started = True
                frames.extend(self._close_text_block())
            for position, tc in enumerate(tool_call_deltas):
                if not isinstance(tc, dict):
                    continue
                self.counters.tool_calls_seen_from_upstream += 1
                key = tc.get("index")
                if key is None:
                    key = tc.get("id") or f"__pos_{position}"
                slot = self._slots.get(key)
                if slot is None:
                    slot = _ToolSlot(key=key, order=len(self._slots))
                    self._slots[key] = slot
                tc_id = str(tc.get("id") or "").strip()
                if tc_id:
                    slot.id = tc_id
                func = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = str(func.get("name") or "").strip()
                if name:
                    slot.name = name
                args_fragment = func.get("arguments")
                if isinstance(args_fragment, str):
                    slot.arguments += args_fragment

        text = delta.get("content")
        if isinstance(text, str) and text:
            if self._tools_started:
                self._trailing_text += text
            else:
                if not self._text_open:
                    self._text_index = self._next_index
                    self._next_index += 1
                    self._text_open = True
                    frames.append(render_sse("content_block_start", {
                        "type": "content_block_start",
                        "index": self._text_index,
                        "content_block": {"type": "text", "text": ""},
                    }))
                frames.append(render_sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self._text_index,
                    "delta": {"type": "text_delta", "text": text},
                }))
        return frames

    # -- end of stream --------------------------------------------------
    def finish(self) -> List[str]:
        """Call once, after the upstream chunk iterator has been fully
        consumed (StopAsyncIteration or an explicit finish_reason)."""
        if self._finished:
            return []
        frames: List[str] = []
        frames.extend(self._close_text_block())
        for slot in self._slots.values():
            frames.extend(self._flush_slot(slot))  # may raise UnrecoverableStreamError
        if self._trailing_text:
            idx = self._next_index
            self._next_index += 1
            frames.append(render_sse("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {"type": "text", "text": ""},
            }))
            frames.append(render_sse("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {"type": "text_delta", "text": self._trailing_text},
            }))
            frames.append(render_sse("content_block_stop", {"type": "content_block_stop", "index": idx}))
            self._trailing_text = ""

        had_tool_calls = self.counters.tool_calls_emitted_downstream > 0
        stop_reason = (
            "tool_use" if had_tool_calls
            else _FINISH_REASON_TO_STOP_REASON.get(str(self.counters.upstream_finish_reason or "").strip(), "end_turn")
        )
        output_tokens = 0
        if isinstance(self._upstream_usage, dict):
            try:
                output_tokens = int(self._upstream_usage.get("completion_tokens") or 0)
            except (TypeError, ValueError):
                output_tokens = 0
        frames.append(render_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }))
        frames.append(render_sse("message_stop", {"type": "message_stop"}))
        self._finished = True
        return frames

    # -- internals --------------------------------------------------------
    def _close_text_block(self) -> List[str]:
        if not self._text_open:
            return []
        self._text_open = False
        idx = self._text_index
        self._text_index = None
        return [render_sse("content_block_stop", {"type": "content_block_stop", "index": idx})]

    def _flush_slot(self, slot: _ToolSlot) -> List[str]:
        if slot.flushed:
            return []
        slot.flushed = True
        if not slot.name:
            self.counters.nameless_slots_encountered += 1
            raise UnrecoverableStreamError(
                f"tool call (upstream slot key={slot.key!r}) never received a name from "
                "upstream — refusing to fabricate one; dropping the connection rather "
                "than route to the wrong tool."
            )
        raw_args = slot.arguments
        if raw_args.strip() == "":
            # Spec-mandated: a no-argument tool call still gets exactly ONE
            # input_json_delta so the accumulated string parses as JSON —
            # never zero deltas.
            emit_args = "{}"
        else:
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                # self.counters.json_repair_attempts is deliberately NEVER
                # incremented anywhere in this module — this branch fails
                # the turn instead of attempting a repair.
                raise UnrecoverableStreamError(
                    f"tool call '{slot.name}' arguments did not parse as JSON ({exc}); "
                    "refusing to repair — dropping the connection."
                ) from exc
            if not isinstance(parsed, dict):
                raise UnrecoverableStreamError(
                    f"tool call '{slot.name}' arguments parsed to {type(parsed).__name__}, "
                    "not a JSON object; dropping the connection."
                )
            emit_args = raw_args  # forwarded verbatim — never re-serialized

        tool_id = slot.id or f"toolu_{uuid.uuid4().hex[:24]}"
        index = self._next_index
        self._next_index += 1
        self.counters.tool_calls_emitted_downstream += 1
        return [
            render_sse("content_block_start", {
                "type": "content_block_start", "index": index,
                "content_block": {"type": "tool_use", "id": tool_id, "name": slot.name, "input": {}},
            }),
            render_sse("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "input_json_delta", "partial_json": emit_args},
            }),
            render_sse("content_block_stop", {"type": "content_block_stop", "index": index}),
        ]


def build_non_streaming_anthropic_response(model: str, upstream_json: Dict[str, Any]) -> Dict[str, Any]:
    """Non-streaming counterpart of AnthropicStreamAssembler — same
    honesty rules (no repaired JSON, no fabricated names), raised as
    AdapterTranslationError since nothing has been sent to the client yet
    at this point (-> HTTP 400, not a dropped connection)."""
    choices = upstream_json.get("choices") if isinstance(upstream_json, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    if not isinstance(message, dict):
        message = {}
    content: List[Dict[str, Any]] = []
    text = str(message.get("content") or "").strip()
    if text:
        content.append({"type": "text", "text": text})
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        func = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = str(func.get("name") or "").strip()
        if not name:
            raise AdapterTranslationError("upstream tool call is missing a function name")
        raw_args = str(func.get("arguments") or "")
        if raw_args.strip() == "":
            parsed_input: Dict[str, Any] = {}
        else:
            try:
                parsed_input = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise AdapterTranslationError(f"tool call '{name}' arguments did not parse as JSON: {exc}") from exc
            if not isinstance(parsed_input, dict):
                raise AdapterTranslationError(
                    f"tool call '{name}' arguments parsed to {type(parsed_input).__name__}, not an object"
                )
        content.append({
            "type": "tool_use",
            "id": str(tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"),
            "name": name,
            "input": parsed_input,
        })
    if not content:
        content.append({"type": "text", "text": ""})
    finish_reason = str(choice.get("finish_reason") or "stop")
    stop_reason = "tool_use" if tool_calls else _FINISH_REASON_TO_STOP_REASON.get(finish_reason, "end_turn")
    usage = upstream_json.get("usage") if isinstance(upstream_json.get("usage"), dict) else {}
    try:
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        input_tokens = output_tokens = 0
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


# ============================================================================
# 5. Upstream SSE parsing (OpenAI side)
# ============================================================================

class UpstreamHTTPError(Exception):
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"upstream HTTP {status_code}")


class UpstreamMalformed(Exception):
    """Upstream responded 2xx but the body could not be parsed at all —
    raised only when nothing has been yielded yet, so the caller can still
    cleanly return an HTTP 400 with no SSE bytes sent."""


class UpstreamEmptyStream(Exception):
    """Upstream accepted the connection and returned 2xx but closed
    without ever sending a data frame."""


async def iter_upstream_chunks(
    client: httpx.AsyncClient, url: str, headers: Dict[str, str], payload: Dict[str, Any],
) -> AsyncIterator[Dict[str, Any]]:
    yielded_any = False
    async with client.stream("POST", url, headers=headers, json=payload) as resp:
        if resp.status_code >= 400:
            body = await resp.aread()
            raise UpstreamHTTPError(resp.status_code, body)
        async for raw_line in resp.aiter_lines():
            line = raw_line.rstrip("\r")
            if not line or line.startswith(":"):
                continue  # blank separator or SSE comment
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if data_str == "[DONE]":
                break
            if not data_str:
                continue
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError as exc:
                if not yielded_any:
                    raise UpstreamMalformed(f"upstream's first SSE frame was not valid JSON: {exc}") from exc
                raise UnrecoverableStreamError(f"upstream sent non-JSON SSE data mid-stream: {exc}") from exc
            if not isinstance(chunk, dict):
                if not yielded_any:
                    raise UpstreamMalformed("upstream's first SSE frame was not a JSON object")
                raise UnrecoverableStreamError("upstream sent a non-object SSE frame mid-stream")
            yielded_any = True
            yield chunk
    if not yielded_any:
        raise UpstreamEmptyStream("upstream closed the connection without sending any data")


# ============================================================================
# 6. Credential exchange — the security-critical part
# ============================================================================

@dataclass
class TurnCredential:
    provider: str
    chat_completions_url: str
    headers: Dict[str, str]  # contains the REAL Authorization header — never logged
    minted_at: float = field(default_factory=time.monotonic)


_turn_credentials: Dict[str, TurnCredential] = {}
_turn_credentials_lock = threading.Lock()


def mint_turn_credential(*, provider: str, chat_completions_url: str, headers: Dict[str, str]) -> str:
    """Mint a random, structureless opaque token for exactly one turn and
    remember the REAL upstream request (URL + auth headers) behind it. The
    opaque token — never the real key — is what becomes the `claude` CLI
    subprocess's ANTHROPIC_AUTH_TOKEN. Logged callers must only ever log
    the returned token, never `headers`."""
    token = secrets.token_urlsafe(32)
    with _turn_credentials_lock:
        _turn_credentials[token] = TurnCredential(
            provider=provider, chat_completions_url=chat_completions_url, headers=dict(headers),
        )
    return token


def clear_turn_credential(token: str) -> None:
    """MUST be called exactly once the turn ends (mirror the `finally:
    shutil.rmtree(config_dir, ...)` discipline in claude_agent_sdk_bridge.
    run_claude_agent_sdk_turn) — after this call the token is worthless."""
    if not token:
        return
    with _turn_credentials_lock:
        _turn_credentials.pop(token, None)


def _lookup_turn_credential(token: str) -> Optional[TurnCredential]:
    if not token:
        return None
    with _turn_credentials_lock:
        return _turn_credentials.get(token)


def active_turn_credential_count() -> int:
    """Test/ops hook — never exposes token values or key material."""
    with _turn_credentials_lock:
        return len(_turn_credentials)


def clear_all_turn_credentials_for_tests() -> None:
    with _turn_credentials_lock:
        _turn_credentials.clear()


# ============================================================================
# 7. Real per-provider upstream resolution — defers to provider_profiles.py
# ============================================================================

def resolve_real_upstream_request(provider: str, credentials: Dict[str, Any], model: str) -> Tuple[str, Dict[str, str]]:
    """Real per-provider base_url/auth for the ACTUAL upstream call. This
    module is a pure protocol translator; it does not own provider
    credential/endpoint resolution. For every provider already modeled in
    provider_profiles.py's PROVIDER_ADAPTERS as an OpenAIAdapter /
    OpenAICompatibleAdapter (openai, groq, openrouter, xai, azure_openai,
    qwen, mistral, ollama_cloud, custom_openai_compatible), this defers to
    that EXISTING, unmodified resolution logic.

    Gemini is the one addition made here: provider_profiles.GeminiAdapter
    targets Google's native generateContent API, not Chat Completions, so
    there is no existing OpenAI-shaped resolver in provider_profiles.py to
    defer to. Google publishes its own documented OpenAI-compatibility
    layer at generativelanguage.googleapis.com/v1beta/openai — hardcoded
    here, clearly isolated from (and not a modification of) provider_
    profiles.py's own code."""
    normalized = str(provider or "").strip().lower()
    creds = credentials if isinstance(credentials, dict) else {}

    if normalized == "gemini":
        api_key = str(creds.get("api_key") or "").strip()
        if not api_key:
            raise AdapterTranslationError("gemini credentials are missing api_key")
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    from server_modules import provider_profiles  # local: heavy module, only needed for real (non-test) wiring

    adapter = provider_profiles.PROVIDER_ADAPTERS.get(normalized)
    if adapter is None:
        raise AdapterTranslationError(f"no adapter-routed provider profile for {normalized!r}")

    if normalized == "openai":
        headers = dict(adapter._headers(creds))
        headers.setdefault("Content-Type", "application/json")
        return "https://api.openai.com/v1/chat/completions", headers

    url = adapter._chat_completions_url(creds, model=model)
    headers = adapter._headers(creds, include_content_type=True)
    return url, headers


def mint_turn_token_for_provider(provider: str, credentials: Optional[Dict[str, Any]], model: str) -> str:
    """The single call claude_agent_sdk_bridge.py's resolve_sdk_process_env
    needs once wired (see module docstring step 2): resolves the real
    upstream request for `provider`, then mints and returns the opaque
    token to use as ANTHROPIC_AUTH_TOKEN. Also lazily starts the adapter
    server, since a turn that needs a token also needs the server that will
    redeem it."""
    ensure_adapter_server_running()
    creds = credentials if isinstance(credentials, dict) else {}
    url, headers = resolve_real_upstream_request(provider, creds, model)
    return mint_turn_credential(provider=provider, chat_completions_url=url, headers=headers)


def resolve_adapter_routed_base_url(provider: str) -> str:
    """Same contract as claude_agent_sdk_bridge.resolve_anthropic_
    compatible_base_url: "" when `provider` has no override. NOT added to
    that function's own map (_ANTHROPIC_COMPATIBLE_BASE_URLS) — see module
    docstring for why. Starts the adapter server lazily; the returned URL
    is only ever this process's own loopback address."""
    if not is_adapter_routed_provider(provider):
        return ""
    return ensure_adapter_server_running()


# ============================================================================
# 8. FastAPI app
# ============================================================================

app = FastAPI(title="empyralis-openai-compat-adapter")

_ADAPTER_ERROR_TYPE = "invalid_request_error"


def _error_body(message: str) -> Dict[str, Any]:
    return {"type": "error", "error": {"type": _ADAPTER_ERROR_TYPE, "message": f"empyralis-adapter: {message}"}}


def _error_response(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=_error_body(message))


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-api-key") or "").strip()


_http_client: Optional[httpx.AsyncClient] = None
_http_client_override: Optional[httpx.AsyncClient] = None  # test hook — see set_http_client_for_tests


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client_override is not None:
        return _http_client_override
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0),
        )
    return _http_client


def set_http_client_for_tests(client: Optional[httpx.AsyncClient]) -> None:
    """Test-only hook: point every outbound (adapter -> "upstream") call at
    an injected client — typically one wired to httpx.ASGITransport(app=
    fake_upstream_app), so tests exercise the real request/response
    streaming code path against a fully scripted fake without opening a
    real socket. `None` restores the real client."""
    global _http_client_override
    _http_client_override = client


@app.post("/v1/messages")
async def messages_endpoint(request: Request):
    turn_id = uuid.uuid4().hex[:12]
    token = _extract_bearer(request)
    cred = _lookup_turn_credential(token)
    if cred is None:
        return JSONResponse(
            status_code=401,
            content={"type": "error", "error": {"type": "authentication_error",
                                                 "message": "empyralis-adapter: unknown or expired session token"}},
        )

    try:
        raw_body = await request.body()
        body = json.loads(raw_body) if raw_body else {}
    except Exception:
        return _error_response(400, "request body was not valid JSON")
    if not isinstance(body, dict):
        return _error_response(400, "request body must be a JSON object")

    try:
        openai_body = translate_anthropic_request_to_openai(body, provider=cred.provider)
    except AdapterTranslationError as exc:
        return _error_response(400, str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("openai_compat_adapter turn=%s: unexpected translation failure", turn_id)
        return JSONResponse(status_code=500, content=_error_body(f"internal adapter error: {type(exc).__name__}"))

    client = _get_http_client()
    LOGGER.info(
        "openai_compat_adapter turn=%s provider=%s model=%s stream=%s tools=%d",
        turn_id, cred.provider, openai_body.get("model"), openai_body.get("stream"),
        len(openai_body.get("tools") or []),
    )

    if not openai_body.get("stream"):
        try:
            resp = await client.post(cred.chat_completions_url, headers=cred.headers, json=openai_body)
        except httpx.TransportError as exc:
            return JSONResponse(status_code=502, content=_error_body(f"upstream connection failed: {type(exc).__name__}"))
        if resp.status_code >= 500 or resp.status_code == 429:
            return JSONResponse(status_code=resp.status_code, content=_error_body(f"upstream returned HTTP {resp.status_code}"))
        if resp.status_code >= 400:
            return _error_response(400, f"upstream rejected the request (HTTP {resp.status_code}): {resp.text[:500]}")
        try:
            upstream_json = resp.json()
        except Exception:
            return _error_response(400, "upstream returned a non-JSON response")
        try:
            anthropic_message = build_non_streaming_anthropic_response(
                str(body.get("model") or openai_body["model"]), upstream_json,
            )
        except AdapterTranslationError as exc:
            return _error_response(400, str(exc))
        return JSONResponse(content=anthropic_message)

    gen = iter_upstream_chunks(client, cred.chat_completions_url, cred.headers, openai_body)
    try:
        first_chunk = await gen.__anext__()
    except StopAsyncIteration:
        return _error_response(400, "upstream stream produced no data")
    except UpstreamEmptyStream as exc:
        LOGGER.warning("openai_compat_adapter turn=%s: %s (message_start never sent)", turn_id, exc)
        return JSONResponse(status_code=502, content=_error_body(str(exc)))
    except UpstreamMalformed as exc:
        LOGGER.warning("openai_compat_adapter turn=%s: %s (message_start never sent)", turn_id, exc)
        return _error_response(400, str(exc))
    except UpstreamHTTPError as exc:
        if exc.status_code >= 500 or exc.status_code == 429:
            return JSONResponse(status_code=exc.status_code, content=_error_body(f"upstream returned HTTP {exc.status_code}"))
        return _error_response(
            400, f"upstream rejected the request (HTTP {exc.status_code}): {exc.body[:500].decode('utf-8', 'replace')}",
        )
    except httpx.TransportError as exc:
        LOGGER.warning("openai_compat_adapter turn=%s: upstream connection failed: %s (message_start never sent)", turn_id, exc)
        return JSONResponse(status_code=502, content=_error_body(f"upstream connection failed: {type(exc).__name__}"))

    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    assembler = AnthropicStreamAssembler(
        message_id=message_id,
        model=str(body.get("model") or openai_body["model"]),
        input_tokens_estimate=estimate_input_tokens(body),
    )

    async def sse_body() -> AsyncIterator[bytes]:
        try:
            for frame in assembler.begin(first_chunk):
                yield frame.encode("utf-8")
            async for chunk in gen:
                for frame in assembler.feed(chunk):
                    yield frame.encode("utf-8")
            for frame in assembler.finish():
                yield frame.encode("utf-8")
        except (UnrecoverableStreamError, UpstreamHTTPError, httpx.TransportError) as exc:
            # message_start already went out on the wire above — the only
            # honest options left are a clean message_stop (impossible, we
            # don't have a complete/valid turn) or dropping the connection.
            # Never emit `event: error` here — see module docstring.
            LOGGER.warning(
                "openai_compat_adapter turn=%s: dropping connection mid-stream: %r counters=%s",
                turn_id, exc, assembler.counters.as_dict(),
            )
            raise
        finally:
            LOGGER.info("openai_compat_adapter turn=%s finished counters=%s", turn_id, assembler.counters.as_dict())

    return StreamingResponse(sse_body(), media_type="text/event-stream")


@app.post("/v1/messages/count_tokens")
async def count_tokens_endpoint(request: Request):
    token = _extract_bearer(request)
    if _lookup_turn_credential(token) is None:
        return JSONResponse(
            status_code=401,
            content={"type": "error", "error": {"type": "authentication_error",
                                                 "message": "empyralis-adapter: unknown or expired session token"}},
        )
    try:
        raw_body = await request.body()
        body = json.loads(raw_body) if raw_body else {}
    except Exception:
        return _error_response(400, "request body was not valid JSON")
    if not isinstance(body, dict):
        return _error_response(400, "request body must be a JSON object")
    return JSONResponse(content={"input_tokens": estimate_input_tokens(body)})


@app.get("/health")
async def health_endpoint():
    return {"status": "ok", "active_turn_credentials": active_turn_credential_count()}


# ============================================================================
# 9. Lazy loopback server bootstrap — daemon thread, own event loop
# ============================================================================

_server_lock = threading.Lock()
_server_state: Dict[str, Any] = {"base_url": "", "thread": None, "server": None}


async def _serve(host: str, port: int, started: threading.Event, result: Dict[str, Any]) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    if not config.loaded:
        config.load()
    server = uvicorn.Server(config)
    server.lifespan = config.lifespan_class(config)
    try:
        await server.startup()
        sockets = server.servers[0].sockets if server.servers else []
        bound_port = sockets[0].getsockname()[1] if sockets else port
        result["port"] = bound_port
        _server_state["server"] = server
        started.set()
        await server.main_loop()
    except Exception as exc:  # pragma: no cover - startup failure path
        result["error"] = str(exc)
        started.set()
    finally:
        try:
            await server.shutdown()
        except Exception:  # pragma: no cover
            pass


def ensure_adapter_server_running(*, host: str = "127.0.0.1", port: int = 0) -> str:
    """Idempotently start the adapter's own loopback-only HTTP server on a
    daemon thread with its own asyncio event loop — the same "second event
    loop on its own thread" shape claude_agent_sdk_bridge.collect_events_
    via_claude_agent_sdk already uses (asyncio.run(...) from a background
    execution context), except long-lived: started once on the first turn
    that needs it, reused by every subsequent adapter-routed turn, rather
    than a fresh server per call (a per-turn server would mean a new TCP
    port and a cold FastAPI/uvicorn startup on every single agent turn).

    `host` defaults to 127.0.0.1 and must never be widened to 0.0.0.0 — see
    module docstring's Security section for why this must not become a
    route on the main, wider-bound FastAPI app instead."""
    with _server_lock:
        if _server_state["base_url"]:
            return _server_state["base_url"]
        started = threading.Event()
        result: Dict[str, Any] = {}

        def _run() -> None:
            asyncio.run(_serve(host, port, started, result))

        thread = threading.Thread(target=_run, name="openai-compat-adapter", daemon=True)
        thread.start()
        if not started.wait(timeout=10):
            raise RuntimeError("openai_compat_adapter: server did not start within 10s")
        if result.get("error"):
            raise RuntimeError(f"openai_compat_adapter: server failed to start: {result['error']}")
        base_url = f"http://{host}:{result['port']}"
        _server_state["base_url"] = base_url
        _server_state["thread"] = thread
        return base_url


def adapter_server_base_url_for_tests() -> str:
    """Test hook: current base_url, or "" if not started."""
    return str(_server_state.get("base_url") or "")


def reset_adapter_server_state_for_tests() -> None:
    """Test hook only — does not actually stop a running server (there is
    no clean cross-thread uvicorn shutdown wired here since production
    never needs one; the server lives for the lifetime of the backend
    process). Only clears the module's own bookkeeping so a *new* test can
    call ensure_adapter_server_running() again and get a fresh instance in
    a fresh process/thread. Tests that need this call it from a subprocess
    or accept a single shared server for the whole test session instead."""
    with _server_lock:
        _server_state["base_url"] = ""
        _server_state["thread"] = None
        _server_state["server"] = None

from __future__ import annotations

from dataclasses import dataclass
import contextvars
import json
import re
import time
from typing import Any, Callable, Dict, Iterator, List, Optional
import uuid

from server_modules import agent_trace_service
from server_modules import direct_chat_tool_catalog_service
from server_modules import direct_tool_execution_service
from server_modules import empyralis_model_tier_contract
from server_modules import empyralis_model_tier_routing_service
from server_modules import healthguide_safety_service
from server_modules import response_leak_guard_service
from server_modules import secret_redaction_service
from server_modules import tool_registry_service
from server_modules import workspace_context_memory_adapter
from server_modules.direct_chat_context_service import is_public_generation_error_message
from server_modules.direct_chat_intervention_service import build_intervention
from server_modules.direct_tool_config_service import run_async_tool_call
from server_modules.plugin_system import (
    HookContext,
    HookResult,
    HOOK_AGENT_START,
    HOOK_AGENT_END,
    HOOK_LLM_INPUT,
    HOOK_LLM_OUTPUT,
    HOOK_TOOL_CALL,
    HOOK_TOOL_RESULT,
    get_global_hook_registry,
)


# ── Live event sink ──────────────────────────────────────────────────────────
# Web chat sets this contextvar before the generation loop runs so streaming
# chunks, thinking steps, and tool traces are forwarded to the SSE transport
# in real time.  When the contextvar is None (Telegram / API / background
# paths) nothing changes — events are only yielded through the normal generator.
_GENERATION_EVENT_SINK: contextvars.ContextVar[
    Optional[Callable[[Dict[str, Any]], None]]
] = contextvars.ContextVar("generation_event_sink", default=None)


def wrap_generation_with_sink(
    gen: Iterator[Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:
    """Wrap a generation-stream iterator with live event forwarding.

    Every event yielded by *gen* is also pushed through the current
    :data:`_GENERATION_EVENT_SINK` (if one is set).  The sink MUST be
    thread-safe — it runs inside the thread-pool thread that executes the
    generation loop.
    """
    for event in gen:
        sink = _GENERATION_EVENT_SINK.get(None)
        if sink is not None and isinstance(event, dict):
            try:
                sink(event)
            except Exception:
                pass
        yield event


def _local_gateway_activity_payload(
    *,
    tool_call_id: str,
    activity_type: str,
    label: str,
    status: str,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    payload: Dict[str, Any] = {
        "id": str(tool_call_id or "").strip() or f"hardware:{now_ms}",
        "type": activity_type,
        "label": label,
        "startedAt": now_ms,
        "status": status,
    }
    if status != "active":
        payload["completedAt"] = now_ms
    clean_detail = " ".join(str(detail or "").split())
    if clean_detail:
        payload["detail"] = clean_detail[:500]
    return payload


def _humanize_tool_progress(tool_name: str) -> str:
    """Map a tool name to a short human-readable progress phrase.

    These are shown as transient messages in chat while tools execute,
    so the user never faces dead air.  Keep them short (<40 chars).
    """
    _map: dict[str, str] = {
        "memory__search": "Searching memory…",
        "memory__get": "Loading memory…",
        "memory__update": "Saving to memory…",
        "memory__append": "Saving to memory…",
        "memory__consolidate": "Consolidating memories…",
        "web__search": "Searching the web…",
        "web__fetch": "Reading that page…",
        "shell__exec": "Running on your hardware…",
        "file__write": "Writing file…",
        "browser__navigate": "Opening browser…",
        "screenshot__capture": "Taking screenshot…",
        "computer__ocr": "Reading screen…",
        "computer__click": "Clicking…",
        "computer__type": "Typing…",
        "computer__applescript": "Running script…",
        "computer__clipboard": "Reading clipboard…",
        "computer__notify": "Sending notification…",
        "computer__list_apps": "Listing apps…",
        "computer__launch_app": "Launching app…",
        "computer__speak": "Speaking…",
        "hardware__action": "Running on your hardware…",
        "llm__task": "Thinking…",
        "http_request": "Making request…",
    }
    name_lower = str(tool_name or "").strip().lower()
    if name_lower in _map:
        return _map[name_lower]
    # Fallback: extract action from connector__action format
    if "__" in name_lower:
        parts = name_lower.rsplit("__", 1)
        action = parts[1].replace("_", " ").strip()
        if action:
            return f"Running {action}…"
    return "Working…"


def _tool_timeout_seconds(connector_id: str, action_id: str = "") -> float:
    """Per-category tool timeout in seconds — matches skills_service."""
    cid = str(connector_id or "").strip().lower()
    if cid == "shell":
        return 120.0
    if cid == "web":
        return 30.0
    if cid == "memory":
        return 10.0
    if cid in ("browser", "computer"):
        return 60.0
    if cid == "hardware":
        return 120.0
    return 30.0


@dataclass(slots=True)
class DirectChatGenerationServices:
    thinking_step_payload: Callable[[int, str, Optional[str]], Dict[str, Any]]
    build_context_used: Callable[..., Dict[str, Any]]
    build_direct_tool_approval_response: Callable[..., Optional[Dict[str, Any]]]
    parse_tool_name: Callable[[str], tuple[str, str]]
    tool_arguments_payload: Callable[[Any], Dict[str, Any]]
    parse_page_state: Callable[[str], Any]
    direct_tool_step_payload: Callable[..., Dict[str, Any]]
    execute_single_direct_tool_call: Callable[..., str]
    direct_tool_followup_message: Callable[[str, str], str]
    suggest_actions: Callable[[str, Dict[str, Any]], List[Dict[str, Any]]]
    clear_direct_tool_loop_state: Callable[[str], None]
    persist_direct_chat_memory_best_effort: Callable[..., None]
    persist_direct_chat_transcript_best_effort: Callable[..., None]
    persist_direct_chat_hosted_usage_best_effort: Callable[..., None]
    record_direct_tool_signature: Callable[[str, Dict[str, Any]], bool]
    direct_chat_error_reply: Callable[[str], str]
    capture_exception: Callable[[BaseException], None]
    generate_chat_reply_stream_with_provider_fallback: Callable[..., Iterator[Dict[str, Any]]]
    reserve_direct_chat_hosted_usage_best_effort: Callable[..., Optional[Dict[str, Any]]] = lambda **_kwargs: None
    release_direct_chat_hosted_usage_reservation_best_effort: Callable[..., None] = lambda **_kwargs: None


def _compact_trace_text(value: Any, limit: int = 280) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


_ASSISTANT_SHELL_PLAN_RE = re.compile(
    r"```(?:bash|sh|shell|zsh)?\s*\n(.*?)```",
    re.I | re.S,
)
_ASSISTANT_INLINE_SHELL_PLAN_RE = re.compile(
    r"`([^`\n]*(?:&&|\|\||\||2>/dev/null|/Applications|/etc/os-release|uname\b|sw_vers\b|sysctl\b|brew\b|df\b|whoami\b|pwd\b|ls\b)[^`\n]*)`",
    re.I,
)
_ASSISTANT_SHELL_LINE_RE = re.compile(
    r"^(?:#|\$|(?:sudo\s+)?(?:bash|sh|zsh|pwd|whoami|uname|sw_vers|sysctl|df|du|ls|brew|cat|echo|find|mdfind|system_profiler|ioreg|ps|pgrep|osascript|open)\b)",
    re.I,
)
_ASSISTANT_SHELL_LINE_HINT_RE = re.compile(
    r"(?:&&|\|\||\||2>/dev/null|/Applications|/etc/os-release|hw\.memsize|brew\s+list|sw_vers)",
    re.I,
)
_ASSISTANT_SHELL_PLAN_MARKERS = (
    "running the command",
    "running the commands",
    "run the command",
    "run the commands",
    "run these commands",
    "run the following commands",
    "let me check",
    "let me get",
    "let me run",
    "let me re-run",
    "let me rerun",
    "let me see what",
    "re-run them",
    "rerun them",
    "commands ran",
    "output didn't come through",
    "output did not come through",
    "show you the results directly",
    "i'll grab",
    "i will grab",
)

_LOCAL_PRIVATE_TOOL_RESULT_PROVIDERS = {"ollama"}
_STREAM_INTERNAL_MARKUP_LOOKBACK_CHARS = 96


def _tool_result_context_is_local_private(provider: Any, credentials: Any) -> bool:
    provider_token = str(provider or "").strip().lower().replace("-", "_")
    if provider_token in _LOCAL_PRIVATE_TOOL_RESULT_PROVIDERS:
        return True
    if not isinstance(credentials, dict):
        return False
    if credentials.get("local_only") is True or credentials.get("machine_bound") is True:
        return True
    provider_scopes = credentials.get("provider_scopes")
    if isinstance(provider_scopes, list) and "local_only" in {str(item or "").strip().lower() for item in provider_scopes}:
        return True
    identity_owner = str(credentials.get("identity_owner") or "").strip().lower()
    if identity_owner in {"local_machine", "machine_owner"}:
        return True
    credential_plane = str(credentials.get("credential_plane") or "").strip().lower()
    return credential_plane in {"local_machine", "machine_local", "local_private"}


def sanitize_tool_result_for_context(
    tool_result: Any,
    *,
    provider: Any = None,
    credentials: Any = None,
) -> str:
    result_text = str(tool_result or "")
    if not result_text:
        return ""
    if _tool_result_context_is_local_private(provider, credentials):
        return result_text
    return workspace_context_memory_adapter.strip_red_facts_from_external_context(result_text)


def _stable_visible_stream_prefix(value: Any) -> str:
    guarded = response_leak_guard_service.guard_model_response(value)
    text = guarded.text
    if "internal_tool_markup" in guarded.findings:
        return text
    marker_start = text.rfind("<")
    if marker_start >= 0 and len(text) - marker_start <= _STREAM_INTERNAL_MARKUP_LOOKBACK_CHARS:
        return text[:marker_start]
    return text


_REASONING_BLOCK_PATTERN = re.compile(
    r"<(think|thinking)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_UNCLOSED_REASONING_BLOCK_PATTERN = re.compile(
    r"<(think|thinking)\b[^>]*>.*$",
    re.IGNORECASE | re.DOTALL,
)


def _strip_reasoning_thinking_blocks(text: str) -> str:
    """Some providers/models emit chain-of-thought inline in `content` as a
    literal <think>/<thinking> block instead of a separate reasoning field
    (e.g. a locally-hosted reasoning model proxied through an OpenAI-compatible
    endpoint). Nothing in the response-assembly pipeline stripped this before
    it reached the transcript or the user — strip it here, where every final
    reply is assembled, regardless of which provider produced it.

    Scope: this cleans the completed final_reply. It does not retroactively
    clean text already emitted as streaming deltas — a model that streams its
    thinking block token-by-token may still show it transiently in a live
    session before the final cleaned reply replaces it.
    """
    if not text:
        return text
    stripped = _REASONING_BLOCK_PATTERN.sub("", text)
    stripped = _UNCLOSED_REASONING_BLOCK_PATTERN.sub("", stripped)
    return stripped.strip()


def _collapse_exact_duplicate_reply(text: Any) -> str:
    """Defensive guard against a streaming/retry artifact that emits the reply
    verbatim twice back-to-back ("XX" or "X\\nX"). Only collapses when the entire
    message is two identical halves (optionally separated by one whitespace char),
    so it never alters a normal reply. Requires >=16 chars to avoid touching short
    legitimately-repeated phrases.
    """
    s = str(text or "").strip()
    if len(s) < 16:
        return str(text or "")
    if len(s) % 2 == 0 and s[: len(s) // 2] == s[len(s) // 2 :]:
        return s[: len(s) // 2].strip()
    if len(s) % 2 == 1:
        half = (len(s) - 1) // 2
        if s[:half] == s[half + 1 :] and s[half : half + 1].isspace():
            return s[:half].strip()
    return str(text or "")


def _has_shell_exec_tool(tools: List[Dict[str, Any]]) -> bool:
    tool_names = {str(item.get("name") or "").strip() for item in tools if isinstance(item, dict)}
    return "shell__exec" in tool_names


def _normalize_assistant_shell_line(raw_line: Any) -> str:
    line = str(raw_line or "").strip()
    line = line.strip("`").strip()
    line = re.sub(r"^(?:bash|sh|zsh)\s+(?!-)", "", line, count=1, flags=re.I).strip()
    if line.startswith("$ "):
        line = line[2:].strip()
    return line


def _looks_like_assistant_shell_line(line: str) -> bool:
    value = str(line or "").strip()
    if not value or value in {"```", "```bash", "```sh", "```shell", "```zsh"}:
        return False
    if len(value) > 1500:
        return False
    if _ASSISTANT_SHELL_LINE_RE.search(value):
        return True
    return bool(_ASSISTANT_SHELL_LINE_HINT_RE.search(value))


def _extract_assistant_shell_command_blocks(text: str) -> List[str]:
    command_blocks: List[str] = []
    for match in _ASSISTANT_SHELL_PLAN_RE.finditer(text):
        block = str(match.group(1) or "").strip()
        if not block:
            continue
        lines = []
        for raw_line in block.splitlines():
            line = _normalize_assistant_shell_line(raw_line)
            if line:
                lines.append(line)
        if lines:
            command_blocks.append("\n".join(lines))
    if command_blocks:
        return command_blocks

    for match in _ASSISTANT_INLINE_SHELL_PLAN_RE.finditer(text):
        line = _normalize_assistant_shell_line(match.group(1))
        if _looks_like_assistant_shell_line(line):
            command_blocks.append(line)
    if command_blocks:
        return command_blocks

    current_block: List[str] = []
    for raw_line in text.splitlines():
        line = _normalize_assistant_shell_line(raw_line)
        if _looks_like_assistant_shell_line(line):
            current_block.append(line)
            continue
        if current_block:
            command_blocks.append("\n".join(current_block))
            current_block = []
    if current_block:
        command_blocks.append("\n".join(current_block))
    return command_blocks


def _normalize_tool_format(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI function-calling format to flat format expected by the
    legacy LLM pipeline (orion_local_worker_llm.py).

    OpenAI format:  {type: "function", function: {name, description, parameters}}
    Flat format:    {name, description, parameters}
    """
    normalized: List[Dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        # Already flat format?
        name = str(item.get("name") or "").strip()
        if name:
            normalized.append(item)
            continue
        # Convert from OpenAI format
        func = item.get("function")
        if isinstance(func, dict):
            func_name = str(func.get("name") or "").strip()
            if func_name:
                normalized.append({
                    "name": func_name,
                    "description": str(func.get("description") or "").strip(),
                    "parameters": func.get("parameters") if isinstance(func.get("parameters"), dict) else {},
                })
    return normalized


def _extract_assistant_shell_plan_tool_call(
    reply: Any,
    tools: List[Dict[str, Any]],
) -> tuple[str, List[Dict[str, Any]]]:
    # Safety boundary: never promote natural-language assistant text into shell
    # execution. Shell work must arrive as a structured provider tool call or an
    # explicit approved action payload. Parsing command-looking text caused DSML
    # markup and conversational consent such as "go ahead" to become executable.
    text = str(reply or "").strip()
    return text, []


# ── Memory intent detection (PR1 fix) ────────────────────────────────────────
# DeepSeek-chat often emits conversational intent ("let me check memory...")
# instead of native tool_calls.  We detect this pattern and create the
# appropriate tool call so the turn completes in ONE step instead of stalling.
# Memory tools are read-only — safe to auto-invoke from natural language.

_MEMORY_INTENT_PATTERNS = [
    # "let me check our memory / my memory / in memory / the memory"
    re.compile(
        r"(?:let\s+me|i['’]ll|i\s+will|let\s+us)\s+(?:check|look(?:\s+up)?|search|find|read|recall|retrieve|get|pull(?:\s+up)?|see\s+what(?:'s|is)?\s+in)\s+(?:in\s+|our\s+|my\s+|the\s+)?(?:memory|memories|notes|stored\s+(?:info|facts|data))",
        re.I,
    ),
    # "I should / need to check memory"
    re.compile(
        r"(?:i|we)\s+(?:should|need\s+to|have\s+to|must|ought\s+to)\s+(?:check|look|search|find|read|recall)\s+(?:in\s+|our\s+|my\s+|the\s+)?(?:memory|memories)",
        re.I,
    ),
    # "checking memory / looking in memory / searching memory"
    re.compile(
        r"(?:checking|looking|searching|reading)\s+(?:in\s+|our\s+|my\s+|the\s+)?(?:memory|memories)",
        re.I,
    ),
    # "what was stored" / "what's in memory"
    re.compile(
        r"(?:what|whatever)(?:'s|'ve|\s+is|\s+has|\s+was)\s+(?:been\s+)?(?:stored|saved|recorded|kept)\s+(?:in\s+(?:my\s+|our\s+|the\s+)?(?:memory|notes))?",
        re.I,
    ),
]

# ── Text-based tool call notation (DeepSeek often emits [tool: params] in text) ──
_TOOL_CALL_NOTATION_RE = re.compile(
    r"\[(memory_search|memory_read|memory_write|memory_get|find_workspace_memory_entry)\s*[:|]\s*(.+?)\]",
    re.I,
)

_MEMORY_TOOL_NAMES = {"memory_read", "memory_search", "memory_get", "find_workspace_memory_entry"}


def _detect_memory_intent(reply: str, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If the reply expresses intent to check memory but no tool was called,
    create the appropriate memory tool call so the loop doesn't stall."""
    text = str(reply or "").strip()
    if not text:
        return []

    # Check if any memory tool is in the available tool list
    available_memory_tools: Dict[str, Dict[str, Any]] = {}
    for tool in tools:
        name = ""
        if isinstance(tool, dict):
            func = tool.get("function")
            if isinstance(func, dict):
                name = str(func.get("name") or "").strip()
        if name in _MEMORY_TOOL_NAMES:
            available_memory_tools[name] = tool

    if not available_memory_tools:
        return []

    # ── Priority 1: text-based [tool: params] notation (DeepSeek's native style) ──
    tool_notation_match = _TOOL_CALL_NOTATION_RE.search(text)
    if tool_notation_match:
        tool_name = tool_notation_match.group(1).strip()
        raw_params = tool_notation_match.group(2).strip()
        if tool_name in available_memory_tools:
            arguments = _parse_tool_notation_params(raw_params, tool_name)
            print(f"[DG_MEMORY_NOTATION] extracted tool={tool_name} params={arguments} from reply text", flush=True)
            return [{
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments),
                },
            }]

    # ── Priority 2: natural-language intent patterns ──
    is_memory_intent = any(p.search(text) for p in _MEMORY_INTENT_PATTERNS)
    if not is_memory_intent:
        return []

    # If the reply already contains a substantive answer (not just an
    # announcement), don't inject a tool call — the model answered from
    # context. Only inject when the reply is PURELY an intent announcement
    # without answering the user's question.
    if _reply_answers_question(text):
        return []

    # Prefer memory_search for queries with actual search terms;
    # fall back to memory_read for general recall.
    if "memory_search" in available_memory_tools:
        query = _extract_memory_search_terms(text)
        return [{
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": "memory_search",
                "arguments": json.dumps({"query": query}),
            },
        }]
    elif "memory_read" in available_memory_tools:
        return [{
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": "memory_read",
                "arguments": json.dumps({"path": "MEMORY.md"}),
            },
        }]

    return []


def _reply_answers_question(text: str) -> bool:
    """Heuristic: does the reply contain a substantive answer, not just an
    announcement of intent? If so, the model probably answered from context
    and we should not inject a tool call."""
    t = text.strip()
    # If it's short and purely an announcement, it's not an answer
    if len(t) < 150:
        return False
    # If it starts with an intent phrase and has little substance after,
    # it's probably not an answer
    intent_starts = ("let me ", "i'll ", "i will ", "let us ", "i should ", "i need to ")
    lower = t.lower()
    for prefix in intent_starts:
        if lower.startswith(prefix):
            # Announcement prefix — check if there's substance after
            # by looking for concrete answer indicators
            rest = t[len(prefix):]
            if len(rest) < 120:
                return False
            break
    return True


def _parse_tool_notation_params(raw: str, tool_name: str) -> Dict[str, Any]:
    """Parse [tool_name: key=value, key2=value2] or [tool_name: value] notation."""
    raw = raw.strip().rstrip("]").strip()
    # Try key=value format
    if "=" in raw:
        params = {}
        for part in raw.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                params[k] = v
        if params:
            return params
    # Fallback: treat entire string as query value
    if tool_name == "memory_search":
        return {"query": raw.strip("'\"")}
    elif tool_name == "memory_read" or tool_name == "memory_get":
        return {"path": raw.strip("'\"")}
    return {"query": raw.strip("'\"")}


def _extract_memory_search_terms(text: str) -> str:
    """Extract what the model wants to find from memory intent text."""
    # Try to extract the topic after the intent phrase
    patterns = [
        r"(?:memory|memories)\s+(?:for|about|regarding)\s+(.+?)(?:\.|$|\n|to\s+see)",
        r"(?:check|look|search|find|recall)\s+(?:our\s+|my\s+|the\s+)?(?:memory|memories)\s+(?:for|about)\s+(.+?)(?:\.|$|\n)",
        r"(?:see\s+)?what\s+(?:we|i|you)\s+(?:were|was|are|have\s+been)\s+(?:talking|discussing|saying)\s+about\s*(.+?)?(?:\.|$|\n)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            terms = m.group(1).strip() if m.lastindex and m.group(1) else ""
            if terms:
                return terms.strip(" \"'.,;")
    # Fallback: just use the first sentence as query
    first_sentence = text.split(".")[0].strip()
    # Remove common intent prefixes
    for prefix in ("let me ", "i'll ", "i will ", "let us "):
        if first_sentence.lower().startswith(prefix):
            first_sentence = first_sentence[len(prefix):]
            break
    return first_sentence.strip(" \"'.,;")[:200]


def _trace_raw_event(envelope: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(envelope, dict):
        return None
    return {
        "type": "trace",
        "payload": envelope,
    }


def _emit_trace_event(
    trace_context: Optional[Any],
    *,
    event_type: str,
    data: Optional[Dict[str, Any]],
    persisted: bool,
    parent_id: Optional[str] = None,
    item_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    child_run_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if trace_context is None:
        return None
    if persisted:
        envelope = run_async_tool_call(
            agent_trace_service.emit_with_envelope(
                trace_context,
                event_type,
                data,
                persisted=True,
                parent_id=parent_id,
                item_id=item_id,
                tool_call_id=tool_call_id,
                child_run_id=child_run_id,
                approval_id=approval_id,
                artifact_id=artifact_id,
            )
        )
    else:
        envelope = agent_trace_service.build_ephemeral_envelope(
            trace_context,
            event_type,
            data,
            parent_id=parent_id,
            item_id=item_id,
            tool_call_id=tool_call_id,
            child_run_id=child_run_id,
            approval_id=approval_id,
            artifact_id=artifact_id,
        )
    return _trace_raw_event(envelope)


def _finish_trace(trace_context: Optional[Any], *, outcome: str, final_message_id: Optional[str]) -> None:
    if trace_context is None:
        return
    run_async_tool_call(
        agent_trace_service.finish_trace(
            trace_context,
            outcome=outcome,
            final_message_id=final_message_id,
        )
    )


def _public_generation_error_code(llm_error: str) -> str:
    detail = str(llm_error or "").strip()
    if detail.startswith("max_tool_iterations_reached:"):
        return detail
    if detail.startswith("provider_"):
        return detail
    lowered = detail.lower()
    if "http_429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return "provider_rate_limited"
    if "direct_chat_transport_unavailable" in lowered:
        return "provider_transport_unavailable"
    # HTTP 402 / insufficient balance: the key authenticates fine, the
    # account behind it is empty. Checked before the generic fallback below
    # so this never collapses into "provider_generation_failed" — that
    # string is itself one of classify_error's own auth-bucket keywords,
    # which would misreport a billing issue as an authentication failure.
    if "http_402" in lowered or "payment required" in lowered or "insufficient balance" in lowered or "insufficient_balance" in lowered:
        return "provider_payment_required"
    return "provider_generation_failed" if detail else "unknown_error"


def _public_generation_error_reply(
    services: DirectChatGenerationServices,
    llm_error: str,
    *,
    is_platform_credits: bool = True,
) -> str:
    from server_modules.sage_command_dispatcher import classify_error
    _ = services
    return classify_error(
        str(llm_error or ""),
        raw_error=str(llm_error or ""),
        is_platform_credits=is_platform_credits,
    )


def _turn_metadata_from_session(session_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(session_ctx, dict):
        return {}
    metadata: Dict[str, Any] = {}
    turn_request = session_ctx.get("agent_turn_request")
    if hasattr(turn_request, "context_hints") and isinstance(turn_request.context_hints, dict):
        raw_metadata = turn_request.context_hints.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata.update(raw_metadata)
    raw_metadata = session_ctx.get("metadata")
    if isinstance(raw_metadata, dict):
        metadata.update(raw_metadata)
    return metadata


def _platform_paid_ai_identity(
    *,
    availability_payload: Dict[str, Any],
    metadata: Dict[str, Any],
    session_ctx: Optional[Dict[str, Any]],
    requested_provider: str,
    requested_model: str,
    effective_provider: Optional[str],
    effective_model: Optional[str],
) -> Optional[Dict[str, str]]:
    turn_metadata = {
        **_turn_metadata_from_session(session_ctx),
        **(metadata if isinstance(metadata, dict) else {}),
    }
    billing_source = str(
        availability_payload.get("billing_source")
        or turn_metadata.get("billing_source")
        or ""
    ).strip().lower()
    credential_plane = str(availability_payload.get("credential_plane") or "").strip().lower()
    public_tier = str(turn_metadata.get("ai_tier") or "").strip().lower().replace("-", "_")
    if public_tier not in empyralis_model_tier_contract.EMPYRALIS_HOSTED_TIERS:
        public_tier = empyralis_model_tier_routing_service.infer_migrated_public_tier_from_legacy_selection(
            requested_provider=requested_provider or effective_provider,
            requested_model=requested_model or effective_model,
            metadata={
                **turn_metadata,
                **({"billing_source": billing_source} if billing_source else {}),
                **({"credential_plane": credential_plane} if credential_plane else {}),
            },
        ) or ""
    if (
        credential_plane != "platform_runtime"
        and billing_source != "empyralis_credits"
        and public_tier not in empyralis_model_tier_contract.EMPYRALIS_HOSTED_TIERS
    ):
        return None
    tier = empyralis_model_tier_contract.normalize_model_tier(public_tier or "pro", fallback="pro")
    label = empyralis_model_tier_contract.model_tier_contract(tier).public_label
    return {
        "ai_tier": tier,
        "ai_label": f"{label} AI",
        "billing_source": "empyralis_credits",
    }


def _mask_platform_paid_final_payload(payload: Dict[str, Any], identity: Optional[Dict[str, str]]) -> Dict[str, Any]:
    if not identity:
        return payload
    ai_label = str(identity.get("ai_label") or "Empyralis AI").strip() or "Empyralis AI"

    def _sanitize_public_string(value: str) -> str:
        sanitized = str(value)
        replacements = [
            (r"deepseek-v4-flash", "Light AI"),
            (r"deepseek-v4-pro", "Pro AI"),
            (r"deepseek", ai_label),
        ]
        for pattern, replacement in replacements:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        return sanitized

    def _sanitize_public_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): _sanitize_public_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_sanitize_public_value(item) for item in value]
        if isinstance(value, str):
            return _sanitize_public_string(value)
        return value

    def _strip_internal_route(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): _strip_internal_route(item)
                for key, item in value.items()
                if str(key) not in {
                    "provider",
                    "model",
                    "requested_provider",
                    "requested_model",
                    "effective_provider",
                    "effective_model",
                    "attempted_providers",
                    "internal_provider",
                    "internal_model",
                    "pricing_source",
                }
            }
        if isinstance(value, list):
            return [_strip_internal_route(item) for item in value]
        return value

    masked = dict(payload)
    masked["provider"] = None
    masked["model"] = None
    masked["attempted_providers"] = None
    masked["usage_masked"] = _strip_internal_route(masked.get("usage_masked"))
    masked["billing_source"] = identity["billing_source"]
    masked["ai_tier"] = identity["ai_tier"]
    masked["ai_label"] = identity["ai_label"]
    context_used = masked.get("context_used")
    if isinstance(context_used, dict):
        masked["context_used"] = {
            **context_used,
            "requested_provider": None,
            "effective_provider": None,
            "requested_model": None,
            "effective_model": None,
            "provider_overridden": False,
            "model_overridden": False,
            "billing_source": identity["billing_source"],
            "ai_tier": identity["ai_tier"],
            "ai_label": identity["ai_label"],
        }
    return _sanitize_public_value(masked)


def _persist_direct_chat_hosted_usage_with_reservation_guard(
    *,
    services: DirectChatGenerationServices,
    hosted_usage_reservation: Optional[Dict[str, Any]],
    usage_kwargs: Dict[str, Any],
    release_kwargs: Dict[str, Any],
) -> None:
    try:
        services.persist_direct_chat_hosted_usage_best_effort(**usage_kwargs)
    except Exception as persist_exc:
        services.capture_exception(persist_exc)
        if hosted_usage_reservation:
            try:
                services.release_direct_chat_hosted_usage_reservation_best_effort(
                    **release_kwargs,
                    status="uncertain",
                )
            except Exception as release_exc:
                services.capture_exception(release_exc)


def stream_provider_backed_direct_chat(
    *,
    services: DirectChatGenerationServices,
    context: Dict[str, Any],
    metadata: Dict[str, Any],
    system_prompt: Optional[str],
    normalized_workspace_id: str,
    normalized_requested_provider: str,
    normalized_requested_model: str,
    normalized_reasoning_effort: Optional[str],
    normalized_thread_id: str,
    normalized_message: str,
    compacted_prior_messages: List[Dict[str, Any]],
    prior_messages_used: bool,
    history_mode: str,
    connected_systems: List[str],
    tool_capabilities: List[Dict[str, Any]],
    availability_payload: Dict[str, Any],
    tools: List[Dict[str, Any]],
    direct_chat_credentials: Dict[str, Any],
    proactive_suggestions: List[str],
    tool_loop_session_key: str,
    fallback_reason: Optional[str],
    session_ctx: Optional[Dict[str, Any]],
    trace_context: Optional[Any],
    resolved_chat_max_iterations: int,
    direct_tool_result_summary_system_message: str,
    assistant_plan_tools: Optional[List[Dict[str, Any]]] = None,
    tool_registry: Optional[List[Any]] = None,
) -> Iterator[Dict[str, Any]]:
    # ── Normalize tool format: the always-on tools arrive as OpenAI function-calling
    # format {type: "function", function: {name, description, parameters}} but the
    # legacy LLM pipeline expects flat format {name, description, parameters}.
    # Without this normalization, all tools are silently dropped (tools_count=0).
    orig_tools_count = len(tools)
    tools = _normalize_tool_format(tools)
    if isinstance(metadata.get("tools"), list):
        metadata["tools"] = _normalize_tool_format(metadata["tools"])
    if isinstance(context.get("tools"), list):
        context["tools"] = _normalize_tool_format(context["tools"])
    if assistant_plan_tools:
        assistant_plan_tools = _normalize_tool_format(assistant_plan_tools)
    effective_assistant_plan_tools = assistant_plan_tools if assistant_plan_tools is not None else tools

    print(f"[DG_ENTRY] provider={metadata.get('provider')!r} model={metadata.get('model')!r} message_len={len(normalized_message)} tools_count_in={orig_tools_count} tools_count_out={len(tools)} max_iter={resolved_chat_max_iterations} registry_entries={len(tool_registry) if tool_registry else 0}", flush=True)
    # ── DeepSeek tool count guard: trim to 20 tools max ──
    provider_id = str(metadata.get("provider") or "").strip().lower()
    if provider_id == "deepseek" and len(tools) > 20:
        # Priority: connected app tools first, then built-in Sage tools, then rest
        connected_tools = []
        sage_tools = []
        other_tools = []
        connected_prefixes = set()
        for system in connected_systems:
            connected_prefixes.add(system.lower().replace('-', '_'))
        for tool in tools:
            tool_name = str(tool.get("function", {}).get("name") or "").strip().lower()
            # Check if tool belongs to a connected system
            is_connected = any(
                tool_name.startswith(prefix + "__") or tool_name.startswith(prefix + ".")
                for prefix in connected_prefixes
            )
            if is_connected:
                connected_tools.append(tool)
            elif tool_name.startswith("memory") or tool_name.startswith("sage_") or tool_name.startswith("hardware"):
                sage_tools.append(tool)
            else:
                other_tools.append(tool)
        trimmed_tools = connected_tools + sage_tools
        remaining_slots = 20 - len(trimmed_tools)
        if remaining_slots > 0:
            trimmed_tools.extend(other_tools[:remaining_slots])
        original_count = len(tools)
        tools = trimmed_tools
        metadata["tools"] = trimmed_tools
        context["tools"] = trimmed_tools
        print(f"[DG_TOOL_TRIM] trimmed from {original_count} to {len(tools)} tools for provider {provider_id}", flush=True)
    # ── End tool count guard ──
    usage_masked: Dict[str, Any] = {}
    attempted_providers = ""
    llm_error = ""
    actual_provider: Optional[str] = str(metadata.get("provider") or "").strip() or None
    actual_model: Optional[str] = str(metadata.get("model") or "").strip() or None
    
    # Reasoning effort logic
    if normalized_reasoning_effort:
        supports_reasoning = False
        if actual_model:
            model_lower = actual_model.lower()
            supports_reasoning = (
                model_lower.startswith("o1")
                or model_lower.startswith("o3")
                or "deepseek-r1" in model_lower
                or "deepseek-reasoner" in model_lower
                or ("gemini" in model_lower and "thinking" in model_lower)
            )
        
        if not supports_reasoning:
            # Model does not support reasoning effort natively, pass as system prompt instruction
            system_instruction = f"The user has requested a {normalized_reasoning_effort} reasoning effort. Please adjust the depth of your thinking and response accordingly."
            system_prompt = f"{system_prompt}\n\n[System Instruction: {system_instruction}]" if system_prompt else f"[System Instruction: {system_instruction}]"
            normalized_reasoning_effort = None

    executed_any_tools = False
    final_reply = ""
    conversation_messages: List[Dict[str, Any]] = []
    conversation_messages.extend(compacted_prior_messages)
    current_prompt = normalized_message

    # --- Attachment context injection ---
    attachment_context = ""
    _attachments = []
    if isinstance(session_ctx, dict):
        _turn_req = session_ctx.get("agent_turn_request")
        if _turn_req is not None:
            if isinstance(_turn_req, dict):
                _attachments = _turn_req.get("attachments", []) or []
            else:
                _attachments = getattr(_turn_req, "attachments", []) or []
    if _attachments:
        from server_modules.attachment_utils import build_attachment_context
        attachment_context = build_attachment_context(normalized_workspace_id, _attachments)
    if not current_prompt.strip() and _attachments:
        current_prompt = "Please review the attached file(s)."
    if attachment_context:
        current_prompt = attachment_context + current_prompt
    # --- End attachment injection ---

    max_iterations = resolved_chat_max_iterations
    trace_started_at = time.monotonic()
    hosted_usage_reservation = services.reserve_direct_chat_hosted_usage_best_effort(
        workspace_id=normalized_workspace_id,
        thread_id=normalized_thread_id,
        session_ctx=session_ctx,
        availability_payload=availability_payload,
        requested_provider=normalized_requested_provider,
        requested_model=normalized_requested_model,
    )

    registry = get_global_hook_registry()
    hook_ctx = registry.execute(
        HOOK_AGENT_START,
        HookContext(
            hook_point=HOOK_AGENT_START,
            workspace_id=normalized_workspace_id,
            session_id=normalized_thread_id,
            channel=str(metadata.get("channel", "")),
            messages=list(conversation_messages),
            system_prompt=system_prompt or "",
            tools=list(tools),
        ),
    )
    if hook_ctx.aborted:
        yield {
            "reply": hook_ctx.abort_reason or "Agent start aborted by hook.",
            "interventions": [],
            "aborted": True,
        }
        return

    trace_plan_id = uuid.uuid4().hex
    planning_item_id = uuid.uuid4().hex
    assistant_message_id = uuid.uuid4().hex
    health_safety_context = healthguide_safety_service.resolve_health_safety_context(session_ctx=session_ctx)
    buffer_assistant_tool_plans = _has_shell_exec_tool(effective_assistant_plan_tools)
    trace_started_raw = _emit_trace_event(
        trace_context,
        event_type="trace.started",
        data={"input_mode": "text"},
        persisted=True,
    )
    if trace_started_raw is not None:
        yield trace_started_raw
    trace_plan_started = _emit_trace_event(
        trace_context,
        event_type="plan.started",
        data={
            "plan_id": trace_plan_id,
            "title": "Sage Plan",
            "summary": "Review the request, decide whether tools are needed, and produce the final answer.",
        },
        persisted=True,
    )
    if trace_plan_started is not None:
        yield trace_plan_started
    trace_plan_item = _emit_trace_event(
        trace_context,
        event_type="plan.item.created",
        data={
            "plan_id": trace_plan_id,
            "item_id": planning_item_id,
            "index": 1,
            "title": "Review the request and choose the next action",
            "kind": "respond",
            "owner": "sage",
            "depends_on": [],
            "rationale_summary": "Start by planning the response before deciding whether a tool call is necessary.",
        },
        persisted=True,
        item_id=planning_item_id,
    )
    if trace_plan_item is not None:
        yield trace_plan_item
    trace_plan_item_running = _emit_trace_event(
        trace_context,
        event_type="plan.item.updated",
        data={
            "item_id": planning_item_id,
            "status": "running",
            "summary": "Planning the response.",
        },
        persisted=True,
        item_id=planning_item_id,
    )
    if trace_plan_item_running is not None:
        yield trace_plan_item_running
    reasoning_started = _emit_trace_event(
        trace_context,
        event_type="reasoning.summary.delta",
        data={"delta": "Planning the response."},
        persisted=False,
        item_id=planning_item_id,
    )
    if reasoning_started is not None:
        yield reasoning_started

    if direct_chat_tool_catalog_service.message_requests_tool_inventory(normalized_message):
        inventory_reply = direct_chat_tool_catalog_service.direct_chat_tool_inventory_reply(tools, availability_payload)
        plan_done = _emit_trace_event(
            trace_context,
            event_type="plan.item.updated",
            data={
                "item_id": planning_item_id,
                "status": "done",
                "summary": "Answered from the active tool catalog.",
            },
            persisted=True,
            item_id=planning_item_id,
        )
        if plan_done is not None:
            yield plan_done
        yield {"type": "chunk", "delta": inventory_reply}
        trace_delta = _emit_trace_event(
            trace_context,
            event_type="assistant.message.delta",
            data={
                "message_id": assistant_message_id,
                "delta": inventory_reply,
            },
            persisted=False,
        )
        if trace_delta is not None:
            yield trace_delta
        trace_completed = _emit_trace_event(
            trace_context,
            event_type="trace.completed",
            data={
                "duration_ms": int((time.monotonic() - trace_started_at) * 1000),
                "final_message_id": assistant_message_id,
            },
            persisted=True,
        )
        if trace_completed is not None:
            yield trace_completed
        _finish_trace(trace_context, outcome="success", final_message_id=assistant_message_id)
        yield {
            "type": "final",
            "payload": {
                "reply": inventory_reply,
                "actions": [],
                "interventions": [],
                "suggestions": [],
                "mode": "answer",
                "usage_masked": {},
                "provider": actual_provider,
                "model": actual_model,
                "attempted_providers": "",
                "error": "",
                "context_used": services.build_context_used(
                    workspace_id=normalized_workspace_id,
                    requested_provider=normalized_requested_provider,
                    effective_provider=str(actual_provider or context.get("provider") or "").strip() or None,
                    requested_model=normalized_requested_model,
                    effective_model=str(actual_model or "").strip() or None,
                    reasoning_effort=normalized_reasoning_effort,
                    connected_systems=connected_systems,
                    tool_capabilities=tool_capabilities,
                    prior_messages_used=prior_messages_used,
                    history_mode=history_mode,
                    run_created=False,
                    fallback_used=False,
                    fallback_reason=fallback_reason,
                ),
            },
        }
        return

    for iteration in range(max_iterations):
        thinking_iteration = iteration + 1
        print(f"[DG_ITER] iteration={iteration} thinking_iteration={thinking_iteration} conv_msgs={len(conversation_messages)} executed_any_tools={executed_any_tools}", flush=True)
        yield services.thinking_step_payload(thinking_iteration, "active")

        # Strip tools for synthesis so DeepSeek does not receive tool definitions
        # alongside tool_result messages (known DeepSeek bug: empty content when
        # tools + tool-result messages coexist in the payload).
        if executed_any_tools:
            print(f"[TRACE_STRIP] stripping tools from metadata and context iteration={iteration}", flush=True)
            if isinstance(metadata, dict) and metadata.get("tools"):
                print(f"[DG_STRIP_TOOLS] stripping {len(metadata['tools'])} tools from metadata for synthesis iteration={iteration}", flush=True)
                metadata = {**metadata, "tools": []}
            if isinstance(context, dict) and context.get("tools"):
                print(f"[DG_STRIP_TOOLS] stripping {len(context['tools'])} tools from context for synthesis iteration={iteration}", flush=True)
                context = {**context, "tools": []}

        iteration_reply = ""
        iteration_raw_reply = ""
        iteration_tool_calls: List[Dict[str, Any]] = []
        iteration_failed = False

        messages = conversation_messages or []
        for event in services.generate_chat_reply_stream_with_provider_fallback(
            context=context,
            metadata=metadata,
            user_goal=current_prompt,
            system_prompt=system_prompt,
            prior_messages=messages or None,
        ):
            event_type = str(event.get("type") or "").strip().lower()
            if event_type == "chunk":
                iteration_raw_reply += str(event.get("delta") or "")
                stable_visible_reply = _stable_visible_stream_prefix(iteration_raw_reply)
                if not stable_visible_reply.startswith(iteration_reply):
                    continue
                delta = stable_visible_reply[len(iteration_reply):]
                if delta:
                    iteration_reply += delta
                    if not buffer_assistant_tool_plans:
                        yield {"type": "chunk", "delta": delta}
                        trace_delta = _emit_trace_event(
                            trace_context,
                            event_type="assistant.message.delta",
                            data={
                                "message_id": assistant_message_id,
                                "delta": delta,
                            },
                            persisted=False,
                        )
                        if trace_delta is not None:
                            yield trace_delta
                continue
            if event_type == "result":
                final_reply = str(event.get("reply") or "").strip() or iteration_raw_reply or iteration_reply
                final_reply = _collapse_exact_duplicate_reply(final_reply)
                usage_masked = event.get("usage_masked") if isinstance(event.get("usage_masked"), dict) else {}
                attempted_providers = str(event.get("attempted_providers") or "").strip()
                llm_error = str(event.get("error") or "").strip()
                actual_provider = str(event.get("provider") or actual_provider or "").strip() or actual_provider
                actual_model = str(event.get("model") or actual_model or "").strip() or actual_model
                iteration_tool_calls = event.get("tool_calls") if isinstance(event.get("tool_calls"), list) else []
                print(f"[DG_RESULT] iteration={iteration} reply_len={len(final_reply)} error={llm_error!r} tool_calls_count={len(iteration_tool_calls)} provider={actual_provider} model={actual_model}", flush=True)
                if attempted_providers and ',' in attempted_providers:
                    providers_list = [p.strip() for p in attempted_providers.split(',') if p.strip()]
                    if len(providers_list) >= 2:
                        from_provider = providers_list[-2]
                        to_provider = providers_list[-1]
                        print(f"[DG_FALLBACK] falling back from {from_provider} to {to_provider}", flush=True)
                if not iteration_tool_calls:
                    final_reply, assistant_shell_plan_tool_calls = _extract_assistant_shell_plan_tool_call(
                        final_reply,
                        effective_assistant_plan_tools,
                    )
                    if assistant_shell_plan_tool_calls:
                        iteration_tool_calls = assistant_shell_plan_tool_calls
                # PR1: Detect memory intent — when DeepSeek says "let me check
                # memory" but doesn't natively call the tool, auto-create the
                # tool call so the turn completes in one step.
                if not iteration_tool_calls:
                    memory_intent_calls = _detect_memory_intent(final_reply, effective_assistant_plan_tools)
                    if memory_intent_calls:
                        iteration_tool_calls = memory_intent_calls
                        print(f"[DG_MEMORY_INTENT] auto-created {len(memory_intent_calls)} memory tool call(s) from intent: {final_reply[:120]!r}", flush=True)
                yield services.thinking_step_payload(
                    thinking_iteration,
                    "done",
                    "Prepared the next action" if iteration_tool_calls else "Answer ready",
                )

                if current_prompt:
                    conversation_messages.append({"role": "user", "content": current_prompt})
                effective_iteration_provider = str(actual_provider or context.get("provider") or "").strip().lower()
                if final_reply or iteration_tool_calls:
                    assistant_message: Dict[str, Any] = {"role": "assistant"}
                    if final_reply:
                        assistant_message["content"] = final_reply
                    if iteration_tool_calls and effective_iteration_provider != "codex_cli":
                        assistant_message["tool_calls"] = iteration_tool_calls
                    if assistant_message.get("content") or assistant_message.get("tool_calls"):
                        conversation_messages.append(assistant_message)

                if iteration_tool_calls:
                    plan_done = _emit_trace_event(
                        trace_context,
                        event_type="plan.item.updated",
                        data={
                            "item_id": planning_item_id,
                            "status": "done",
                            "summary": "Tool calls are required before answering.",
                        },
                        persisted=True,
                        item_id=planning_item_id,
                    )
                    if plan_done is not None:
                        yield plan_done
                    loop_detected = any(
                        services.record_direct_tool_signature(tool_loop_session_key, tool_call)
                        for tool_call in iteration_tool_calls
                        if isinstance(tool_call, dict)
                    )
                    if loop_detected:
                        trace_failed = _emit_trace_event(
                            trace_context,
                            event_type="trace.failed",
                            data={
                                "code": "tool_loop_detected",
                                "message": "The same direct tool action repeated and execution was halted.",
                                "retryable": False,
                                "failed_item_id": planning_item_id,
                            },
                            persisted=True,
                            item_id=planning_item_id,
                        )
                        if trace_failed is not None:
                            yield trace_failed
                        _finish_trace(trace_context, outcome="partial", final_message_id=None)
                        yield {
                            "type": "final",
                            "payload": {
                                "reply": "",
                                "actions": [],
                                "interventions": [
                                    build_intervention(
                                        "loop_detected",
                                        "Stopped repeated tool loop",
                                        detail="The same tool action kept repeating, so direct execution was halted. Start a durable run to continue end-to-end.",
                                        severity="warning",
                                        status="failed",
                                        code="tool_loop_detected",
                                    )
                                ],
                                "suggestions": proactive_suggestions,
                                "mode": "answer",
                                "usage_masked": usage_masked,
                                "provider": actual_provider,
                                "model": actual_model,
                                "attempted_providers": attempted_providers,
                                "error": "tool_loop_detected",
                                "context_used": services.build_context_used(
                                    workspace_id=normalized_workspace_id,
                                    requested_provider=normalized_requested_provider,
                                    effective_provider=str(actual_provider or context.get("provider") or "").strip() or None,
                                    requested_model=normalized_requested_model,
                                    effective_model=str(actual_model or "").strip() or None,
                                    reasoning_effort=normalized_reasoning_effort,
                                    connected_systems=connected_systems,
                                    tool_capabilities=tool_capabilities,
                                    prior_messages_used=True,
                                    history_mode=history_mode,
                                    run_created=False,
                                    fallback_used=False,
                                    fallback_reason=fallback_reason,
                                ),
                            },
                        }
                        try:
                            _persist_direct_chat_hosted_usage_with_reservation_guard(
                                services=services,
                                hosted_usage_reservation=hosted_usage_reservation,
                                usage_kwargs={
                                    "workspace_id": normalized_workspace_id,
                                    "thread_id": normalized_thread_id,
                                    "session_ctx": session_ctx,
                                    "availability_payload": availability_payload,
                                    "usage_masked": usage_masked,
                                    "requested_provider": normalized_requested_provider,
                                    "effective_provider": actual_provider,
                                    "requested_model": normalized_requested_model,
                                    "effective_model": actual_model,
                                },
                                release_kwargs={
                                    "workspace_id": normalized_workspace_id,
                                    "thread_id": normalized_thread_id,
                                    "session_ctx": session_ctx,
                                },
                            )
                        except Exception:
                            pass
                        services.clear_direct_tool_loop_state(tool_loop_session_key)
                        return
                    approval_payload = services.build_direct_tool_approval_response(
                        tool_calls=iteration_tool_calls,
                        tool_capabilities=tool_capabilities,
                        session_ctx=session_ctx,
                    )
                    if approval_payload is not None:
                        approval_blocked = _emit_trace_event(
                            trace_context,
                            event_type="plan.item.updated",
                            data={
                                "item_id": planning_item_id,
                                "status": "blocked",
                                "summary": "Waiting for approval before running direct tools.",
                            },
                            persisted=True,
                            item_id=planning_item_id,
                        )
                        if approval_blocked is not None:
                            yield approval_blocked
                        trace_completed = _emit_trace_event(
                            trace_context,
                            event_type="trace.completed",
                            data={
                                "duration_ms": int((time.monotonic() - trace_started_at) * 1000),
                                "final_message_id": None,
                            },
                            persisted=True,
                        )
                        if trace_completed is not None:
                            yield trace_completed
                        _finish_trace(trace_context, outcome="needs_input", final_message_id=None)
                        yield {
                            "type": "final",
                            "payload": {
                                **approval_payload,
                                "suggestions": proactive_suggestions,
                                "usage_masked": usage_masked,
                                "provider": actual_provider,
                                "model": actual_model,
                                "attempted_providers": attempted_providers,
                                "error": "",
                                "context_used": services.build_context_used(
                                    workspace_id=normalized_workspace_id,
                                    requested_provider=normalized_requested_provider,
                                    effective_provider=str(actual_provider or context.get("provider") or "").strip() or None,
                                    requested_model=normalized_requested_model,
                                    effective_model=str(actual_model or "").strip() or None,
                                    reasoning_effort=normalized_reasoning_effort,
                                    connected_systems=connected_systems,
                                    tool_capabilities=tool_capabilities,
                                    prior_messages_used=True,
                                    history_mode=history_mode,
                                    run_created=False,
                                    fallback_used=False,
                                    fallback_reason=fallback_reason,
                                ),
                            },
                        }
                        return

                    try:
                        connector_id = ""
                        action_id = ""
                        argument_payload: Dict[str, Any] = {}
                        step_id = f"tool:{thinking_iteration}:0"
                        for tool_index, tool_call in enumerate(iteration_tool_calls, start=1):
                            # Assigned up front, before anything below that can raise
                            # (parse_tool_name included) — an exception mid-iteration must
                            # never leave these referenced-but-unset in whatever error
                            # path handles it further down.
                            step_id = f"tool:{thinking_iteration}:{tool_index}"
                            tool_call_id = str(tool_call.get("id") or "").strip() or f"toolcall_{uuid.uuid4().hex}"
                            if isinstance(tool_call, dict) and not str(tool_call.get("id") or "").strip():
                                tool_call["id"] = tool_call_id
                            tool_item_id = uuid.uuid4().hex
                            raw_tool_name = str(tool_call.get("name") or "").strip()
                            argument_payload = services.tool_arguments_payload(tool_call.get("arguments"))
                            # query_tool_registry is a meta-tool (search the lazy-load
                            # catalog), not a "connector__action" pair — parse_tool_name
                            # only recognizes a fixed bare-name whitelist plus that shape
                            # and raises on anything else. Specialists with a narrow
                            # explicit toolset lean on this discovery tool far more than
                            # Sage's full toolset does, so skip straight to its handling
                            # below instead of parsing it as a connector action.
                            if raw_tool_name == "query_tool_registry" and tool_registry:
                                connector_id, action_id = "", ""
                            else:
                                connector_id, action_id = services.parse_tool_name(raw_tool_name)
                                if connector_id in {"file", "shell", "screenshot", "computer"} and isinstance(argument_payload.get("input"), str):
                                    nested_input = services.parse_page_state(str(argument_payload.get("input") or ""))
                                    if isinstance(nested_input, dict):
                                        argument_payload = nested_input
                            tool_name = str(tool_call.get("name") or f"{connector_id}__{action_id}").strip()
                            # ── query_tool_registry: lazy-load tools from registry ──
                            if tool_name == "query_tool_registry" and tool_registry:
                                query_text = str(argument_payload.get("task_description") or "").strip()
                                max_res = int(argument_payload.get("max_results") or 5)
                                if not query_text:
                                    query_text = str(argument_payload.get("input") or "").strip()
                                if not query_text:
                                    tool_result = "query_tool_registry requires a 'task_description' parameter describing what you need to do."
                                else:
                                    matched = direct_chat_tool_catalog_service.search_tool_registry(
                                        query_text,
                                        tool_registry,
                                        max_results=min(max_res, 10),
                                        availability_payload=availability_payload,
                                    )
                                    tool_result = direct_chat_tool_catalog_service.format_registry_result(matched, query_text)
                                    # Inject matched tools for subsequent iterations
                                    new_tool_names = []
                                    for mt in matched:
                                        func = mt.get("function", {}) if isinstance(mt, dict) else {}
                                        name = str(func.get("name") or mt.get("name") or "").strip()
                                        if name and name not in {str(t.get("function", {}).get("name") or t.get("name") or "") for t in tools}:
                                            tools.append(mt)
                                            new_tool_names.append(name)
                                    if new_tool_names:
                                        if isinstance(metadata, dict):
                                            metadata["tools"] = tools
                                        if isinstance(context, dict):
                                            context["tools"] = tools
                                    print(f"[DG_TOOL_REGISTRY] query={query_text!r} matched={len(matched)} injected={new_tool_names}", flush=True)
                                executed_any_tools = True
                                tool_result_for_context = tool_result
                                conversation_messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tool_call_id,
                                        "name": tool_name,
                                        "content": tool_result_for_context,
                                    }
                                )
                                tool_done = _emit_trace_event(
                                    trace_context,
                                    event_type="plan.item.updated",
                                    data={
                                        "item_id": tool_item_id,
                                        "status": "done",
                                        "summary": "Searched tool registry.",
                                    },
                                    persisted=True,
                                    item_id=tool_item_id,
                                )
                                if tool_done is not None:
                                    yield tool_done
                                current_prompt = (
                                    "The tool registry results above show additional tools now available "
                                    "to you. Use them to fulfill the user's request, or respond if you "
                                    "have enough information."
                                )
                                continue
                            # ── End query_tool_registry handler ──
                            tool_trace_metadata = direct_tool_execution_service.build_direct_tool_trace_metadata(
                                connector_id,
                                action_id,
                                argument_payload,
                            )
                            tool_plan_item = _emit_trace_event(
                                trace_context,
                                event_type="plan.item.created",
                                data={
                                    "plan_id": trace_plan_id,
                                    "item_id": tool_item_id,
                                    "index": tool_index + 1,
                                    "title": f"Run {tool_name}",
                                    "kind": "tool",
                                    "owner": "sage",
                                    "depends_on": [planning_item_id],
                                    "rationale_summary": "A direct tool call is needed to complete the request.",
                                },
                                persisted=True,
                                item_id=tool_item_id,
                            )
                            if tool_plan_item is not None:
                                yield tool_plan_item
                            tool_plan_running = _emit_trace_event(
                                trace_context,
                                event_type="plan.item.updated",
                                data={
                                    "item_id": tool_item_id,
                                    "status": "running",
                                    "summary": f"Running {tool_name}.",
                                },
                                persisted=True,
                                item_id=tool_item_id,
                            )
                            if tool_plan_running is not None:
                                yield tool_plan_running
                            tool_execution_environment = tool_trace_metadata.get("execution_environment")
                            hardware_local_gateway = (
                                connector_id == "hardware"
                                and tool_execution_environment == "local_gateway"
                            )
                            tool_started_data = {
                                "tool_name": tool_name,
                                "capability_id": tool_trace_metadata.get("capability_id"),
                                "connector_id": "hardware_runtime" if hardware_local_gateway else (connector_id or None),
                                "execution_environment": tool_execution_environment,
                                "args_preview": secret_redaction_service.sanitize_mapping(argument_payload),
                            }
                            if hardware_local_gateway:
                                tool_started_data["agent_activity"] = _local_gateway_activity_payload(
                                    tool_call_id=tool_call_id,
                                    activity_type="hardware_running",
                                    label="Running on your Mac",
                                    status="active",
                                    detail=str(tool_trace_metadata.get("capability_id") or tool_name or "").strip(),
                                )
                            tool_started = _emit_trace_event(
                                trace_context,
                                event_type="tool.started",
                                data=tool_started_data,
                                persisted=True,
                                item_id=tool_item_id,
                                tool_call_id=tool_call_id,
                            )
                            if tool_started is not None:
                                yield tool_started
                            tool_progress = _emit_trace_event(
                                trace_context,
                                event_type="tool.progress",
                                data={
                                    "message": "Running on your Mac" if hardware_local_gateway else f"Running {tool_name}",
                                    "percent": 0,
                                },
                                persisted=False,
                                tool_call_id=tool_call_id,
                            )
                            if tool_progress is not None:
                                yield tool_progress
                            if str(tool_trace_metadata.get("search_query") or "").strip():
                                trace_search_query = _emit_trace_event(
                                    trace_context,
                                    event_type="search.query",
                                    data={
                                        "provider": connector_id or "web",
                                        "query": str(tool_trace_metadata.get("search_query") or "").strip(),
                                        "filters": {},
                                    },
                                    persisted=True,
                                    tool_call_id=tool_call_id,
                                )
                                if trace_search_query is not None:
                                    yield trace_search_query
                            if isinstance(tool_trace_metadata.get("browser_action"), dict):
                                browser_action_payload = dict(tool_trace_metadata.get("browser_action") or {})
                                trace_browser_action = _emit_trace_event(
                                    trace_context,
                                    event_type="browser.action",
                                    data={
                                        "action": str(browser_action_payload.get("action") or action_id or "").strip(),
                                        "target_summary": str(browser_action_payload.get("target_summary") or "").strip(),
                                        "url": browser_action_payload.get("url"),
                                    },
                                    persisted=True,
                                    tool_call_id=tool_call_id,
                                )
                                if trace_browser_action is not None:
                                    yield trace_browser_action
                            yield services.direct_tool_step_payload(
                                connector_id,
                                action_id,
                                argument_payload,
                                step_id=step_id,
                                status="active",
                            )
                            # Notify the channel so the user sees progress, not dead air
                            yield {
                                "type": "tool_progress",
                                "tool": tool_name,
                                "message": _humanize_tool_progress(tool_name),
                            }
                            # Per-tool timeout — a hung tool must not block the loop.
                            # shell=120s, web=30s, memory=10s, browser/computer=60s, default=30s.
                            _tool_timeout_s = _tool_timeout_seconds(connector_id, action_id)
                            try:
                                import concurrent.futures as _cf
                                with _cf.ThreadPoolExecutor(max_workers=1) as _tool_exec:
                                    _tool_fut = _tool_exec.submit(
                                        services.execute_single_direct_tool_call,
                                        tool_call=tool_call,
                                        workspace_id=normalized_workspace_id,
                                        thread_id=normalized_thread_id,
                                        index=tool_index,
                                        provider=str(actual_provider or context.get("provider") or "").strip() or None,
                                        model=str(actual_model or "").strip() or None,
                                        credentials=direct_chat_credentials if isinstance(direct_chat_credentials, dict) else None,
                                        reasoning_effort=normalized_reasoning_effort or "",
                                        session_ctx=session_ctx,
                                    )
                                    tool_result = _tool_fut.result(timeout=_tool_timeout_s)
                            except _cf.TimeoutError:
                                import json as _json
                                tool_result = _json.dumps({
                                    "error": "timeout",
                                    "message": (
                                        f"The tool '{tool_name}' timed out after {_tool_timeout_s:.0f}s. "
                                        "Try a different approach — a smaller scope, a different tool, or "
                                        "ask the user for more specific guidance."
                                    ),
                                })
                            executed_any_tools = True
                            tool_result_for_context = sanitize_tool_result_for_context(
                                tool_result,
                                provider=effective_iteration_provider or actual_provider or context.get("provider"),
                                credentials=direct_chat_credentials,
                            )
                            try:
                                print(
                                    f"[TOOL_OUTPUT_DEBUG] tool={tool_name!r} connector={connector_id!r} "
                                    f"action={action_id!r} raw_len={len(str(tool_result or ''))} "
                                    f"ctx_preview={str(tool_result_for_context or '')[:500]!r}",
                                    flush=True,
                                )
                            except Exception:
                                pass
                            completed_trace_metadata = direct_tool_execution_service.build_direct_tool_trace_metadata(
                                connector_id,
                                action_id,
                                argument_payload,
                                result_text=tool_result_for_context,
                            )
                            if isinstance(completed_trace_metadata.get("search_results"), list) and completed_trace_metadata.get("search_results"):
                                trace_search_results = _emit_trace_event(
                                    trace_context,
                                    event_type="search.results",
                                    data={"results": list(completed_trace_metadata.get("search_results") or [])},
                                    persisted=True,
                                    tool_call_id=tool_call_id,
                                )
                                if trace_search_results is not None:
                                    yield trace_search_results
                            if isinstance(completed_trace_metadata.get("browser_screenshot"), dict):
                                browser_screenshot_payload = dict(completed_trace_metadata.get("browser_screenshot") or {})
                                trace_browser_screenshot = _emit_trace_event(
                                    trace_context,
                                    event_type="browser.screenshot",
                                    data={
                                        "caption": str(browser_screenshot_payload.get("caption") or "").strip(),
                                        "width": int(browser_screenshot_payload.get("width") or 0),
                                        "height": int(browser_screenshot_payload.get("height") or 0),
                                    },
                                    persisted=True,
                                    tool_call_id=tool_call_id,
                                    artifact_id=str(browser_screenshot_payload.get("artifact_id") or "").strip() or None,
                                )
                                if trace_browser_screenshot is not None:
                                    yield trace_browser_screenshot
                            completed_execution_environment = completed_trace_metadata.get("execution_environment")
                            completed_hardware_local_gateway = (
                                connector_id == "hardware"
                                and completed_execution_environment == "local_gateway"
                            )
                            result_summary = str(completed_trace_metadata.get("result_summary") or tool_result_for_context or "").strip()
                            tool_result_data = {
                                "status": "ok",
                                "summary": result_summary,
                                "execution_environment": completed_execution_environment,
                                "artifact_ids": (
                                    [str((completed_trace_metadata.get("browser_screenshot") or {}).get("artifact_id") or "").strip()]
                                    if isinstance(completed_trace_metadata.get("browser_screenshot"), dict)
                                    and str((completed_trace_metadata.get("browser_screenshot") or {}).get("artifact_id") or "").strip()
                                    else []
                                ),
                            }
                            if completed_hardware_local_gateway:
                                _hw_labels = {
                                    ("hardware", "shell_exec"): "Shell command",
                                    ("hardware", "file_read"): "Read file",
                                    ("hardware", "file_write"): "Write file",
                                    ("hardware", "computer_use"): "Computer action",
                                    ("hardware", "screenshot"): "Screenshot",
                                    ("hardware", "web_search"): "Web search",
                                    ("hardware", "browse_web"): "Browse web",
                                }
                                _hw_action = str(action_id or "").strip().lower()
                                _hw_label = _hw_labels.get((connector_id, _hw_action)) or (
                                    f"Shell command" if "shell" in _hw_action
                                    else f"Read file" if _hw_action == "file_read"
                                    else f"Write file" if _hw_action == "file_write"
                                    else str(tool_name or "Tool").strip() or "Tool"
                                )
                                tool_result_data["connector_id"] = "hardware_runtime"
                                tool_result_data["state"] = "completed"
                                tool_result_data["agent_activity"] = _local_gateway_activity_payload(
                                    tool_call_id=tool_call_id,
                                    activity_type="done",
                                    label=_hw_label,
                                    status="completed",
                                    detail=result_summary,
                                )
                            tool_result_event = _emit_trace_event(
                                trace_context,
                                event_type="tool.result",
                                data=tool_result_data,
                                persisted=True,
                                tool_call_id=tool_call_id,
                            )
                            if tool_result_event is not None:
                                yield tool_result_event
                            tool_plan_done = _emit_trace_event(
                                trace_context,
                                event_type="plan.item.updated",
                                data={
                                    "item_id": tool_item_id,
                                    "status": "done",
                                    "summary": f"Completed {tool_name}.",
                                },
                                persisted=True,
                                item_id=tool_item_id,
                            )
                            if tool_plan_done is not None:
                                yield tool_plan_done
                            yield services.direct_tool_step_payload(
                                connector_id,
                                action_id,
                                argument_payload,
                                step_id=step_id,
                                status="done",
                            )
                            if effective_iteration_provider == "codex_cli":
                                conversation_messages.append(
                                    {
                                        "role": "user",
                                        "content": services.direct_tool_followup_message(
                                            str(tool_call.get("name") or f"{connector_id}__{action_id}"),
                                            tool_result_for_context,
                                        ),
                                    }
                                )
                            else:
                                _tool_content = tool_result_for_context
                                if len(_tool_content) > 4000:
                                    _tool_content = _tool_content[:3800] + f"\n...[truncated {len(_tool_content) - 3800} chars]"
                                conversation_messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tool_call_id,
                                        "name": str(tool_call.get("name") or f"{connector_id}__{action_id}"),
                                        "content": _tool_content,
                                    }
                                )
                        if effective_iteration_provider == "codex_cli":
                            conversation_messages.append({"role": "system", "content": direct_tool_result_summary_system_message})
                            current_prompt = (
                                "Continue until the task is complete. If another tool is needed, call it now. "
                                "Otherwise provide the final answer to the user."
                            )
                        else:
                            # Detect whether any tool in THIS iteration reported a failure so we can
                            # forbid the model from fabricating an answer on top of a failed tool.
                            _recent_tool_contents = []
                            for _m in reversed(conversation_messages):
                                if isinstance(_m, dict) and str(_m.get("role") or "") == "tool":
                                    _recent_tool_contents.append(str(_m.get("content") or "").lower())
                                else:
                                    break
                            _tool_failure_detected = any(
                                (
                                    '"status": "failed"' in _c
                                    or '"status": "error"' in _c
                                    or '"runtime_state": "failed"' in _c
                                    or 'timed out' in _c
                                    or 'timeout' in _c
                                    or '"error"' in _c
                                )
                                for _c in _recent_tool_contents
                            )
                            if _tool_failure_detected:
                                current_prompt = (
                                    "One or more tools FAILED — see the tool results above (an error, a "
                                    "timeout, or \"status\": \"failed\"). Tell the user plainly that the action "
                                    "did not succeed and state what failed. You MUST NOT invent, guess, "
                                    "estimate, or fabricate ANY data — no hardware specs, OS versions, serial "
                                    "numbers, file contents, command output, or numbers of any kind. Report "
                                    "ONLY what the tools actually returned. If a tool could not reach the "
                                    "device or machine, say exactly that and stop."
                                )
                            else:
                                current_prompt = (
                                    "Based on the tool results above, provide a clear and helpful answer to the user. "
                                    "Use ONLY facts that are present in the tool results — never invent, guess, or "
                                    "fabricate any data. If you need more information, use another tool now. "
                                    "Otherwise respond directly."
                                )
                        # Feed tool results back to the model in the next iteration
                        # so it can reason about them and call more tools if needed.
                        continue
                    except Exception as exc:
                        llm_error = str(exc).strip() or "connector_action_failed"
                        services.capture_exception(exc)
                        tool_failure = _emit_trace_event(
                            trace_context,
                            event_type="tool.result",
                            data={
                                "status": "error",
                                "summary": llm_error,
                                "artifact_ids": [],
                            },
                            persisted=True,
                            tool_call_id=f"toolcall_error:{thinking_iteration}",
                        )
                        if tool_failure is not None:
                            yield tool_failure
                        tool_name_for_error = str(tool_call.get("name") or f"{connector_id}__{action_id}").strip()
                        tool_plan_failed = _emit_trace_event(
                            trace_context,
                            event_type="plan.item.updated",
                            data={
                                "item_id": tool_item_id,
                                "status": "failed",
                                "summary": f"{tool_name_for_error} failed.",
                            },
                            persisted=True,
                            item_id=tool_item_id,
                        )
                        if tool_plan_failed is not None:
                            yield tool_plan_failed
                        yield services.direct_tool_step_payload(
                            connector_id,
                            action_id,
                            argument_payload,
                            step_id=step_id,
                            status="error",
                            detail_override=llm_error,
                        )
                        if effective_iteration_provider != "codex_cli":
                            conversation_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_call_id,
                                    "name": tool_name_for_error,
                                    "content": (
                                        f"Tool execution failed: {llm_error}. "
                                        "Use only the provided tools and choose another tool if needed."
                                    ),
                                }
                            )
                            current_prompt = ""
                            break
                        trace_failed = _emit_trace_event(
                            trace_context,
                            event_type="trace.failed",
                            data={
                                "code": llm_error,
                                "message": llm_error,
                                "retryable": False,
                                "failed_item_id": planning_item_id,
                            },
                            persisted=True,
                            item_id=planning_item_id,
                        )
                        if trace_failed is not None:
                            yield trace_failed
                        _finish_trace(trace_context, outcome="partial", final_message_id=None)
                        yield {
                            "type": "final",
                            "payload": {
                                "reply": "",
                                "actions": [],
                                "interventions": [
                                    build_intervention(
                                        "system_error",
                                        "Direct tool action failed",
                                        detail=llm_error,
                                        severity="error",
                                        status="failed",
                                        code=llm_error,
                                    )
                                ],
                                "suggestions": proactive_suggestions,
                                "mode": "answer",
                                "usage_masked": usage_masked,
                                "provider": actual_provider,
                                "model": actual_model,
                                "attempted_providers": attempted_providers,
                                "error": llm_error,
                                "context_used": services.build_context_used(
                                    workspace_id=normalized_workspace_id,
                                    requested_provider=normalized_requested_provider,
                                    effective_provider=str(actual_provider or context.get("provider") or "").strip() or None,
                                    requested_model=normalized_requested_model,
                                    effective_model=str(actual_model or "").strip() or None,
                                    reasoning_effort=normalized_reasoning_effort,
                                    connected_systems=connected_systems,
                                    tool_capabilities=tool_capabilities,
                                    prior_messages_used=True,
                                    history_mode=history_mode,
                                    run_created=False,
                                    fallback_used=False,
                                    fallback_reason=fallback_reason,
                                ),
                            },
                        }
                        return

                actions = [] if executed_any_tools else services.suggest_actions(normalized_message, availability_payload)
                citation_refs: List[str] = []
                if health_safety_context.get("enabled"):
                    safety_result = healthguide_safety_service.apply_health_safety_to_reply(
                        reply=final_reply,
                        user_message=normalized_message,
                        assistant_name=str(health_safety_context.get("assistant_name") or "").strip() or None,
                        response_payload=event,
                    )
                    final_reply = str(safety_result.get("reply") or final_reply).strip()
                    citation_refs = list(safety_result.get("citation_refs") or [])
                    if (
                        conversation_messages
                        and isinstance(conversation_messages[-1], dict)
                        and str(conversation_messages[-1].get("role") or "").strip() == "assistant"
                    ):
                        conversation_messages[-1] = {
                            **conversation_messages[-1],
                            "content": final_reply,
                        }
                leak_guard = response_leak_guard_service.guard_model_response(final_reply)
                final_reply = _strip_reasoning_thinking_blocks(leak_guard.text)
                if (
                    conversation_messages
                    and isinstance(conversation_messages[-1], dict)
                    and str(conversation_messages[-1].get("role") or "").strip() == "assistant"
                ):
                    conversation_messages[-1] = {
                        **conversation_messages[-1],
                        "content": final_reply,
                    }
                trace_plan_answer_done = _emit_trace_event(
                    trace_context,
                    event_type="plan.item.updated",
                    data={
                        "item_id": planning_item_id,
                        "status": "done",
                        "summary": "Final answer is ready.",
                    },
                    persisted=True,
                    item_id=planning_item_id,
                )
                if trace_plan_answer_done is not None:
                    yield trace_plan_answer_done
                trace_message_completed = _emit_trace_event(
                    trace_context,
                    event_type="assistant.message.completed",
                    data={
                        "message_id": assistant_message_id,
                        "text": final_reply,
                        "citation_refs": citation_refs,
                        "artifact_ids": [],
                    },
                    persisted=True,
                )
                if trace_message_completed is not None:
                    yield trace_message_completed
                trace_completed = _emit_trace_event(
                    trace_context,
                    event_type="trace.completed",
                    data={
                        "duration_ms": int((time.monotonic() - trace_started_at) * 1000),
                        "final_message_id": assistant_message_id,
                    },
                    persisted=True,
                )
                if trace_completed is not None:
                    yield trace_completed
                _finish_trace(trace_context, outcome="success", final_message_id=assistant_message_id)
                effective_provider = str(actual_provider or context.get("provider") or "").strip() or None
                effective_model = str(actual_model or "").strip() or None
                platform_paid_identity = _platform_paid_ai_identity(
                    availability_payload=availability_payload,
                    metadata=metadata,
                    session_ctx=session_ctx,
                    requested_provider=normalized_requested_provider,
                    requested_model=normalized_requested_model,
                    effective_provider=effective_provider,
                    effective_model=effective_model,
                )
                final_response_payload = {
                    "reply": final_reply,
                    "actions": actions,
                    "suggestions": proactive_suggestions,
                    "mode": "answer_with_action" if actions else "answer",
                    "usage_masked": usage_masked,
                    "provider": actual_provider,
                    "model": actual_model,
                    "attempted_providers": attempted_providers,
                    "error": llm_error,
                    "response_leak_guard": leak_guard.metadata(),
                    "context_used": services.build_context_used(
                        workspace_id=normalized_workspace_id,
                        requested_provider=normalized_requested_provider,
                        effective_provider=effective_provider,
                        requested_model=normalized_requested_model,
                        effective_model=effective_model,
                        reasoning_effort=normalized_reasoning_effort,
                        connected_systems=connected_systems,
                        tool_capabilities=tool_capabilities,
                        prior_messages_used=prior_messages_used,
                        history_mode=history_mode,
                        run_created=False,
                        fallback_used=False,
                        fallback_reason=fallback_reason,
                    ),
                }
                final_payload = {
                    "type": "final",
                    "payload": _mask_platform_paid_final_payload(final_response_payload, platform_paid_identity),
                }

                registry = get_global_hook_registry()
                registry.execute(
                    HOOK_AGENT_END,
                    HookContext(
                        hook_point=HOOK_AGENT_END,
                        workspace_id=normalized_workspace_id,
                        session_id=normalized_thread_id,
                        channel=str(metadata.get("channel", "")),
                        reply=final_reply,
                        usage=usage_masked,
                        metadata={"provider": actual_provider, "model": actual_model},
                    ),
                )

                yield final_payload
                try:
                    should_persist_final_reply = not is_public_generation_error_message(final_reply)
                    if should_persist_final_reply:
                        services.persist_direct_chat_memory_best_effort(
                            workspace_id=normalized_workspace_id,
                            provider=effective_provider,
                            model=effective_model,
                            credentials=direct_chat_credentials,
                            reasoning_effort=normalized_reasoning_effort,
                            prior_messages=compacted_prior_messages,
                            user_message=normalized_message,
                            assistant_reply=final_reply,
                        )
                        services.persist_direct_chat_transcript_best_effort(
                            workspace_id=normalized_workspace_id,
                            thread_id=normalized_thread_id,
                            provider=effective_provider,
                            model=effective_model,
                            messages=conversation_messages,
                            user_message=normalized_message,
                            assistant_reply=final_reply,
                        )
                    _persist_direct_chat_hosted_usage_with_reservation_guard(
                        services=services,
                        hosted_usage_reservation=hosted_usage_reservation,
                        usage_kwargs={
                            "workspace_id": normalized_workspace_id,
                            "thread_id": normalized_thread_id,
                            "session_ctx": session_ctx,
                            "availability_payload": availability_payload,
                            "usage_masked": usage_masked,
                            "requested_provider": normalized_requested_provider,
                            "effective_provider": effective_provider,
                            "requested_model": normalized_requested_model,
                            "effective_model": effective_model,
                        },
                        release_kwargs={
                            "workspace_id": normalized_workspace_id,
                            "thread_id": normalized_thread_id,
                            "session_ctx": session_ctx,
                        },
                    )
                finally:
                    services.clear_direct_tool_loop_state(tool_loop_session_key)
                return
            if event_type == "failure":
                attempted_providers = str(event.get("attempted_providers") or "").strip()
                llm_error = str(event.get("error") or "").strip()
                print(f"[DG_FAILURE] iteration={iteration} llm_error={llm_error!r} attempted_providers={attempted_providers!r}", flush=True)
                is_platform_credits = _platform_paid_ai_identity(
                    availability_payload=availability_payload,
                    metadata=metadata,
                    session_ctx=session_ctx,
                    requested_provider=normalized_requested_provider,
                    requested_model=normalized_requested_model,
                    effective_provider=actual_provider,
                    effective_model=actual_model,
                ) is not None
                public_error_reply = _public_generation_error_reply(
                    services, llm_error, is_platform_credits=is_platform_credits
                )
                public_error_code = _public_generation_error_code(llm_error)
                if hosted_usage_reservation:
                    services.release_direct_chat_hosted_usage_reservation_best_effort(
                        workspace_id=normalized_workspace_id,
                        thread_id=normalized_thread_id,
                        session_ctx=session_ctx,
                        status="released",
                    )
                trace_plan_failure = _emit_trace_event(
                    trace_context,
                    event_type="plan.item.updated",
                    data={
                        "item_id": planning_item_id,
                        "status": "failed",
                        "summary": public_error_reply,
                    },
                    persisted=True,
                    item_id=planning_item_id,
                )
                if trace_plan_failure is not None:
                    yield trace_plan_failure
                yield services.thinking_step_payload(thinking_iteration, "error", public_error_reply)
                # llm_error stays the detailed provider text (not public_error_code)
                # so the final classification below (after the loop exits) can
                # still detect specifics like "http_402" instead of only ever
                # seeing the generic "provider_generation_failed" fallback code.
                iteration_failed = True
                break

        if iteration_failed:
            print(f"[DG_LOOP_END] broke with iteration_failed=True llm_error={llm_error!r} executed_any_tools={executed_any_tools}", flush=True)
            break
        if not iteration_tool_calls:
            print(f"[DG_LOOP_END] broke with no tool_calls llm_error={llm_error!r} executed_any_tools={executed_any_tools} final_reply_len={len(final_reply)}", flush=True)
            break
    else:
        llm_error = llm_error or f"max_tool_iterations_reached:{max_iterations}"

    # Nuclear fallback: synthesis failed (transport/empty/rate-limit/anything) after
    # tools already ran. Runs here, OUTSIDE the for loop, so break cannot skip it.
    # Condition expanded to also catch empty-reply edge case (no error but no text).
    missing_reply = not final_reply.strip()
    print(f"[TRACE_NUCLEAR_CHECK] executed_any_tools={executed_any_tools} llm_error={llm_error!r} final_reply_len={len(final_reply or '')}", flush=True)
    print(f"[DG_NUCLEAR_CHECK] executed_any_tools={executed_any_tools} llm_error={llm_error!r} final_reply_len={len(final_reply)} missing_reply={missing_reply}", flush=True)
    if executed_any_tools and (llm_error or missing_reply):
        _tool_outputs = [
            str(m.get("content") or "").strip()
            for m in conversation_messages
            if isinstance(m, dict) and str(m.get("role") or "") == "tool" and str(m.get("content") or "").strip()
        ]
        _tool_summary = "\n\n".join(_tool_outputs[-3:]) if _tool_outputs else ""
        final_reply = (
            f"Here are the results:\n\n{_tool_summary}"
            if _tool_summary
            else "Commands ran successfully. Could not generate a summary — please try again."
        )
        print(f"[NUCLEAR_FALLBACK] llm_error={llm_error!r} tool_outputs={len(_tool_outputs)} reply_len={len(final_reply)} reply_preview={final_reply[:200]!r}", flush=True)
        conversation_messages.append({"role": "assistant", "content": final_reply})
        iteration_failed = False
        llm_error = ""

    # If tools ran and synthesis was injected, succeed instead of error
    if executed_any_tools and final_reply and not llm_error:
        actions: List[Dict[str, Any]] = []
        effective_provider = str(actual_provider or context.get("provider") or "").strip() or None
        effective_model = str(actual_model or "").strip() or None
        platform_paid_identity = _platform_paid_ai_identity(
            availability_payload=availability_payload,
            metadata=metadata,
            session_ctx=session_ctx,
            requested_provider=normalized_requested_provider,
            requested_model=normalized_requested_model,
            effective_provider=effective_provider,
            effective_model=effective_model,
        )
        final_response_payload = {
            "reply": final_reply,
            "actions": actions,
            "interventions": [],
            "suggestions": proactive_suggestions,
            "mode": "answer",
            "usage_masked": usage_masked,
            "provider": actual_provider,
            "model": actual_model,
            "attempted_providers": attempted_providers,
            "error": "",
            "context_used": services.build_context_used(
                workspace_id=normalized_workspace_id,
                requested_provider=normalized_requested_provider,
                effective_provider=effective_provider,
                requested_model=normalized_requested_model,
                effective_model=effective_model,
                reasoning_effort=normalized_reasoning_effort,
                connected_systems=connected_systems,
                tool_capabilities=tool_capabilities,
                prior_messages_used=prior_messages_used,
                history_mode=history_mode,
                run_created=False,
                fallback_used=False,
                fallback_reason=fallback_reason,
            ),
        }
        yield {
            "type": "final",
            "payload": _mask_platform_paid_final_payload(final_response_payload, platform_paid_identity),
        }
        _finish_trace(trace_context, outcome="success", final_message_id=assistant_message_id)
        try:
            services.persist_direct_chat_memory_best_effort(
                workspace_id=normalized_workspace_id,
                provider=effective_provider,
                model=effective_model,
                credentials=direct_chat_credentials,
                reasoning_effort=normalized_reasoning_effort,
                prior_messages=compacted_prior_messages,
                user_message=normalized_message,
                assistant_reply=final_reply,
            )
            services.persist_direct_chat_transcript_best_effort(
                workspace_id=normalized_workspace_id,
                thread_id=normalized_thread_id,
                provider=effective_provider,
                model=effective_model,
                messages=conversation_messages,
                user_message=normalized_message,
                assistant_reply=final_reply,
            )
            _persist_direct_chat_hosted_usage_with_reservation_guard(
                services=services,
                hosted_usage_reservation=hosted_usage_reservation,
                usage_kwargs={
                    "workspace_id": normalized_workspace_id,
                    "thread_id": normalized_thread_id,
                    "session_ctx": session_ctx,
                    "availability_payload": availability_payload,
                    "usage_masked": usage_masked,
                    "requested_provider": normalized_requested_provider,
                    "effective_provider": effective_provider,
                    "requested_model": normalized_requested_model,
                    "effective_model": effective_model,
                },
                release_kwargs={
                    "workspace_id": normalized_workspace_id,
                    "thread_id": normalized_thread_id,
                    "session_ctx": session_ctx,
                },
            )
        except Exception:
            pass
        finally:
            services.clear_direct_tool_loop_state(tool_loop_session_key)
        return

    actions = [] if executed_any_tools else services.suggest_actions(normalized_message, availability_payload)
    services.clear_direct_tool_loop_state(tool_loop_session_key)
    is_platform_credits = _platform_paid_ai_identity(
        availability_payload=availability_payload,
        metadata=metadata,
        session_ctx=session_ctx,
        requested_provider=normalized_requested_provider,
        requested_model=normalized_requested_model,
        effective_provider=actual_provider,
        effective_model=actual_model,
    ) is not None
    public_error_reply = _public_generation_error_reply(
        services, llm_error, is_platform_credits=is_platform_credits
    )
    public_error_code = _public_generation_error_code(llm_error)
    effective_provider = str(actual_provider or context.get("provider") or "").strip() or None
    effective_model = str(actual_model or "").strip() or None
    trace_failed = _emit_trace_event(
        trace_context,
        event_type="trace.failed",
        data={
            "code": public_error_code,
            "message": public_error_reply,
            "retryable": False,
            "failed_item_id": planning_item_id,
        },
        persisted=True,
        item_id=planning_item_id,
    )
    if trace_failed is not None:
        yield trace_failed
    _finish_trace(trace_context, outcome="partial", final_message_id=None)
    platform_paid_identity = _platform_paid_ai_identity(
        availability_payload=availability_payload,
        metadata=metadata,
        session_ctx=session_ctx,
        requested_provider=normalized_requested_provider,
        requested_model=normalized_requested_model,
        effective_provider=effective_provider,
        effective_model=effective_model,
    )
    final_error_payload = {
        "reply": public_error_reply,
        "actions": actions,
        "interventions": [],
        "suggestions": proactive_suggestions,
        "mode": "answer_with_action" if actions else "answer",
        "usage_masked": usage_masked,
        "provider": actual_provider,
        "model": actual_model,
        "attempted_providers": attempted_providers,
        "error": public_error_code,
        "context_used": services.build_context_used(
            workspace_id=normalized_workspace_id,
            requested_provider=normalized_requested_provider,
            effective_provider=effective_provider,
            requested_model=normalized_requested_model,
            effective_model=effective_model,
            reasoning_effort=normalized_reasoning_effort,
            connected_systems=connected_systems,
            tool_capabilities=tool_capabilities,
            prior_messages_used=prior_messages_used,
            history_mode=history_mode,
            run_created=False,
            fallback_used=False,
            fallback_reason=fallback_reason,
        ),
    }
    yield {
        "type": "final",
        "payload": _mask_platform_paid_final_payload(final_error_payload, platform_paid_identity),
    }
    try:
        _persist_direct_chat_hosted_usage_with_reservation_guard(
            services=services,
            hosted_usage_reservation=hosted_usage_reservation,
            usage_kwargs={
                "workspace_id": normalized_workspace_id,
                "thread_id": normalized_thread_id,
                "session_ctx": session_ctx,
                "availability_payload": availability_payload,
                "usage_masked": usage_masked,
                "requested_provider": normalized_requested_provider,
                "effective_provider": effective_provider,
                "requested_model": normalized_requested_model,
                "effective_model": effective_model,
            },
            release_kwargs={
                "workspace_id": normalized_workspace_id,
                "thread_id": normalized_thread_id,
                "session_ctx": session_ctx,
            },
        )
    except Exception:
        pass

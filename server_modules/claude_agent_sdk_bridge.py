"""MAN-310: Claude Agent SDK bridge — a second, selectable turn engine.

Empyralis stops maintaining its own agent harness (compaction, subagents,
skills, tool loop) and instead runs the Claude Agent SDK underneath its own
UI, as a second engine alongside the existing one. The existing runtime
(direct_chat_generation_service.stream_provider_backed_direct_chat, driven
from server_modules/sage_agent_runtime_service.py's _collect_stream_events)
stays in place, unmodified, and keeps serving the cheap platform-credit
tier. This module is purely additive: nothing outside the explicit
per-turn engine flag at that seam ever imports or calls it, so a deployment
that never sets the flag runs byte-for-byte as it did before this file
existed.

Prior art: spikes/man-310-claude-agent-sdk/ (branch
worktree-agent-ab0972dff6480d82d, commit b3958aea2) verified claude-agent-sdk
0.2.130 installs cleanly against this repo's requirements.txt with zero
version bumps to shared deps, and documented the SDK's message/event
dataclass shapes via both live introspection and the real parser fed
synthetic wire input. Re-run in this worktree with the same result before
this module was written — see that spike's inspect_event_shapes.py.

Two things this module is NOT:
  - Not a tool reimplementation. Every tool call is dispatched through
    Empyralis's EXISTING executor (generation_services.execute_single_direct_
    tool_call — the exact bound callable direct_chat_generation_service.py's
    own tool loop already calls), reached via an in-process SDK MCP server
    (claude_agent_sdk.create_sdk_mcp_server + @tool — no subprocess/IPC
    overhead). Governance (specialist tool-binding guard, broker decisions),
    secret redaction (secret_redaction_service), billing attribution, and
    tool_result_status classification all run exactly where they do today.
    This module only translates between the SDK's message vocabulary and
    Empyralis's connector_id/action_id/capability_id vocabulary, using the
    SAME derivation direct_chat_generation_service.py's own tool loop uses:
    direct_chat_operator_binding_service.parse_tool_name for connector_id/
    action_id and direct_tool_execution_service.build_direct_tool_trace_
    metadata for capability_id/execution_environment/kind-shaped metadata
    (mirroring direct_tool_execution_service.direct_tool_step_payload's own
    connector_id/action_id -> label/kind derivation, :209).
  - Not a full harness port. Three meta-tools the legacy engine handles
    INLINE in its own loop rather than through execute_single_direct_tool_
    call (task_complete, update_plan, query_tool_registry — see
    tool_registry_service.ALWAYS_ON_TOOL_NAMES's docstring) are deliberately
    excluded from what's registered with the SDK (_UNSUPPORTED_TOOL_NAMES
    below) rather than half-reimplemented. task_complete's function is
    already native to the SDK (a turn ends when the model stops calling
    tools; that's the ResultMessage this module already translates).
    update_plan (continuous-work-past-max-iterations) and query_tool_
    registry (lazy Tier-2 tool discovery) have no SDK equivalent wired yet —
    a real, intentional v1 gap, not an oversight; see this module's use in
    sage_agent_runtime_service.py and the MAN-310 report for what that
    means in practice.

Non-Anthropic backend: Anthropic does not officially support pointing the
SDK/CLI at a non-Anthropic backend — see
https://github.com/anthropics/claude-agent-sdk-python/issues/410. Empyralis
does this knowingly: the founder runs Claude Code directly against
DeepSeek's Anthropic-Messages-API-compatible endpoint
(https://api.deepseek.com/anthropic) and reports it working flawlessly.
resolve_sdk_process_env() below takes ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL
from explicit per-turn arguments (falling back to the turn's resolved
`credentials` dict), never hardcoded, and hands them to
ClaudeAgentOptions(env=...) — which only overrides those two keys in the
spawned CLI subprocess's environment (subprocess_cli.py:792-797 merges
options.env on top of the inherited process env), never os.environ,
never a global default.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from server_modules import agent_trace_service
from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import secret_redaction_service

LOGGER = logging.getLogger(__name__)

# The value _run_sage_action_loop_v3's `engine_options={"engine": ...}` must
# carry to select this engine for a turn. Everything else (empty dict, key
# absent, any other value) takes the existing/legacy path unchanged.
ENGINE_ID = "claude_agent_sdk"

_MCP_SERVER_NAME = "empyralis"
_MCP_TOOL_PREFIX = f"mcp__{_MCP_SERVER_NAME}__"

# Meta-tools the legacy engine's own loop (direct_chat_generation_service.py)
# handles INLINE rather than by dispatching to execute_single_direct_tool_
# call — see this module's docstring. Registering these with the SDK and
# routing them through execute_single_direct_tool_call would fail (their
# bare names aren't in direct_chat_operator_binding_service.parse_tool_
# name's special-case list, so parsing raises and connector_id/action_id
# come back empty) or silently do the wrong thing, so they are filtered out
# before ever reaching the SDK, not half-wired.
_UNSUPPORTED_TOOL_NAMES = frozenset({"task_complete", "update_plan", "query_tool_registry"})


def resolve_sdk_process_env(
    *,
    credentials: Optional[Dict[str, Any]] = None,
    anthropic_api_key: str = "",
    anthropic_base_url: str = "",
) -> Dict[str, str]:
    """Build the `env` override for ClaudeAgentOptions — never os.environ,
    never a hardcoded URL. Explicit per-turn overrides win; otherwise falls
    back to whatever the turn's own resolved provider `credentials` dict
    carries (api_key is the standard key every provider profile in this
    codebase uses — see provider_profiles.py; base_url is opt-in and absent
    for providers whose profile doesn't set one, e.g. plain Anthropic).

    A workspace pointed at DeepSeek's Anthropic-compatible endpoint would
    pass anthropic_base_url="https://api.deepseek.com/anthropic" (or a
    credentials dict carrying that as "base_url") — this function does not
    special-case DeepSeek or hardcode that URL anywhere; it is only ever
    data the caller supplies for this turn.
    """
    creds = credentials if isinstance(credentials, dict) else {}
    env: Dict[str, str] = {}
    api_key = str(
        anthropic_api_key or creds.get("api_key") or creds.get("anthropic_api_key") or ""
    ).strip()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    base_url = str(
        anthropic_base_url or creds.get("base_url") or creds.get("anthropic_base_url") or ""
    ).strip()
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    return env


def strip_mcp_tool_prefix(name: str) -> str:
    """The Claude Code CLI exposes SDK MCP tools to the model as
    `mcp__{server_name}__{tool_name}` (standard MCP-tool naming — the CLI
    subprocess applies this, not the Python SDK, so it isn't documented in
    claude_agent_sdk's own source). Strip it back to the registered
    Empyralis tool name before handing it to parse_tool_name, which knows
    nothing about that convention. A name without the prefix passes through
    unchanged, so this is safe to call unconditionally."""
    token = str(name or "").strip()
    if token.startswith(_MCP_TOOL_PREFIX):
        return token[len(_MCP_TOOL_PREFIX):]
    return token


def _safe_parse_tool_name(tool_name: str) -> Tuple[str, str]:
    try:
        connector_id, action_id = direct_chat_operator_binding_service.parse_tool_name(tool_name)
        return connector_id, action_id
    except Exception:
        return "", ""


def _tool_result_block_text(content: Any) -> str:
    """Flatten a ToolResultBlock.content value (str | list[dict] | None —
    see claude_agent_sdk.types.ToolResultBlock) into plain text, the same
    shape build_direct_tool_trace_metadata's `result_text` parameter
    expects (it already only ever sees plain text from the legacy path's
    own tool_result_for_context)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _humanize_tool_progress(tool_name: str) -> str:
    # direct_chat_generation_service._humanize_tool_progress is a plain
    # module-level function (no DI needed) — imported lazily here to avoid
    # importing that (large) module's own heavy import chain at THIS
    # module's import time, since sage_agent_runtime_service.py imports
    # this module unconditionally regardless of whether the flag is ever
    # set.
    from server_modules import direct_chat_generation_service

    return direct_chat_generation_service._humanize_tool_progress(tool_name)


def _tool_timeout_seconds(connector_id: str, action_id: str) -> float:
    from server_modules import direct_chat_generation_service

    return direct_chat_generation_service._tool_timeout_seconds(connector_id, action_id)


@dataclass
class TranslationState:
    """Mutable state threaded across one turn's SDK message stream. A tool
    result (UserMessage -> ToolResultBlock) carries only a `tool_use_id` —
    the SDK never repeats the tool's name or input on that message — so the
    tool.started -> tool.result correlation this module needs (same
    connector_id/action_id/capability_id on both trace events, same
    tool_call_id key the collector buckets by) requires remembering what
    the matching ToolUseBlock (AssistantMessage, seen earlier in the
    stream) said."""

    tool_use_names: Dict[str, str] = field(default_factory=dict)
    tool_use_inputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reply_text_parts: List[str] = field(default_factory=list)


def translate_sdk_message(
    message: Any,
    *,
    state: TranslationState,
    trace_context: Optional[agent_trace_service.TraceContext],
) -> List[Dict[str, Any]]:
    """Translate ONE claude_agent_sdk message object into zero or more of
    Empyralis's event dicts — the exact {"type": "tool_progress" | "final"
    | "trace", ...} shapes sage_agent_runtime_service._collect_sage_
    operator_loop_v3_events() already parses (only "tool.started",
    "tool.result", "search.query", "trace.failed" and "plan.item.updated"
    are consumed under "trace" — see that function).

    Pure function of (message, state, trace_context): no I/O, no tool
    execution. Tool execution happens inside the @tool handlers
    build_sdk_tools() registers, which the SDK subprocess calls BEFORE it
    ever emits the UserMessage/ToolResultBlock this function reads back —
    by the time that message arrives, the real tool call (governance,
    redaction, billing, the lot) has already happened.

    Matched by class NAME (not isinstance) against claude_agent_sdk.types
    so this stays importable, and unit-testable with plain SimpleNamespace
    stand-ins, without a hard import of claude_agent_sdk at module load —
    every real caller passes real SDK dataclass instances, whose type name
    is exactly what's checked here.
    """
    events: List[Dict[str, Any]] = []

    def _envelope(
        event_type: str,
        data: Dict[str, Any],
        *,
        tool_call_id: Optional[str] = None,
        item_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        envelope = agent_trace_service.build_ephemeral_envelope(
            trace_context,
            event_type,
            data,
            tool_call_id=tool_call_id,
            item_id=item_id,
        )
        if envelope is None:
            return None
        return {"type": "trace", "payload": envelope}

    cls_name = type(message).__name__

    if cls_name == "AssistantMessage":
        for block in list(getattr(message, "content", None) or []):
            block_type = type(block).__name__
            if block_type == "TextBlock":
                text = str(getattr(block, "text", "") or "")
                if text:
                    state.reply_text_parts.append(text)
                continue
            if block_type != "ToolUseBlock":
                # ThinkingBlock, ServerToolUseBlock, ServerToolResultBlock:
                # none map to a type _collect_sage_operator_loop_v3_events
                # consumes (reasoning.summary.delta is EPHEMERAL_TRACE_EVENT_
                # TYPES-only even on the legacy path — see agent_trace_
                # service.py) — dropped, same as that path.
                continue
            tool_use_id = str(getattr(block, "id", "") or "")
            raw_name = str(getattr(block, "name", "") or "")
            raw_input = getattr(block, "input", None)
            tool_input = dict(raw_input) if isinstance(raw_input, dict) else {}
            tool_name = strip_mcp_tool_prefix(raw_name)
            state.tool_use_names[tool_use_id] = tool_name
            state.tool_use_inputs[tool_use_id] = tool_input
            connector_id, action_id = _safe_parse_tool_name(tool_name)
            trace_meta = direct_tool_execution_service.build_direct_tool_trace_metadata(
                connector_id, action_id, tool_input,
            )
            started_data = {
                "tool_name": tool_name,
                "capability_id": trace_meta.get("capability_id"),
                "connector_id": connector_id or None,
                "execution_environment": trace_meta.get("execution_environment"),
                "args_preview": secret_redaction_service.sanitize_mapping(tool_input),
            }
            started_event = _envelope("tool.started", started_data, tool_call_id=tool_use_id)
            if started_event is not None:
                events.append(started_event)
            events.append({
                "type": "tool_progress",
                "tool": tool_name,
                "message": _humanize_tool_progress(tool_name),
            })
            search_query = str(trace_meta.get("search_query") or "").strip()
            if search_query:
                query_event = _envelope(
                    "search.query",
                    {"provider": connector_id or "web", "query": search_query, "filters": {}},
                    tool_call_id=tool_use_id,
                )
                if query_event is not None:
                    events.append(query_event)
        return events

    if cls_name == "UserMessage":
        content = getattr(message, "content", None)
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if type(block).__name__ != "ToolResultBlock":
                continue
            tool_use_id = str(getattr(block, "tool_use_id", "") or "")
            tool_name = state.tool_use_names.get(tool_use_id, "")
            tool_input = state.tool_use_inputs.get(tool_use_id, {})
            connector_id, action_id = _safe_parse_tool_name(tool_name)
            result_text = _tool_result_block_text(getattr(block, "content", None))
            trace_meta = direct_tool_execution_service.build_direct_tool_trace_metadata(
                connector_id, action_id, tool_input, result_text=result_text,
            )
            is_error = bool(getattr(block, "is_error", False) or False)
            result_summary = str(trace_meta.get("result_summary") or result_text or "").strip()
            result_data = {
                # "failed"/"ok" — the exact two tokens tool_result_status.
                # classify_tool_result treats as an explicit status verdict
                # (its _FAILURE_STATUS_TOKENS / _SUCCESS_STATUS_TOKENS sets),
                # matching the legacy producer (direct_chat_generation_
                # service.py's own tool.result data) token-for-token.
                "status": "failed" if is_error else "ok",
                "summary": result_summary,
                "execution_environment": trace_meta.get("execution_environment"),
            }
            result_event = _envelope("tool.result", result_data, tool_call_id=tool_use_id)
            if result_event is not None:
                events.append(result_event)
            plan_item_id = f"tool:{tool_use_id}"
            plan_event = _envelope(
                "plan.item.updated",
                {
                    "item_id": plan_item_id,
                    "status": "failed" if is_error else "done",
                    "summary": (
                        f"{tool_name} failed." if is_error else f"Completed {tool_name}."
                    ),
                },
                item_id=plan_item_id,
            )
            if plan_event is not None:
                events.append(plan_event)
        return events

    if cls_name == "ResultMessage":
        is_error = bool(getattr(message, "is_error", False) or False)
        result_text = getattr(message, "result", None)
        reply = str(result_text or "").strip() or "".join(state.reply_text_parts).strip()
        payload: Dict[str, Any] = {"reply": reply}
        if is_error:
            error_code = str(getattr(message, "subtype", "") or "").strip() or "provider_generation_failed"
            payload["error"] = error_code
            failed_event = _envelope("trace.failed", {"code": error_code, "message": reply or error_code})
            if failed_event is not None:
                events.append(failed_event)
        usage = getattr(message, "usage", None)
        if isinstance(usage, dict):
            payload["usage"] = usage
        total_cost_usd = getattr(message, "total_cost_usd", None)
        if total_cost_usd is not None:
            payload["total_cost_usd"] = total_cost_usd
        events.append({"type": "final", "payload": payload})
        return events

    # SystemMessage / StreamEvent / RateLimitEvent: none of these map to a
    # type _collect_sage_operator_loop_v3_events consumes — dropped.
    return events


def build_sdk_tools(
    *,
    tool_defs: Sequence[Dict[str, Any]],
    generation_services: Any,
    workspace_id: str,
    thread_id: str,
    provider: Optional[str],
    model: Optional[str],
    credentials: Optional[Dict[str, Any]],
    reasoning_effort: str,
    session_ctx: Optional[Dict[str, Any]],
) -> List[Any]:
    """One in-process SDK MCP tool per Empyralis direct-chat tool
    definition, each dispatching to the EXISTING executor —
    generation_services.execute_single_direct_tool_call, the exact bound
    callable direct_chat_generation_service.py's own tool loop calls (see
    that module's `services.execute_single_direct_tool_call(...)` call
    site) — so governance, secret redaction, billing attribution and
    result classification all run unchanged. Nothing here reimplements a
    tool; this only adapts the calling convention (SDK args-in/content-out
    <-> Empyralis's tool_call-dict-in/str-out executor).

    `_UNSUPPORTED_TOOL_NAMES` are silently skipped — see module docstring.
    """
    from claude_agent_sdk import tool as sdk_tool  # local: only needed on this engine's path

    index_counter = itertools.count(1)
    sdk_tools: List[Any] = []
    for tool_def in tool_defs:
        if not isinstance(tool_def, dict):
            continue
        name = str(tool_def.get("name") or "").strip()
        if not name or name in _UNSUPPORTED_TOOL_NAMES:
            continue
        description = str(tool_def.get("description") or name).strip() or name
        parameters = tool_def.get("parameters")
        input_schema: Dict[str, Any] = (
            dict(parameters) if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        )

        def _make_handler(tool_name: str) -> Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]:
            connector_id, action_id = _safe_parse_tool_name(tool_name)
            timeout_s = _tool_timeout_seconds(connector_id, action_id)

            async def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
                call_index = next(index_counter)
                tool_call = {"name": tool_name, "arguments": dict(args or {})}
                try:
                    result_text = await asyncio.wait_for(
                        asyncio.to_thread(
                            generation_services.execute_single_direct_tool_call,
                            tool_call=tool_call,
                            workspace_id=workspace_id,
                            thread_id=thread_id,
                            index=call_index,
                            provider=provider,
                            model=model,
                            credentials=credentials if isinstance(credentials, dict) else None,
                            reasoning_effort=reasoning_effort or "",
                            session_ctx=session_ctx,
                        ),
                        timeout=timeout_s,
                    )
                except asyncio.TimeoutError:
                    return {
                        "content": [{
                            "type": "text",
                            "text": (
                                f"The tool '{tool_name}' timed out after {timeout_s:.0f}s. "
                                "Try a different approach — a smaller scope, a different tool, "
                                "or ask the user for more specific guidance."
                            ),
                        }],
                        "is_error": True,
                    }
                except Exception as exc:  # governance denial, broker block, etc. — surface
                    # to the model as a tool-level error rather than crashing the
                    # whole turn. (This is one deliberate behavioral difference
                    # from the legacy path, where such an exception propagates out
                    # of the ThreadPoolExecutor future and can fail the turn — see
                    # the MAN-310 report.)
                    LOGGER.warning(
                        "claude_agent_sdk_bridge: tool %s failed: %s", tool_name, exc,
                    )
                    return {
                        "content": [{"type": "text", "text": str(exc) or "Tool call failed."}],
                        "is_error": True,
                    }
                return {"content": [{"type": "text", "text": str(result_text or "")}]}

            return _handler

        sdk_tools.append(sdk_tool(name, description, input_schema)(_make_handler(name)))
    return sdk_tools


def render_prompt(message: str, prior_messages: Optional[List[Dict[str, Any]]]) -> str:
    """Fold prior turns into a single prompt string for query()'s one-shot
    `prompt` mode.

    v1 simplification, flagged in the MAN-310 report: this does not use
    ClaudeAgentOptions' session continuity (continue_conversation/resume) or
    the SDK's streaming-input mode (an AsyncIterable[dict] prompt) — either
    would be the more native way to carry multi-turn history once this
    engine is wired to a persistent per-thread SDK session. Folding history
    into one string keeps the wire format legible for a synchronous
    request/response turn (exactly how the legacy engine's
    generate_chat_reply_stream_with_provider_fallback receives prior_
    messages) with no session-store dependency, at the cost of not
    benefiting from prompt caching across turns.
    """
    prior = prior_messages or []
    if not prior:
        return message
    lines: List[str] = []
    for entry in prior:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip() or "user"
        content = entry.get("content")
        text = content if isinstance(content, str) else json.dumps(content, default=str) if content else ""
        text = str(text or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    if not lines:
        return message
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nuser: {message}"


async def run_claude_agent_sdk_turn(
    *,
    message: str,
    system_prompt: str,
    prior_messages: Optional[List[Dict[str, Any]]],
    tool_defs: Sequence[Dict[str, Any]],
    generation_services: Any,
    workspace_id: str,
    thread_id: str,
    provider: Optional[str],
    model: Optional[str],
    credentials: Optional[Dict[str, Any]],
    reasoning_effort: str = "",
    session_ctx: Optional[Dict[str, Any]] = None,
    trace_context: Optional[agent_trace_service.TraceContext] = None,
    max_turns: int = 5,
    anthropic_api_key: str = "",
    anthropic_base_url: str = "",
) -> List[Dict[str, Any]]:
    """Drive one turn through claude_agent_sdk.query(), translating every
    yielded message into Empyralis's event dicts. Returns the SAME
    list[dict] shape sage_agent_runtime_service._collect_sage_operator_
    loop_v3_events already parses — this is the function
    server_modules/sage_agent_runtime_service.py's _collect_stream_events
    calls when a turn's engine_options select ENGINE_ID.

    Requires the `claude` CLI (Node.js + Claude Code) on PATH — the SDK's
    only shipped Transport spawns it as a subprocess (see this module's
    docstring and the MAN-310 spike). Requires an ANTHROPIC_API_KEY (or an
    equivalent credential for whatever ANTHROPIC_BASE_URL is configured) —
    see resolve_sdk_process_env.
    """
    from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query

    usable_tool_defs = [
        tool_def
        for tool_def in tool_defs
        if isinstance(tool_def, dict) and str(tool_def.get("name") or "").strip() not in _UNSUPPORTED_TOOL_NAMES
    ]
    sdk_tools = build_sdk_tools(
        tool_defs=usable_tool_defs,
        generation_services=generation_services,
        workspace_id=workspace_id,
        thread_id=thread_id,
        provider=provider,
        model=model,
        credentials=credentials,
        reasoning_effort=reasoning_effort,
        session_ctx=session_ctx,
    )
    mcp_server = create_sdk_mcp_server(name=_MCP_SERVER_NAME, tools=sdk_tools)
    allowed_tools = [f"{_MCP_TOOL_PREFIX}{tool_def.get('name')}" for tool_def in usable_tool_defs]

    options = ClaudeAgentOptions(
        system_prompt=system_prompt or None,
        mcp_servers={_MCP_SERVER_NAME: mcp_server},
        allowed_tools=allowed_tools,
        model=model or None,
        max_turns=max_turns,
        env=resolve_sdk_process_env(
            credentials=credentials,
            anthropic_api_key=anthropic_api_key,
            anthropic_base_url=anthropic_base_url,
        ),
    )

    prompt = render_prompt(message, prior_messages)
    state = TranslationState()
    events: List[Dict[str, Any]] = []
    async for sdk_message in query(prompt=prompt, options=options):
        events.extend(translate_sdk_message(sdk_message, state=state, trace_context=trace_context))
    return events


def collect_events_via_claude_agent_sdk(**kwargs: Any) -> List[Dict[str, Any]]:
    """Sync wrapper for run_claude_agent_sdk_turn — sage_agent_runtime_
    service.py's _collect_stream_events is itself a plain sync function
    (run inside asyncio.to_thread by its caller, _run_sage_action_loop_v3),
    so it needs a blocking entry point, exactly like the legacy branch's
    `list(...)` over a sync generator. asyncio.run() is safe here because
    _collect_stream_events already runs on its OWN thread with no event
    loop of its own — see the asyncio.to_thread(_collect_stream_events)
    call site."""
    return asyncio.run(run_claude_agent_sdk_turn(**kwargs))

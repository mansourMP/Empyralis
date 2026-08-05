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
    query_tool_registry (lazy Tier-2 tool discovery) has no SDK equivalent
    wired yet — a real, intentional v1 gap, not an oversight.

    update_plan (MAN-310 Phase 2 investigated this one specifically —
    verdict: NOT building an equivalent, for two independent reasons):
      1. Its plan-tracking/visibility half is substantially covered IN
         SPIRIT by the SDK's own native TodoWrite tool and Claude Code's
         trained-in planning behavior — the model already tracks multi-step
         work without Empyralis having to offer a tool or explain the
         convention in the system prompt, the way the legacy engine must.
         Translating a TodoWrite call into Empyralis's own "plan.updated"
         trace event (so it renders in the Work tab the same way) would be
         a real, buildable feature — but it is a visibility/polish
         enhancement, not a correctness or reliability gap, and per this
         phase's own priority order it does not belong in this pass.
         Separately: reading claude_agent_sdk's own subprocess_cli.py
         confirms TodoWrite likely is not even reliably CALLABLE today
         under this bridge's configuration — `ClaudeAgentOptions.tools` is
         never set (so the CLI's full built-in toolset, including
         TodoWrite, is visible to the model), but `allowed_tools` only ever
         lists this bridge's own `mcp__empyralis__*` tools, `permission_
         mode` is never set, and no `can_use_tool` callback is registered;
         in headless/no-TTY execution (exactly how query() runs here) a
         tool outside `--allowedTools` has no prompt path to be approved
         through — see PermissionMode's own "dontAsk" doc ("deny if not
         pre-approved"). A model that reaches for TodoWrite here would most
         likely see it denied, not silently skip using it.
      2. Its iteration-budget-extension half (continuous work past
         max_iterations while a plan has open tasks — direct_chat_
         generation_service.py's _continuous_work_enabled /
         _plan_has_open_tasks / _continuous_work_budget_allows_more) has NO
         safe SDK-native equivalent to port without ALSO porting the
         token-budget gate that makes it safe on the legacy engine. This
         bridge's ClaudeAgentOptions.max_turns is set once, to the SAME
         _SAGE_OPERATOR_LOOP_MAX_ITERATIONS every legacy turn that never
         calls update_plan is ALSO capped at — i.e. today's SDK-engine
         behavior already matches the legacy engine's own no-plan default,
         byte for byte. Raising it unconditionally (e.g. to direct_chat_
         generation_service._CONTINUOUS_WORK_HARD_ITERATION_CAP) without a
         corresponding "is there still real, budgeted work pending" signal
         would trade a bounded, well-understood cap for a more expensive
         one with no matching safety check — a worse reliability/cost
         posture, not a better one. Left exactly as-is.

Non-Anthropic backend: Anthropic does not officially support pointing the
SDK/CLI at a non-Anthropic backend — see
https://github.com/anthropics/claude-agent-sdk-python/issues/410. Empyralis
does this knowingly: the founder runs Claude Code directly against
DeepSeek's Anthropic-Messages-API-compatible endpoint
(https://api.deepseek.com/anthropic) and reports it working flawlessly.
resolve_sdk_process_env() below takes ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL
from explicit per-turn arguments (falling back to the turn's resolved
`credentials` dict), never hardcoded, and hands them to
ClaudeAgentOptions(env=...), never os.environ, never a global default.

Credential isolation (multi-tenancy): the Python SDK's Transport MERGES
options.env ON TOP OF the parent (Empyralis backend) process's own
environment rather than replacing it (subprocess_cli.py's env handling;
also documented at code.claude.com/docs/en/llm-gateway-connect's Agent SDK
section — the TypeScript SDK does the opposite). A dict that only sets the
keys it has real per-turn values for is therefore NOT enough isolation: any
ANTHROPIC_*/CLAUDE_CODE_* variable ambiently set in Empyralis's own process
would otherwise leak into every spawned tenant subprocess. resolve_sdk_
process_env() always returns every credential-shaped key it knows about
(_CREDENTIAL_ENV_KEYS), blanked to "" when this turn has no value for it —
see that function's own docstring — and run_claude_agent_sdk_turn always
gives each turn a brand-new CLAUDE_CONFIG_DIR that has never had `claude
/login` run against it, destroyed the moment the turn ends, and spawns
exactly one fresh subprocess per turn (never a pooled/reused warm CLI
process across tenants).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import shutil
import tempfile
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


# Credential-shaped env vars claude_agent_sdk's spawned `claude` CLI
# subprocess recognizes, in Anthropic's own documented precedence order
# (code.claude.com/docs/en/authentication's "Authentication precedence"
# table, fetched 2026-08-05): cloud-provider flags (rank #1) outrank
# ANTHROPIC_AUTH_TOKEN (#2), which outranks ANTHROPIC_API_KEY (#3), which
# outranks CLAUDE_CODE_OAUTH_TOKEN (#5) and any subscription OAuth already
# saved via `claude /login` (#6, last).
#
# Why this list matters: per code.claude.com/docs/en/llm-gateway-connect's
# Agent SDK section, the PYTHON SDK's Transport MERGES ClaudeAgentOptions
# (env=...) ON TOP OF the parent process's own environment rather than
# replacing it (the TypeScript SDK does the opposite). A dict that only
# sets keys it has a real value for is therefore NOT sufficient isolation:
# any ANTHROPIC_*/CLAUDE_CODE_* variable that happens to be set in
# Empyralis's OWN backend process (an operator's local `claude /login`,
# a future accidental export, a cloud-provider flag meant for some other
# purpose) would leak into every spawned tenant subprocess, unrelated to
# that tenant's own resolved credential -- wrong billing attribution at
# best, cross-tenant credential use at worst (cloud-provider flags are
# rank #1 and would silently override everything this module passes).
# resolve_sdk_process_env() below therefore ALWAYS returns every one of
# these keys, blanked to "" when this turn has no value for them, rather
# than omitting the key -- an explicit "" in options.env still wins the
# merge over parent os.environ, which is what actually blocks the leak.
_CREDENTIAL_ENV_KEYS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_USE_MANTLE",
)

# A provider's ordinary profile base_url (provider_profiles.py) is its
# OpenAI-shaped endpoint — DeepSeek's, for instance, is .../v1. Handing that
# to the `claude` CLI would point an Anthropic-Messages client at an
# OpenAI-protocol URL. This maps the providers that ship their OWN native
# Anthropic-Messages-compatible endpoint to that endpoint instead. Only
# providers verified against a real turn belong here: per the project's
# standing rule, a provider is supported only if it publishes a native
# Anthropic-compatible endpoint — never via a translating proxy. Anthropic
# itself is absent deliberately (no base_url override; the CLI's own default
# is correct).
_ANTHROPIC_COMPATIBLE_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/anthropic",
}


def resolve_anthropic_compatible_base_url(provider: str) -> str:
    """The Anthropic-Messages-compatible endpoint for `provider`, or "" when
    that provider has none (so no ANTHROPIC_BASE_URL is emitted and the CLI
    talks to Anthropic directly). Never falls back to the provider's
    OpenAI-shaped profile URL — a wrong protocol is worse than no override,
    because it fails as an opaque HTTP error rather than an obvious
    unsupported-provider one."""
    return _ANTHROPIC_COMPATIBLE_BASE_URLS.get(str(provider or "").strip().lower(), "")


def resolve_sdk_process_env(
    *,
    credentials: Optional[Dict[str, Any]] = None,
    anthropic_api_key: str = "",
    anthropic_base_url: str = "",
    config_dir: str = "",
    provider: str = "",
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

    Every key in _CREDENTIAL_ENV_KEYS is ALWAYS present in the returned
    dict (see that constant's own docstring for why an omitted key is not
    safe here, given the SDK's merge-over-parent-env behavior) — this
    function never reads os.environ itself, so nothing ambient can leak in
    through it either.

    `config_dir`, when given, is forwarded as BOTH CLAUDE_CONFIG_DIR and
    CLAUDE_SECURESTORAGE_CONFIG_DIR (the latter covers macOS local dev,
    where credential storage also touches a Keychain entry namespaced by
    this directory, in addition to CLAUDE_CONFIG_DIR's on-disk store that
    covers Linux, how this deploys) — see run_claude_agent_sdk_turn, which
    always passes a fresh, never-logged-in-to directory for this reason.
    """
    creds = credentials if isinstance(credentials, dict) else {}
    env: Dict[str, str] = {key: "" for key in _CREDENTIAL_ENV_KEYS}
    api_key = str(
        anthropic_api_key or creds.get("api_key") or creds.get("anthropic_api_key") or ""
    ).strip()
    # ANTHROPIC_AUTH_TOKEN (rank #2), not ANTHROPIC_API_KEY (rank #3): an
    # API_KEY value the CLI hasn't seen before triggers a one-time
    # interactive "approve this key?" consent gate cached in .claude.json
    # (nothing this module could answer, and pointless anyway given
    # config_dir below is fresh every turn — the cache would never carry
    # over). AUTH_TOKEN carries no such gate. DeepSeek's own setup guide
    # for its Anthropic-Messages-API-compatible endpoint (see this module's
    # docstring) also instructs ANTHROPIC_AUTH_TOKEN for exactly this
    # reason.
    if api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
    # An explicit per-turn override wins; otherwise the provider's own
    # Anthropic-compatible endpoint. creds["base_url"] is deliberately NOT a
    # fallback here — that is the provider's OpenAI-shaped URL (see
    # _ANTHROPIC_COMPATIBLE_BASE_URLS), which this client cannot speak.
    base_url = str(
        anthropic_base_url or creds.get("anthropic_base_url") or ""
    ).strip() or resolve_anthropic_compatible_base_url(provider)
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
        env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = config_dir
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
        # session_id is a required (non-Optional) field on every real
        # ResultMessage the SDK yields — carried through here so the caller
        # (sage_agent_runtime_service._run_sage_action_loop_v3) can persist
        # it against Empyralis's own thread identity and resume THIS
        # conversation on a later turn instead of re-folding full history
        # (see run_claude_agent_sdk_turn's resume_session_id parameter).
        session_id = str(getattr(message, "session_id", "") or "").strip()
        if session_id:
            payload["session_id"] = session_id
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

    MAN-310 Phase 2: this is now the FALLBACK path, not the only path. When
    the caller has a resumable SDK session for this conversation (see
    run_claude_agent_sdk_turn's resume_session_id parameter), the session
    itself already carries the history — folding it into the prompt text
    AGAIN would be sending Empyralis's memory of the conversation and the
    SDK's own resumed memory of the same conversation at once, which is
    redundant at best (wasted tokens, a broken prompt-cache prefix on every
    turn since the folded text keeps growing) and confusing at worst (the
    model sees its own prior turns twice, once live-in-context from the
    resumed session and once again as inert transcript text). This function
    still runs unconditionally on a fresh/unresumable session (no session
    yet, a stale one, or a resume attempt that failed) — see
    run_claude_agent_sdk_turn's fallback branch — where it's exactly as
    necessary as before: query()'s one-shot `prompt` mode has no OTHER way
    to see anything before this turn.
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
    # MAN-310 Phase 2: a claude_agent_sdk session id previously captured for
    # THIS conversation (see sage_agent_runtime_service._sdk_engine_session_
    # lookup, which is also what verifies it's still safe to resume before
    # ever passing it here — this function trusts its caller on that). When
    # set, this turn resumes that session (ClaudeAgentOptions.resume) and
    # sends ONLY the new message as the prompt — the resumed session already
    # has everything render_prompt would otherwise fold in, and prompt
    # caching only pays off when the sent prefix doesn't change turn to
    # turn. Empty (default) — every existing caller, and any caller with no
    # resumable session — takes the exact pre-Phase-2 path: render_prompt
    # folds prior_messages in every time, byte-for-byte unchanged.
    resume_session_id: str = "",
) -> List[Dict[str, Any]]:
    """Drive one turn through claude_agent_sdk.query(), translating every
    yielded message into Empyralis's event dicts. Returns the SAME
    list[dict] shape sage_agent_runtime_service._collect_sage_operator_
    loop_v3_events already parses — this is the function
    server_modules/sage_agent_runtime_service.py's _collect_stream_events
    calls when a turn's engine_options select ENGINE_ID.

    Requires the `claude` CLI (Node.js + Claude Code) on PATH — the SDK's
    only shipped Transport spawns it as a subprocess (see this module's
    docstring and the MAN-310 spike). Requires an ANTHROPIC_AUTH_TOKEN (or
    an equivalent credential for whatever ANTHROPIC_BASE_URL is
    configured) — see resolve_sdk_process_env.

    Isolation: this spawns exactly ONE fresh `claude` subprocess for this
    one turn — never a pooled/reused warm process across turns or tenants,
    since the CLI holds process-global in-memory auth/client state that
    would otherwise bleed between tenants. It also allocates a brand-new,
    never-logged-in-to CLAUDE_CONFIG_DIR (tempfile.mkdtemp, destroyed in the
    `finally` below the moment this turn ends) rather than reusing whatever
    config dir the CLI would default to — on Linux (how this deploys) the
    entire credential store is one file under that directory, so a fresh
    directory is provably clean of any other tenant's or the operator's own
    `claude /login` session. See resolve_sdk_process_env's docstring for the
    companion fix this pairs with (blanking every credential-shaped env key
    the SDK could otherwise merge in from Empyralis's own process env).
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

    config_dir = tempfile.mkdtemp(prefix="empyralis-claude-sdk-")
    try:
        def _build_options(*, resume: str) -> Any:
            return ClaudeAgentOptions(
                system_prompt=system_prompt or None,
                mcp_servers={_MCP_SERVER_NAME: mcp_server},
                allowed_tools=allowed_tools,
                # Drop the CLI's OWN built-in toolset (--tools "") so the only
                # tools reachable are Empyralis's, served over the in-process
                # MCP server above. allowed_tools alone does NOT do this:
                # `tools` defaults to the full claude_code preset, leaving
                # TaskCreate/TodoWrite/Read/Write/Bash callable.
                #
                # Caught by driving a real product turn: asked to create a
                # task, the model called the CLI's built-in TaskCreate instead
                # of Empyralis's project_task__create. It returned "Task #1
                # created successfully", Empyralis translated that into a
                # genuine tool.started/tool.result pair, and the customer was
                # told the task existed — while project_tasks stayed empty.
                # tool_honesty_guard cannot catch this: a real tool really was
                # called and really did succeed, just in the CLI's own
                # bookkeeping instead of the product's. Work that never
                # happened must never be reportable as done.
                tools=[],
                model=model or None,
                max_turns=max_turns,
                resume=resume or None,
                env=resolve_sdk_process_env(
                    credentials=credentials,
                    anthropic_api_key=anthropic_api_key,
                    anthropic_base_url=anthropic_base_url,
                    config_dir=config_dir,
                    provider=provider or "",
                ),
            )

        async def _consume(sdk_message: Any, *, state: TranslationState) -> List[Dict[str, Any]]:
            new_events = translate_sdk_message(sdk_message, state=state, trace_context=trace_context)
            # MAN-310 Phase 2 (trace persistence): translate_sdk_message itself
            # stays synchronous/pure (see its own docstring — unit-tested
            # directly, no event loop) and only ever builds the EPHEMERAL
            # envelope (agent_trace_service.build_ephemeral_envelope,
            # persisted=False). This async follow-up, run from here where an
            # event loop is actually available, durably persists the subset of
            # those envelopes agent_trace_service.PERSISTED_TRACE_EVENT_TYPES
            # says should survive — matching what the legacy engine's own
            # _emit_trace_event(persisted=True) call sites already do for the
            # SAME event types (tool.started/tool.result/search.query/trace.
            # failed/plan.item.updated). Reuses the envelope's OWN seq/event_id
            # (see persist_ephemeral_envelope's docstring for why — minting a
            # second seq here would persist a differently-numbered duplicate of
            # what a live consumer already saw).
            for event in new_events:
                if isinstance(event, dict) and event.get("type") == "trace":
                    await agent_trace_service.persist_ephemeral_envelope(trace_context, event.get("payload"))
            return new_events

        resume_token = str(resume_session_id or "").strip()
        prompt = message if resume_token else render_prompt(message, prior_messages)
        options = _build_options(resume=resume_token)

        state = TranslationState()
        events: List[Dict[str, Any]] = []
        received_any_message = False
        try:
            async for sdk_message in query(prompt=prompt, options=options):
                received_any_message = True
                events.extend(await _consume(sdk_message, state=state))
        except Exception:
            if not resume_token or received_any_message:
                # Either there was nothing to fall back FROM (no resume was
                # attempted, so this is just a real failure), or the model turn
                # was already underway — possibly having already called a tool
                # with a real side effect (sent an email, created a task, ...)
                # through the SAME in-process executor the legacy engine uses.
                # Blindly retrying from scratch there could re-run that tool
                # call a second time. Only a resume that failed before yielding
                # ANYTHING is safe to retry fresh — that failure mode is "the
                # CLI couldn't find/load that session id" (e.g. its local
                # session store was lost to a restart, or this turn landed on a
                # different machine than the one that captured it), not
                # "something went wrong partway through the model's work".
                raise
            LOGGER.warning(
                "claude_agent_sdk_bridge: resume=%s failed before yielding any message — "
                "retrying this turn fresh (full history, new session).",
                resume_token,
            )
            state = TranslationState()
            events = []
            fallback_prompt = render_prompt(message, prior_messages)
            fallback_options = _build_options(resume="")
            async for sdk_message in query(prompt=fallback_prompt, options=fallback_options):
                events.extend(await _consume(sdk_message, state=state))
        return events
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


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

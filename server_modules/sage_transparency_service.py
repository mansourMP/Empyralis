"""
Sage / Main Agent transparency event emission.

Wraps the AgentTransparencyEvent model and emits events for the real
stages that exist in the Sage turn lifecycle.  Never exposes raw
chain-of-thought, private memory content, or model internals.

Callers should:

    from server_modules.sage_transparency_service import (
        emit_sage_turn_transparency_events,
    )

    events = emit_sage_turn_transparency_events(
        trace_id=result["trace_id"],
        workspace_id=workspace_id,
        user_message=message,
        sage_result=result,
        surface="chat",
        audience="owner",
    )

The returned list[AgentTransparencyEvent] can be serialised and sent to
the frontend via the existing SSE / chat response envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from server_modules.agent_transparency_events import (
    AgentTransparencyEvent,
    Audience,
    TransparencyEventType,
)

# ── helpers ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_label(value: Any) -> str:
    return str(value or "").strip()


def _safe_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


# ── public entry point ───────────────────────────────────────────────

def emit_sage_turn_transparency_events(
    *,
    trace_id: str,
    workspace_id: str,
    user_message: str,
    sage_result: Dict[str, Any],
    surface: str = "chat",
    audience: Audience = "owner",
    agent_id: Optional[str] = None,
) -> List[AgentTransparencyEvent]:
    """
    Emit one transparency event per real stage present in the Sage result.

    Only stages with real data are emitted — no fake / placeholder events.
    """
    events: List[AgentTransparencyEvent] = []
    ts = _now()
    msg_text = _safe_label(user_message)

    # ── 1. user_message_received ─────────────────────────────────
    if msg_text:
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actor_type="sage",
                surface=surface if surface in ("chat", "channel") else "chat",
                audience=audience,
                event_type="user_message_received",
                title="Message received",
                summary=f"User message: {msg_text[:200]}",
                status="completed",
                timestamp=ts,
            )
        )

    # ── 2. memory_loaded ─────────────────────────────────────────
    used_context = _safe_list(sage_result.get("used_context"))
    if used_context:
        context_labels = [
            _safe_label(ctx.get("name") or ctx.get("source"))
            for ctx in used_context
            if isinstance(ctx, dict)
        ]
        if context_labels:
            events.append(
                AgentTransparencyEvent(
                    event_id=f"stevt-{uuid4().hex[:12]}",
                    trace_id=trace_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    actor_type="sage",
                    surface="chat",
                    audience=audience,
                    event_type="memory_loaded",
                    title="Memory loaded",
                    summary=f"Loaded: {', '.join(context_labels[:5])}",
                    status="completed",
                    timestamp=_now(),
                    memory_scope="workspace",
                    metadata={"context_labels": context_labels[:5]},
                )
            )

    # ── 3. blocked_tools ─────────────────────────────────────────
    # blocked_tools is populated by SEVERAL structurally different
    # producers (provider errors, SDK bookkeeping anomalies, execution
    # failures) and NONE of them is a genuine tool-capability POLICY
    # decision — a real capability denial raises during tool execution and
    # lands in `tool_calls` instead, never here. This used to run a
    # classifier (sage_blocked_tools_outcome.py, since deleted) that could,
    # in principle, distinguish a real per-agent tool-policy code from a
    # failure — but 2026-08-14 (CLAUDE.md, founder decision) removed the
    # only thing that could ever produce that code: there is no more
    # per-agent Tools enable/disable checklist, so "an owner switched this
    # off" is no longer an event that can happen. Keeping a
    # "policy_blocked" branch "future-proofed" for a condition that is now
    # structurally impossible would be dead code pretending to be caution.
    # Every non-empty blocked_tools is unconditionally "turn_failed" —
    # never blames the customer's tool settings, matching the identical
    # collapse in agent_turn_runtime_service.py's chat-reply substitution
    # (SAGE_TURN_NO_REPLY_UNKNOWN), so the Work-tab/Inbox event and the
    # chat reply can never disagree about what happened.
    blocked_tools = _safe_list(sage_result.get("blocked_tools"))
    if blocked_tools:
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actor_type="sage",
                surface="chat",
                audience=audience,
                event_type="turn_failed",
                title="Turn failed",
                summary=f"The turn did not complete normally ({len(blocked_tools)} step(s) affected).",
                status="failed",
                timestamp=_now(),
                metadata={"blocked_tools": blocked_tools[:10]},
            )
        )

    # ── 4. tool_calls (started / completed / failed) ─────────────
    tool_calls = _safe_list(sage_result.get("tool_calls"))
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        tc_name = _safe_label(tc.get("name") or tc.get("tool_name"))
        tc_status = _safe_label(tc.get("status") or "completed")
        tc_error = _safe_label(tc.get("error") or tc.get("error_message") or "")
        if not tc_name:
            continue

        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actor_type="sage",
                surface="chat",
                audience=audience,
                event_type="tool_started",
                title=f"Tool started: {tc_name}",
                summary=f"Tool {tc_name} started.",
                status="running",
                timestamp=_now(),
                tool_name=tc_name,
                metadata={
                    "iteration": tc.get("iteration"),
                    "action_loop_version": tc.get("action_loop_version"),
                },
            )
        )

        if tc_status in ("completed", "success", "done"):
            events.append(
                AgentTransparencyEvent(
                    event_id=f"stevt-{uuid4().hex[:12]}",
                    trace_id=trace_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    actor_type="sage",
                    surface="chat",
                    audience=audience,
                    event_type="tool_completed",
                    title=f"Tool completed: {tc_name}",
                    summary=f"Tool {tc_name} completed successfully.",
                    status="completed",
                    timestamp=_now(),
                    tool_name=tc_name,
                )
            )
        elif tc_status in ("failed", "error"):
            events.append(
                AgentTransparencyEvent(
                    event_id=f"stevt-{uuid4().hex[:12]}",
                    trace_id=trace_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    actor_type="sage",
                    surface="chat",
                    audience=audience,
                    event_type="tool_failed",
                    title=f"Tool failed: {tc_name}",
                    summary=f"Tool {tc_name} failed: {tc_error[:200]}" if tc_error else f"Tool {tc_name} failed.",
                    status="failed",
                    timestamp=_now(),
                    tool_name=tc_name,
                )
            )
        else:
            continue

    # ── 4b. browser_action (browser__ tool calls) ─────────────────
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        tc_name = _safe_label(tc.get("name") or tc.get("tool_name"))
        if not tc_name.startswith("browser__"):
            continue
        tc_status = _safe_label(tc.get("status") or "completed")
        tc_output = _safe_label(tc.get("output") or tc.get("error") or "")
        tc_args = tc.get("arguments") or {}
        url = _safe_label(tc_args.get("url") or tc_args.get("target") or tc_args.get("input") or "")
        summary_parts = [f"Action: {tc_name}"]
        if url:
            summary_parts.append(f"URL: {url[:200]}")
        if tc_status in ("completed", "success", "done"):
            summary_parts.append("Status: completed")
        elif tc_status in ("failed", "error"):
            summary_parts.append(f"Status: failed — {tc_output[:100]}" if tc_output else "Status: failed")
        else:
            summary_parts.append(f"Status: {tc_status}")

        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actor_type="sage",
                surface="chat",
                audience=audience,
                event_type="browser_action",
                title=f"Browser: {tc_name.replace('browser__', '')}",
                summary=" | ".join(summary_parts),
                status="completed" if tc_status in ("completed", "success", "done") else "failed" if tc_status in ("failed", "error") else "running",
                timestamp=_now(),
                tool_name=tc_name,
                metadata={
                    "url": url,
                    "action": tc_name,
                    "status": tc_status,
                },
            )
        )

    # ── 5. skill_executed (MCP/skill tool calls) ───────────────
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        tc_name = _safe_label(tc.get("name") or tc.get("tool_name"))
        if not tc_name:
            continue
        # Only emit skill_executed for MCP tools or tools whose
        # name starts with an MCP prefix, but NOT built-in tools
        # like browser__* or web__*
        is_mcp_tool = tc_name.startswith("mcp:") or tc_name.startswith("skill:")
        if not is_mcp_tool:
            continue
        tc_status = _safe_label(tc.get("status") or "completed")
        tc_output = _safe_label(tc.get("output") or "")
        tc_error = _safe_label(tc.get("error") or "")
        evt_metadata: dict[str, Any] = {
            "execution_adapter": "mcp_tool",
            "skill_id": tc_name,
            "status": tc_status,
        }
        if tc_output:
            evt_metadata["output_preview"] = tc_output[:200]
        if tc_error:
            evt_metadata["error"] = tc_error[:200]
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actor_type="sage",
                surface="chat",
                audience=audience,
                event_type="skill_executed",
                title=f"MCP tool executed: {tc_name}",
                summary=tc_output[:200] if tc_output else f"MCP tool {tc_name} executed.",
                status=tc_status,
                timestamp=_now(),
                tool_name=tc_name,
                metadata=evt_metadata,
            )
        )

    # ── 7. error / llm failure ──────────────────────────────────
    sage_error = _safe_label(sage_result.get("error"))
    if sage_error:
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actor_type="sage",
                surface="chat",
                audience=audience,
                event_type="tool_failed",
                title="The agent encountered an error",
                summary="The agent was unable to complete this request.",
                status="failed",
                timestamp=_now(),
                metadata={"error_class": sage_error.split(":")[0][:100]},
            )
        )

    # ── 8. final_response_sent ──────────────────────────────────
    reply_text = _safe_label(sage_result.get("message"))
    if reply_text:
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actor_type="sage",
                surface="chat",
                audience=audience,
                event_type="final_response_sent",
                title="Response sent",
                summary=f"The agent replied ({len(reply_text)} chars)",
                status="completed",
                timestamp=_now(),
                metadata={"response_length": len(reply_text)},
            )
        )

    return events


def events_to_payloads(
    events: List[AgentTransparencyEvent],
    *,
    audience: Audience = "owner",
) -> List[Dict[str, Any]]:
    """Convert a list of events into serialisable payloads for the given audience."""
    return [evt.to_user_payload() if audience != "customer" else evt.to_customer_payload() for evt in events]

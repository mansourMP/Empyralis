"""
Studio Agent transparency event emission for test/playground flows.

Produces AgentTransparencyEvent records for the simulated test-turn
pipeline.  Events are based on the real data available in the test
turn result — no fake events for stages that aren't simulated.

The actual production channel turn execution path lives in
channel_execution_service.execute_canonical_channel_turn and inherits
the Sage transparency events emitted by agent_turn.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from server_modules.agent_transparency_events import (
    AgentTransparencyEvent,
    Audience,
    VisibilityLevel,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_deployed_agent_test_turn_events(
    *,
    trace_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    user_message: str,
    test_result: Dict[str, Any],
    audience: Audience = "owner",
    visibility_level: VisibilityLevel = "standard",
) -> List[AgentTransparencyEvent]:
    """
    Emit transparency events for a Studio Agent test turn.

    Only stages with real data in the simulated test result are emitted.
    """
    events: List[AgentTransparencyEvent] = []
    aid = deployed_agent_id
    msg = str(user_message or "").strip()

    # 1. user_message_received
    if msg:
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=aid,
                actor_type="studio_agent",
                surface="studio_test",
                audience=audience,
                visibility_level=visibility_level,
                event_type="user_message_received",
                title="Test message received",
                summary=f"Test message: {msg[:200]}",
                status="completed",
                timestamp=_now(),
            )
        )

    # 2. memory_context
    memory_ctx = test_result.get("memory_context")
    if isinstance(memory_ctx, dict) and memory_ctx.get("applied"):
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=aid,
                actor_type="studio_agent",
                surface="studio_test",
                audience=audience,
                visibility_level=visibility_level,
                event_type="memory_loaded",
                title="Memory loaded",
                summary="Deployed agent memory context applied",
                status="completed",
                timestamp=_now(),
                memory_scope="studio_scoped" if not memory_ctx.get("owner_memory_included") else "workspace",
            )
        )
    elif isinstance(memory_ctx, dict) and not memory_ctx.get("applied") and memory_ctx:
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=aid,
                actor_type="studio_agent",
                surface="studio_test",
                audience=audience,
                visibility_level=visibility_level,
                event_type="memory_excluded",
                title="Memory excluded",
                summary="Memory was not applied for this test turn",
                status="completed",
                timestamp=_now(),
            )
        )

    # 3. policy_decisions
    policy_decisions = test_result.get("policy_decisions")
    if isinstance(policy_decisions, list):
        for pd in policy_decisions:
            if not isinstance(pd, dict):
                continue
            policy_name = str(pd.get("policy") or "")
            if pd.get("allowed") is False:
                events.append(
                    AgentTransparencyEvent(
                        event_id=f"stevt-{uuid4().hex[:12]}",
                        trace_id=trace_id,
                        workspace_id=workspace_id,
                        agent_id=aid,
                        actor_type="studio_agent",
                        surface="studio_test",
                        audience=audience,
                        visibility_level=visibility_level,
                        event_type="policy_blocked",
                        title=f"Policy blocked: {policy_name}",
                        summary=f"Policy '{policy_name}' blocked this action",
                        status="blocked",
                        timestamp=_now(),
                        metadata={"policy": policy_name},
                    )
                )

    # 4. tools_considered
    tools_considered = test_result.get("tools_considered")
    if isinstance(tools_considered, list):
        for tc in tools_considered:
            if not isinstance(tc, dict):
                continue
            skill_id = str(tc.get("skill_id") or tc.get("id") or "")
            tool_allowed = tc.get("allowed", True)
            if tool_allowed:
                events.append(
                    AgentTransparencyEvent(
                        event_id=f"stevt-{uuid4().hex[:12]}",
                        trace_id=trace_id,
                        workspace_id=workspace_id,
                        agent_id=aid,
                        actor_type="studio_agent",
                        surface="studio_test",
                        audience=audience,
                        visibility_level=visibility_level,
                        event_type="tool_selected",
                        title=f"Tool available: {skill_id}",
                        summary=f"Tool {skill_id} is allowed and available",
                        status="completed",
                        timestamp=_now(),
                        tool_name=skill_id or None,
                    )
                )
            else:
                events.append(
                    AgentTransparencyEvent(
                        event_id=f"stevt-{uuid4().hex[:12]}",
                        trace_id=trace_id,
                        workspace_id=workspace_id,
                        agent_id=aid,
                        actor_type="studio_agent",
                        surface="studio_test",
                        audience=audience,
                        visibility_level=visibility_level,
                        event_type="policy_blocked",
                        title=f"Tool blocked: {skill_id}",
                        summary=f"Tool {skill_id} is blocked by policy",
                        status="blocked",
                        timestamp=_now(),
                        tool_name=skill_id or None,
                    )
                )

    # 5. tools_used
    tools_used = test_result.get("tools_used")
    if isinstance(tools_used, list):
        for tu in tools_used:
            events.append(
                AgentTransparencyEvent(
                    event_id=f"stevt-{uuid4().hex[:12]}",
                    trace_id=trace_id,
                    workspace_id=workspace_id,
                    agent_id=aid,
                    actor_type="studio_agent",
                    surface="studio_test",
                    audience=audience,
                    visibility_level=visibility_level,
                    event_type="tool_completed",
                    title=f"Tool used: {tu}",
                    summary=f"Tool {tu} was used in this turn",
                    status="completed",
                    timestamp=_now(),
                    tool_name=str(tu) or None,
                )
            )

    # 6. approval_required
    if test_result.get("approval_required"):
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=aid,
                actor_type="studio_agent",
                surface="studio_test",
                audience=audience,
                visibility_level=visibility_level,
                event_type="safety_gate",
                title="Safety gate",
                summary="This action is gated by the current safety mode",
                status="running",
                timestamp=_now(),
            )
        )

    # 7. final_response_sent
    reply = str(test_result.get("reply") or "").strip()
    if reply:
        events.append(
            AgentTransparencyEvent(
                event_id=f"stevt-{uuid4().hex[:12]}",
                trace_id=trace_id,
                workspace_id=workspace_id,
                agent_id=aid,
                actor_type="studio_agent",
                surface="studio_test",
                audience=audience,
                visibility_level=visibility_level,
                event_type="final_response_sent",
                title="Response sent",
                summary=f"Test reply ({len(reply)} chars)",
                status="completed",
                timestamp=_now(),
                metadata={"response_length": len(reply)},
            )
        )

    return events

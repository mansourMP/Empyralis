"""Phase UB: Audience tool-catalog scoping — manifest-driven.

The audience filter reads `audience_safe` from each tool's payload (set by
ToolDescriptor.audience_safe in skills_service.py). This is the single source
of truth — no hardcoded lists. The owner can override by marking tools as
customer_facing.

PRESETS (defined in agent_presets.py):
  - customer_facing: serve-only, 8-tool set (task_complete, memory_search,
    memory_read, memory_get, web__search, web__fetch, sage_service__list_state,
    memory_list_versions), audience instructions ON
  - internal_assistant: owner-only toolset, no audience exposure
  - operator: Sage-class, fleet tools, owner sessions only

Each preset sets a default; owner overrides (customer_facing_tool_names etc.)
still apply on top.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


# ── Sender class resolution ──────────────────────────────────────────────────


def resolve_sender_class(
    *,
    identity_result: str,
    audience_enabled: bool = False,
) -> str:
    """Normalize identity result to sender class: "owner" | "audience" | "unknown"."""
    identity = str(identity_result or "").strip().lower()
    if identity == "owner":
        return "owner"
    if identity == "audience":
        return "audience"
    if audience_enabled:
        return "audience"
    return "unknown"


# ── Manifest-driven tool filter ──────────────────────────────────────────────


def is_tool_audience_safe(tool: Dict[str, Any]) -> bool:
    """Check if a tool is audience-safe by reading its `audience_safe` manifest field.

    The field is set in ToolDescriptor.audience_safe in skills_service.py.
    If the field is absent (legacy tool), defaults to False (safety-first).
    """
    if not isinstance(tool, dict):
        return False
    return bool(tool.get("audience_safe", False))


def tool_audience_note(tool: Dict[str, Any]) -> str:
    """Read the audience_note from a tool's manifest."""
    if not isinstance(tool, dict):
        return ""
    return str(tool.get("audience_note") or "").strip()


def filter_tools_for_audience(
    tools: List[Dict[str, Any]],
    *,
    customer_facing_tool_names: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Filter tool list to audience-safe subset using the capability manifest.

    Each tool's `audience_safe` field (from ToolDescriptor) drives the decision.
    Owner-approved tools (customer_facing_tool_names) bypass the check.

    Returns:
        Filtered tool list with audience_note preserved for behavioral instructions.
    """
    owner_approved = customer_facing_tool_names or set()
    filtered: List[Dict[str, Any]] = []

    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or tool.get("function", {}).get("name") or "").strip()
        if not name:
            continue

        if name in owner_approved:
            filtered.append(tool)
            continue

        if is_tool_audience_safe(tool):
            filtered.append(tool)

    return filtered


def blocked_tool_notes(tools: List[Dict[str, Any]], filtered: List[Dict[str, Any]]) -> str:
    """Generate audience_note lines for tools that were blocked.

    Flows into agent instructions so the model knows WHY tools are absent.
    """
    filtered_names = {
        str(t.get("name") or "").strip()
        for t in filtered
        if isinstance(t, dict)
    }
    notes: List[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name or name in filtered_names:
            continue
        note = tool_audience_note(tool)
        if note:
            notes.append(f"- **{name}**: {note}")

    if not notes:
        return ""

    return (
        "\n## Unavailable Tools (audience session)\n"
        "The following tools are not available in this session. "
        "If asked about them, explain they require the workspace owner:\n\n"
        + "\n".join(notes)
        + "\n"
    )


# ── Behavioral layer ─────────────────────────────────────────────────────────


def audience_behavior_instructions() -> str:
    """Return behavioral instructions for audience (non-owner) sessions."""
    return (
        "\n## Audience Session Rules\n"
        "You are serving a customer or audience member — NOT the workspace owner.\n"
        "Treat every request as a SERVICE REQUEST, never as a command.\n\n"
        "CRITICAL RULES:\n"
        "- You CAN: answer questions, look up information, check status, reply helpfully.\n"
        "- You CANNOT: change your own configuration, modify tools, write to memory,\n"
        "  run shell commands, access hardware, manage agents, modify connectors,\n"
        "  change workspace settings, or perform any privileged operation.\n"
        "- If asked to do something you cannot do: decline gracefully, explain it\n"
        "  requires the workspace owner, and offer an alternative way to help.\n"
        "- Never say \"I don't have permission\" — instead say \"That requires the\n"
        "  workspace owner to configure. I can help you with [available services].\"\n"
        "- Be warm but professional. You are a concierge, not a doorman.\n"
    )

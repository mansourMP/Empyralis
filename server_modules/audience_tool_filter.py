"""Phase U2: Audience tool-catalog scoping.

Enforces that AUDIENCE sessions only see serve-only tools — no shell, no hardware,
no fleet tools, no memory_write of instructions, no connector writes unless
owner-flagged as customer-facing.

OWNER sessions: full granted toolset.
AUDIENCE sessions: serve-only subset.
UNKNOWN sessions: restricted (same as audience for safety).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

# ── Tools that are ALWAYS allowed for audience sessions ─────────────────────
AUDIENCE_ALWAYS_ALLOWED: Set[str] = {
    # Communication
    "reply",
    "send_message",
    "send_photo",
    # Read-only memory (scoped to own session)
    "memory_read",
    "memory_search",
    "memory_lookup",
    # Web tools (read-only info gathering)
    "web__search",
    "web__fetch",
    "web_search",
    "web_fetch",
    # Lookup/booking-type tools
    "lookup",
    "search",
    "check_availability",
    "get_status",
    "list_items",
    # Universal
    "task_complete",
}

# ── Tools that are NEVER allowed for audience sessions ──────────────────────
AUDIENCE_NEVER_ALLOWED: Set[str] = {
    # Shell / execution
    "shell__exec",
    "shell_exec",
    "shell",
    "execute",
    "run_command",
    "bash",
    # File writes
    "file__write",
    "file_write",
    "filesystem__write",
    "filesystem_write",
    # Memory writes (instructions, config)
    "memory_write",
    "memory_update",
    "memory_set",
    "memory_store",
    "memory_save",
    # Fleet management
    "fleet_list_agents",
    "fleet_create_agent",
    "fleet_delete_agent",
    "fleet_update_agent",
    "fleet_manage",
    "agent_create",
    "agent_delete",
    "agent_configure",
    "deploy_agent",
    # Hardware / desktop control
    "hardware__",
    "computer_control",
    "screenshot",
    "clipboard",
    "mouse",
    "keyboard",
    "applescript",
    # Connector writes
    "connector_write",
    "connector_configure",
    "connector_create",
    "channel_configure",
    # Billing / workspace
    "billing",
    "workspace_settings",
    "workspace_configure",
    "install_agent",
    "uninstall_agent",
    # Dangerous
    "sudo",
    "admin",
    "root",
}

# ── Prefix patterns that are blocked for audience ───────────────────────────
AUDIENCE_BLOCKED_PREFIXES: tuple = (
    "shell",
    "file__write",
    "filesystem__write",
    "memory_write",
    "memory_save",
    "memory_set",
    "memory_update",
    "fleet",
    "agent_create",
    "agent_delete",
    "agent_update",
    "agent_configure",
    "hardware",
    "computer",
    "screenshot",
    "clipboard",
    "connector_write",
    "connector_configure",
    "billing",
    "workspace_",
    "sudo",
    "admin",
    "deploy",
)


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
        # Stricter: unknown on audience-enabled channel = audience
        return "audience"
    return "unknown"


def is_tool_audience_safe(tool_name: str) -> bool:
    """Check if a single tool is safe for audience sessions."""
    name = str(tool_name or "").strip()
    if not name:
        return False

    # Always allowed
    if name in AUDIENCE_ALWAYS_ALLOWED:
        return True

    # Never allowed
    if name in AUDIENCE_NEVER_ALLOWED:
        return False

    # Check blocked prefixes
    for prefix in AUDIENCE_BLOCKED_PREFIXES:
        if name.startswith(prefix):
            return False

    # Default: block unknown tools for audience sessions (safety-first)
    return False


def filter_tools_for_audience(
    tools: List[Dict[str, Any]],
    *,
    customer_facing_tool_names: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Filter tool list to audience-safe subset.

    Args:
        tools: Full tool list the model would normally see.
        customer_facing_tool_names: Optional set of tool names the owner has
            explicitly marked as customer-facing. These bypass the blocklist.

    Returns:
        Filtered tool list suitable for audience sessions.
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

        if is_tool_audience_safe(name):
            filtered.append(tool)

    return filtered


def audience_behavior_instructions() -> str:
    """Return behavioral instructions for audience (non-owner) sessions.

    These are appended to the system prompt when sender_class != "owner".
    """
    return (
        "\n## Audience Session Rules (Phase U2)\n"
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

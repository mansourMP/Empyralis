"""
Agent policy context builder — injects INTERNALIZED governance into the
system prompt so the agent decides, the platform audits silently.

Consumers NEVER see approval buttons, permission dialogs, or "blocked" cards.
Governance becomes invisible to the consumer — the agent uses its own judgment
with policy context injected each turn.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# ── Tier definitions ──────────────────────────────────────────────────────

class AgentTier:
    T0 = "cloud_only"       # web, API, content, memory — no sandbox
    T1 = "cloud_compute"    # + cloud sandbox (browser, shell, file in VM)
    T2 = "hardware"         # + full hardware (shell, filesystem, browser on user machine)


# Plans that map to each tier
PLAN_TIER_MAP: Dict[str, str] = {
    "free": AgentTier.T0,
    "pro": AgentTier.T1,
    "pilot": AgentTier.T2,
    "enterprise": AgentTier.T2,
}

# Fallback tier
DEFAULT_TIER = AgentTier.T0

# Capabilities available per tier
TIER_CAPABILITIES: Dict[str, list[str]] = {
    AgentTier.T0: [
        "web_search", "web_fetch", "memory_read", "memory_write",
        "content_generation", "connector_read",
    ],
    AgentTier.T1: [
        "web_search", "web_fetch", "memory_read", "memory_write",
        "content_generation", "connector_read", "connector_write",
        "cloud_browser", "cloud_shell", "cloud_file_access",
    ],
    AgentTier.T2: [
        "web_search", "web_fetch", "memory_read", "memory_write",
        "content_generation", "connector_read", "connector_write",
        "cloud_browser", "cloud_shell", "cloud_file_access",
        "local_shell", "local_filesystem", "local_browser",
        "screenshot", "computer_control",
    ],
}

# Platform rules injected into system prompt
PLATFORM_RULES = (
    "- Never delete system-critical files or directories.\n"
    "- Before destructive operations (rm, format, delete), briefly explain what "
    "you're about to do and why — then proceed.\n"
    "- Respect the consumer's stated preferences and prior instructions.\n"
    "- Do not access credential files, private keys, or password stores unless "
    "the consumer explicitly asks you to."
)

# Hardware access notice (shown ONCE at setup — injected here for agent awareness)
HARDWARE_ACCESS_NOTICE = (
    "You have full access to the consumer's machine. They were informed at setup "
    "that you can read, write, execute, browse, and control their computer. "
    "Use this access freely and naturally — do not ask for permission before "
    "each action."
)

# Kill switch notice (injected when kill switch is active)
KILL_SWITCH_NOTICE = (
    "Your capabilities are currently suspended by the platform. Respond helpfully "
    "but do not attempt tool calls. Do not mention the suspension or that you are "
    "being restricted — simply say you cannot do that right now and suggest the "
    "consumer try again later."
)


def resolve_agent_tier(
    *,
    workspace_id: str = "",
    plan_id: str = "free",
    has_hardware_profile: bool = False,
    is_agent_machine: bool = False,
) -> str:
    """Resolve the consumer's agent tier from plan and hardware profile status.

    Returns one of: AgentTier.T0, AgentTier.T1, AgentTier.T2
    """
    normalized_plan = str(plan_id or "free").strip().lower()
    base_tier = PLAN_TIER_MAP.get(normalized_plan, DEFAULT_TIER)

    # Agent machine mode always gets T2
    if is_agent_machine:
        return AgentTier.T2

    # Hardware profile attached upgrades cloud_compute → hardware.
    # Hardware is user-owned, not platform-gated — if the user has
    # explicitly connected and selected a computer, they get T2.
    if has_hardware_profile and base_tier != AgentTier.T2:
        return AgentTier.T2

    return base_tier


def build_agent_policy_context(
    *,
    workspace_id: str = "",
    plan_id: str = "free",
    has_hardware_profile: bool = False,
    is_agent_machine: bool = False,
    kill_switch_active: bool = False,
    allowed_capabilities: Optional[list[str]] = None,
) -> str:
    """Build a structured policy block to inject into the agent's system prompt.

    This replaces external governance gates (approval buttons, blocked cards)
    with INTERNALIZED agent judgment. The agent receives its tier, capabilities,
    and any restrictions as natural context each turn.

    Returns a string suitable for appending to the system prompt.
    """
    tier = resolve_agent_tier(
        workspace_id=workspace_id,
        plan_id=plan_id,
        has_hardware_profile=has_hardware_profile,
        is_agent_machine=is_agent_machine,
    )

    caps = allowed_capabilities or TIER_CAPABILITIES.get(tier, TIER_CAPABILITIES[AgentTier.T0])
    caps_text = ", ".join(caps)

    tier_label = {
        AgentTier.T0: "Cloud-Only",
        AgentTier.T1: "Cloud Compute",
        AgentTier.T2: "Hardware",
    }.get(tier, "Cloud-Only")

    sections: list[str] = []

    # ── Section 1: Tier and capabilities ──
    sections.append(
        f"## Your Platform Tier: {tier_label}\n"
        f"Available capabilities: {caps_text}.\n"
        f"Use any of these freely. Do not ask for permission."
    )

    # ── Section 2: Platform rules ──
    sections.append(f"## Platform Rules\n{PLATFORM_RULES}")

    # ── Section 3: Hardware access (T2 only) ──
    if tier == AgentTier.T2:
        sections.append(f"## Hardware Access\n{HARDWARE_ACCESS_NOTICE}")

    # ── Section 4: Kill switch (if active) ──
    if kill_switch_active:
        sections.append(f"## IMPORTANT: Capabilities Suspended\n{KILL_SWITCH_NOTICE}")

    return "\n\n".join(sections)


def build_agent_policy_context_from_workspace(
    *,
    workspace_id: str,
    tenant_id: str = "",
    check_kill_switch: bool = True,
) -> str:
    """Convenience wrapper that resolves tier/plan/hardware from the workspace.

    Uses the existing platform services to determine:
    - plan_id from workspace billing / entitlements
    - hardware profile presence from runtime_attachment_service
    - kill switch status from kill_switch_gate
    """
    import os
    from server_modules import kill_switch_gate

    # ── Plan ID ──
    plan_id = "free"
    try:
        from server_modules import entitlements_service
        plan = entitlements_service.get_workspace_plan(workspace_id)
        if isinstance(plan, dict):
            plan_id = str(plan.get("plan_id") or plan.get("id") or "free").strip().lower()
    except Exception:
        pass

    # ── Hardware profile ──
    # Note: list_workspace_runtime_attachments is async — callers should
    # resolve hardware status beforehand and pass it as has_hardware_profile.
    # This sync wrapper skips the async call to avoid unawaited coroutine warnings.
    has_hardware = False  # Callers override this via build_agent_policy_context() directly

    # ── Kill switch ──
    kill_active = False
    if check_kill_switch:
        try:
            decision = kill_switch_gate.evaluate_kill_switch(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
            )
            kill_active = decision.blocked
        except Exception:
            pass

    # ── Agent machine mode ──
    is_agent_machine = os.getenv("AGENT_MACHINE_MODE", "personal").strip().lower() == "agent"

    return build_agent_policy_context(
        workspace_id=workspace_id,
        plan_id=plan_id,
        has_hardware_profile=has_hardware,
        is_agent_machine=is_agent_machine,
        kill_switch_active=kill_active,
    )

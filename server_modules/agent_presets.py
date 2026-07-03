"""Phase UB: Agent presets — formalizing audience authority into product.

Three presets that pre-configure an agent's tool access + identity rules.
Owner overrides (customer_facing_tool_names etc.) apply on top of any preset.

Presets:
  customer_facing   — Serve-only, read-only memory, no privileged tools.
                      Default when binding to an audience-enabled channel.
  internal_assistant — Owner-only toolset, no audience exposure.
  operator           — Sage-class: fleet tools, full access, owner sessions only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


# ── Preset definitions ───────────────────────────────────────────────────────

AGENT_PRESETS: Dict[str, Dict[str, Any]] = {
    "customer_facing": {
        "id": "customer_facing",
        "label": "Customer-Facing",
        "description": (
            "Serve-only agent for public and audience channels. "
            "Can reply, search memory (read-only), look up information, "
            "and check status. No shell, hardware, fleet, memory write, "
            "or connector write access."
        ),
        "triage_enabled": True,
        "identity_rules": [
            {"match": "owner", "behavior": "full"},
            {"match": "audience", "behavior": "restricted"},
            {"match": "unknown", "behavior": "restricted"},
        ],
        "audience_enabled": True,
        "audience_instructions": True,
        # Tools the owner has explicitly approved for customer use
        # (these bypass the audience_safe=False block in the manifest)
        "customer_facing_tool_names": [],
    },
    "internal_assistant": {
        "id": "internal_assistant",
        "label": "Internal Assistant",
        "description": (
            "Owner-only agent for internal workspace tasks. "
            "Full toolset available, no audience exposure. "
            "Ideal for admin, operations, and private work."
        ),
        "triage_enabled": False,
        "identity_rules": [
            {"match": "owner", "behavior": "full"},
            {"match": "audience", "behavior": "silent"},
            {"match": "unknown", "behavior": "silent"},
        ],
        "audience_enabled": False,
        "audience_instructions": False,
        "customer_facing_tool_names": [],
    },
    "operator": {
        "id": "operator",
        "label": "Operator (Sage)",
        "description": (
            "Sage-class operator agent. Fleet management, full toolset, "
            "owner sessions only. Can create/configure/delete agents, "
            "manage channels, connectors, hardware, memory, and billing."
        ),
        "triage_enabled": False,
        "identity_rules": [
            {"match": "owner", "behavior": "full"},
            {"match": "audience", "behavior": "silent"},
            {"match": "unknown", "behavior": "silent"},
        ],
        "audience_enabled": False,
        "audience_instructions": False,
        "customer_facing_tool_names": [],
    },
}


def resolve_preset(preset_id: str) -> Dict[str, Any]:
    """Resolve a preset by ID. Returns customer_facing if unknown."""
    preset_id_clean = str(preset_id or "").strip().lower()
    if preset_id_clean in AGENT_PRESETS:
        return dict(AGENT_PRESETS[preset_id_clean])
    # Default: customer_facing (safety-first for audience channels)
    return dict(AGENT_PRESETS["customer_facing"])


def preset_for_channel_binding(
    *,
    channel_type: str,
    audience_enabled: bool = False,
    owner_explicit_preset: Optional[str] = None,
) -> str:
    """Determine which preset to apply when binding an agent to a channel.

    Rules (in priority order):
      1. Owner explicitly chose a preset → use it.
      2. Channel has audience enabled → customer_facing (safety-first).
      3. Otherwise → internal_assistant.
    """
    if owner_explicit_preset and str(owner_explicit_preset).strip():
        explicit = str(owner_explicit_preset).strip().lower()
        if explicit in AGENT_PRESETS:
            return explicit

    if audience_enabled:
        return "customer_facing"

    return "internal_assistant"


def apply_preset_to_triage_config(
    preset_id: str,
    *,
    owner_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a triage config from a preset, with owner overrides on top.

    Returns the full triage config dict ready for resolve_triage_config().
    """
    preset = resolve_preset(preset_id)
    overrides = dict(owner_overrides or {})

    return {
        "enabled": bool(overrides.get("triage_enabled", preset.get("triage_enabled", False))),
        "scope_description": str(
            overrides.get("scope_description", preset.get("scope_description", ""))
        ).strip(),
        "out_of_scope_behavior": str(
            overrides.get("out_of_scope_behavior", preset.get("out_of_scope_behavior", "polite_decline"))
        ).strip().lower(),
        "identity_rules": overrides.get("identity_rules", preset.get("identity_rules", [])),
        "audience_enabled": bool(overrides.get("audience_enabled", preset.get("audience_enabled", False))),
        "audience_instructions": bool(overrides.get("audience_instructions", preset.get("audience_instructions", False))),
        "customer_facing_tool_names": list(
            overrides.get("customer_facing_tool_names", preset.get("customer_facing_tool_names", []))
        ),
        "uncertain_goes_to_full_loop": True,
    }

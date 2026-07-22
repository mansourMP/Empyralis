"""Capability presets for agent creation (Phase 5B).

A capability preset seeds an agent install's DEFAULTS along the capability axis
(hardware, tools, model tier, subagents, context policy). This is distinct from
the existing *purpose* presets in fleet_tools.py's _VALID_PURPOSE_PRESETS
(customer_facing / internal_assistant / operator), which shape
instructions/audience. (The old agent_presets.py module — a separate,
never-wired purpose-preset implementation whose only real payload was
triage config — was removed 2026-07-23 as orphaned by the founder ruling
that cut the Phase P input-blocking gate it fed.)

Three capability presets:
  - knowledge: a read/answer agent. Hardware access DENIED at the policy level
    and LOCKED (can't be granted without an explicit preset change + ledger),
    tools limited to docs/memory + MCP connectors, cheap model tier, subagents
    off, aggressive (compact-early) context policy.
  - standard: current defaults — nothing constrained beyond the normal
    specialist baseline.
  - operator: Sage-class (fleet tools, full access). Reserved — NOT creatable
    through the normal create-agent flow.

Presets are DEFAULTS, not cages: every field stays overridable via
fleet_configure_agent AFTER creation, with one exception — a knowledge agent's
hardware access is policy-locked and can only change by changing the preset.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

PRESET_KNOWLEDGE = "knowledge"
PRESET_STANDARD = "standard"
PRESET_OPERATOR = "operator"

VALID_CAPABILITY_PRESETS = {PRESET_KNOWLEDGE, PRESET_STANDARD, PRESET_OPERATOR}
# Presets a normal (non-Sage) create flow may request.
CREATABLE_CAPABILITY_PRESETS = {PRESET_KNOWLEDGE, PRESET_STANDARD}

# Read-only / safe-basics toolset: memory + docs/web read + task completion.
# No shell, hardware, fleet, or write tools. MCP connectors are added on top
# per the agent's connector bindings. These are LLM-dispatch tool names (the
# format _resolve_specialist_toolset enforces at runtime, e.g. sage_agent_
# runtime_service.py's _specialist_tool_allowed) — NOT skill_registry.py's
# hyphenated display ids (web-search, memory-manager, ...). The two id spaces
# are presently disconnected (fleet_get_agent_tools' Tools-tab toggle display
# is keyed by the hyphenated id, enforcement is keyed by this underscore
# name), so seeding this list makes the tools actually callable but will not
# show as "on" in the Tools tab — a pre-existing, separate display bug this
# preset does not attempt to fix.
_SAFE_DEFAULT_TOOLS: List[str] = [
    "memory_search",
    "memory_get",
    "memory_read",
    "memory_list_versions",
    "web__search",
    "web__fetch",
    "task_complete",
]
_KNOWLEDGE_TOOLS: List[str] = _SAFE_DEFAULT_TOOLS

CAPABILITY_PRESETS: Dict[str, Dict[str, Any]] = {
    PRESET_KNOWLEDGE: {
        "id": PRESET_KNOWLEDGE,
        "label": "Knowledge",
        "description": "Read/answer agent — docs + MCP connectors only, no hardware, cheap model, compact context.",
        "hardware_access": "none",
        "hardware_locked": True,
        "subagents_enabled": False,
        "enabled_tools": list(_KNOWLEDGE_TOOLS),
        "model_tier": "cheap",
        "context_budget_preset": "compact",
        "context_policy": {"max_context_tokens": 8000, "on_context_full": "compact"},
    },
    PRESET_STANDARD: {
        "id": PRESET_STANDARD,
        "label": "Standard",
        "description": "Current defaults — normal specialist baseline.",
        "hardware_access": "none",
        "hardware_locked": False,
        "subagents_enabled": False,
        # Safe basics ON at creation (web search, memory read, task
        # completion) so a fresh agent can do something useful on its first
        # turn instead of failing every tool call silently. Hardware, shell,
        # write, and fleet-management tools stay OFF — the owner opts in via
        # the Tools tab. Still fully overridable post-creation.
        "enabled_tools": list(_SAFE_DEFAULT_TOOLS),
        "model_tier": "standard",
        "context_budget_preset": "standard",
        "context_policy": {"max_context_tokens": 0, "on_context_full": "compact"},  # 0 = use model default
    },
    PRESET_OPERATOR: {
        "id": PRESET_OPERATOR,
        "label": "Operator",
        "description": "Sage-class operator (fleet tools, full access). Reserved.",
        "hardware_access": "all",
        "hardware_locked": False,
        "subagents_enabled": True,
        "enabled_tools": None,
        "model_tier": "standard",
        "context_budget_preset": "extended",
        "context_policy": {"max_context_tokens": 0, "on_context_full": "compact"},
    },
}


def normalize_capability_preset(preset_id: Any, *, default: str = PRESET_STANDARD) -> str:
    token = str(preset_id or "").strip().lower()
    return token if token in VALID_CAPABILITY_PRESETS else default


def resolve_capability_preset(preset_id: Any) -> Dict[str, Any]:
    return dict(CAPABILITY_PRESETS[normalize_capability_preset(preset_id)])


def is_creatable(preset_id: Any) -> bool:
    return normalize_capability_preset(preset_id) in CREATABLE_CAPABILITY_PRESETS


def build_install_defaults(preset_id: Any) -> Dict[str, Any]:
    """Return the install fields a preset seeds:
      - metadata additions (capability_preset, model_tier, context_policy, etc.)
      - hardware_access (column value)
      - subagents_enabled (column value)
      - enabled_tools (column value, or None to inherit)
      - policy_context_overrides additions (hardware lock for knowledge)
    """
    preset = resolve_capability_preset(preset_id)
    pid = preset["id"]
    policy_overrides: Dict[str, Any] = {}
    if preset.get("hardware_locked"):
        policy_overrides["hardware_access_locked"] = True
        policy_overrides["hardware_access"] = "none"
    metadata = {
        "capability_preset": pid,
        "model_tier": preset.get("model_tier"),
        "context_budget_preset": preset.get("context_budget_preset"),
        "context_policy": dict(preset.get("context_policy") or {}),
        "hardware_access_locked": bool(preset.get("hardware_locked")),
    }
    return {
        "metadata": metadata,
        "hardware_access": preset.get("hardware_access", "none"),
        "subagents_enabled": bool(preset.get("subagents_enabled")),
        "enabled_tools": preset.get("enabled_tools"),
        "policy_context_overrides": policy_overrides,
        "context_policy": dict(preset.get("context_policy") or {}),
    }


def hardware_is_locked(install: Optional[Dict[str, Any]]) -> bool:
    """True when this install's hardware access is policy-locked (knowledge
    preset). Checked from both metadata and policy_context_overrides."""
    if not isinstance(install, dict):
        return False
    meta = install.get("install_metadata") or install.get("metadata") or {}
    if isinstance(meta, dict) and bool(meta.get("hardware_access_locked")):
        return True
    pco = install.get("policy_context_overrides") or {}
    if isinstance(pco, dict) and bool(pco.get("hardware_access_locked")):
        return True
    if isinstance(meta, dict) and normalize_capability_preset(meta.get("capability_preset"), default="") == PRESET_KNOWLEDGE:
        return True
    return False

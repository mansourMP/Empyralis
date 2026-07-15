"""Specialist runtime context resolution (Phase 4).

When an inbound channel turn resolves to a specific agent install, the turn must
run as THAT agent — its persona, model/provider binding, and memory scope — not
the shared Sage runtime with an attributed label (the 3B behaviour).

This module owns the decision: "given the active install for this turn, is it a
specialist, and if so what persona/model/scope does it run under?" It returns
None for the workspace master (Sage), so the existing Sage path stays byte-for-
byte unchanged.

The two hard guarantees a specialist context carries:
  - agent_install_id: the ACTING install id. Memory namespaces
    (agent_memory._memory_db_path) and the fleet-tool operator gate
    (tool_broker._enforce_fleet_tool_role) both key off this, so running under
    the specialist's id automatically isolates its memory and denies it
    operator powers.
  - is_specialist: specialists never receive fleet_tools / operator powers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

MASTER_AGENT_KIND = "master"
SPECIALIST_AGENT_KIND = "specialist"


@dataclass
class SpecialistRuntimeContext:
    """Resolved runtime identity for a specialist turn."""

    agent_install_id: str
    agent_label: str
    agent_kind: str
    persona: str
    provider: str = ""
    model: str = ""
    # BYO-brain: the agent's model_config binding. mode selects the brain lane
    # (platform_credits | byok_api | cli_subscription | local); gateway_binding
    # names the paired box that runs it; runtime is the on-box engine
    # (claude_code | codex | ollama). Empty mode = platform default.
    mode: str = ""
    gateway_binding: str = ""
    runtime: str = ""
    # Reasoning-effort override from the Fleet Model tab (model_config.
    # reasoning_effort) — one of _VALID_REASONING_EFFORTS in
    # sage_agent_runtime_service.py ("low"/"medium"/"high"/"xhigh"), or empty
    # for "no override" (provider/model default). Only meaningful for
    # mode in (platform_credits, byok_api) — those are the two lanes that
    # reach stream_provider_backed_direct_chat, the one call site that
    # actually consumes it (natively for models it recognizes as reasoning
    # models, degraded to a system-prompt instruction otherwise — see that
    # function's own "Reasoning effort logic" block). cli_subscription/local
    # dispatch to the Gateway instead, which has no reasoning_effort
    # plumbing today — an override saved under those modes is carried here
    # but never read by handle_sage_chat's gateway-dispatch branches, so it
    # has no effect (matches the Fleet UI, which hides the picker for those
    # two modes rather than promising something that doesn't happen yet).
    reasoning_effort: str = ""
    # The paired box this specialist's TOOL calls (shell/file/browser) prefer,
    # distinct from gateway_binding above (which names the box that hosts the
    # AI brain itself, only used in local/cli_subscription mode). Empty = no
    # preference; dispatch falls back to any live gateway in the workspace.
    preferred_gateway_id: str = ""
    project_id: str = ""
    context_policy: Dict[str, Any] = field(default_factory=dict)
    is_specialist: bool = True
    source: Dict[str, Any] = field(default_factory=dict)

    def memory_scope(self) -> str:
        """The memory namespace this turn reads/writes under — the install id."""
        return self.agent_install_id


def _text(value: Any) -> str:
    return str(value or "").strip()


def _default_specialist_persona(label: str) -> str:
    name = label or "this specialist"
    return (
        f"You are {name}, a specialist agent in this workspace. When someone "
        "greets you or asks who you are, introduce yourself by name and briefly "
        "say what you help with — even if your specific purpose hasn't been "
        "configured yet — instead of asking who they are. "
        "You handle only the work you were configured for. You do NOT manage the "
        "fleet, create or reconfigure other agents, or take workspace-operator "
        "actions — those belong to Sage, the operator. If a request falls outside "
        "your scope, escalate it to the operator instead of acting."
    )


def _persona_from_bundle(bundle: Mapping[str, Any], label: str) -> str:
    """Per-agent customization always wins. `instructions` is the field the
    product actually writes (the creation wizard's one-liner + Settings' own
    Persona editor); `persona`/`system_prompt` are kept as forward-compat keys
    in case something else starts writing them. Deliberately never falls back
    to the agent definition's manifest `default_prompt`/`system_prompt`: that
    text is shared across every install of a definition, not per-agent, and
    isn't meant to be shown to a real end user (see agent_registry_repository's
    fleet-specialist definition, which used to ship an owner-onboarding script
    here — the "who are you and who am I" bug)."""
    meta = bundle.get("metadata") if isinstance(bundle.get("metadata"), dict) else {}
    persona = _text(meta.get("instructions")) or _text(meta.get("persona")) or _text(meta.get("system_prompt"))
    return persona or _default_specialist_persona(label)


def _model_provider_from(bundle: Mapping[str, Any], metadata: Optional[Mapping[str, Any]]) -> tuple[str, str]:
    """Prefer the per-turn metadata (already populated by
    channel_turn_request_service from the install), then the install metadata."""
    meta = dict(metadata or {})
    inst_meta = bundle.get("metadata") if isinstance(bundle.get("metadata"), dict) else {}
    model_config = inst_meta.get("model_config") if isinstance(inst_meta.get("model_config"), dict) else {}
    provider = _text(meta.get("provider")) or _text(inst_meta.get("provider")) or _text(model_config.get("provider")) or _text(model_config.get("resolved_provider"))
    model = _text(meta.get("model")) or _text(inst_meta.get("model")) or _text(model_config.get("model")) or _text(model_config.get("resolved_model"))
    return provider, model


def _agent_kind(bundle: Mapping[str, Any]) -> str:
    definition = bundle.get("agent_definition") if isinstance(bundle.get("agent_definition"), dict) else {}
    kind = _text(definition.get("agent_kind")).lower()
    return kind or SPECIALIST_AGENT_KIND


async def resolve_specialist_runtime_context(
    *,
    workspace_id: str,
    tenant_id: str,
    active_agent_install_id: Optional[str],
    metadata: Optional[Mapping[str, Any]] = None,
) -> Optional[SpecialistRuntimeContext]:
    """Return a SpecialistRuntimeContext when this turn should run as a specialist,
    or None when it should run as the workspace master (Sage) unchanged.

    None is returned when: no active install id is given; the active install is
    the workspace master; or the install can't be resolved (fail safe to Sage).
    """
    active_id = _text(active_agent_install_id)
    if not active_id:
        return None

    from server_modules import agent_registry_repository as reg

    try:
        master = await reg.get_workspace_master_agent_install(tenant_id=tenant_id or "default", workspace_id=workspace_id)
    except Exception:
        master = None
    master_id = _text((master or {}).get("id"))
    if active_id == master_id:
        return None  # the master (Sage) runs its normal runtime

    try:
        bundle = await reg.get_workspace_agent_install_bundle(active_id, tenant_id=tenant_id or "default", workspace_id=workspace_id)
    except Exception as exc:
        logger.warning("specialist context: failed to load install %s: %s — falling back to Sage", active_id, exc)
        return None
    if not isinstance(bundle, dict):
        return None

    kind = _agent_kind(bundle)
    if kind == MASTER_AGENT_KIND:
        return None  # a second master, if it ever exists, keeps the Sage runtime

    label = _text(bundle.get("label")) or active_id
    persona = _persona_from_bundle(bundle, label)
    provider, model = _model_provider_from(bundle, metadata)
    _inst_meta = bundle.get("metadata") if isinstance(bundle.get("metadata"), dict) else {}
    _ctx_policy = _inst_meta.get("context_policy") if isinstance(_inst_meta.get("context_policy"), dict) else {}
    _model_config = _inst_meta.get("model_config") if isinstance(_inst_meta.get("model_config"), dict) else {}

    return SpecialistRuntimeContext(
        agent_install_id=active_id,
        agent_label=label,
        agent_kind=kind,
        persona=persona,
        provider=provider,
        model=model,
        mode=_text(_model_config.get("mode")).lower(),
        gateway_binding=_text(_model_config.get("gateway_binding")),
        runtime=_text(_model_config.get("runtime")).lower(),
        reasoning_effort=_text(_model_config.get("reasoning_effort")).lower(),
        preferred_gateway_id=_text(_inst_meta.get("preferred_gateway_id")),
        project_id=_text(bundle.get("project_id")),
        context_policy=dict(_ctx_policy),
        is_specialist=True,
        source={"resolved_from": "install_bundle", "master_id": master_id},
    )

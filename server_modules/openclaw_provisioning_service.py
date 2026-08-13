"""Cloud half of OpenClaw provisioning (CHANNEL-ADOPTION-PLAN.md step 4).

Turns Empyralis's STORED policy into a `openclaw.provision` capability
invocation on the gateway that runs the customer's OpenClaw instance. Dispatch
shape mirrors gateway_doctor_service.run_gateway_doctor() /
gateway_self_update_service.trigger_gateway_self_update(): one
gateway_execution_service.execute_tool_via_gateway() call over the gateway's
existing outbound cloud WS, no SSH, no second control channel.

WHY THIS EXISTS — the load-bearing fact from step 2
---------------------------------------------------
OpenClaw's own config is a SECOND authorization policy store, and it runs
FIRST: `decideChannelIngress` can drop a message with an effect literally named
"block-dispatch" before our tap ever fires. So `dmPolicy` / `groupPolicy` /
`requireMention` in THEIR config decide what Empyralis ever sees; our three
gates can only narrow it further.

If their config is not generated from this database, the owner's Empyralis
settings silently stop describing reality — CLAUDE.md's "silent misrouting
beats loud failure", the failure mode that already cost this product a banned
account. This module is the one place that reads the policy and pushes it.

WHAT IS SENT, AND WHAT IS NOT
-----------------------------
Policy only, in Empyralis's own vocabulary (dm_policy / group_policy exactly as
personal_channels_service stores them). No OpenClaw config is rendered here —
that happens on the box, in
empyralis-gateway/src/openclaw/provisioning/openclaw-config-plan.ts, next to
the version pin and the schema audit that decide whether a given rendering is
valid against the OpenClaw actually installed. A cloud-side renderer would be
a second renderer that cannot see which build it is rendering for.

No secrets are sent either: the box already holds both the OpenClaw gateway
token and the loopback bridge secret (empyralis-gateway/src/config.ts).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4

from server_modules import (
    agent_bindings_repository,
    agent_registry_repository,
    gateway_execution_service,
    openclaw_channel_registry,
    personal_channels_service,
)

_logger = logging.getLogger(__name__)

# Must match OPENCLAW_PROVISION_CAPABILITY in empyralis-gateway/src/openclaw/
# provisioning/openclaw-provisioning-runtime.ts.
OPENCLAW_PROVISION_CAPABILITY = "openclaw.provision"

# Locating the binary, reading a 2.5MB config schema, patching, reading back
# six config subtrees, running `openclaw security audit`, and writing a
# launchd/systemd unit. Every step is bounded on the box; this is the outer
# bound, generous over the audit (the slowest, and the only one that shells out
# to Docker probes).
DEFAULT_PROVISION_TIMEOUT_SECONDS = 180


class OpenClawProvisioningError(RuntimeError):
    """Raised by provision_openclaw_gateway(); a route maps status_code +
    message straight into an HTTPException, same contract as
    GatewayDoctorError."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def openclaw_channel_id(channel_key: str) -> str:
    """`openclaw_feishu` -> `feishu`.

    No longer a bare prefix strip. The suffix IS OpenClaw's own channel id —
    an invariant that used to rest on five hand-typed strings and was violated
    exactly once (`openclaw_qq` vs their `qqbot`), breaking the lane in both
    directions and silently. Those keys are now generated from OpenClaw's own
    registry, so the invariant holds by construction; this lookup is what
    makes it hold at RUNTIME too, so a key that reaches here with no real
    channel behind it fails at this call rather than arriving at OpenClaw as
    "unsupported channel" — or, on the way in, not failing at all.
    """
    return openclaw_channel_registry.openclaw_channel_id(channel_key)


async def channels_in_use(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
) -> set:
    """The OpenClaw `channel_key`s this owner has actually reached for.

    WHY THIS QUESTION IS SEPARATE FROM "WHAT IS THIS CHANNEL'S POLICY"
    ------------------------------------------------------------------
    Policy is pushed for EVERY channel on every run — build_openclaw_channel_
    policies below explains why, and that stays true. But twenty of OpenClaw's
    twenty-seven channels are separate npm packages that provisioning now
    installs, and installing all twenty on every customer's machine to write a
    policy nobody asked for would be minutes of network per boot plus twenty
    third-party packages loaded inside the instance that carries their
    messages. So "what may this channel do" and "does this box need this
    channel's code" are asked separately, and only the second is narrowed.

    TWO SIGNALS, UNIONED, AND WHY NEITHER ALONE WOULD DO
    -----------------------------------------------------
      1. an ENABLED `agent_channel_bindings` row — agent_bindings_repository's
         own definition of connected ("credential scoped to agent exists AND
         binding row enabled"). Authoritative, but it is written only once a
         session reaches `connected`, and a session cannot connect without the
         plugin. Alone, it deadlocks: no plugin -> no connection -> no binding
         -> no plugin.
      2. a stored policy KEY for that channel in the agent's install metadata.
         Written the moment the owner touches the channel's settings at all,
         which is the earliest honest signal of intent and breaks the deadlock.

    Key PRESENCE is the test, never the policy's value: the loaders normalize a
    missing entry into a full default document, so a value comparison cannot
    tell "never configured" from "configured, and happens to match the
    default" (personal_channels_service._normalize_dm_policy_config(None)
    returns exactly what a stored default returns).

    Fails CLOSED — an unreadable install bundle or an unavailable control plane
    yields fewer channels, never more. The cost of a false negative is a
    reported `installed: false` the owner can act on; the cost of a false
    positive is third-party code fetched onto a machine we do not own.
    """
    in_use: set = set()
    openclaw_keys = set(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS)

    try:
        bindings = await agent_bindings_repository.list_agent_channel_bindings(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_install_id=agent_id,
            enabled_only=True,
        )
    except Exception:
        _logger.warning(
            "could not read agent channel bindings for agent_id=%s; treating no channel as bound",
            agent_id,
            exc_info=True,
        )
        bindings = []
    for row in bindings or []:
        key = str((row or {}).get("key") or "").strip()
        if key in openclaw_keys:
            in_use.add(key)

    try:
        install = await agent_registry_repository.get_workspace_agent_install_bundle(
            agent_id, tenant_id=tenant_id, workspace_id=workspace_id
        )
    except Exception:
        _logger.warning(
            "could not read the agent install bundle for agent_id=%s; treating no channel as configured",
            agent_id,
            exc_info=True,
        )
        install = None
    meta = (install or {}).get("install_metadata") or (install or {}).get("metadata") or {}
    if isinstance(meta, dict):
        for policy_field in ("dm_policy", "group_policy"):
            stored = meta.get(policy_field)
            if not isinstance(stored, dict):
                continue
            for key in stored:
                normalized = str(key or "").strip()
                if normalized in openclaw_keys:
                    in_use.add(normalized)

    return in_use


async def _resolve_channel_owners_for_gateway(
    *,
    tenant_id: str,
    workspace_id: str,
    gateway_id: str,
) -> Dict[str, List[str]]:
    """channel_key -> agent_ids (among the agents that share this gateway,
    i.e. install_metadata.preferred_gateway_id == gateway_id) that hold an
    ENABLED agent_channel_bindings row for it.

    Empty when gateway_id is blank or fewer than two agents share this
    gateway — the ordinary single-agent-per-box case is untouched and costs
    nothing extra. One entry per channel with more than zero owners; a
    channel with exactly one owner is unambiguous (compose using that
    agent's policy — see the caller), a channel with more than one owner is
    a real conflict OpenClaw's own config schema cannot express (verified
    against the pinned build's own `openclaw config schema`: every channel
    node is a single account, never a named-accounts map).

    ONE binding read per gateway-sharing agent, not one per channel — a
    provisioning call touches every OpenClaw channel, so this is computed
    once and reused, never re-derived per channel."""
    if not str(gateway_id or "").strip():
        return {}
    gateway_agents = await personal_channels_service.agents_sharing_gateway(
        tenant_id=tenant_id, workspace_id=workspace_id, gateway_id=gateway_id,
    )
    if len(gateway_agents) <= 1:
        return {}
    owners: Dict[str, List[str]] = {}
    for candidate_id in gateway_agents:
        try:
            bindings = await agent_bindings_repository.list_agent_channel_bindings(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                agent_install_id=candidate_id,
                enabled_only=True,
            )
        except Exception:
            _logger.warning(
                "openclaw provisioning: binding lookup failed for gateway_id=%s candidate agent_id=%s",
                gateway_id, candidate_id, exc_info=True,
            )
            continue
        for row in bindings or []:
            key = str((row or {}).get("key") or "").strip()
            if key in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS:
                owners.setdefault(key, []).append(candidate_id)
    return owners


async def build_openclaw_channel_policies(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    gateway_id: str = "",
    install_channel_keys: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """The full policy set for every OpenClaw-transported channel, read
    through the SAME loaders the live inbound gates use
    (_load_agent_dm_policy_config / _load_agent_group_policy_config).

    Reusing those loaders rather than reading install_metadata directly is the
    point: they carry the fail-closed fallbacks (an unresolvable identity is
    owner_only / disabled + require_mention, never open), the normalization,
    and the defaults. A second reader here would be a second, drifting opinion
    about what a policy means — and the two opinions would disagree exactly
    where it matters, since one decides what reaches the box and the other
    decides what the box lets through.

    EVERY channel is always included, not just configured ones. A channel
    omitted from the payload would keep whatever policy the previous
    provisioning run left in OpenClaw's config — the stale derived artifact
    this whole step exists to eliminate.

    MULTI-AGENT-PER-BOX (gateway_id): provisioning writes ONE config file for
    the whole box, and — before this — always rendered EVERY channel from the
    CALLING agent's own policy, even for a channel a different agent on the
    same gateway actually owns. Whichever agent provisioned most recently
    silently overwrote every other agent's channel with its own (often
    fail-closed-default) policy — a working channel could go dark because a
    completely unrelated agent on the same box saved an unrelated setting.
    When gateway_id is given, each channel's policy is now read from
    whichever agent on this SAME gateway actually holds the enabled binding
    for it (_resolve_channel_owners_for_gateway), falling back to the
    calling agent only for channels nobody on this gateway has claimed yet.
    Two agents on one box using two DIFFERENT channels now compose correctly
    instead of racing to overwrite each other. Two agents both claiming the
    SAME channel is a genuine conflict — see OpenClawProvisioningConflictError
    below — because OpenClaw's config has no way to hold two accounts on one
    channel node; provisioning refuses rather than silently picking one.
    Callers that omit gateway_id (or pass "") get the prior single-agent
    behavior exactly as before, at zero extra cost.

    `install_plugin` is the ONE per-channel axis that is narrowed, and it is a
    different question entirely: not "what may this channel do" but "does this
    box need this channel's third-party plugin package on disk". See
    channels_in_use(). `install_channel_keys` force-adds to that set, which is
    how an explicit setup action ("connect Feishu") brings a channel up before
    any binding or stored policy exists for it. A channel composed from a
    DIFFERENT agent's binding also folds that agent's own channels_in_use into
    the install set — otherwise a box with two agents where only the first
    ever calls provision would never fetch the second agent's channel plugin.
    """
    channel_owners = await _resolve_channel_owners_for_gateway(
        tenant_id=tenant_id, workspace_id=workspace_id, gateway_id=gateway_id,
    )
    conflicts = {key: owners for key, owners in channel_owners.items() if len(owners) > 1}
    if conflicts:
        detail = "; ".join(
            f"{key} is claimed by {len(owners)} agents ({', '.join(sorted(owners))})"
            for key, owners in sorted(conflicts.items())
        )
        raise OpenClawProvisioningError(
            "This computer's OpenClaw channel transport cannot be provisioned: more than one "
            f"agent on this gateway holds an enabled binding for the same channel — {detail}. "
            "OpenClaw supports exactly one account per channel. Disable the channel binding for "
            "every agent except the one that should own it, then provision again.",
            status_code=409,
        )

    requested = {
        str(key or "").strip()
        for key in (install_channel_keys or [])
        if str(key or "").strip() in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS
    }
    install_keys = requested | await channels_in_use(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id
    )
    other_owning_agents = {
        owner_id
        for owners in channel_owners.values()
        for owner_id in owners
        if owner_id != agent_id
    }
    for other_agent_id in other_owning_agents:
        install_keys |= await channels_in_use(
            tenant_id=tenant_id, workspace_id=workspace_id, agent_id=other_agent_id
        )

    policies: List[Dict[str, Any]] = []
    for channel_key in sorted(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS):
        owners = channel_owners.get(channel_key) or []
        # Exactly one non-conflicting owner on this gateway -> compose using
        # THEIR policy. No owner yet (or gateway_id not given) -> the calling
        # agent's own policy, unchanged from before this function knew about
        # sharing.
        policy_agent_id = owners[0] if len(owners) == 1 else agent_id
        dm_policy = await personal_channels_service._load_agent_dm_policy_config(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=policy_agent_id,
            channel_key=channel_key,
        )
        group_policy = await personal_channels_service._load_agent_group_policy_config(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=policy_agent_id,
            channel_key=channel_key,
        )
        policies.append(
            {
                "channel_id": openclaw_channel_id(channel_key),
                "channel_key": channel_key,
                "enabled": True,
                "install_plugin": channel_key in install_keys,
                "dm_policy": {
                    "mode": dm_policy.get("mode"),
                    "allowlist": list(dm_policy.get("allowlist") or []),
                },
                "group_policy": {
                    "mode": group_policy.get("mode"),
                    "allowlist": list(group_policy.get("allowlist") or []),
                    "require_mention": bool(group_policy.get("require_mention")),
                },
            }
        )
    return policies


async def provision_openclaw_gateway(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    actor_id: Optional[str] = None,
    install_channel_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Push the current policy to the box and return its verbatim result.

    `install_channel_keys` names channels whose PLUGIN this box should acquire
    even though nothing has been connected on them yet — the "bring this
    channel up" half of a setup action, which cannot come from the connected
    set because a channel cannot connect before its plugin exists.

    The gateway's answer is trusted as-is and never re-interpreted here: it is
    the only party that knows which OpenClaw is installed, what its schema
    says, what the effective config reads back as, and what
    `openclaw security audit` reported. Re-deriving any of that cloud-side
    would be inventing a second opinion about a machine we cannot see.
    """
    run_id = f"openclaw-provision-{uuid4().hex[:12]}"
    channels = await build_openclaw_channel_policies(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        gateway_id=gateway_id,
        install_channel_keys=install_channel_keys,
    )
    try:
        execution = await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=OPENCLAW_PROVISION_CAPABILITY,
            arguments={"channels": channels},
            run_id=run_id,
            trace_id=run_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            agent_scope="sage",
            timeout_seconds=DEFAULT_PROVISION_TIMEOUT_SECONDS,
        )
    except (ValueError, PermissionError) as exc:
        raise OpenClawProvisioningError(
            str(exc), status_code=403 if isinstance(exc, PermissionError) else 409
        ) from exc

    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {"gateway_id": gateway_id, "run_id": run_id, **result}


async def reconcile_openclaw_policy_best_effort(
    *,
    channel_key: str,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    actor_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Called right after an owner changes a policy for an OpenClaw-transported
    channel, so their setting takes effect on the box instead of at the next
    boot.

    BEST EFFORT, and deliberately so: the policy is already saved in Postgres
    and the box re-asserts it from its own provisioning record on every boot
    (OpenClawProvisioningRuntime.reconcileFromLastAppliedPolicy), so an offline
    or unpaired gateway must never turn a successful settings change into a
    500. Returns None when the push could not be attempted — including when
    build_openclaw_channel_policies refuses because a DIFFERENT agent on this
    same gateway already holds the enabled binding for this channel (see its
    own docstring); the specific "why", including which agent conflicts,
    is in the log line this swallows, not in this function's return value.
    A caller that needs to explain the refusal to the owner should call
    provision_openclaw_gateway directly (as the explicit "provision" action
    already does) rather than read anything into a None here. Returns the
    box's result otherwise — including a `disabled_channels` entry when the
    setting they just chose is one OpenClaw cannot carry, which is the ONLY
    moment that fact can be put in front of them (the messages it affects
    are dropped
    before Empyralis ever sees them).

    Not a fire-and-forget task on purpose: the caller surfaces the result.
    """
    if channel_key not in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS:
        return None
    if not str(gateway_id or "").strip() or not str(agent_id or "").strip():
        return None
    try:
        return await provision_openclaw_gateway(
            gateway_id=gateway_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
    except OpenClawProvisioningError as exc:
        _logger.info(
            "openclaw provisioning reconcile skipped for gateway_id=%s channel=%s: %s",
            gateway_id,
            channel_key,
            exc,
        )
        return None
    except Exception:
        _logger.warning(
            "openclaw provisioning reconcile failed for gateway_id=%s channel=%s",
            gateway_id,
            channel_key,
            exc_info=True,
        )
        return None

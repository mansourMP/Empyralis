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
    gateway_reason_messages,
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
    GatewayDoctorError.

    `reason_code`: the raw gateway_reason_messages token this error was
    built from, when it was one — None for a validation-style error that was
    already human prose to begin with (e.g. `_validate_credential_values`'s
    raises in openclaw_channel_setup_service.py). `message` is ALWAYS
    customer-facing text; `reason_code` is the structured fact a caller can
    branch on (e.g. FleetAgentDetail.tsx deciding whether a retry control
    makes sense) without resorting to matching on the message's words — see
    CLAUDE.md's "stale string matching" rule.
    """

    def __init__(self, message: str, *, status_code: int = 400, reason_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason_code = reason_code


class OpenClawProvisioningConflictError(OpenClawProvisioningError):
    """Two different agents on the SAME gateway both hold an enabled
    binding for the same OpenClaw channel — the one configuration OpenClaw's
    own config schema cannot express (verified against every channel in the
    pinned build's own generated manifest: a channel node is a single
    credential, never a named-accounts map; see CLAUDE.md
    "Multi-agent-per-box"). A distinct subclass, not a bare
    OpenClawProvisioningError with a different message, so a caller can
    `except OpenClawProvisioningConflictError` and treat it as its own
    outcome rather than lumping it in with "the box could not be reached" —
    the exact three-facts-collapsed-into-two shape this codebase keeps
    re-discovering (filter_channel_outbound_reply's silent/undelivered
    split, the workspace-invite email's three delivery states).

    THE MESSAGE IS THE OWNER-FACING TEXT, not a log line. str(this) is
    already plain language, names the specific agents and channel, and says
    what to do — no "binding", "provisioning", "channel_key", or "gateway"
    anywhere in it, the same no-mechanism rule CLAUDE.md documents for this
    surface's remediation copy elsewhere. This is deliberate: the existing
    `.../provision` route already does `detail=str(exc)` on any
    OpenClawProvisioningError, and the frontend's `getErrorMessage` already
    surfaces a string `detail` verbatim — so this reaches the "Set up"
    button's error display with ZERO frontend changes, by construction,
    rather than by remembering to special-case a new error shape.

    `conflicts` is also kept as structured data (channel_key/channel_id/
    channel_label/agent_ids/agent_labels per conflicting channel) for a
    caller that wants to render something richer than one string —
    reconcile_openclaw_policy_best_effort's return value uses it."""

    def __init__(self, conflicts: List[Dict[str, Any]]) -> None:
        self.conflicts = conflicts
        super().__init__(_conflict_owner_message(conflicts), status_code=409)


def _conflict_owner_message(conflicts: List[Dict[str, Any]]) -> str:
    """Plain language. Names which agents, names which channel, says what to
    do about it. Never a mechanism word."""
    sentences: List[str] = []
    for conflict in conflicts:
        names = [str(name).strip() for name in (conflict.get("agent_labels") or conflict.get("agent_ids") or []) if str(name).strip()]
        channel_label = str(conflict.get("channel_label") or "").strip() or "this channel"
        if len(names) > 2:
            who = ", ".join(names[:-1]) + f", and {names[-1]}"
        elif len(names) == 2:
            who = f"{names[0]} and {names[1]}"
        elif names:
            who = names[0]
        else:
            who = "more than one agent"
        sentences.append(
            f"{who} are both set up to use {channel_label} on this computer, which can only "
            f"connect one account per channel. Turn {channel_label} off for one of them, then try again."
        )
    return " ".join(sentences) or "This computer can only connect one account per channel, and more than one agent is set up for the same one."


async def _agent_display_name(agent_id: str, *, tenant_id: str, workspace_id: str) -> str:
    """Best-effort human label for an agent_install_id — an owner's own
    chosen label first, the agent definition's own name second, the bare id
    as a last resort (never blank; a conflict message with an empty name is
    worse than one with a raw id, but the id itself must never be silently
    dropped from the picture either)."""
    try:
        bundle = await agent_registry_repository.get_workspace_agent_install_bundle(
            agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
        )
    except Exception:
        bundle = None
    if isinstance(bundle, dict):
        label = str(bundle.get("label") or "").strip()
        if label:
            return label
        definition_name = str(bundle.get("agent_definition_name") or "").strip()
        if definition_name:
            return definition_name
    return agent_id


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
    i.e. install_metadata.preferred_gateway_id == gateway_id) that this
    module's OWN channels_in_use() already calls "in use" for that channel.

    Reuses channels_in_use rather than reading agent_channel_bindings
    directly — deliberately, and not merely for consistency. Verified while
    building this: agent_channel_bindings is populated for OpenClaw-
    transported channels by NOTHING in this codebase today (the only writer,
    _ensure_agent_channel_binding_enabled, fires exclusively for
    whatsapp_personal/telegram_personal's own "connected" state sync). A
    bindings-only version of this function would be structurally correct
    and practically inert for the very channel family this exists to
    protect. channels_in_use's own two-signal definition (an enabled
    binding OR a stored dm_policy/group_policy key — the SAME "presence,
    never value" rule that breaks the connect/plugin-install deadlock
    elsewhere in this module) is the one place a channel's real usage is
    actually observable today, so this reuses it rather than inventing a
    narrower second opinion.

    Empty when gateway_id is blank or fewer than two agents share this
    gateway — the ordinary single-agent-per-box case is untouched and costs
    nothing extra. One entry per channel with more than zero owners; a
    channel with exactly one owner is unambiguous (compose using that
    agent's policy — see the caller), a channel with more than one owner is
    a real conflict OpenClaw's own config schema cannot express (verified
    against the pinned build's own `openclaw config schema`: every channel
    node is a single account, never a named-accounts map).

    ONE channels_in_use call per gateway-sharing agent, not one per channel
    — a provisioning call touches every OpenClaw channel, so this is
    computed once and reused, never re-derived per channel."""
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
            in_use = await channels_in_use(
                tenant_id=tenant_id, workspace_id=workspace_id, agent_id=candidate_id,
            )
        except Exception:
            _logger.warning(
                "openclaw provisioning: channels_in_use lookup failed for gateway_id=%s candidate agent_id=%s",
                gateway_id, candidate_id, exc_info=True,
            )
            continue
        for key in in_use:
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
    conflicting_keys = sorted(key for key, owners in channel_owners.items() if len(owners) > 1)
    if conflicting_keys:
        structured_conflicts: List[Dict[str, Any]] = []
        for key in conflicting_keys:
            owner_ids = sorted(channel_owners[key])
            owner_labels = [
                await _agent_display_name(owner_id, tenant_id=tenant_id, workspace_id=workspace_id)
                for owner_id in owner_ids
            ]
            structured_conflicts.append(
                {
                    "channel_key": key,
                    "channel_id": openclaw_channel_id(key),
                    "channel_label": personal_channels_service.OPENCLAW_PERSONAL_CHANNELS.get(key, {}).get(
                        "label", key
                    ),
                    "agent_ids": owner_ids,
                    "agent_labels": owner_labels,
                }
            )
        raise OpenClawProvisioningConflictError(structured_conflicts)

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

    Enforces personal_channels_service.assert_agent_placed_on_gateway before
    building or pushing anything — the same execution-locality gate the
    WhatsApp/Telegram/iMessage personal-channel functions all call, applied
    here too so a caller cannot provision an agent's OpenClaw-transported
    channels onto a gateway that agent was never placed on, even though this
    module already composes multi-agent-per-box policy correctly for agents
    that ARE legitimately sharing gateway_id (see build_openclaw_channel_
    policies's own docstring — that composition is unaffected by this guard,
    since it only ever runs with the CALLING agent's own gateway_id).
    """
    try:
        await personal_channels_service.assert_agent_placed_on_gateway(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            gateway_id=gateway_id,
        )
    except personal_channels_service.AgentNotPlacedOnGatewayError as exc:
        raise OpenClawProvisioningError(
            str(exc), status_code=403, reason_code="agent_not_placed_on_gateway",
        ) from exc
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
        # str(exc) here is EITHER already-human prose (most PermissionErrors,
        # and some ValueErrors raised directly inside
        # _require_active_gateway_registration) OR one of
        # gateway_execution_service's internal snake_case readiness tokens
        # (gateway_capability_missing, gateway_offline, ...) raised verbatim
        # by execute_tool_via_gateway's own readiness check. Only the second
        # kind gets translated — see humanize_if_reason_token's own
        # docstring for why gateway_reason_message() itself cannot be reused
        # here. Passing the raw token straight to a customer was exactly the
        # 2026-08-13 audit's #2/#2b finding.
        raw_reason = str(exc)
        raise OpenClawProvisioningError(
            gateway_reason_messages.humanize_if_reason_token(
                raw_reason, capability_id=OPENCLAW_PROVISION_CAPABILITY
            ),
            status_code=403 if isinstance(exc, PermissionError) else 409,
            reason_code=raw_reason if raw_reason in gateway_reason_messages.KNOWN_REASON_TOKENS else None,
        ) from exc

    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {"gateway_id": gateway_id, "run_id": run_id, **result}


_RECONCILE_UNREACHABLE_MESSAGE = (
    "This computer could not be reached to apply the change just now. It will pick up the new "
    "setting automatically the next time it is online."
)


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

    THREE FACTS, NEVER TWO. This function's return value distinguishes:
      - "it worked" — status "provisioned" (or the box's own "refused" for a
        box-side reason, e.g. a version mismatch or a lockdown finding —
        passed through verbatim, box's own vocabulary),
      - "another agent already owns this channel" — status "agent_conflict",
        carrying WHICH agents and WHICH channel (OpenClawProvisioningConflictError's
        own structured `conflicts` plus its already-owner-facing message), and
      - "the box could not be reached at all right now" — status
        "unreachable".
    Collapsing the last two into a bare `None` was the exact "silence is a
    decision" defect this codebase keeps re-discovering at other seams
    (filter_channel_outbound_reply's silent/undelivered collapse, the
    workspace-invite email's three delivery states) — an owner who saw
    nothing had no way to learn a setting they just changed silently isn't
    in force, or why, or what to do about it.

    STILL BEST-EFFORT in the sense that matters: "unreachable" is not raised
    as an error, because the box re-asserts the last policy it can reach
    from its own provisioning record at next boot regardless — a transient
    network gap must never turn a successful settings save into something
    the owner has to act on right now. "agent_conflict" is different: it will
    NOT resolve itself at the next boot, because the same two conflicting
    channel claims will still exist then, so it is returned as a real,
    distinct outcome rather than folded into the same "try again later"
    bucket as an offline box.

    Both new-agents-conflict and not-reachable now ALWAYS return a dict
    (never a bare `None`) once this function has actually attempted a push —
    the two early guard clauses below (not an OpenClaw channel; missing
    gateway_id/agent_id) stay `None` because those are "this call does not
    apply", never a real attempted-and-failed outcome to report.

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
    except OpenClawProvisioningConflictError as exc:
        _logger.info(
            "openclaw provisioning conflict for gateway_id=%s channel=%s: %s",
            gateway_id,
            channel_key,
            exc,
        )
        return {
            "status": "agent_conflict",
            "channel_key": channel_key,
            "conflicts": exc.conflicts,
            "message": str(exc),
        }
    except OpenClawProvisioningError as exc:
        if exc.reason_code == "agent_not_placed_on_gateway":
            # A FOURTH, permanent outcome — never the same bucket as
            # "unreachable". An offline box resolves itself the moment it
            # reconnects; this will not, because the mismatch is between
            # this agent's own placement and the gateway_id the caller
            # passed, and re-trying the identical call changes nothing.
            # Collapsing the two would tell an owner to "just wait" for a
            # setting that can never take effect on its own.
            _logger.info(
                "openclaw provisioning reconcile refused (agent not placed on gateway) "
                "for gateway_id=%s channel=%s agent_id=%s",
                gateway_id,
                channel_key,
                agent_id,
            )
            return {
                "status": "wrong_hardware",
                "channel_key": channel_key,
                "message": str(exc),
            }
        _logger.info(
            "openclaw provisioning reconcile skipped for gateway_id=%s channel=%s: %s",
            gateway_id,
            channel_key,
            exc,
        )
        return {"status": "unreachable", "channel_key": channel_key, "message": _RECONCILE_UNREACHABLE_MESSAGE}
    except Exception:
        _logger.warning(
            "openclaw provisioning reconcile failed for gateway_id=%s channel=%s",
            gateway_id,
            channel_key,
            exc_info=True,
        )
        return {"status": "unreachable", "channel_key": channel_key, "message": _RECONCILE_UNREACHABLE_MESSAGE}

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


async def build_openclaw_channel_policies(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
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
    """
    policies: List[Dict[str, Any]] = []
    for channel_key in sorted(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS):
        dm_policy = await personal_channels_service._load_agent_dm_policy_config(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            channel_key=channel_key,
        )
        group_policy = await personal_channels_service._load_agent_group_policy_config(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            channel_key=channel_key,
        )
        policies.append(
            {
                "channel_id": openclaw_channel_id(channel_key),
                "channel_key": channel_key,
                "enabled": True,
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
) -> Dict[str, Any]:
    """Push the current policy to the box and return its verbatim result.

    The gateway's answer is trusted as-is and never re-interpreted here: it is
    the only party that knows which OpenClaw is installed, what its schema
    says, what the effective config reads back as, and what
    `openclaw security audit` reported. Re-deriving any of that cloud-side
    would be inventing a second opinion about a machine we cannot see.
    """
    run_id = f"openclaw-provision-{uuid4().hex[:12]}"
    channels = await build_openclaw_channel_policies(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id
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
    500. Returns None when the push could not be attempted; returns the box's
    result otherwise — including a `disabled_channels` entry when the setting
    they just chose is one OpenClaw cannot carry, which is the ONLY moment that
    fact can be put in front of them (the messages it affects are dropped
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

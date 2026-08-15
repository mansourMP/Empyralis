from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server_modules.auth import enforce_workspace_access
from server_modules.runtime_common import require_api_key
from server_modules.kill_switch_gate import KillSwitchBlockedError
from server_modules.safety_error_contract import kill_switch_error, to_http_body, to_http_status
from server_modules import (
    channel_lane_contract_service,
    gateway_state_repository,
    openclaw_channel_registry,
    openclaw_channel_setup_service,
    openclaw_provisioning_service,
    personal_channels_service,
    security_audit_service,
)


router = APIRouter()


class PersonalOutboundRequest(BaseModel):
    remote_jid: str = Field(min_length=1)
    text: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    reply_to_external_message_id: Optional[str] = None


class GroupPolicyUpdateRequest(BaseModel):
    """Body for PATCH .../group-policy. `mode` is required and validated
    against personal_channels_service.GROUP_POLICY_MODES by the service
    layer (a typo gets a real 400, not a silent fallback to the default).
    `require_mention` omitted means "use this build's safe default" — see
    update_agent_group_policy_config's own docstring."""

    mode: str = Field(min_length=1)
    allowlist: List[str] = Field(default_factory=list)
    require_mention: Optional[bool] = None


class DmPolicyUpdateRequest(BaseModel):
    """Body for PATCH .../dm-policy. Same contract as
    GroupPolicyUpdateRequest above: `mode` is validated by the service layer
    against personal_channels_service.DM_POLICY_MODES, and — for an
    OpenClaw-transported channel — additionally against the narrower set
    that transport can actually carry."""

    mode: str = Field(min_length=1)
    allowlist: List[str] = Field(default_factory=list)


class DmPolicyPairingApprovalRequest(BaseModel):
    """Body for POST .../dm-policy/pairing-approvals. Either identifier the
    owner has on hand: the sender's own id, or the one-time code the pairing
    challenge sent them. At least one is required — the service refuses an
    empty request with pairing_request_not_found rather than guessing."""

    sender_id: Optional[str] = None
    code: Optional[str] = None


# DERIVED from the service's own map, never re-declared here.
#
# This used to be a third hardcoded copy of the local-bridge channel list
# (personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS is the first,
# empyralis-gateway/src/channels/local-bridge-runtime.ts the second). When
# step 2 merged the OpenClaw-transported channels into the service's map,
# this copy was not updated — so an owner could receive an OpenClaw message
# and get an automatic reply, but POST
# /personal-channels/openclaw_line/gateways/{id}/messages answered 404,
# because this dict had never heard of the channel. Reading the service's
# map means a channel added there can never again be silently missing here.
LOCAL_BRIDGE_CHANNELS: Dict[str, Dict[str, str]] = dict(
    personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS
)


def _personal_channel_governance_metadata(action: str, channel_key: str) -> dict:
    normalized_action = str(action or "").strip().lower()
    if normalized_action.endswith(".configure"):
        return {
            "action_class": "credential_change",
            "risk_level": "high",
            "governance_boundary": "paired_gateway",
            "requires_approval": False,
            "external_side_effect": False,
        }
    if normalized_action.endswith(".send"):
        return {
            "action_class": "channel_send",
            "risk_level": "critical",
            "governance_boundary": "paired_gateway",
            "requires_approval": True,
            "external_side_effect": True,
        }
    return {
        "action_class": f"{channel_key}_action",
        "risk_level": "moderate",
        "governance_boundary": "paired_gateway",
        "requires_approval": False,
        "external_side_effect": False,
    }


def _coerce_current_user_id(current_user) -> str:
    if not isinstance(current_user, dict):
        return ""
    return str(
        current_user.get("user_id")
        or current_user.get("sub")
        or current_user.get("id")
        or ""
    ).strip()


def _coerce_current_user_role(current_user) -> str:
    if not isinstance(current_user, dict):
        return ""
    return str(current_user.get("role") or "").strip().lower()


def _registration_owner_user_id(registration: dict) -> str:
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    return str(
        registration.get("user_id")
        or registration.get("owner_user_id")
        or registration.get("paired_user_id")
        or metadata.get("user_id")
        or metadata.get("owner_user_id")
        or metadata.get("paired_user_id")
        or metadata.get("auth_user_id")
        or ""
    ).strip()


def _require_accessible_gateway_registration(
    gateway_id: str,
    current_user,
    *,
    minimum_role: str,
) -> dict:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        registration_workspace_id,
        minimum_role=minimum_role,
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    owner_user_id = _registration_owner_user_id(registration)
    current_user_id = _coerce_current_user_id(current_user)
    current_role = _coerce_current_user_role(current_user)
    if owner_user_id:
        if not current_user_id or current_user_id != owner_user_id:
            raise HTTPException(status_code=403, detail="This personal gateway belongs to another user.")
    elif current_role not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="Gateway ownership is missing. Ask an owner to reconnect this Agent Computer.",
        )
    return registration


def _emit_personal_channel_audit(
    *,
    action: str,
    status: str,
    registration: dict,
    current_user,
    gateway_id: str,
    channel_key: str,
    detail: str,
    metadata: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    security_audit_service.emit_security_audit_event(
        action=action,
        status=status,
        tenant_id=str(registration.get("tenant_id") or "").strip() or None,
        workspace_id=str(registration.get("workspace_id") or "").strip() or None,
        current_user=current_user if isinstance(current_user, dict) else None,
        channel=channel_key,
        machine_id=str(gateway_id or "").strip() or None,
        detail=detail,
        metadata={
            "gateway_id": str(gateway_id or "").strip(),
            "channel_key": channel_key,
            **_personal_channel_governance_metadata(action, channel_key),
            **dict(metadata or {}),
        },
        idempotency_key=idempotency_key,
    )


@router.get("/personal-channels/gateways/{gateway_id}/channels")
async def get_personal_gateway_channel_surfaces(
    request: Request,
    gateway_id: str,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="viewer",
    )
    try:
        return personal_channels_service.get_gateway_personal_channel_surfaces(gateway_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# The whatsapp/telegram/imessage-specific routes that used to live here
# (GET/POST .../whatsapp/gateways/{id}[/setup|/disconnect|/messages],
# GET/POST .../telegram/gateways/{id}[/setup|/disconnect|/messages], POST
# .../imessage/gateways/{id}[/recheck|/install]) are DELETED 2026-08-14
# (full OpenClaw channel cutover) along with the personal_channels_service
# functions they called (configure/disconnect/send_whatsapp_personal_*,
# configure/disconnect/send_telegram_personal_*, recheck_imessage_personal_
# gateway, install_imessage_imsg_gateway) and the Baileys/gramjs/imsg-RPC
# gateway runtimes those served. WhatsApp, Telegram and iMessage are now
# OpenClaw-transported, configured entirely through the generic OpenClaw
# provisioning/credential routes below (POST .../openclaw/gateways/{id}/
# provision, PUT .../openclaw/gateways/{id}/channels/{channel_key}/
# credential) and reachable for manual send through the generic
# send_local_bridge_personal_message route directly below, exactly like
# every other OpenClaw-transported channel. Git history has the deleted
# routes if a first-party revival is ever needed.



@router.post("/personal-channels/{channel_key}/gateways/{gateway_id}/messages")
async def send_local_bridge_personal_message(
    request: Request,
    channel_key: str,
    gateway_id: str,
    body: PersonalOutboundRequest,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    normalized_channel_key = str(channel_key or "").strip().lower()
    spec = LOCAL_BRIDGE_CHANNELS.get(normalized_channel_key)
    if not spec:
        raise HTTPException(status_code=404, detail="Personal local-bridge channel was not found.")
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    action_name = f"personal_channel.{normalized_channel_key.split('_', 1)[0]}.send"
    try:
        result = await personal_channels_service.send_local_bridge_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            channel_key=normalized_channel_key,
            provider=spec["provider"],
            remote_jid=body.remote_jid,
            text=body.text,
            idempotency_key=body.idempotency_key,
            reply_to_external_message_id=body.reply_to_external_message_id,
        )
        _emit_personal_channel_audit(
            action=action_name,
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=f"A {spec['label']} personal test or manual message was dispatched through the paired gateway bridge.",
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
            },
            idempotency_key=f"{action_name}:{gateway_id}:{body.idempotency_key}",
        )
        return result
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action=action_name,
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=detail,
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
            },
            idempotency_key=f"{action_name}.denied:{gateway_id}:{body.idempotency_key}",
        )
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


# ── Group policy (Gate 2 allowlist + Gate 3 require_mention) ───────────
#
# Wires personal_channels_service.update_agent_group_policy_config /
# _persist_agent_group_policy_config to a real route — both had zero
# callers anywhere in the codebase before this build (see
# CHANNEL-GATEWAY-PLAN.md §4: the write path existed, but nothing could
# ever call it, so Gate 2 was permanently stuck open for every agent). The
# read path (_load_agent_group_policy_config) was already wired into the
# live inbound handlers; this route is the missing other half.
#
# channel_key is generic (not restricted to LOCAL_BRIDGE_CHANNELS like
# send_local_bridge_personal_message above) because group_policy applies
# to WhatsApp/Telegram Personal too — validated against
# personal_channels_service.GROUP_POLICY_CHANNEL_KEYS instead.


@router.patch("/personal-channels/{channel_key}/gateways/{gateway_id}/group-policy")
async def update_personal_channel_group_policy(
    request: Request,
    channel_key: str,
    gateway_id: str,
    body: GroupPolicyUpdateRequest,
    current_user=Depends(require_api_key),
    agent_id: Optional[str] = None,
):
    """"member" (not "viewer") — matches configure_whatsapp_personal_gateway
    / configure_telegram_personal_gateway above: this changes which chats
    the agent will actually reply in, the same bar as any other channel
    configuration change. agent_id is a required query param (not part of
    the body) for the same reason every other route here takes it that
    way — group_policy is stored per (agent, channel), not per gateway."""
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    normalized_channel_key = str(channel_key or "").strip().lower()
    if normalized_channel_key not in personal_channels_service.GROUP_POLICY_CHANNEL_KEYS:
        raise HTTPException(status_code=404, detail="Personal channel was not found.")
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    normalized_agent_id = str(agent_id or "").strip()
    try:
        updated = await personal_channels_service.update_agent_group_policy_config(
            tenant_id=str(registration.get("tenant_id") or "default"),
            workspace_id=str(registration.get("workspace_id") or "default"),
            agent_id=normalized_agent_id,
            channel_key=normalized_channel_key,
            mode=body.mode,
            allowlist=body.allowlist,
            require_mention=body.require_mention,
        )
        if updated is None:
            detail = "Agent install was not found in this workspace, or the update could not be saved."
            _emit_personal_channel_audit(
                action="personal_channel.group_policy.configure",
                status="denied",
                registration=registration,
                current_user=current_user,
                gateway_id=gateway_id,
                channel_key=normalized_channel_key,
                detail=detail,
                metadata={"agent_id": normalized_agent_id, "requested_mode": body.mode},
            )
            raise HTTPException(status_code=404, detail=detail)
        _emit_personal_channel_audit(
            action="personal_channel.group_policy.configure",
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail="Group policy (allowed chats / mention gate) was updated for a personal-channel agent binding.",
            metadata={
                "agent_id": normalized_agent_id,
                "mode": updated.get("mode"),
                "require_mention": updated.get("require_mention"),
                "allowlist_size": len(updated.get("allowlist") or []),
            },
        )
        # CHANNEL-ADOPTION-PLAN.md step 4. For an OpenClaw-transported channel
        # the policy just saved here is NOT what decides whether a message ever
        # arrives — OpenClaw's own config runs first and can block-dispatch
        # before any Empyralis gate sees it. Push the new policy to the box now,
        # so the setting is in force rather than merely stored.
        #
        # Best effort by design (an offline box re-asserts this from its own
        # provisioning record at next boot, so a push failure must never fail a
        # setting that saved cleanly) — but the RESULT is returned, because this
        # is the only moment an owner can be told that the setting they just
        # chose is one OpenClaw physically cannot carry. `openclaw_provisioning
        # .disabled_channels` is that message; the traffic it concerns is
        # dropped before Empyralis exists, so there is no later opportunity to
        # notice.
        provisioning = await openclaw_provisioning_service.reconcile_openclaw_policy_best_effort(
            channel_key=normalized_channel_key,
            gateway_id=gateway_id,
            tenant_id=str(registration.get("tenant_id") or "default"),
            workspace_id=str(registration.get("workspace_id") or "default"),
            agent_id=normalized_agent_id,
            actor_id=str(current_user.get("id") or "") or None,
        )
        return {
            "channel_key": normalized_channel_key,
            "agent_id": normalized_agent_id,
            "group_policy": updated,
            "openclaw_provisioning": provisioning,
        }
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.group_policy.configure",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=detail,
            metadata={"agent_id": normalized_agent_id, "requested_mode": body.mode},
        )
        raise HTTPException(status_code=400, detail=detail) from exc


@router.get("/personal-channels/{channel_key}/gateways/{gateway_id}/group-policy")
async def get_personal_channel_group_policy(
    request: Request,
    channel_key: str,
    gateway_id: str,
    current_user=Depends(require_api_key),
    agent_id: Optional[str] = None,
):
    """Read-only counterpart to the PATCH above — "viewer" is enough, same
    bar as the other GET status routes in this file. Lets a direct API
    caller (or a future settings UI) confirm what's actually persisted
    without needing to trigger a live inbound message first."""
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    normalized_channel_key = str(channel_key or "").strip().lower()
    if normalized_channel_key not in personal_channels_service.GROUP_POLICY_CHANNEL_KEYS:
        raise HTTPException(status_code=404, detail="Personal channel was not found.")
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="viewer",
    )
    normalized_agent_id = str(agent_id or "").strip()
    config = await personal_channels_service._load_agent_group_policy_config(
        tenant_id=str(registration.get("tenant_id") or "default"),
        workspace_id=str(registration.get("workspace_id") or "default"),
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
    )
    return {
        "channel_key": normalized_channel_key,
        "agent_id": normalized_agent_id,
        "group_policy": config,
    }


# ── DM policy (who may message this agent directly) ────────────────────
#
# The other half of the gate pair above, and until 2026-08-14 the half that
# had NO route at all — see personal_channels_service
# .update_agent_dm_policy_config and DEFAULT_OPENCLAW_DM_POLICY_MODE for the
# full chain, but the short version is that _persist_agent_dm_policy_config's
# only two callers both lived inside the service module and neither could be
# reached from outside it. The mode could therefore never leave its default,
# and on the OpenClaw-transported lane that default rendered as OpenClaw's
# own `dmPolicy: "open"`, which their mandatory `security audit` calls
# critical — so the entire channel transport could not be provisioned on any
# box, through any surface, ever.
#
# Shaped deliberately as a mirror of the group-policy pair above (same auth
# bar, same agent_id query param, same reconcile-after-save, same audit
# events) rather than as a new pattern: they are two axes of one gate, and an
# owner editing one should not be meeting a different set of rules than when
# they edit the other.


def _dm_policy_registration_or_404(
    *,
    channel_key: str,
    gateway_id: str,
    current_user,
    minimum_role: str,
):
    normalized_channel_key = str(channel_key or "").strip().lower()
    if normalized_channel_key not in personal_channels_service.DM_POLICY_CHANNEL_KEYS:
        raise HTTPException(status_code=404, detail="Personal channel was not found.")
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role=minimum_role,
    )
    return normalized_channel_key, registration


@router.patch("/personal-channels/{channel_key}/gateways/{gateway_id}/dm-policy")
async def update_personal_channel_dm_policy(
    request: Request,
    channel_key: str,
    gateway_id: str,
    body: DmPolicyUpdateRequest,
    current_user=Depends(require_api_key),
    agent_id: Optional[str] = None,
):
    """"member", matching PATCH .../group-policy above: this changes who the
    agent will actually answer, the same bar as any other channel
    configuration change."""
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    normalized_channel_key, registration = _dm_policy_registration_or_404(
        channel_key=channel_key,
        gateway_id=gateway_id,
        current_user=current_user,
        minimum_role="member",
    )
    normalized_agent_id = str(agent_id or "").strip()
    try:
        updated = await personal_channels_service.update_agent_dm_policy_config(
            tenant_id=str(registration.get("tenant_id") or "default"),
            workspace_id=str(registration.get("workspace_id") or "default"),
            agent_id=normalized_agent_id,
            channel_key=normalized_channel_key,
            mode=body.mode,
            allowlist=body.allowlist,
        )
    except personal_channels_service.UnsupportedDmPolicyModeError as exc:
        # 422, not 400: the request is well-formed and the mode is real — it
        # is this CHANNEL's transport that cannot carry it. A 400 would read
        # as "you sent something malformed" and send an owner looking for a
        # typo that isn't there. str(exc) is already the owner-facing
        # sentence (no "dmPolicy", no "OpenClaw config", no mode tokens), and
        # getErrorMessage surfaces a string `detail` verbatim.
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.dm_policy.configure",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=detail,
            metadata={"agent_id": normalized_agent_id, "requested_mode": body.mode},
        )
        raise HTTPException(status_code=422, detail=detail) from exc
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.dm_policy.configure",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=detail,
            metadata={"agent_id": normalized_agent_id, "requested_mode": body.mode},
        )
        raise HTTPException(status_code=400, detail=detail) from exc
    if updated is None:
        detail = "Agent install was not found in this workspace, or the update could not be saved."
        _emit_personal_channel_audit(
            action="personal_channel.dm_policy.configure",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=detail,
            metadata={"agent_id": normalized_agent_id, "requested_mode": body.mode},
        )
        raise HTTPException(status_code=404, detail=detail)
    # allowlist SIZE, never its entries: an allowlist entry is a real
    # person's phone number / handle / account id, and an audit row is not
    # the place for it. Same shape the group-policy audit above already uses.
    _emit_personal_channel_audit(
        action="personal_channel.dm_policy.configure",
        status="success",
        registration=registration,
        current_user=current_user,
        gateway_id=gateway_id,
        channel_key=normalized_channel_key,
        detail="Direct-message policy (who may message this agent) was updated for a personal-channel agent binding.",
        metadata={
            "agent_id": normalized_agent_id,
            "mode": updated.get("mode"),
            "allowlist_size": len(updated.get("allowlist") or []),
        },
    )
    provisioning = await _reconcile_openclaw_after_policy_change(
        channel_key=normalized_channel_key,
        gateway_id=gateway_id,
        registration=registration,
        agent_id=normalized_agent_id,
        current_user=current_user,
    )
    return {
        "channel_key": normalized_channel_key,
        "agent_id": normalized_agent_id,
        "dm_policy": updated,
        "openclaw_provisioning": provisioning,
    }


@router.get("/personal-channels/{channel_key}/gateways/{gateway_id}/dm-policy")
async def get_personal_channel_dm_policy(
    request: Request,
    channel_key: str,
    gateway_id: str,
    current_user=Depends(require_api_key),
    agent_id: Optional[str] = None,
):
    """Read-only counterpart — "viewer", same bar as GET .../group-policy.

    `settable_modes` is served alongside the config because it is a property
    of this CHANNEL's transport, not of the request: a UI that offered the
    three modes this lane cannot carry would be rendering controls whose only
    outcome is a 422."""
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    normalized_channel_key, registration = _dm_policy_registration_or_404(
        channel_key=channel_key,
        gateway_id=gateway_id,
        current_user=current_user,
        minimum_role="viewer",
    )
    normalized_agent_id = str(agent_id or "").strip()
    config = await personal_channels_service._load_agent_dm_policy_config(
        tenant_id=str(registration.get("tenant_id") or "default"),
        workspace_id=str(registration.get("workspace_id") or "default"),
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
    )
    # pending_pairing carries a stranger's own id and the one-time code we
    # sent them — the owner needs both to approve, so this is deliberately
    # returned, and deliberately only to someone who already passed
    # _require_accessible_gateway_registration for this workspace.
    settable = (
        personal_channels_service.OPENCLAW_SETTABLE_DM_POLICY_MODES
        if normalized_channel_key in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS
        else personal_channels_service.DM_POLICY_MODES
    )
    return {
        "channel_key": normalized_channel_key,
        "agent_id": normalized_agent_id,
        "dm_policy": config,
        "settable_modes": sorted(settable),
    }


@router.post("/personal-channels/{channel_key}/gateways/{gateway_id}/dm-policy/pairing-approvals")
async def approve_personal_channel_dm_pairing(
    request: Request,
    channel_key: str,
    gateway_id: str,
    body: DmPolicyPairingApprovalRequest,
    current_user=Depends(require_api_key),
    agent_id: Optional[str] = None,
):
    """Wires personal_channels_service.approve_dm_policy_pairing_request,
    which had zero callers outside its own module until now. `pairing` mode
    challenges a stranger and records them as pending; without this route
    nothing could ever move one of those pending entries into the allowlist,
    so the challenge went out and the approval it asked for was impossible.

    "member", matching the PATCH above — approving a pairing request adds a
    sender to the allowlist, which is the same act by a different door."""
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    normalized_channel_key, registration = _dm_policy_registration_or_404(
        channel_key=channel_key,
        gateway_id=gateway_id,
        current_user=current_user,
        minimum_role="member",
    )
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "agent_id is required to approve a pairing request — pairing requests are stored per "
                "agent+channel, not per gateway."
            ),
        )
    result = await personal_channels_service.approve_dm_policy_pairing_request(
        tenant_id=str(registration.get("tenant_id") or "default"),
        workspace_id=str(registration.get("workspace_id") or "default"),
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
        sender_id=body.sender_id,
        code=body.code,
    )
    # Three outcomes, three answers — never one "failed". "no such request"
    # is the owner's own typo or an already-approved sender (404, nothing to
    # retry); "persist_failed" is ours and IS worth retrying (503); approved
    # is approved.
    if not result.get("approved"):
        reason = str(result.get("reason") or "")
        detail = (
            "That request could not be saved just now — nothing was changed, so it is safe to try again."
            if reason == "persist_failed"
            else "No pending request matches that person or code. It may already have been approved."
        )
        _emit_personal_channel_audit(
            action="personal_channel.dm_policy.pairing_approve",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=detail,
            metadata={"agent_id": normalized_agent_id, "reason": reason or "unknown"},
        )
        raise HTTPException(status_code=503 if reason == "persist_failed" else 404, detail=detail)
    _emit_personal_channel_audit(
        action="personal_channel.dm_policy.pairing_approve",
        status="success",
        registration=registration,
        current_user=current_user,
        gateway_id=gateway_id,
        channel_key=normalized_channel_key,
        detail="A pending pairing request was approved and the sender was added to this agent's allowed list.",
        metadata={"agent_id": normalized_agent_id},
    )
    updated = await personal_channels_service._load_agent_dm_policy_config(
        tenant_id=str(registration.get("tenant_id") or "default"),
        workspace_id=str(registration.get("workspace_id") or "default"),
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
    )
    provisioning = await _reconcile_openclaw_after_policy_change(
        channel_key=normalized_channel_key,
        gateway_id=gateway_id,
        registration=registration,
        agent_id=normalized_agent_id,
        current_user=current_user,
    )
    return {
        "channel_key": normalized_channel_key,
        "agent_id": normalized_agent_id,
        "approved_sender_id": result.get("sender_id"),
        "dm_policy": updated,
        "openclaw_provisioning": provisioning,
    }


async def _reconcile_openclaw_after_policy_change(
    *,
    channel_key: str,
    gateway_id: str,
    registration,
    agent_id: str,
    current_user,
):
    """CHANNEL-ADOPTION-PLAN.md step 4, identical to the group-policy route's
    own post-save push and factored out so the two axes cannot drift into
    saving the same kind of setting and only one of them taking effect.

    For an OpenClaw-transported channel the policy just saved is NOT what
    decides whether a message ever arrives — OpenClaw's own config runs first
    and can block-dispatch before any Empyralis gate sees it. Push it now, so
    the setting is in force rather than merely stored.

    Best effort by design (an offline box re-asserts this from its own
    provisioning record at next boot, so a push failure must never fail a
    setting that saved cleanly) — but the RESULT is returned, because this is
    the only moment an owner can be told the box did not take it."""
    return await openclaw_provisioning_service.reconcile_openclaw_policy_best_effort(
        channel_key=channel_key,
        gateway_id=gateway_id,
        tenant_id=str(registration.get("tenant_id") or "default"),
        workspace_id=str(registration.get("workspace_id") or "default"),
        agent_id=agent_id,
        actor_id=str(current_user.get("id") or "") or None,
    )


# ── OpenClaw transport: first-run provisioning ─────────────────────────
#
# Until this route existed there was NO way to set up the OpenClaw transport
# on a box. Every trigger for openclaw_provisioning_service was downstream of
# something else having already happened:
#
#   PATCH .../group-policy   -> reconcile_openclaw_policy_best_effort, which
#                               (a) needs an agent binding and a policy the
#                               owner wants to CHANGE, and (b) is best effort
#                               by design, so a box that is offline, unpaired,
#                               or refusing returns 200 with
#                               openclaw_provisioning: null. Correct for a
#                               settings save; useless as a setup action,
#                               because "nothing happened" and "it worked"
#                               look identical.
#   boot reconcile           -> OpenClawProvisioningRuntime
#                               .reconcileFromLastAppliedPolicy(), which is a
#                               documented NO-OP on a box that has never been
#                               provisioned. It re-asserts a stored policy; it
#                               cannot create the first one.
#
# So the first provisioning run on any machine had no entry point at all, and
# every trigger downstream of it was dead code in practice. This is that entry
# point, and nothing more: it is deliberately NOT best-effort (an explicit
# setup action that cannot reach the box must fail loudly). The policy it
# pushes is whatever is already stored for this agent, read through the same
# loaders the live inbound gates use.
#
# Its one optional input is `install_channels`, and it configures nothing — it
# names channels whose PLUGIN this box should acquire. That cannot be inferred
# from what is connected, because twenty of OpenClaw's channels are separate
# npm packages and a channel physically cannot connect before its package is
# installed: "install once connected" is a deadlock. This is the lever that
# breaks it, and it is the request a future "Connect Feishu" button makes.


class OpenClawProvisionRequest(BaseModel):
    """`install_channels`: Empyralis channel_keys (`openclaw_feishu`, …).

    Additive only. It never disables or removes a channel — the plugin set on a
    box is a union over time, because uninstalling a plugin out from under a
    connected account would take a working channel down to satisfy a request
    that was only ever about which code is present.
    """

    install_channels: Optional[List[str]] = None


@router.post("/personal-channels/openclaw/gateways/{gateway_id}/provision")
async def provision_openclaw_transport(
    request: Request,
    gateway_id: str,
    payload: Optional[OpenClawProvisionRequest] = None,
    current_user=Depends(require_api_key),
    agent_id: Optional[str] = None,
):
    """Set up (or re-assert) this computer's OpenClaw channel transport.

    "member", matching PATCH .../group-policy above rather than the "viewer"
    of the read routes: this writes a config on the customer's machine and
    installs a supervised process, which is at least as consequential as
    changing which chats an agent answers in.

    `agent_id` is required for the same reason it is on the group-policy
    routes — the policy being pushed is stored per (agent, channel), and
    provisioning from an unresolved identity would write the fail-closed
    fallback into OpenClaw's config as though the owner had chosen it.
    """
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise HTTPException(
            status_code=400,
            detail="agent_id is required: the channel policy is generated from a specific agent's settings.",
        )
    try:
        result = await openclaw_provisioning_service.provision_openclaw_gateway(
            gateway_id=gateway_id,
            tenant_id=str(registration.get("tenant_id") or "default"),
            workspace_id=str(registration.get("workspace_id") or "default"),
            agent_id=normalized_agent_id,
            actor_id=str(current_user.get("id") or "") or None,
            install_channel_keys=list(payload.install_channels or []) if payload else None,
        )
    except openclaw_provisioning_service.OpenClawProvisioningError as exc:
        _emit_personal_channel_audit(
            action="personal_channel.openclaw.provision",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="openclaw",
            detail=str(exc),
            metadata={"agent_id": normalized_agent_id},
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    # A "refused" result is a SUCCESSFUL round trip that carries a refusal
    # reason from the box — a wrong OpenClaw version, a failed lockdown
    # read-back, an unclean security audit. It is returned as 200 with the
    # refusal intact rather than raised, because the caller needs the whole
    # report (which channels were disabled and why, which settings could not
    # be expressed), and an HTTPException carries one sentence.
    _emit_personal_channel_audit(
        action="personal_channel.openclaw.provision",
        status="success" if str(result.get("status") or "") == "provisioned" else "denied",
        registration=registration,
        current_user=current_user,
        gateway_id=gateway_id,
        channel_key="openclaw",
        detail=(
            "The OpenClaw channel transport was provisioned on this computer."
            if str(result.get("status") or "") == "provisioned"
            else "OpenClaw provisioning was refused by the computer; the channel transport is not in service."
        ),
        metadata={
            "agent_id": normalized_agent_id,
            "status": result.get("status"),
            "refusal_code": (result.get("refusal") or {}).get("code") if isinstance(result.get("refusal"), dict) else None,
            "profile": result.get("profile"),
            "config_changed": result.get("config_changed"),
            "restart_required": result.get("restart_required"),
            "installed_channel_plugins": sorted(
                str(entry.get("channel_id") or "")
                for entry in (result.get("channel_plugins") or [])
                if isinstance(entry, dict) and entry.get("requires_plugin") and entry.get("installed")
            ),
        },
    )
    return {"gateway_id": gateway_id, "agent_id": normalized_agent_id, "openclaw_provisioning": result}


# ── Channel setup from the browser: three states, one credential ────────
#
# The read is deliberately a JOIN of two different things, kept separable:
#
#   catalog   the GENERATED setup form. A property of the pinned OpenClaw and
#             therefore knowable from the repository alone, so an offline box
#             still renders the right form for every channel.
#   observed  installed / configured / enabled, read off the box itself.
#             Absent when the box cannot be reached, and SAID SO — never
#             defaulted to "not connected", which reads as a fact about the
#             channel when it is a fact about the network.
#
# The write is a PASS-THROUGH. Nothing here persists the credential; see
# openclaw_channel_setup_service's module docstring for why that beats the
# vault, and note the consequence that no GET on this router can return one.


@router.get("/personal-channels/openclaw/catalog")
async def get_openclaw_channel_catalog(
    request: Request,
    current_user=Depends(require_api_key),
):
    """The CATALOG half of `get_openclaw_channel_setup` below, with no
    gateway involved at all.

    `openclaw_channel_setup_catalog()` is a pure read of the checked-in
    manifest — true before any computer has ever been paired, exactly like
    that route's own catalog half. This route exists because the unified
    channel grid (FleetAgentDetail.tsx's ChannelsTab) was rendering the
    transported cards ONLY when an agent had a bound gateway —
    `agentGatewayId ? [...] : []` — so with no gateway paired, ~20
    transported platforms (Feishu, LINE, Matrix, Zalo, ...) were silently
    absent from the grid instead of showing the same "Needs Gateway" state
    the first-party cards (WhatsApp, Signal, iMessage) already render in that
    situation. The customer could not discover those channels exist at all —
    the "built, tested, and never wired" shape one level up from the code:
    the catalog was always gateway-independent, only the route to reach it
    wasn't.

    `require_api_key` only — deliberately no
    `_require_accessible_gateway_registration`, unlike every other route in
    this file: there is no `gateway_id` in this path and nothing tenant- or
    customer-scoped in the response. This returns a static product catalog —
    channel ids, labels and generated-form FIELD NAMES straight off the
    pinned manifest, the same values `get_openclaw_channel_setup` below
    returns as its `channels` key — with no secret, no per-customer state and
    no observed device data. `require_api_key`'s "someone is logged in, any
    tenant" is exactly the right amount of gate for that, not a shortcut
    around a missing tenancy check (see CLAUDE.md on `require_api_key` not
    being an authorization check for anything that IS tenant-scoped — this
    route is the case where there is genuinely nothing to scope).
    """
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    catalog = openclaw_channel_setup_service.openclaw_channel_setup_catalog()
    # Same computation as the gateway-scoped route below, never a second
    # hand-typed list beside it.
    already_available_channels = sorted(
        (channel.label for channel in channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS),
        key=str.lower,
    )
    return {
        "openclaw_version": openclaw_channel_registry.OPENCLAW_VERSION,
        "channels": catalog,
        "already_available_channels": already_available_channels,
    }


@router.get("/personal-channels/openclaw/gateways/{gateway_id}/setup")
async def get_openclaw_channel_setup(
    request: Request,
    gateway_id: str,
    current_user=Depends(require_api_key),
):
    """The generated setup form per channel, joined with observed device state.

    "viewer", matching the other read routes: this returns no secret and cannot
    return one — the only credential-shaped data in the response is a per-field
    `set` boolean, and OpenClaw redacts secret values before they ever leave the
    customer's machine.
    """
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="viewer",
    )
    catalog = openclaw_channel_setup_service.openclaw_channel_setup_catalog()
    observed: Optional[Dict[str, Any]] = None
    observed_error: Optional[str] = None
    observed_error_code: Optional[str] = None
    try:
        observed = await openclaw_channel_setup_service.read_channel_setup_state(
            gateway_id=gateway_id,
            workspace_id=str(registration.get("workspace_id") or "default"),
            actor_id=str(current_user.get("id") or "") or None,
        )
    except openclaw_channel_setup_service.OpenClawProvisioningError as exc:
        # An unreachable box is not a 4xx on a READ: the catalog half is still
        # true and still worth rendering, and collapsing "we could not ask" into
        # "the channel is not connected" is exactly the state confusion this
        # screen exists to remove.
        observed_error = str(exc)
        # The STRUCTURED reason, alongside the human message above — the
        # frontend banner branches on this (e.g. "the box never had the
        # transport installed" vs "the box is offline right now") rather
        # than matching on the message's words, per CLAUDE.md's "stale
        # string matching" rule. None when the caught error wasn't one of
        # gateway_execution_service's known readiness tokens.
        observed_error_code = exc.reason_code
    # The channels a first-party Empyralis runtime already carries (Telegram,
    # WhatsApp, Discord, Signal, iMessage, Slack, WeChat, SMS as of writing) —
    # why they are absent from `catalog` above. COMPUTED from the same
    # overlap logic that decides `catalog` itself
    # (channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS), never a
    # second hand-typed list beside it, so it cannot drift from what the
    # catalog actually excludes.
    already_available_channels = sorted(
        (channel.label for channel in channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS),
        key=str.lower,
    )
    return {
        "gateway_id": gateway_id,
        "openclaw_version": openclaw_channel_registry.OPENCLAW_VERSION,
        "channels": catalog,
        "observed": observed,
        "observed_error": observed_error,
        "observed_error_code": observed_error_code,
        "already_available_channels": already_available_channels,
    }


class OpenClawChannelCredentialRequest(BaseModel):
    """`values`: field name -> value, for fields in the channel's derived shape.

    A field left OUT is left unchanged on the box (`config patch` merges). There
    is deliberately no "clear" here: an empty string is not a decision, and
    reading one as "delete this credential" is how a save that meant nothing
    takes a working channel down.
    """

    values: Dict[str, str]


@router.put("/personal-channels/openclaw/gateways/{gateway_id}/channels/{channel_key}/credential")
async def put_openclaw_channel_credential(
    request: Request,
    gateway_id: str,
    channel_key: str,
    payload: OpenClawChannelCredentialRequest,
    current_user=Depends(require_api_key),
):
    """Send an owner-supplied channel credential to their own machine.

    "member", matching the provision route: this writes into the config of a
    process running on the customer's computer.

    The response carries the RE-READ device state, never the submitted values.
    """
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    normalized_key = str(channel_key or "").strip().lower()
    if not openclaw_channel_registry.is_openclaw_channel_key(normalized_key):
        raise HTTPException(
            status_code=404,
            detail=f"{channel_key!r} is not a channel this transport carries.",
        )
    try:
        result = await openclaw_channel_setup_service.write_channel_credential(
            gateway_id=gateway_id,
            workspace_id=str(registration.get("workspace_id") or "default"),
            channel_key=normalized_key,
            values=payload.values,
            actor_id=str(current_user.get("id") or "") or None,
        )
    except openclaw_channel_setup_service.OpenClawProvisioningError as exc:
        _emit_personal_channel_audit(
            action="personal_channel.openclaw.credential",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_key,
            detail=str(exc),
            # FIELD NAMES only. An audit row is durable; a value in one is a
            # permanent leak, and this is the one path a value passes through.
            metadata={"fields": sorted(str(name) for name in (payload.values or {}))},
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    refused = str(result.get("status") or "") == "refused"
    _emit_personal_channel_audit(
        action="personal_channel.openclaw.credential",
        status="denied" if refused else "success",
        registration=registration,
        current_user=current_user,
        gateway_id=gateway_id,
        channel_key=normalized_key,
        detail=(
            "The computer refused the channel credential."
            if refused
            else "A channel credential was written on this computer."
        ),
        metadata={
            "fields": sorted(str(name) for name in (result.get("written_fields") or [])),
            "refusal_code": (result.get("refusal") or {}).get("code")
            if isinstance(result.get("refusal"), dict)
            else None,
        },
    )
    return {"gateway_id": gateway_id, "channel_key": normalized_key, "openclaw_channel_setup": result}

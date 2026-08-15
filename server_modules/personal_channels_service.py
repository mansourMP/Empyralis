from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from server_modules import (
    channel_blocking_policy_service,
    channel_lane_contract_service,
    gateway_state_repository,
    kill_switch_gate,
    mention_gating_service,
    openclaw_channel_registry,
    personal_channel_sage_bridge_service,
    personal_channels_repository,
    rust_runtime_kernel_client,
    secret_redaction_service,
    security_audit_service,
)
# Imported as a MODULE, not as bare names: every delivery seam in this file
# must go through channel_adapter.resolve_channel_reply_outcome(), and
# test_channel_reply_outcome_drift.py AST-asserts that this file never calls
# filter_channel_outbound_reply directly again. Re-adding a bare-name import
# of the filter is how a fourth seam would quietly reintroduce the
# silence/failure conflation this module was fixed for.
from server_modules import channel_adapter

_logger = logging.getLogger(__name__)


class _LazyGatewayProtocolService:
    def __getattr__(self, name: str) -> Any:
        from server_modules import gateway_protocol_service as module

        return getattr(module, name)


gateway_protocol_service = _LazyGatewayProtocolService()


class _LazyGatewayExecutionService:
    def __getattr__(self, name: str) -> Any:
        from server_modules import gateway_execution_service as module

        return getattr(module, name)


gateway_execution_service = _LazyGatewayExecutionService()


# 2026-08-14 full OpenClaw channel cutover: whatsapp_personal, telegram_personal
# and imessage_personal are no longer real channels — channel_lane_contract_
# service.PERSONAL_CHANNEL_SPECS no longer declares them, so
# assert_personal_gateway_channel(...) on any of these three strings now
# raises. These three identifiers are kept as VESTIGIAL STRING LITERALS
# (never re-derived from the catalog) purely so the ~15 legacy branches still
# scattered through this file (self-chat/loop-guard identity resolution,
# health/reconnect summaries, the disconnect dispatch table, state-sync
# reads) continue to compile and behave exactly as before: every one of
# those branches compares a REAL inbound channel_key against these strings,
# and no real channel_key can ever equal them again, so each is now
# permanently unreachable dead code rather than a crash. Excising all ~15
# call sites individually was judged too large a surgery to do safely in the
# same pass as the runtime deletion — several touch loop-guard/self-chat
# security logic this pass did not have time to re-verify line by line — so
# it is left as a known follow-up rather than risked under this task's own
# time pressure. Do NOT restore the assert_personal_gateway_channel() derivation
# for these three; do NOT add a new live caller that could match them.
#
# telegram_personal briefly stopped being vestigial: between 2026-08-15's cloud
# lane fix and its deletion later the same day, the lane contract re-declared
# the key so `cloud-session-manager/` could keep delivering, which quietly made
# every branch below reachable again. The cloud lane is gone now and the
# paragraph above is true of all three again — but note how little it took to
# invalidate it, and that nothing failed when it did.
WHATSAPP_PERSONAL_CHANNEL_KEY = "whatsapp_personal"
WHATSAPP_PERSONAL_PROVIDER = "whatsapp_baileys"
TELEGRAM_PERSONAL_CHANNEL_KEY = "telegram_personal"
TELEGRAM_PERSONAL_PROVIDER = "telegram_gramjs"
IMESSAGE_PERSONAL_CHANNEL_KEY = "imessage_personal"
IMESSAGE_PERSONAL_PROVIDER = "bluebubbles_local_bridge"

# 2026-08-14 full OpenClaw channel cutover: signal_personal, imessage_personal
# and wechat_personal (the first-party local-bridge family) DELETED — their
# gateway runtimes (LocalBridgePersonalChannelRuntime + the signal-cli/
# BlueBubbles bridges) are deleted in the same change. This dict is now
# populated ENTIRELY by the OPENCLAW_PERSONAL_CHANNELS merge below —
# openclaw_signal/openclaw_imessage/openclaw_openclaw-weixin are the live
# replacements, and the handler-registration loop further down registers
# _OpenClawPersonalChannelHandler for every key here automatically.
LOCAL_BRIDGE_PERSONAL_CHANNELS: Dict[str, Dict[str, str]] = {}

# ── OpenClaw-transported channels (CHANNEL-ADOPTION-PLAN.md step 2) ───────
#
# These are local-bridge channels in every sense that matters to this
# module: a separate process on the owner's own machine speaks the platform
# protocol, and the Empyralis gateway republishes what it sees on the same
# `channel.inbound` event. They are therefore MERGED INTO
# LOCAL_BRIDGE_PERSONAL_CHANNELS below rather than given a parallel
# membership test, so every existing local-bridge-family behaviour applies
# to them by construction instead of by remembering to add a second branch:
#
#   _unresolved_identity_group_policy_config  -> fails CLOSED (disabled /
#                                                require_mention) when no
#                                                agent identity resolves
#   _resolve_agent_id_for_inbound             -> local-bridge state lookup
#   _resolve_local_bridge_agent_id            -> preferred_gateway_id claim
#   _claim_agent_channel_state                -> explicit owner action
#   GROUP_POLICY_CHANNEL_KEYS                 -> the Gate 2/3 WRITE route
#
# The one thing they do NOT share is the inbound handler: OpenClaw's
# `message_received` hook carries weaker addressing facts than a first-party
# bridge does, so they get _OpenClawPersonalChannelHandler, which normalizes
# those facts fail-closed before delegating to the very same
# _handle_local_bridge_gateway_channel_inbound. See that class.
OPENCLAW_TRANSPORT_PROVIDER = channel_lane_contract_service.OPENCLAW_TRANSPORT_PROVIDER
OPENCLAW_CHANNEL_KEY_PREFIX = openclaw_channel_registry.CHANNEL_KEY_PREFIX

# DERIVED, not listed. This used to be a five-entry map written out by hand
# beside a five-entry tuple in channel_lane_contract_service, with a set-
# equality check between them — two hand-maintained copies plus a check that
# they agreed with EACH OTHER, which is the weakest guarantee available: both
# could be wrong together, and both were. Five channels out of OpenClaw's
# twenty-seven, and one of the five carried an id (`qq`) OpenClaw does not
# have.
#
# There is now one source upstream of both: openclaw_channel_registry, whose
# manifest is generated from the pinned OpenClaw install.
#
# LABELS ARE OPENCLAW'S OWN display names (their channel catalog's `label`),
# never a parallel hand-written map. The old map called `openclaw_qqbot` "QQ";
# OpenClaw calls it "QQ Bot", which is the more accurate name — it is their
# Bot API channel, not a personal QQ account.
OPENCLAW_PERSONAL_CHANNELS: Dict[str, Dict[str, str]] = {
    channel.channel_key: {"provider": OPENCLAW_TRANSPORT_PROVIDER, "label": channel.label}
    for channel in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS
}

# The failure a set-equality check between two hand-written maps could never
# catch, because two empty sets are equal: the derivation producing nothing.
# An empty map is indistinguishable from a working one until a customer's
# channel goes quiet — every OpenClaw inbound would be refused as an unknown
# channel_key with no error anywhere saying why.
if not OPENCLAW_PERSONAL_CHANNELS:
    raise RuntimeError(
        "The OpenClaw transport resolved to zero channels. Refusing to boot rather than "
        "silently disabling every OpenClaw-transported channel."
    )

# No OpenClaw channel_key may collide with a first-party one. It cannot today
# (the `openclaw_` prefix guarantees it), but these keys are now generated
# from an upstream registry instead of typed here, so the day OpenClaw ships
# an id that would collide this must be a boot failure — never a silent
# `.update()` that replaces a live channel's handler with the transport's.
_openclaw_key_collisions = sorted(set(OPENCLAW_PERSONAL_CHANNELS) & set(LOCAL_BRIDGE_PERSONAL_CHANNELS))
if _openclaw_key_collisions:
    raise RuntimeError(
        f"OpenClaw channel keys collide with first-party local-bridge channels: "
        f"{_openclaw_key_collisions}. Refusing to overwrite a live channel's handler."
    )

# Still asserted, now for a different reason: the two are derived from one
# source, so this can no longer drift by authorship — but an `.update()` or a
# test monkeypatch elsewhere could still diverge them at runtime, and this
# pair is what decides whether a real message gets a turn.
if set(OPENCLAW_PERSONAL_CHANNELS) != set(channel_lane_contract_service.OPENCLAW_PERSONAL_CHANNEL_SPECS):
    raise RuntimeError(
        "OpenClaw channel list drift: personal_channels_service.OPENCLAW_PERSONAL_CHANNELS "
        f"{sorted(OPENCLAW_PERSONAL_CHANNELS)} != channel_lane_contract_service."
        f"OPENCLAW_PERSONAL_CHANNEL_SPECS {sorted(channel_lane_contract_service.OPENCLAW_PERSONAL_CHANNEL_SPECS)}"
    )

LOCAL_BRIDGE_PERSONAL_CHANNELS.update(OPENCLAW_PERSONAL_CHANNELS)

# ── The cloud-session lane: DELETED 2026-08-15 ───────────────────────────
#
# `CLOUD_SESSION_CHANNEL_KEYS` held exactly one key, `telegram_personal`, put
# on the wire by `cloud-session-manager/` — a SECOND gramjs Telegram runtime,
# cloud-hosted, with its own HMAC HTTP relay, that the 2026-08-14 cutover
# missed because nothing traced it. It was the same retired account channel
# (the owner's own number, ban-risk) as the on-box twin the cutover did
# delete, it had no self-serve creation path anywhere in the product, and
# `CLOUD_SESSION_MANAGER_ENABLED` still defaulted TRUE. Removed whole: the
# route, the handler, the outbound dispatcher, both proactive callers, the
# flag and the directory. See channel_lane_contract_service's own
# PERSONAL_CHANNEL_SPECS comment for the full record.
#
# The two policy sets below are now exactly LOCAL_BRIDGE_PERSONAL_CHANNELS,
# which the OPENCLAW_PERSONAL_CHANNELS merge fills — the same shape the
# whatsapp_personal drop left them in, and for the same reason.


def _enforce_personal_gateway_config_decision(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    capability_id: str,
    run_id: str,
    trace_id: str,
) -> Dict[str, Any]:
    metadata = dict(registration.get("metadata") or {})
    session_id = str(
        registration.get("active_session_id")
        or metadata.get("gateway_session_id")
        or metadata.get("session_id")
        or metadata.get("auth_session_id")
        or metadata.get("runtime_session_id")
        or ""
    ).strip()
    if not session_id:
        session_id = str(gateway_id or registration.get("gateway_id") or "").strip()
    payload = {
        "operation": "tool_execute",
        "tenant_id": str(registration.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(registration.get("workspace_id") or "default").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": session_id,
        "request_id": str(trace_id or "").strip() or None,
        "capability_id": str(capability_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": str(registration.get("device_trust_state") or "trusted").strip() or "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": bool(session_id),
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "personal_gateway_config_denied").strip()
        raise ValueError(f"Rust gateway-service blocked tool_execute: {reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "dispatch_gateway_operation":
        raise ValueError(
            "Rust gateway-service returned unexpected next_action for "
            f"tool_execute: {next_action or 'missing'}"
        )
    return decision


def _enforce_personal_channel_dispatch_decision(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    capability_id: str,
    request_id: str,
) -> Dict[str, Any]:
    metadata = dict(registration.get("metadata") or {})
    session_id = str(
        registration.get("active_session_id")
        or metadata.get("gateway_session_id")
        or metadata.get("session_id")
        or metadata.get("auth_session_id")
        or metadata.get("runtime_session_id")
        or ""
    ).strip()
    if not session_id:
        session_id = str(gateway_id or registration.get("gateway_id") or "").strip()
    payload = {
        "operation": "protocol_route",
        "tenant_id": str(registration.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(registration.get("workspace_id") or "default").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": session_id,
        "request_id": str(request_id or "").strip() or None,
        "capability_id": str(capability_id or "").strip(),
        "trace_id": str(request_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": str(registration.get("device_trust_state") or "trusted").strip() or "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": bool(session_id),
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "personal_channel_dispatch_denied").strip()
        raise ValueError(f"Rust gateway-service blocked protocol_route: {reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "dispatch_gateway_operation":
        raise ValueError(
            "Rust gateway-service returned unexpected next_action for "
            f"protocol_route: {next_action or 'missing'}"
        )
    return decision


# 2026-08-14 full OpenClaw channel cutover: signal_personal/imessage_personal/
# wechat_personal are gone (see LOCAL_BRIDGE_PERSONAL_CHANNELS above). Every
# `.get(channel_key, {})` reader below already defaults safely to {}, and the
# OpenClaw channels that replaced these three carry their own generic
# remediation copy on the frontend (openclaw-channel-copy.ts's
# `remediationFor`, derived from the manifest) — this dict was legacy,
# local-bridge-specific status copy that has no OpenClaw equivalent to
# migrate into, so it is emptied rather than repopulated.
_LOCAL_BRIDGE_PERSONAL_CHANNEL_COPY: Dict[str, Dict[str, str]] = {}


# ── Handler registry ──────────────────────────────────────────────────

from server_modules.personal_channel_handler_registry import (
    PersonalChannelHandler,
    PersonalChannelHandlerRegistry,
)


# _WhatsAppPersonalChannelHandler and _TelegramPersonalChannelHandler
# DELETED 2026-08-14 (full OpenClaw channel cutover). WhatsApp and
# Telegram are now handled generically by _OpenClawPersonalChannelHandler
# below, exactly like every other OpenClaw-transported channel.

class _LocalBridgePersonalChannelHandler(PersonalChannelHandler):
    """Generic Sage personal-channel handler for Agent Computer local bridges."""

    def __init__(self, channel_key: str, provider: str, label: str) -> None:
        self._channel_key = channel_key
        self._provider = provider
        self._label = label

    @property
    def channel_key(self) -> str:
        return self._channel_key

    @property
    def provider(self) -> str:
        return self._provider

    def build_state_sync_payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "linked_name": str(message.get("linked_name") or message.get("push_name") or "").strip() or None,
        }

    def audit_action_prefix(self) -> str:
        return self._channel_key.split("_", 1)[0]

    async def handle_inbound(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await _handle_local_bridge_gateway_channel_inbound(
            gateway_id=gateway_id,
            registration=registration,
            payload=payload,
            channel_key=self._channel_key,
            provider=self._provider,
            label=self._label,
        )

    # deliver_reply passthrough deleted 2026-08-08 — see the note on
    # _WhatsAppPersonalChannelHandler above. This copy was the worst of the
    # three: it had no agent_id to give, so wiring it would have marked every
    # local-bridge inbound row under the legacy blank scope again.

    async def send_message(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        remote_jid: str,
        text: str,
        idempotency_key: str,
        reply_to_external_message_id: Optional[str] = None,
        media: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return await send_local_bridge_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            channel_key=self._channel_key,
            provider=self._provider,
            remote_jid=remote_jid,
            text=text,
            idempotency_key=idempotency_key,
            reply_to_external_message_id=reply_to_external_message_id,
            media=media,
        )

    def get_view(self, gateway_id: str) -> Dict[str, Any]:
        return get_gateway_personal_channel_surfaces(gateway_id)

    async def configure(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise ValueError(f"{self._label} is configured on Agent Computer through its local bridge.")


# Reason code recorded on the message when this handler had to assume
# "group" because OpenClaw did not say. Stable token, matched nowhere by
# prose (CLAUDE.md: match on codes, never on wording).
OPENCLAW_GROUPNESS_ASSUMED = "openclaw_groupness_unknown_assumed_group"
OPENCLAW_GROUPNESS_DERIVED_DIRECT = "openclaw_groupness_derived_direct_from_identity"


def _openclaw_identity_tail(value: Any) -> str:
    """The bare id out of an addressing string, however it is namespaced.

    `remote_jid` arrives channel-prefixed ("telegram:1932934047") while
    `sender_jid` does not ("1932934047"), and other channels on this lane use
    their own separators ("@", "/"). Comparing the raw strings would answer
    "no" for every DM on every channel, which is exactly the bug this exists
    to close, so compare the trailing identifier instead.
    """

    text = str(value or "").strip().lower()
    if not text:
        return ""
    for separator in (":", "@", "/"):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    return text.strip()


def _openclaw_message_is_direct(message: Dict[str, Any]) -> bool:
    """True only when the conversation IS the sender — i.e. a direct message.

    Returns False on anything unproven (either id missing, or they differ),
    so the caller keeps its fail-closed assumed-group default. This can only
    ever move a message from "assumed group" to "known DM", never the
    reverse, which is what makes it safe to consult before the gates.
    """

    chat = _openclaw_identity_tail(message.get("remote_jid"))
    sender = _openclaw_identity_tail(message.get("sender_jid"))
    if not chat or not sender:
        return False
    return chat == sender


def normalize_openclaw_gate_facts(message: Dict[str, Any]) -> Dict[str, Any]:
    """Turn OpenClaw's weaker addressing facts into the ones the three gates
    consume, FAIL-CLOSED, returning a new dict (never mutating the caller's).

    Why this exists at all — verified against openclaw@2026.6.10's shipped
    bundle on 2026-08-08, not from their docs:

    1. `message_received`'s event has NO `wasMentioned` and NO `isGroup`.
       `dist/message-hook-mappers-*.js`'s `toPluginMessageReceivedEvent`
       forwards neither, while the sibling `toPluginInboundClaimEvent` in
       the same file forwards both. Their canonical internal fact is
       `isGroup = Boolean(ctx.GroupSubject || ctx.GroupChannel)`, and only
       `GroupChannel` (as `metadata.channelName`) survives into the hook —
       so a Telegram/WhatsApp group, whose group-ness comes from
       `GroupSubject`, reaches our tap looking exactly like a DM.

    2. Therefore the bridge plugin can only ever report `isGroup: true` or
       `isGroup: undefined` (see its `deriveIsGroupBestEffort`). It cannot
       say `false` today.

    UNKNOWN GROUP-NESS IS TREATED AS A GROUP. Treating it as a DM would
    route the message to Gate 1, whose default for a resolved binding is
    DM_POLICY_OPEN — i.e. a stranger in a public group would get a turn.
    That is precisely the incident in CHANNEL-GATEWAY-PLAN.md §1. Treating
    it as a group routes it to Gates 2 and 3, whose defaults are allowlist
    and require-mention. Strict side of the union, always.

    MENTION IS NEVER SYNTHESIZED. `is_mentioned` is passed through only when
    OpenClaw actually asserted it (it does not, today — the field is
    reserved for a future release; the plugin's schema already carries it).
    `is_reply_to_sage` is never asserted from `isReply`, because "a reply to
    some message" is not "a reply to the agent" — OpenClaw computes its own
    `reply_to_bot` implicit mention from the bot's user id and does not
    forward it. Deriving a mention from raw message text was considered and
    rejected: it needs the bot's own handle/user id (absent from the
    payload), it misses Telegram `text_mention` entities and WhatsApp
    `mentionedJid` entirely, and `mention_gating_service`'s module contract
    forbids reading message content at all.

    NET EFFECT TODAY: an OpenClaw group message is denied at Gate 2 unless
    the owner has explicitly allowlisted that chat, and denied at Gate 3
    even then unless the owner has explicitly turned require_mention off for
    that binding. Both are existing, owner-writable settings
    (update_agent_group_policy_config / the PATCH route), not new surfaces.
    """
    normalized = dict(message)
    if normalized.get("is_group") is None:
        # One structural exception to "unknown means group", and it can only
        # ever prove the DM direction — never turn a group into a DM.
        #
        # The payload carries BOTH the conversation and the sender:
        #
        #   remote_jid  "telegram:1932934047"   the chat this arrived in
        #   sender_jid  "1932934047"            who sent it
        #
        # In a DM those are the same identity. In a group they cannot be:
        # the chat is the group and the sender is one participant of it
        # (Telegram group ids are negative, user ids positive; the same
        # split holds for every channel on this lane, where a group's
        # conversation id is the group's own).
        #
        # So an EQUAL pair is positive evidence of a direct message, not an
        # absence of evidence — which is the whole reason the assumed-group
        # default exists. An unequal or unparseable pair changes nothing and
        # still falls through to the strict side below.
        #
        # This is structural, not textual: it reads two identifiers, never
        # message content, so it does not touch the rule the docstring sets
        # out about never synthesizing a mention.
        #
        # It matters because without it a DM is undeliverable on this
        # transport, permanently: group-ness is unknown for EVERY OpenClaw
        # message, so every DM was routed to Gate 2, denied by an empty
        # group allowlist, and dropped as `group_policy_denied`. Observed
        # live 2026-08-15 on a real Telegram DM from the workspace owner —
        # OpenClaw's own log said "(direct)" on the very same message.
        if _openclaw_message_is_direct(normalized):
            normalized["is_group"] = False
            normalized["openclaw_groupness"] = OPENCLAW_GROUPNESS_DERIVED_DIRECT
        else:
            normalized["is_group"] = True
            normalized["openclaw_groupness"] = OPENCLAW_GROUPNESS_ASSUMED
    else:
        normalized["is_group"] = bool(normalized["is_group"])
    # Absent stays absent: mention_gating_service.mention_facts_from_message
    # collapses a missing is_mentioned to False, which is the fail-closed
    # answer we want. Writing an explicit False here would be identical in
    # effect but would read as an assertion we are not entitled to make.
    if normalized.get("is_mentioned") is None:
        normalized.pop("is_mentioned", None)
    # Never inferred — see the docstring.
    normalized.pop("is_reply_to_sage", None)
    # An inbound hook event is by definition not the owner's own self-chat;
    # OpenClaw has no equivalent fact, and a forged one would bypass BOTH
    # the group and DM gates (_enforce_group_policy's and _is_owner_message's
    # self-chat shortcuts).
    normalized["is_self_chat"] = False
    return normalized


class _OpenClawPersonalChannelHandler(_LocalBridgePersonalChannelHandler):
    """A local-bridge channel whose bridge happens to be OpenClaw.

    Identical to its parent in every respect except that it normalizes the
    inbound addressing facts fail-closed first — see
    normalize_openclaw_gate_facts. It deliberately does NOT add a fourth
    gate, weaken an existing one, or introduce a second copy of the gate
    chain: after that one substitution it calls straight into
    _handle_local_bridge_gateway_channel_inbound, the same function
    Signal/iMessage/WeChat use.
    """

    async def handle_inbound(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_payload = dict(payload)
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        normalized_payload["message"] = normalize_openclaw_gate_facts(message)
        return await super().handle_inbound(
            gateway_id=gateway_id,
            registration=registration,
            payload=normalized_payload,
        )

    async def configure(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise ValueError(
            f"{self._label} is configured on the OpenClaw gateway running alongside Agent Computer."
        )


_handler_registry = PersonalChannelHandlerRegistry()
# WhatsApp/Telegram no longer get their own explicit .register() call — they
# are keys in LOCAL_BRIDGE_PERSONAL_CHANNELS now (via the OPENCLAW_PERSONAL_
# CHANNELS merge below it), so the loop registers _OpenClawPersonalChannel
# Handler for them exactly like every other cut-over platform.
for _channel_key, _channel_spec in LOCAL_BRIDGE_PERSONAL_CHANNELS.items():
    _handler_cls = (
        _OpenClawPersonalChannelHandler
        if _channel_key in OPENCLAW_PERSONAL_CHANNELS
        else _LocalBridgePersonalChannelHandler
    )
    _handler_registry.register(
        _handler_cls(
            _channel_key,
            _channel_spec["provider"],
            _channel_spec["label"],
        )
    )




def _emit_automatic_reply_audit(
    *,
    action: str,
    status: str,
    registration: Dict[str, Any],
    gateway_id: str,
    channel_key: str,
    provider: str,
    detail: str,
    metadata: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    trace_id: str = "",
) -> None:
    security_audit_service.emit_security_audit_event(
        action=action,
        status=status,
        tenant_id=str(registration.get("tenant_id") or "").strip() or None,
        workspace_id=str(registration.get("workspace_id") or "").strip() or None,
        actor_user_id=str(registration.get("user_id") or "").strip() or None,
        actor_auth_type="paired_gateway",
        channel=channel_key,
        machine_id=str(gateway_id or "").strip() or None,
        detail=detail,
        trace_id=trace_id or "",
        metadata={
            "gateway_id": str(gateway_id or "").strip(),
            "channel_key": channel_key,
            "provider": provider,
            "action_class": "automatic_inbound_reply",
            "risk_level": "critical",
            "governance_boundary": "paired_gateway",
            "requires_approval": status == "approval_required",
            "external_side_effect": status not in {"approval_required", "blocked", "ignored"},
            **dict(metadata or {}),
        },
        idempotency_key=idempotency_key,
    )


async def _control_command_block_result(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    inbound: Dict[str, Any],
    channel_key: str,
    provider: str,
    # REQUIRED, deliberately without a default: this must be the SAME
    # agent_id the caller passed to record_inbound_message for this message.
    # mark_inbound_processed below is an UPDATE scoped by
    # (gateway_id, channel_key, agent_id, external_message_id) — a default
    # here would silently address the legacy-unscoped row instead, match zero
    # rows, and lose the "seen, deliberately not replied to" marker. That is
    # exactly the bug this parameter was added to close, so a caller that
    # forgets must fail loudly rather than fall through to "".
    agent_id: str,
    external_message_id: str,
    remote_jid: str,
    text: str,
    # Trusted, server-computed owner signal — the caller's dm_decision["is_owner"]
    # from _enforce_dm_policy, evaluated a few lines above this call at every
    # call site. NEVER derive this from the inbound message or its metadata:
    # nothing in an inbound channel payload ever sets a role field (that was
    # this function's original bug — it read sender_role off
    # message["sender_role"]/["role"]/metadata equivalents that no producer
    # anywhere ever populates, so it was permanently None and every slash
    # command — including the owner's own /help — was silently blocked), and
    # even if a producer someday did, an inbound payload is untrusted data
    # from the remote party. OpenClaw's own CVE record is a client-asserted
    # senderIsOwner flag trusted over loopback — the same shape. is_owner
    # here is _is_owner_message's signal instead: message.is_self_chat
    # (computed gateway-side from the live connection's own account id) or a
    # match against this channel's previously linked owner identity.
    is_owner: bool,
    duplicate: bool,
    no_reply_prefix: str,
    trace_id: str = "",
) -> Optional[Dict[str, Any]]:
    sender_role = "owner" if is_owner else None
    command_check = channel_blocking_policy_service.check_personal_channel_control_command(
        text=text,
        sender_role=sender_role,
    )
    if not command_check or not bool(command_check.get("blocked")):
        return None
    no_reply_idempotency_key = f"{no_reply_prefix}command_blocked:{external_message_id}"
    refreshed_inbound = personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        agent_id=agent_id,
        external_message_id=external_message_id,
        reply_idempotency_key=no_reply_idempotency_key,
    )
    _emit_automatic_reply_audit(
        action=f"personal_channel.{channel_key.split('_', 1)[0]}.control_command",
        status="denied",
        registration=registration,
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        detail="Personal-channel control command was blocked before reaching the model.",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "command": command_check.get("command"),
            "sender_role": sender_role,
            "policy_reason": command_check.get("reason"),
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.control_command.denied:{gateway_id}:{channel_key}:{external_message_id}",
    )
    # Silence is a decision, not the absence of one (CLAUDE.md). A refused
    # command must say so — outbound: None here is byte-identical to "the
    # agent saw this and chose not to reply", which is exactly how the
    # original bug (sender_role always None) went unnoticed for so long.
    # Dispatch the SAME fixed, non-LLM refusal string
    # check_personal_channel_control_command already returns, the same way
    # _handle_dm_policy_blocked dispatches its pairing challenge: a fixed
    # string, never routed through the model.
    refusal_text = str(command_check.get("reply") or "").strip()
    outbound: Optional[Dict[str, Any]] = None
    if refusal_text:
        refusal_idempotency_key = f"{no_reply_prefix}command_blocked_reply:{external_message_id}"
        outbound, outbound_created = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            agent_id=agent_id,
            idempotency_key=refusal_idempotency_key,
            remote_jid=remote_jid,
            text=refusal_text,
            reply_to_external_message_id=external_message_id,
            metadata={"reply_source": "control_command_blocked"},
        )
        if outbound_created and str(outbound.get("status") or "").strip() != "delivered":
            try:
                _enforce_personal_channel_dispatch_decision(
                    gateway_id=str(gateway_id or "").strip(),
                    registration=registration,
                    capability_id=f"{channel_key}.send",
                    request_id=refusal_idempotency_key,
                )
                dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
                    gateway_id=str(gateway_id or "").strip(),
                    channel_key=channel_key,
                    provider=provider,
                    remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
                    text=refusal_text,
                    idempotency_key=refusal_idempotency_key,
                    reply_to_external_message_id=None,
                )
                delivered = personal_channels_repository.mark_outbound_delivered(
                    gateway_id=str(gateway_id or "").strip(),
                    channel_key=channel_key,
                    agent_id=agent_id,
                    idempotency_key=refusal_idempotency_key,
                    external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
                    metadata={"dispatch_result": dispatch_result},
                )
                outbound = delivered or outbound
            except Exception:
                _logger.warning(
                    "control_command: failed to dispatch refusal reply channel=%s gateway=%s",
                    channel_key, gateway_id, exc_info=True,
                )
    return {
        "duplicate": duplicate,
        "inbound": refreshed_inbound or inbound,
        "outbound": outbound,
        "blocked": True,
        "policy": command_check,
    }


# ── Shared /command dispatcher waist ───────────────────────────────────
#
# sage_command_dispatcher.dispatch_command() -> command_registry (/new /main
# /compact /stop /clear /export /model /thinking /help /commands /tools
# /status /whoami /usage /memory /forget /tasks /agents /skills /config /mcp
# /plugins /debug /tts /bash — 24 commands) was only reached from
# _deliver_whatsapp_personal_reply, handle_cloud_channel_inbound, and the
# hosted-Telegram route. Every OTHER personal-channel delivery path —
# Telegram-personal (QR) and the whole local-bridge/OpenClaw family
# (Signal, iMessage, WeChat, and every openclaw_* transported channel) —
# never dispatched a command at all: an owner's "/compact" passed
# _control_command_block_result (above) and then fell through to an
# ordinary LLM turn, which received the literal text "/compact" and chatted
# about it.
#
# Fixed by giving every Gateway-WS delivery path ONE waist to cross instead
# of growing its own copy of this block — which is exactly how WhatsApp's
# own inline block acquired its OWN bug (see
# git blame on _deliver_whatsapp_personal_reply's command branch): it wrote
# the outbound row and returned WITHOUT ever calling dispatch_channel_
# outbound, so a recognized command stayed "pending" forever. Authorization
# is NOT decided here: the dmPolicy/group gates (_enforce_dm_policy,
# _enforce_group_policy) and _control_command_block_result already ran in
# every caller before this is reached, and this function does not re-derive
# owner-ness from the inbound payload — OpenClaw has a real CVE from a
# client-asserted senderIsOwner.
async def _dispatch_personal_channel_command(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    inbound: Dict[str, Any],
    channel_key: str,
    provider: str,
    capability_id: str,
    # REQUIRED, no default — mark_inbound_processed is an UPDATE scoped by
    # (gateway_id, channel_key, agent_id, external_message_id). A default
    # here would silently address the legacy-unscoped row instead of the one
    # record_inbound_message actually wrote, and lose the "this inbound
    # message was handled" marker — the exact defect documented on
    # _deliver_local_bridge_personal_reply's own agent_id parameter above.
    agent_id: str,
    # Whether OUTBOUND rows for this channel family are scoped by agent_id.
    # WhatsApp and Telegram-personal scope them (pass agent_id here too); the
    # local-bridge/OpenClaw family deliberately does NOT — its outbound rows
    # must share one unscoped idempotency namespace with the explicit
    # POST .../messages send route, which has no agent_id to pass at all (see
    # _deliver_local_bridge_personal_reply's own comment on its outbound
    # calls). Leave this None (the default) to stay unscoped, matching that.
    outbound_agent_id: Optional[str] = None,
    external_message_id: str,
    remote_jid: str,
    text: str,
    duplicate: bool,
    trace_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Run *text* through the shared /command registry; return None when it
    was not a recognised command (caller falls through to a normal turn), or
    the standard {"duplicate", "inbound", "outbound"} envelope once the
    command's reply has been durably dispatched.
    """
    from server_modules.sage_command_dispatcher import dispatch_command as _dispatch_cmd

    workspace_id = str(registration.get("workspace_id") or "").strip()
    cmd_reply = await _dispatch_cmd(
        command=text,
        workspace_id=workspace_id,
        thread_id="sage-main",
        channel_origin=channel_key,
        sender_id=remote_jid or None,
    )
    if cmd_reply is None:
        return None

    idempotency_key = f"{channel_key}:cmd:{external_message_id}"
    outbound_scope_kwargs: Dict[str, Any] = (
        {"agent_id": outbound_agent_id} if outbound_agent_id is not None else {}
    )
    outbound, _ = personal_channels_repository.create_or_get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=idempotency_key,
        remote_jid=remote_jid,
        text=cmd_reply,
        reply_to_external_message_id=external_message_id,
        **outbound_scope_kwargs,
    )

    if str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            agent_id=agent_id,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": duplicate, "inbound": inbound, "outbound": outbound, "command_handled": True}

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=capability_id,
        request_id=idempotency_key,
    )
    # THE fix this whole helper exists for: a per-branch copy of this block
    # (WhatsApp's own inline version, before it was routed through here) once
    # wrote the outbound row above and RETURNED without ever calling
    # dispatch_channel_outbound, leaving the command's reply "pending"
    # forever — nothing ever actually sent it. Continuing to the real
    # dispatch below is the whole difference, and a single call site means
    # there is only one place this can regress.
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        provider=provider,
        remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
        text=str(outbound.get("text") or "").strip(),
        idempotency_key=idempotency_key,
        # Command replies are never threaded as a quote-reply bubble, same
        # as every other automatic reply on these channels.
        reply_to_external_message_id=None,
        media=[],
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=idempotency_key,
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
        **outbound_scope_kwargs,
    )
    personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        agent_id=agent_id,
        external_message_id=external_message_id,
        reply_idempotency_key=idempotency_key,
    )
    _emit_automatic_reply_audit(
        action=f"personal_channel.{channel_key.split('_', 1)[0]}.command",
        status="delivered",
        registration=registration,
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        detail="Slash command reply was dispatched immediately.",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "reply_text_length": len(cmd_reply),
            "dispatched": True,
            "dispatch_external_message_id": str(dispatch_result.get("external_message_id") or "").strip() or None,
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.{channel_key}.command.delivered:{gateway_id}:{idempotency_key}",
    )
    return {"duplicate": duplicate, "inbound": inbound, "outbound": delivered or outbound, "command_handled": True}


# ── dmPolicy: sender allowlist / pairing enforcement ──────────────────
#
# The channel manifests (empyralis-gateway's PersonalChannelCapabilityManifest)
# declare `safety: {ownerPairingRequired: true, allowlistRequired: ...}` for
# every personal channel. Until this section existed that was a false claim:
# nothing server-side ever checked WHO was messaging before generating a
# reply — any contact who texted the owner's own WhatsApp/Telegram number
# got an automatic reply, indistinguishable from the owner themselves. This
# is the real gate. See docs/PLATFORM-MAP.md for the write-up.
#
# Modelled on OpenClaw's dmPolicy (extensions/signal/src/monitor/access-policy.ts,
# extensions/slack/src/monitor/dm-auth.ts, src/channels/message-access/sender-gates.ts):
# a per-channel policy of open | allowlist | pairing (+ a hard "disabled"
# there we don't need, since dropping the whole channel is a separate
# on/off switch already). We add owner_only as Empyralis's OWN strictest
# mode and make it the default — OpenClaw's channels don't have a concept
# of "the DM channel IS one specific person's own account", personal
# channels here do.

DM_POLICY_OWNER_ONLY = "owner_only"
DM_POLICY_ALLOWLIST = "allowlist"
DM_POLICY_PAIRING = "pairing"
DM_POLICY_OPEN = "open"
DM_POLICY_MODES = {DM_POLICY_OWNER_ONLY, DM_POLICY_ALLOWLIST, DM_POLICY_PAIRING, DM_POLICY_OPEN}
# Default OPEN (reply to everyone) to preserve existing live-agent behavior and
# NOT retroactively silence already-configured personal-channel agents; the
# channel manifest now honestly reflects the enforced mode. The owner can
# restrict any agent to owner_only / allowlist / pairing per channel. The strict
# default (owner_only) is a product decision deferred to the owner, who can test
# it on a live channel — flipping this constant is the one-line change to adopt it.
#
# SCOPE OF THIS CONSTANT — read before touching it. It governs ONE thing only:
# the mode a REAL, resolved agent install falls back to when it has never
# explicitly saved a dm_policy for this channel (_normalize_dm_policy_config's
# "mode not in DM_POLICY_MODES" branch below). It must never be read by, or
# substituted into, the SEPARATE fallback for when no real agent identity
# could be resolved AT ALL — see _unresolved_identity_dm_policy_config, always
# owner_only, hardcoded, independent of this constant.
#
# History of why that distinction now exists as two separate functions:
# ed9c2cdd60 added a safe owner_only default (one constant, both fallbacks).
# ee3fca4f7c flipped THIS constant to open as a deliberate live-agent-compat
# product decision — but because both fallbacks still shared the one
# constant at the time, that single-line change also silently reopened the
# identity-less fallback (agent_id=="" — the PERMANENT case for local-bridge
# Signal/iMessage/WeChat inbound, which has no per-agent identity table to
# resolve against at all) to "reply to any stranger," directly contradicting
# _load_agent_dm_policy_config's own docstring and the "every sender is
# blocked" comment at its local-bridge call site. Splitting the two fallbacks
# means a future change to this constant can never do that again.
DEFAULT_DM_POLICY_MODE = DM_POLICY_OPEN

# ── The OpenClaw-transported lane is a DIFFERENT lane, with a different
#    default and a narrower set of expressible modes. ────────────────────
#
# THE DEADLOCK THIS EXISTS TO BREAK (found live, 2026-08-14, against a real
# openclaw@2026.6.10 with a real Telegram bot token on the box):
#
#   DEFAULT_DM_POLICY_MODE = open              (above — first-party lane's
#                                               deliberate live-agent-compat
#                                               product decision)
#          │  every OpenClaw channel rendered from it, since nothing could
#          │  ever write a different one (see DM_POLICY_CHANNEL_KEYS below:
#          │  _persist_agent_dm_policy_config had two callers, both inside
#          │  this module, neither reachable from any route)
#          ▼
#   openclaw-config-plan.ts  ─▶  dmPolicy: "open", allowFrom: ["*"]
#          ▼
#   `openclaw security audit`  ─▶  channels.<id>.dm.open  [CRITICAL]
#          ▼
#   blockingAuditFindings  ─▶  provisioning REFUSED, on every box, forever.
#
# So the whole channel transport was unprovisionable through every surface
# the product offers. Not a credential problem: a missing write path
# colliding with a mandatory audit.
#
# WHY THE FIX IS `allowlist` AND NOT `pairing`/`owner_only`. Read
# empyralis-gateway/src/openclaw/provisioning/openclaw-config-plan.ts's own
# dmPolicy switch before changing this: THREE of our four modes render as
# OpenClaw's `open`, deliberately and for good reasons —
#
#   open        -> open + ["*"]   (exact)
#   pairing     -> open + ["*"]   dm_pairing_widened_to_open: their pairing
#                                 blocks dispatch, so our pairing challenge
#                                 (a REPLY we send) could never be sent
#   owner_only  -> open + ["*"]   dm_owner_only_widened_to_open: they have
#                                 no owner_only, and an allowlist of the
#                                 owner's id is STRICTER the moment that id
#                                 is stale — the unrecoverable direction
#   allowlist   -> allowlist      (exact, and the ONLY non-open rendering)
#
# and their audit flags `dmPolicy === "open"` UNCONDITIONALLY — the
# `allowFrom` wildcard their remediation text mentions only clears the
# separate `dm.open_invalid` warn, never the critical. Verified by reading
# their own dist/audit-channel.collect.*.js, not inferred from the message.
# So `allowlist` is not a preference here; it is the only mode this
# transport can carry at all.
#
# It is also the doctrinally correct default for this lane, which is why
# this is a fix rather than a workaround. CLAUDE.md, on the OpenClaw
# transport: gate-before-model, and "silent BY DESIGN until the owner
# allowlists the chat". An empty allowlist means exactly that — nobody yet,
# stated honestly — and DM_POLICY_CHANNEL_KEYS' route below is the thing
# that finally lets an owner fill it.
#
# DELIBERATELY NOT a flip of DEFAULT_DM_POLICY_MODE. That constant is a
# live-agent-compat decision for the first-party channels and is untouched;
# this is a second, lane-scoped default, so the two can never move
# together. See DEFAULT_DM_POLICY_MODE's own comment for the last time one
# constant served two callers and a single-line change reached somewhere
# nobody intended.
DEFAULT_OPENCLAW_DM_POLICY_MODE = DM_POLICY_ALLOWLIST

# The modes an owner may actually SET on an OpenClaw-transported channel.
# Everything else in DM_POLICY_MODES renders as OpenClaw's `open` and would
# make this box unprovisionable from the moment it was saved — a control
# that bricks the transport is worse than no control, so the write path
# refuses it with an owner-facing reason instead of storing it and letting
# provisioning fail later with no way back.
#
# NOT hand-transcribed from that switch: test_personal_channels_dm_policy.py
# DERIVES the widened set from the gateway's own source (the
# `dm_<mode>_widened_to_open` codes it emits, which name their own modes)
# and asserts this set is exactly the complement. If someone later teaches
# the gateway to express `pairing` natively, that test goes red here rather
# than leaving this list quietly refusing a mode that now works.
OPENCLAW_SETTABLE_DM_POLICY_MODES = frozenset({DM_POLICY_ALLOWLIST})


def _is_openclaw_transported_channel(channel_key: str) -> bool:
    return str(channel_key or "").strip().lower() in OPENCLAW_PERSONAL_CHANNELS


def _default_dm_policy_mode_for_channel(channel_key: str) -> str:
    """The configured-but-unset default, per LANE. See
    DEFAULT_OPENCLAW_DM_POLICY_MODE's comment block above for why the
    OpenClaw-transported lane cannot share the first-party default."""
    if _is_openclaw_transported_channel(channel_key):
        return DEFAULT_OPENCLAW_DM_POLICY_MODE
    return DEFAULT_DM_POLICY_MODE


def _unresolved_identity_dm_policy_config(*, channel_key: str = "") -> Dict[str, Any]:
    """Fail-closed fallback for _load_agent_dm_policy_config when NO real
    per-agent install could even be identified: no agent_id resolved at all
    (personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID). Until this
    build this was the permanent case for every local-bridge channel
    (Signal/iMessage/WeChat), which had no per-agent state table to resolve
    an owner identity against at all. It no longer is —
    _resolve_local_bridge_agent_id now resolves a real agent_id for these
    three the same way WhatsApp/Telegram always have — but this fallback
    stays reachable whenever that resolution genuinely can't land (an
    ambiguous or not-yet-claimed preferred_gateway_id, or an install lookup
    that failed / returned nothing); see that function's own docstring for
    the full contract.

    Deliberately NOT DEFAULT_DM_POLICY_MODE: that constant is the
    configured-but-unset default for a REAL agent an owner can actually go
    open Settings and change the policy for (see its own comment). There is
    no agent to apply a per-agent policy to here, so there is no "the owner
    already saw this and can adjust it" story to justify anything looser
    than the strictest mode. Always owner_only, hardcoded, independent of
    DEFAULT_DM_POLICY_MODE, so a future change to that constant (a live
    product decision another engineer is entitled to make) can never
    silently reopen this identity-less fallback too — see
    DEFAULT_DM_POLICY_MODE's own comment for exactly that having already
    happened once (ee3fca4f7c).

    `channel_key` selects the STRICTEST EXPRESSIBLE mode for the lane, and
    changes nothing about how closed this is. On the OpenClaw-transported
    lane owner_only is not expressible — it renders as OpenClaw's own
    `open` (see DEFAULT_OPENCLAW_DM_POLICY_MODE above), which is both the
    opposite of what this fallback means AND the finding that makes the box
    unprovisionable. An empty `allowlist` admits strictly FEWER senders
    than owner_only does (nobody at all, rather than the owner), so this
    stays fail-closed in the only direction that matters. Still hardcoded
    per lane and still independent of both default constants."""
    if _is_openclaw_transported_channel(channel_key):
        return {"mode": DM_POLICY_ALLOWLIST, "allowlist": [], "pending_pairing": {}}
    return {"mode": DM_POLICY_OWNER_ONLY, "allowlist": [], "pending_pairing": {}}


def _normalize_dm_policy_config(raw: Any, *, channel_key: str = "") -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in DM_POLICY_MODES:
        mode = _default_dm_policy_mode_for_channel(channel_key)
    allowlist = sorted({str(x).strip() for x in (data.get("allowlist") or []) if str(x or "").strip()})
    pending_raw = data.get("pending_pairing") if isinstance(data.get("pending_pairing"), dict) else {}
    pending = {
        str(sender_id): dict(entry)
        for sender_id, entry in pending_raw.items()
        if str(sender_id or "").strip() and isinstance(entry, dict)
    }
    return {"mode": mode, "allowlist": allowlist, "pending_pairing": pending}


async def _load_agent_dm_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
) -> Dict[str, Any]:
    """Read-only, safe-by-default. Persisted at
    install_metadata.dm_policy[channel_key] on the agent's own
    workspace_agent_installs row (the same "config lives in install
    metadata" convention triage_service.resolve_triage_config uses for
    its own per-agent config).

    Any lookup failure, missing install, or an unresolved agent_id
    (personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID — see
    _resolve_agent_id_for_inbound's docstring for when that happens)
    returns owner_only with an empty allowlist. This NEVER fails open to
    "open" or "allowlist" just because the config lookup itself failed —
    only an explicit, successfully-loaded config can relax the default.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return _unresolved_identity_dm_policy_config(channel_key=channel_key)
    try:
        from server_modules import agent_registry_repository as _repo

        install = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id,
            tenant_id=str(tenant_id or "default").strip() or "default",
            workspace_id=str(workspace_id or "default").strip() or "default",
        )
    except Exception:
        _logger.warning("dm_policy: install lookup failed for agent_id=%s — defaulting to owner_only", normalized_agent_id, exc_info=True)
        return _unresolved_identity_dm_policy_config(channel_key=channel_key)
    if not isinstance(install, dict):
        return _unresolved_identity_dm_policy_config(channel_key=channel_key)
    meta = dict(install.get("install_metadata") or install.get("metadata") or {})
    all_policies = meta.get("dm_policy") if isinstance(meta.get("dm_policy"), dict) else {}
    return _normalize_dm_policy_config(all_policies.get(channel_key), channel_key=channel_key)


async def _persist_agent_dm_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    config: Dict[str, Any],
) -> bool:
    """Write path for a future settings API / owner-approval command to call.
    Best-effort: returns False (never raises) on any failure — a persistence
    failure must never surface as a 500 in the middle of message handling.
    update_workspace_agent_install merges `metadata` shallowly at the TOP
    level only, so we read-modify-write the WHOLE dm_policy dict (all
    channels), not just this channel_key's entry, or a concurrent update
    to a sibling channel's policy could get clobbered."""
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return False
    resolved_tenant_id = str(tenant_id or "default").strip() or "default"
    resolved_workspace_id = str(workspace_id or "default").strip() or "default"
    try:
        from server_modules import agent_registry_repository as _repo

        install = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id, tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id,
        )
        if not isinstance(install, dict):
            return False
        meta = dict(install.get("install_metadata") or install.get("metadata") or {})
        all_policies = dict(meta.get("dm_policy")) if isinstance(meta.get("dm_policy"), dict) else {}
        all_policies[channel_key] = _normalize_dm_policy_config(config, channel_key=channel_key)
        updated = await _repo.update_workspace_agent_install(
            normalized_agent_id,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            metadata={"dm_policy": all_policies},
        )
        return updated is not None
    except Exception:
        _logger.warning("dm_policy: persist failed for agent_id=%s channel=%s", normalized_agent_id, channel_key, exc_info=True)
        return False


async def approve_dm_policy_pairing_request(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    sender_id: Optional[str] = None,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    """Owner-approval primitive: move a pending pairing request into the
    allowlist. Matches by sender_id OR by the one-time pairing code
    (whichever the approval route has on hand).

    Wired to POST .../dm-policy/pairing-approvals (routes_personal_channels.py)
    as of 2026-08-14. Before that it had zero callers anywhere outside this
    module — the same "built, tested, and never wired" shape
    _persist_agent_group_policy_config sat in until 2026-08-07, and half of
    why the pairing mode it serves was unreachable in practice.

    Returns {"approved": bool, "sender_id": Optional[str], "reason": Optional[str]}.
    """
    config = await _load_agent_dm_policy_config(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key,
    )
    pending = config["pending_pairing"]
    target_sender_id = str(sender_id or "").strip()
    if not target_sender_id and str(code or "").strip():
        normalized_code = str(code or "").strip()
        for candidate_id, entry in pending.items():
            if str(entry.get("code") or "").strip() == normalized_code:
                target_sender_id = candidate_id
                break
    if not target_sender_id or target_sender_id not in pending:
        return {"approved": False, "sender_id": target_sender_id or None, "reason": "pairing_request_not_found"}
    pending = dict(pending)
    pending.pop(target_sender_id, None)
    config["pending_pairing"] = pending
    if target_sender_id not in config["allowlist"]:
        config["allowlist"] = sorted(set(config["allowlist"]) | {target_sender_id})
    persisted = await _persist_agent_dm_policy_config(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key, config=config,
    )
    if not persisted:
        return {"approved": False, "sender_id": target_sender_id, "reason": "persist_failed"}
    return {"approved": True, "sender_id": target_sender_id, "reason": None}


# Channel keys dm_policy can ever apply to. Deliberately the SAME membership
# rule GROUP_POLICY_CHANNEL_KEYS uses (WhatsApp/Telegram Personal plus the
# whole local-bridge set, which LOCAL_BRIDGE_PERSONAL_CHANNELS.update()
# already folds every OpenClaw-transported channel into) — a message on any
# of them crosses _enforce_dm_policy, so a channel the gate reads and the
# owner cannot write is exactly the deadlock this build exists to close.
#
# WHATSAPP_PERSONAL_CHANNEL_KEY DROPPED 2026-08-15. It is the mirror image of
# the deadlock this set exists to close: a channel the OWNER can write and the
# GATE never reads. The 2026-08-14 cutover deleted the Baileys runtime, every
# WhatsApp inbound handler, and the lane spec, and cloud-session-manager has
# no whatsapp producer at all (`cloud-session-manager/` was telegram-only, and
# is itself deleted as of 2026-08-15 — see the note below),
# so no message can ever carry this key into _enforce_dm_policy again — while
# the route went on accepting the write and persisting a policy with no
# reader. A write that silently goes nowhere is worse than a 400: the owner
# believes they configured something.
#
# TELEGRAM_PERSONAL_CHANNEL_KEY JOINED IT 2026-08-15, when the cloud-session
# lane was deleted. Until then it was the one first-party key still in this
# set, because handle_cloud_channel_inbound genuinely ran _enforce_dm_policy
# with it. With that handler gone there is no producer of the key left, so it
# is now in exactly the position whatsapp_personal was.
DM_POLICY_CHANNEL_KEYS = frozenset(LOCAL_BRIDGE_PERSONAL_CHANNELS.keys())


class UnsupportedDmPolicyModeError(ValueError):
    """`mode` is a real DM_POLICY_MODES value, but not one this channel's
    transport can carry. A distinct type (not a bare ValueError) so a route
    can tell "you typed a mode that does not exist" from "that mode exists
    and this transport cannot express it" — different facts, different
    remedies, and collapsing them would tell an owner to fix a typo that
    isn't there."""


async def update_agent_dm_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    mode: str,
    allowlist: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Owner-facing write PRIMITIVE for the DM gate — the validating,
    route-callable counterpart to _persist_agent_dm_policy_config, which had
    exactly two callers before this build, BOTH inside this module and
    NEITHER reachable from any route: approve_dm_policy_pairing_request
    (itself unwired) and the inbound branch that only fires when the mode is
    already `pairing`. So the mode could never leave its default by any means
    the product offered, which — on the OpenClaw-transported lane, where that
    default rendered as OpenClaw's `open` — made `openclaw security audit`
    permanently unclean and the whole transport unprovisionable. See
    DEFAULT_OPENCLAW_DM_POLICY_MODE's comment block for the full chain.

    Same contract as update_agent_group_policy_config beside it: ValueError
    for a missing/unresolvable agent_id, an unknown channel_key, or a mode
    outside DM_POLICY_MODES (the route maps that to 400); None if the agent
    install could not be found/updated (404).

    UnsupportedDmPolicyModeError (a ValueError subclass, so an existing
    `except ValueError` route still degrades to a 400) for a mode this
    channel's transport cannot express — see
    OPENCLAW_SETTABLE_DM_POLICY_MODES. Refused at WRITE time on purpose: the
    alternative is storing it, returning 200, and having the box refuse every
    later provisioning run with a finding that names OpenClaw's config rather
    than the setting that caused it.

    pending_pairing is CARRIED OVER, never reset. It is a live record of
    strangers who have already been challenged; dropping it on an unrelated
    mode/allowlist edit would re-challenge every one of them the next time
    they wrote — the same "record but do not re-message" contract
    _enforce_dm_policy's own repeat branch exists to keep.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        raise ValueError(
            "agent_id is required to configure a per-agent direct-message policy — pass the specific "
            "agent install this policy applies to (dm_policy is stored per agent+channel, not per gateway)."
        )
    normalized_channel_key = str(channel_key or "").strip().lower()
    if normalized_channel_key not in DM_POLICY_CHANNEL_KEYS:
        raise ValueError(f"channel_key must be one of {sorted(DM_POLICY_CHANNEL_KEYS)}.")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in DM_POLICY_MODES:
        raise ValueError(f"mode must be one of {sorted(DM_POLICY_MODES)}.")
    if (
        _is_openclaw_transported_channel(normalized_channel_key)
        and normalized_mode not in OPENCLAW_SETTABLE_DM_POLICY_MODES
    ):
        label = OPENCLAW_PERSONAL_CHANNELS.get(normalized_channel_key, {}).get("label", normalized_channel_key)
        raise UnsupportedDmPolicyModeError(
            f"{label} can only be limited to a list of specific people. This computer's channel software "
            "refuses to run at all while any channel accepts messages from everyone, so Empyralis cannot "
            "save that setting here. Add the people who may message this agent instead."
        )
    normalized_allowlist = sorted({str(x).strip() for x in (allowlist or []) if str(x or "").strip()})
    existing = await _load_agent_dm_policy_config(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
    )
    config = {
        "mode": normalized_mode,
        "allowlist": normalized_allowlist,
        "pending_pairing": dict(existing.get("pending_pairing") or {}),
    }
    persisted = await _persist_agent_dm_policy_config(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
        config=config,
    )
    if not persisted:
        return None
    return await _load_agent_dm_policy_config(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
    )


def _channel_owner_linked_id(*, channel_key: str, state: Optional[Dict[str, Any]]) -> str:
    """The owner's own identity on this channel, as last established by a
    real login/connect event or a deliberate owner action — see
    _resolve_linked_identity_for_sync's docstring for why this is NEVER
    derived from an inbound message's own sender fields.

    The local-bridge branch was MISSING until 2026-08-15, and its absence is
    why owner authority was unreachable from every live channel in the
    product. This function branched on exactly the two first-party keys, and
    the 2026-08-14 cutover moved every channel onto `openclaw_*` — which
    LOCAL_BRIDGE_PERSONAL_CHANNELS carries — so it returned "" for every real
    message:

        _channel_owner_linked_id -> ""            no linked identity possible
          -> _is_owner_message False              (is_self_chat is hardcoded
                                                   False on this transport by
                                                   design, so it is the only
                                                   remaining owner route)
          -> "[Telegram · DM · from <name> — NOT your owner]" on every turn
          -> _resolve_channel_sender_class -> "audience" -> no shell/hardware

    The column it reads has existed since the local-bridge table was created
    (personal_channel_local_bridge_states.linked_identity, keyed by exactly
    the (gateway_id, channel_key, agent_id) tuple this fact belongs to, and
    already the third source list_owner_linked_channel_identities_for_workspace
    reads for the turn's own tool-authority decision) — nothing wrote it and
    nothing read it. Wiring the read here rather than inventing a parallel
    store keeps ONE answer to "who is the owner on this channel", which is
    the property sage_agent_runtime_service._resolve_channel_sender_class's
    docstring is emphatic about not splitting in two.

    Deliberately NOT the dm_policy allowlist: "may message this agent" and
    "IS the owner" are different facts, and reading the allowlist here would
    silently promote every allowlisted sender to owner authority.
    """
    if not isinstance(state, dict):
        return ""
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        return str(state.get("linked_jid") or "").strip()
    if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        return str(state.get("linked_user_id") or "").strip()
    if channel_key in LOCAL_BRIDGE_PERSONAL_CHANNELS:
        return str(state.get("linked_identity") or "").strip()
    return ""


def _channel_prefixed_identity_tail(*, channel_key: str, value: Any) -> str:
    """`value` with THIS channel's own transport prefix removed, and nothing
    else removed.

    The lane addresses one person two ways in one payload — `remote_jid`
    arrives channel-prefixed ("telegram:1932934047") while `sender_jid` does
    not ("1932934047") — so a sender id that fell back to `remote_jid` would
    never equal a stored bare identity, and the owner would be a stranger on
    exactly the channels where sender_jid happens to be absent.

    Strips ONLY the channel's own id followed by a colon, resolved from the
    pinned manifest, never an arbitrary separator. That distinction is the
    whole point: a general "take everything after the last colon" rule would
    make an attacker-supplied `"anything:1932934047"` canonicalize to the
    owner's own id, i.e. it would hand owner authority to whoever can put a
    colon in a sender field. A prefix the registry itself names cannot be
    chosen by a sender. Unknown/unprefixed values come back unchanged.

    Resolved through `channel_for_key`, NOT `openclaw_channel_id`: the latter
    RAISES for a channel this transport does not carry, and the two live
    callers both legitimately see such keys — the DM gate still runs for the
    first-party keys, and _resolve_channel_sender_class sees every
    channel_origin in the product. A raise there is not a loud failure, it is
    a silent one: that call site fails closed to "audience", so an owner on a
    hosted channel would have been downgraded with nothing said.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    channel = openclaw_channel_registry.channel_for_key(str(channel_key or "").strip())
    channel_id = str(getattr(channel, "id", "") or "").strip()
    if not channel_id:
        return text
    prefix = f"{channel_id}:"
    if text.lower().startswith(prefix.lower()):
        return text[len(prefix):].strip()
    return text


class UnsupportedOwnerIdentityChannelError(ValueError):
    """`channel_key` is a real channel, but not one this identity can be
    established on. A distinct type for the same reason
    UnsupportedDmPolicyModeError is one: "that channel does not exist" and
    "that channel establishes its owner some other way" are different facts
    with different remedies."""


# Channel keys an owner identity can be set on. The local-bridge family,
# which LOCAL_BRIDGE_PERSONAL_CHANNELS.update() folds every
# OpenClaw-transported channel into — i.e. every channel that is live today.
#
# DERIVED from the same map DM_POLICY_CHANNEL_KEYS / GROUP_POLICY_CHANNEL_KEYS
# read, never a fourth hand-written channel list: a channel OpenClaw adds
# tomorrow must not need an edit here, and this module has already learned
# what a transcribed copy costs (see the LOCAL_BRIDGE channel-map note in
# routes_personal_channels.py).
#
# WhatsApp/Telegram Personal are absent because their own identity is
# established by their login/connect event (linked_jid / linked_user_id),
# not by an owner typing it — and both keys are retired anyway. A write path
# for a fact something else already owns is exactly the "two answers to one
# question" shape this build exists to close.
OWNER_IDENTITY_CHANNEL_KEYS = frozenset(LOCAL_BRIDGE_PERSONAL_CHANNELS.keys())


def get_personal_channel_owner_identity(
    *, gateway_id: str, channel_key: str, agent_id: str,
) -> Dict[str, Any]:
    """Read side of the owner link, for the owner's own settings screen.

    Returns ``{"sender_id": str|None, "channel_key": str, "agent_id": str}``.
    Reads the SAME column _channel_owner_linked_id reads at gate time, so
    what the screen shows and what the gate uses cannot drift.
    """
    normalized_channel_key = str(channel_key or "").strip().lower()
    normalized_agent_id = str(agent_id or "").strip()
    state = personal_channels_repository.get_local_bridge_state(
        str(gateway_id or "").strip(),
        channel_key=normalized_channel_key,
        agent_id=normalized_agent_id,
    )
    return {
        "channel_key": normalized_channel_key,
        "agent_id": normalized_agent_id,
        "sender_id": _channel_owner_linked_id(
            channel_key=normalized_channel_key, state=state
        )
        or None,
    }


def set_personal_channel_owner_identity(
    *,
    registration: Dict[str, Any],
    gateway_id: str,
    channel_key: str,
    agent_id: str,
    sender_id: Optional[str],
) -> Dict[str, Any]:
    """Establish (or clear, on an empty sender_id) which identity on this
    channel is the OWNER, for this one agent.

    This is the deliberate owner action the whole owner route hangs off, and
    it is the ONLY writer of the column. It exists as a separate action
    rather than a per-message inference for the reason
    _resolve_linked_identity_for_sync's docstring records at length: the old
    per-message sync passed the inbound message's own sender field straight
    into the linked-identity column, so the owner's identity was silently
    replaced by whichever stranger had most recently texted. An identity
    that any inbound message can rewrite is not an identity.

    It is also deliberately NOT the dm_policy allowlist, and must never be
    folded into it. "May message this agent" and "IS the owner" are
    different facts: the allowlist is a list the owner grows to admit other
    people, and reading it as owner identity would promote every one of them
    to shell/hardware authority the moment they were admitted.

    Scoped to (gateway_id, channel_key, agent_id) — per AGENT, not per box.
    Two agents sharing one Agent Computer have two rows, so establishing an
    owner on one never grants it on the other.

    Raises UnsupportedOwnerIdentityChannelError for a channel whose owner
    identity is established elsewhere, and ValueError for a missing agent_id
    (this fact has no meaning unscoped — writing it under
    LEGACY_UNSCOPED_AGENT_ID would attach an owner to a sentinel row that
    _resolve_local_bridge_agent_id can hand to any agent).
    """
    normalized_channel_key = str(channel_key or "").strip().lower()
    if normalized_channel_key not in OWNER_IDENTITY_CHANNEL_KEYS:
        raise UnsupportedOwnerIdentityChannelError(
            "This channel signs in as the owner directly, so there is nothing to set here."
        )
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise ValueError(
            "Choose which agent this is for — an owner is established for one agent on one channel."
        )
    normalized_gateway_id = str(gateway_id or "").strip()
    # Canonicalized on the way IN, once, so the stored value and every
    # comparison against it are already in the same shape — rather than
    # canonicalizing at each read and hoping every future reader remembers.
    resolved_sender_id = _channel_prefixed_identity_tail(
        channel_key=normalized_channel_key, value=sender_id
    )
    existing = personal_channels_repository.get_local_bridge_state(
        normalized_gateway_id, channel_key=normalized_channel_key, agent_id=normalized_agent_id,
    )
    personal_channels_repository.upsert_local_bridge_state(
        gateway_id=normalized_gateway_id,
        tenant_id=str(registration.get("tenant_id") or "default").strip() or "default",
        workspace_id=str(registration.get("workspace_id") or "default").strip() or "default",
        user_id=str(registration.get("user_id") or "").strip(),
        channel_key=normalized_channel_key,
        agent_id=normalized_agent_id,
        provider=str(
            (existing or {}).get("provider")
            or LOCAL_BRIDGE_PERSONAL_CHANNELS.get(normalized_channel_key, {}).get(
                "provider", normalized_channel_key
            )
        ).strip(),
        # Preserve whatever the connection actually is. This action says who
        # the owner is; it does not claim the channel connected.
        status=str((existing or {}).get("status") or "connecting").strip() or "connecting",
        connected_at=str((existing or {}).get("connected_at") or "").strip() or None,
        # "" clears, a value sets — never None here, which the repository
        # reads as "I have nothing to say", i.e. a clear would silently be a
        # no-op and the screen would report a removal that did not happen.
        linked_identity=resolved_sender_id,
        metadata={"owner_identity_source": "owner_action"},
    )
    return get_personal_channel_owner_identity(
        gateway_id=normalized_gateway_id,
        channel_key=normalized_channel_key,
        agent_id=normalized_agent_id,
    )


def _resolve_linked_identity_for_sync(*, current_value: Optional[str], preserved_value: str) -> Optional[str]:
    """What to write for linked_jid/linked_user_id on a PER-MESSAGE state
    sync. `current_value` is the signal carried on THIS message (only ever
    non-empty for a genuine owner/self-chat event — callers must not pass
    an arbitrary contact's sender_jid here). `preserved_value` is whatever
    is already persisted.

    Why this function exists: personal_channels_repository.upsert_whatsapp_state
    / upsert_telegram_state overwrite linked_jid/linked_user_id UNCONDITIONALLY
    on every call (no COALESCE — unlike their own `metadata` column, which
    IS merged). The per-message sync in _handle_whatsapp_gateway_channel_inbound
    used to pass message.get("sender_jid") directly — which for a plain 1:1
    DM equals remote_jid, i.e. the CONTACT's own jid, not the owner's. That
    silently clobbered the real owner identity (set once at login) with
    whichever stranger most recently texted the owner, on every single
    inbound message. Fixed here: only a genuine owner signal overwrites the
    persisted identity; everything else preserves it unchanged.
    """
    resolved_current = str(current_value or "").strip()
    if resolved_current:
        return resolved_current
    resolved_preserved = str(preserved_value or "").strip()
    return resolved_preserved or None


def _dm_policy_sender_id(message: Dict[str, Any], *, remote_jid: str) -> str:
    """The identity dmPolicy authorizes against: the specific participant
    who sent the message (sender_jid — meaningful inside a WhatsApp group,
    where it differs from the group's own remote_jid) if present, else the
    chat/contact id (remote_jid — the normal 1:1 DM case, where sender_jid
    already equals remote_jid anyway)."""
    return str(message.get("sender_jid") or "").strip() or str(remote_jid or "").strip()


def _is_owner_message(
    *,
    channel_key: str,
    message: Dict[str, Any],
    sender_id: str,
    existing_state: Optional[Dict[str, Any]],
) -> bool:
    """True when this inbound message is from the OWNER's own identity:
    WhatsApp/Telegram/Signal self-chat (message.is_self_chat — computed
    gateway-side from the live connection's own account id, see
    empyralis-gateway/src/channels/whatsapp/message-mapper.ts for WhatsApp
    and empyralis-gateway/src/bridges/signal-cli-bridge.ts's
    mapSignalCliReceiveNotification for Signal's "Note to Self" equivalent),
    or a sender that matches this channel's previously-established linked
    owner identity (also correctly identifies the owner posting inside a
    WhatsApp group they're a member of, since a person's JID is the same in
    every context).

    Reuses triage_service.resolve_sender_identity for the actual identity
    resolution rather than re-implementing it — see that function's
    docstring. channel_bindings is built fresh here from the (clobber-fixed)
    linked identity rather than routing through triage's audience-registry
    concept, which models a different question (customer/audience senders
    on an audience-facing channel, not "is this sender the channel's own
    owner").
    """
    if bool(message.get("is_self_chat")):
        return True
    linked = _channel_owner_linked_id(channel_key=channel_key, state=existing_state)
    if not linked:
        return False
    from server_modules.triage_service import resolve_sender_identity

    # Canonicalize BOTH sides through the same channel-prefix rule before
    # the one comparison, rather than adding a second, looser comparison
    # beside it — resolve_sender_identity is an exact string equality, and
    # two answers to "is this the owner" is precisely the shape CLAUDE.md
    # records as having produced a silent owner downgrade once already.
    identity = resolve_sender_identity(
        sender_id=_channel_prefixed_identity_tail(channel_key=channel_key, value=sender_id),
        channel_origin=channel_key,
        channel_bindings=[
            {
                "channel_type": channel_key,
                "linked_user_id": _channel_prefixed_identity_tail(
                    channel_key=channel_key, value=linked
                ),
            }
        ],
    )
    return identity == "owner"


def _generate_pairing_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _pairing_challenge_text(*, label: str) -> str:
    return (
        f"This {label} number is a private assistant line. It only replies to its "
        "owner and to contacts the owner has approved. The owner has been notified "
        "that you messaged, and can approve you to enable replies."
    )


async def _enforce_dm_policy(
    *,
    registration: Dict[str, Any],
    channel_key: str,
    agent_id: str,
    message: Dict[str, Any],
    remote_jid: str,
    existing_state: Optional[Dict[str, Any]],
    label: str,
) -> Dict[str, Any]:
    """THE gate. Must run before any reply — including a control-command
    reply — is generated for an inbound personal-channel message. Returns::

        {"allowed": bool, "mode": str, "sender_id": str, "is_owner": bool,
         "system_reply": Optional[str], "config_changed": bool}

    `system_reply`, when present, is a FIXED string (never LLM-generated)
    the caller should dispatch directly and then stop — a pairing challenge
    must never reach the model.
    """
    sender_id = _dm_policy_sender_id(message, remote_jid=remote_jid)
    is_owner = _is_owner_message(
        channel_key=channel_key, message=message, sender_id=sender_id, existing_state=existing_state,
    )
    if is_owner:
        return {
            "allowed": True, "mode": "owner", "sender_id": sender_id, "is_owner": True,
            "system_reply": None, "config_changed": False,
        }

    # ── Group traffic on the OpenClaw lane belongs to the GROUP gate. ─────
    #
    # This gate is Gate 1 (who may DM this agent). CLAUDE.md's own
    # description of the OpenClaw transport says group messages "land on
    # Gates 2/3 (allowlist + require_mention) instead of Gate 1" — and
    # _enforce_group_policy has already run and allowed this message by the
    # time we get here, keyed on the GROUP's own chat id, which
    # _group_policy_group_id's docstring explains is the only identity that
    # answers "is this group allowed" ("a group's membership list is
    # irrelevant").
    #
    # Applying a DM SENDER allowlist to a group message is a category error
    # with a concrete cost: `sender_id` in a group is the individual
    # participant, so an owner who deliberately allowed a group would ALSO
    # have to enumerate every one of its members before the agent answered
    # anybody in it. It never showed because the first-party default is
    # `open`, which admitted everything; the moment this lane got the
    # allowlist default its own transport requires, an allowed group went
    # silent.
    #
    # SCOPED TO THIS LANE, deliberately. The same skip on the first-party
    # channels would LOOSEN an owner who has explicitly chosen owner_only or
    # allowlist on WhatsApp/Telegram today — their group traffic is
    # currently blocked here, and quietly un-blocking it is not a side
    # effect this change is entitled to have. Whether Gate 1 should stop
    # deciding group traffic everywhere is a real question and a separate
    # one; nothing here answers it.
    if _is_openclaw_transported_channel(channel_key) and bool(message.get("is_group")):
        return {
            "allowed": True, "mode": "group_gate", "sender_id": sender_id, "is_owner": False,
            "system_reply": None, "config_changed": False,
        }

    tenant_id = str(registration.get("tenant_id") or "default").strip() or "default"
    workspace_id = str(registration.get("workspace_id") or "default").strip() or "default"
    config = await _load_agent_dm_policy_config(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key,
    )
    mode = config["mode"]

    if mode == DM_POLICY_OPEN:
        return {
            "allowed": True, "mode": mode, "sender_id": sender_id, "is_owner": False,
            "system_reply": None, "config_changed": False,
        }

    if sender_id and sender_id in config["allowlist"]:
        return {
            "allowed": True, "mode": mode, "sender_id": sender_id, "is_owner": False,
            "system_reply": None, "config_changed": False,
        }

    if mode == DM_POLICY_PAIRING and sender_id:
        pending = dict(config["pending_pairing"])
        if sender_id in pending:
            # Already challenged once — stay silent on repeats (mirrors
            # OpenClaw's issuePairingChallenge: no reply when !created).
            return {
                "allowed": False, "mode": mode, "sender_id": sender_id, "is_owner": False,
                "system_reply": None, "config_changed": False,
            }
        pending[sender_id] = {
            "code": _generate_pairing_code(),
            "sender_name": str(message.get("push_name") or "").strip() or None,
            "requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        config["pending_pairing"] = pending
        persisted = await _persist_agent_dm_policy_config(
            tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key, config=config,
        )
        # Only send the challenge once we know it's actually recorded — an
        # unrecorded challenge would re-trigger (and re-message the
        # stranger) on every retry instead of going silent like a recorded
        # one does.
        reply = _pairing_challenge_text(label=label) if persisted else None
        return {
            "allowed": False, "mode": mode, "sender_id": sender_id, "is_owner": False,
            "system_reply": reply, "config_changed": persisted,
        }

    # owner_only (default) and allowlist-with-no-match both land here:
    # blocked, silent — no system reply, matching "record but do not reply."
    return {
        "allowed": False, "mode": mode, "sender_id": sender_id, "is_owner": False,
        "system_reply": None, "config_changed": False,
    }


async def _handle_dm_policy_blocked(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    inbound: Dict[str, Any],
    channel_key: str,
    provider: str,
    agent_id: str,
    external_message_id: str,
    remote_jid: str,
    duplicate: bool,
    decision: Dict[str, Any],
    trace_id: str = "",
) -> Dict[str, Any]:
    """Record the drop (and, for `pairing` mode's first contact, dispatch
    the one-time system challenge) — but never hand the message to the
    model, and never run the normal automatic-reply path."""
    no_reply_idempotency_key = f"{channel_key}:dmpolicy:{decision.get('mode')}:{external_message_id}"
    refreshed_inbound = personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        agent_id=agent_id,
        external_message_id=external_message_id,
        reply_idempotency_key=no_reply_idempotency_key,
    )
    system_reply = str(decision.get("system_reply") or "").strip()
    status = "pairing_challenge" if system_reply else "blocked"
    sender_id = str(decision.get("sender_id") or "")
    _emit_automatic_reply_audit(
        action=f"personal_channel.{channel_key.split('_', 1)[0]}.dm_policy",
        status=status,
        registration=registration,
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        detail=f"Inbound personal-channel message dropped by dmPolicy (mode={decision.get('mode')}).",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "dm_policy_mode": decision.get("mode"),
            "sender_id_hash": hashlib.sha256(sender_id.encode("utf-8")).hexdigest()[:16] if sender_id else None,
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.dm_policy.{status}:{gateway_id}:{channel_key}:{external_message_id}",
    )
    outbound: Optional[Dict[str, Any]] = None
    if system_reply:
        pairing_idempotency_key = f"{channel_key}:dmpolicy_pairing:{sender_id}"
        outbound, outbound_created = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            agent_id=agent_id,
            idempotency_key=pairing_idempotency_key,
            remote_jid=remote_jid,
            text=system_reply,
            reply_to_external_message_id=external_message_id,
            metadata={"reply_source": "dm_policy_pairing_challenge"},
        )
        if outbound_created and str(outbound.get("status") or "").strip() != "delivered":
            try:
                _enforce_personal_channel_dispatch_decision(
                    gateway_id=str(gateway_id or "").strip(),
                    registration=registration,
                    capability_id=f"{channel_key}.send",
                    request_id=pairing_idempotency_key,
                )
                dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
                    gateway_id=str(gateway_id or "").strip(),
                    channel_key=channel_key,
                    provider=provider,
                    remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
                    text=system_reply,
                    idempotency_key=pairing_idempotency_key,
                    reply_to_external_message_id=None,
                )
                delivered = personal_channels_repository.mark_outbound_delivered(
                    gateway_id=str(gateway_id or "").strip(),
                    channel_key=channel_key,
                    agent_id=agent_id,
                    idempotency_key=pairing_idempotency_key,
                    external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
                    metadata={"dispatch_result": dispatch_result},
                )
                outbound = delivered or outbound
            except Exception:
                _logger.warning(
                    "dm_policy: failed to dispatch pairing challenge channel=%s gateway=%s",
                    channel_key, gateway_id, exc_info=True,
                )
    return {
        "duplicate": duplicate,
        "inbound": refreshed_inbound or inbound,
        "outbound": outbound,
        "blocked": True,
        "policy": {"gate": "dm_policy", **decision},
    }


# ── groupPolicy: which groups the agent is even active in, + mention-gating ──
#
# TWO ORTHOGONAL AXES, both owner-configurable, neither ever an AI runtime
# decision:
#
#   1. group_policy (this section's storage, mirroring dm_policy's exact
#      install_metadata.<key>[channel_key] shape — see
#      _load_agent_dm_policy_config/_persist_agent_dm_policy_config above,
#      whose pattern this copies): open | allowlist | disabled. Is this
#      SPECIFIC group (keyed on the group's own chat id, not a sender id)
#      allowed to have the agent active at all. Parallels OpenClaw's
#      groupPolicy (dist/runtime-group-policy-BEjP88cf.js — see
#      docs/OpenClaw.md's GROUP/MENTION GATING section).
#   2. requireMention (also stored here, per-group-policy-config) — whether
#      an unaddressed message in an allowed group should be skipped.
#      Delegated to mention_gating_service.resolve_inbound_mention_decision,
#      the ONE shared resolver mirroring OpenClaw's
#      resolveInboundMentionDecision formula.
#
# DEFAULTS — read this before ever touching either constant below:
#
# Two standing rulings governed the MENTION-GATING axis specifically, and
# were in tension on their face:
#   Ruling A (2026-07-16): "Groups = see-and-decide, NOT mention-gated. The
#   agent should SEE every group message and decide to reply or stay silent
#   by its own judgment ... Do NOT build rigid gates."
#   Ruling B (2026-07-23): no filter may flag-and-withhold a message from
#   the reasoning model on CONTENT grounds; every message reaches the model
#   unconditionally (see sage_turn_adapter.py's removed Phase-P gate).
#
# The ACTUAL code shipped between those two dates (0fe9ada19 2026-07-18,
# c8b8fbed0 2026-07-19) added a hard, non-configurable gate to Telegram and
# the local-bridge channels (mirroring WhatsApp's pre-existing one): an
# unaddressed group message is silently dropped BEFORE ever reaching the
# model (see test_personal_channel_group_gate.py's pre-existing
# test_unaddressed_group_message_is_ignored_and_never_dispatched, which
# encodes exactly that as "today's" behavior). That gate is real, was
# deliberately built to fix a live "family group" spam incident, and is
# what a naive "preserve exact current behavior" reading of this section
# would keep as the default.
#
# The founder's resolution (2026-07-23) restored Ruling A as the default:
# Ruling A is explicitly "still in force" and the 2026-07-18/19 gate was a
# rigid gate of exactly the kind Ruling A says not to build. requireMention
# defaulted OFF — the agent sees every group message and can choose
# [SILENT] via its own judgment, given the group context this handler
# threads through regardless (is_group/chat_label — same as any other
# turn). test_personal_channel_group_gate.py's unaddressed-message tests
# were updated (not merely "no longer applicable") to reflect it, and tests
# proved requireMention=True (an explicit, owner-configured lever — never
# an AI runtime decision) reproduces the pre-2026-07-23 hard-gate behavior
# byte-for-byte, so nothing relying on the old strict gate lost the ability
# to have it.
#
# SUPERSEDED 2026-08-07 for the two DEFAULT_* constants below (see
# CHANNEL-GATEWAY-PLAN.md §4/§5 for the full incident writeup) — Ruling A's
# philosophy is NOT overturned by this, and neither is Ruling B: the agent
# still gets full group context on every turn it IS given, and this gate
# still keys ONLY on platform-computed addressing facts (was this message
# an @mention or a reply-to-agent; which chat is this) — NEVER on message
# content. What changed is that "unconfigured" stopped being a safe state
# to leave an owner in by construction. The founder's own personal
# Telegram account was added to a large public group; requireMention
# defaulted False AND group_policy's write path
# (_persist_agent_group_policy_config, below) had ZERO callers anywhere in
# the codebase — Gate 2 was unconditionally "open" for every agent,
# permanently, because nothing could ever change it. The agent replied to
# every message from every stranger in that group until the account got
# banned. An owner who never opened Settings (because there was nothing
# there to open) got "reply to literally everyone, everywhere" as their
# unconfigured starting point — that is the specific thing this change
# fixes, not Ruling A's see-and-decide philosophy itself. An owner who
# wants the original behavior back still can: set group_policy to "open"
# and require_mention to False explicitly, now that
# update_agent_group_policy_config (below) — wired to a real PATCH route,
# see routes_personal_channels.py — gives them a real lever to do it,
# which is the thing that didn't exist before this build.
#
# DEFAULT_GROUP_POLICY_MODE / DEFAULT_REQUIRE_MENTION below are the
# configured-but-unset default for a REAL, resolved agent identity. As of
# this build (see _resolve_local_bridge_agent_id) that now includes
# Signal/iMessage/WeChat-personal (LOCAL_BRIDGE_PERSONAL_CHANNELS) alongside
# Telegram Personal / WhatsApp Personal — all five channels resolve through
# _resolve_agent_id_for_inbound and land here once resolved, so a
# newly-claimed local-bridge binding gets the exact same safe
# allowlist/require-mention starting point WhatsApp/Telegram already do,
# configurable the same way (update_agent_group_policy_config,
# GROUP_POLICY_CHANNEL_KEYS already included the local-bridge keys before
# this build — only the identity resolution to ever reach that write path
# was missing).
#
# They do NOT govern the separate unresolved-identity fallback
# (_unresolved_identity_group_policy_config, below) — split out for the
# exact reason DEFAULT_DM_POLICY_MODE's own comment documents already
# having gone wrong once for dm_policy (ee3fca4f7c): a future change to the
# resolved-agent default must never silently reach into the
# unresolved-identity fallback too. That fallback is no longer the
# PERMANENT state for local-bridge channels (identity resolves for them
# now, in the common case), but it remains reachable — an ambiguous or
# not-yet-claimed preferred_gateway_id (see _resolve_local_bridge_agent_id),
# or any lookup failure — and per CHANNEL-GATEWAY-PLAN.md §"the last
# unscoped channels", any path that can still produce a genuinely unknown
# identity must fail CLOSED, not open. See
# _unresolved_identity_group_policy_config's own docstring for the fix
# (2026-08-07: flipped from GROUP_POLICY_OPEN to GROUP_POLICY_DISABLED).
GROUP_POLICY_OPEN = "open"
GROUP_POLICY_ALLOWLIST = "allowlist"
GROUP_POLICY_DISABLED = "disabled"
GROUP_POLICY_MODES = {GROUP_POLICY_OPEN, GROUP_POLICY_ALLOWLIST, GROUP_POLICY_DISABLED}
# Resolved-but-unconfigured default — SAFE, per CHANNEL-GATEWAY-PLAN.md §5
# step 2 (2026-08-07): matches OpenClaw's consistent default across every
# channel, not Telegram's legacy-open behavior. A brand-new agent binding,
# or an existing one whose owner has never touched group_policy, now
# starts allowlist/require-mention instead of open/no-mention-check. See
# scripts/backfill_group_policy_safe_defaults.py for the (not-yet-run,
# founder-approval-required) migration that pins EXISTING bindings to this
# same state explicitly rather than leaving them to it only implicitly.
DEFAULT_GROUP_POLICY_MODE = GROUP_POLICY_ALLOWLIST
DEFAULT_REQUIRE_MENTION = True

# Reason codes _enforce_group_policy returns when blocking — matches the
# pre-existing "group_no_mention" literal's style (a short, stable,
# machine-readable token every {"ignored": True, "reason": ...} caller
# already surfaces/traces, never a silent drop).
GROUP_GATE_REASON_NO_MENTION = "group_no_mention"
GROUP_GATE_REASON_POLICY_DENIED = "group_policy_denied"


def _normalize_group_policy_config(raw: Any) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in GROUP_POLICY_MODES:
        mode = DEFAULT_GROUP_POLICY_MODE
    allowlist = sorted({str(x).strip() for x in (data.get("allowlist") or []) if str(x or "").strip()})
    require_mention = _boolish(data.get("require_mention"), default=DEFAULT_REQUIRE_MENTION)
    return {"mode": mode, "allowlist": allowlist, "require_mention": require_mention}


def _unresolved_identity_group_policy_config(channel_key: str = "") -> Dict[str, Any]:
    """Fallback for _load_agent_group_policy_config when NO real per-agent
    install could even be identified (agent_id="" /
    personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID).

    CHANNEL-AWARE as of this build — two genuinely different situations
    both land on agent_id="", and conflating them would either reopen the
    local-bridge incident or silently regress a real, separately-decided
    WhatsApp/Telegram path:

    - LOCAL_BRIDGE_PERSONAL_CHANNELS (Signal/iMessage/WeChat-personal): until
      this build, agent_id="" was the PERMANENT case for these three —
      _resolve_agent_id_for_inbound had no lookup branch for them at all. It
      no longer is: _resolve_local_bridge_agent_id now resolves a real
      agent_id for them, either from an explicit owner action (iMessage's
      recheck/install routes) or a reverse preferred_gateway_id lookup. This
      fallback is still reachable whenever that resolution genuinely can't
      land (an ambiguous or not-yet-claimed preferred_gateway_id, or an
      install-lookup failure — see _resolve_local_bridge_agent_id's own
      docstring), and for THESE channels it now fails CLOSED
      (GROUP_POLICY_DISABLED/require_mention=True): this was literally the
      incident (CHANNEL-GATEWAY-PLAN.md's "the last unscoped channels") — an
      unresolved identity defaulting open, permanently and unconfigurably,
      let an agent reply unprompted in a large public group until the
      account got banned. A genuinely unknown local-bridge identity has no
      "the owner already configured this specific agent" story to lean on,
      so it must fail closed rather than open, matching dm_policy's own
      identity-less fallback (_unresolved_identity_dm_policy_config, already
      owner_only). Unlike the pre-this-build state, this is no longer a dead
      end: it means the gateway isn't (yet, or unambiguously) claimed by any
      agent's preferred_gateway_id — a real, fixable configuration gap.

    - Every other caller (WhatsApp/Telegram Personal's own agent_id=""
      case): a SEPARATE, pre-existing, deliberate product state — "Sage's
      own legacy Connect tab" binds a channel workspace-wide rather than to
      one specific agent (PersonalChannelConnectPanel.tsx's
      agentGatewayId===undefined path), which is not the incident this
      build fixes and is out of this build's scope to change. Stays
      GROUP_POLICY_OPEN/require_mention=False — today's actual, unchanged,
      separately-decided behavior for that path."""
    if str(channel_key or "").strip() in LOCAL_BRIDGE_PERSONAL_CHANNELS:
        return {"mode": GROUP_POLICY_DISABLED, "allowlist": [], "require_mention": True}
    return {"mode": GROUP_POLICY_OPEN, "allowlist": [], "require_mention": False}


async def _load_agent_group_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
) -> Dict[str, Any]:
    """Read-only — mirrors _load_agent_dm_policy_config's shape, persisted
    at install_metadata.group_policy[channel_key].

    An unresolved agent identity (see _unresolved_identity_group_policy_config
    above) returns that dedicated, channel-aware fallback, independent of
    DEFAULT_GROUP_POLICY_MODE. A RESOLVED agent_id whose install lookup
    fails, or who simply has no group_policy entry yet, gets
    DEFAULT_GROUP_POLICY_MODE/DEFAULT_REQUIRE_MENTION (via
    _normalize_group_policy_config(None) below) — the safe, owner-
    changeable default, now allowlist/require-mention as of 2026-08-07.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return _unresolved_identity_group_policy_config(channel_key)
    try:
        from server_modules import agent_registry_repository as _repo

        install = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id,
            tenant_id=str(tenant_id or "default").strip() or "default",
            workspace_id=str(workspace_id or "default").strip() or "default",
        )
    except Exception:
        _logger.warning("group_policy: install lookup failed for agent_id=%s — defaulting to DEFAULT_GROUP_POLICY_MODE", normalized_agent_id, exc_info=True)
        return _normalize_group_policy_config(None)
    if not isinstance(install, dict):
        return _normalize_group_policy_config(None)
    meta = dict(install.get("install_metadata") or install.get("metadata") or {})
    all_policies = meta.get("group_policy") if isinstance(meta.get("group_policy"), dict) else {}
    return _normalize_group_policy_config(all_policies.get(channel_key))


async def _persist_agent_group_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    config: Dict[str, Any],
) -> bool:
    """Write path. Had zero callers anywhere in the codebase until
    2026-08-07 (see CHANNEL-GATEWAY-PLAN.md §4) — wired to a real PATCH
    route via update_agent_group_policy_config below, the validating
    public wrapper routes_personal_channels.py calls; do not call this
    private function directly from a route, call the wrapper instead so a
    typo'd mode gets a real 400 rather than being silently coerced to the
    default by _normalize_group_policy_config. Mirrors
    _persist_agent_dm_policy_config's read-modify-write-the-whole-dict
    shape exactly (update_workspace_agent_install merges `metadata`
    shallowly at the TOP level only, so a naive per-channel write would
    clobber sibling channels' group_policy). Best-effort: never raises."""
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return False
    resolved_tenant_id = str(tenant_id or "default").strip() or "default"
    resolved_workspace_id = str(workspace_id or "default").strip() or "default"
    try:
        from server_modules import agent_registry_repository as _repo

        install = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id, tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id,
        )
        if not isinstance(install, dict):
            return False
        meta = dict(install.get("install_metadata") or install.get("metadata") or {})
        all_policies = dict(meta.get("group_policy")) if isinstance(meta.get("group_policy"), dict) else {}
        all_policies[channel_key] = _normalize_group_policy_config(config)
        updated = await _repo.update_workspace_agent_install(
            normalized_agent_id,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            metadata={"group_policy": all_policies},
        )
        return updated is not None
    except Exception:
        _logger.warning("group_policy: persist failed for agent_id=%s channel=%s", normalized_agent_id, channel_key, exc_info=True)
        return False


# Channel keys group_policy can ever apply to — WhatsApp/Telegram Personal
# plus the local-bridge set (Signal/iMessage/WeChat-personal). The WRITE
# path (this route/function) always worked for all five: it takes agent_id
# as an explicit, caller-supplied argument (the owner naming which agent's
# policy they're editing, from routes_personal_channels.py's own query
# param), never _resolve_agent_id_for_inbound. What was genuinely broken
# until this build was the READ side at INBOUND time: local-bridge messages
# always resolved to LEGACY_UNSCOPED_AGENT_ID, so whatever an owner
# configured here was persisted correctly but never actually consulted for
# a real incoming message (see _resolve_local_bridge_agent_id, which closes
# that gap).
#
# WHATSAPP_PERSONAL_CHANNEL_KEY DROPPED 2026-08-15 — see DM_POLICY_CHANNEL_KEYS
# above for the full reason. Same defect, mirrored: the owner could write a
# group policy for a channel that no longer has a handler, a catalog entry or
# a lane spec, and the write persisted. TELEGRAM_PERSONAL_CHANNEL_KEY joined
# it 2026-08-15 when the cloud-session lane — the last thing running
# _enforce_group_policy with that key — was deleted.
GROUP_POLICY_CHANNEL_KEYS = frozenset(LOCAL_BRIDGE_PERSONAL_CHANNELS.keys())


async def update_agent_group_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    mode: str,
    allowlist: Optional[List[str]] = None,
    require_mention: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Owner-facing write PRIMITIVE for the group_policy gate (Gate 2's
    allowlist axis + Gate 3's require_mention lever) — the validating,
    route-callable counterpart to _persist_agent_group_policy_config, which
    had zero callers anywhere before this build (see
    CHANNEL-GATEWAY-PLAN.md §4). routes_personal_channels.py's PATCH
    .../group-policy route is the only intended caller; call this instead
    of _persist_agent_group_policy_config directly so a bad `mode` gets a
    real error instead of being silently coerced to the default by
    _normalize_group_policy_config (correct behavior for a READ path facing
    possibly-stale stored data, wrong for a WRITE path facing a live
    caller).

    Raises ValueError for a missing/unresolvable agent_id, an unknown
    channel_key, or a mode outside GROUP_POLICY_MODES — the caller (the
    route) maps that to 400. Returns None if the agent install itself
    could not be found/updated (e.g. agent_id doesn't exist in this
    workspace) — the caller maps that to 404. This distinction mirrors
    every other config primitive in this module (see
    configure_whatsapp_personal_gateway's ValueError contract).

    require_mention=None means "use this build's safe default"
    (DEFAULT_REQUIRE_MENTION, currently True) rather than silently turning
    mention-gating off — an owner explicitly loosening it must pass
    require_mention=False themselves.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        raise ValueError(
            "agent_id is required to configure a per-agent group policy — pass the specific "
            "agent install this policy applies to (group_policy is stored per agent+channel, "
            "not per gateway)."
        )
    normalized_channel_key = str(channel_key or "").strip().lower()
    if normalized_channel_key not in GROUP_POLICY_CHANNEL_KEYS:
        raise ValueError(f"channel_key must be one of {sorted(GROUP_POLICY_CHANNEL_KEYS)}.")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in GROUP_POLICY_MODES:
        raise ValueError(f"mode must be one of {sorted(GROUP_POLICY_MODES)}.")
    normalized_allowlist = sorted({str(x).strip() for x in (allowlist or []) if str(x or "").strip()})
    normalized_require_mention = DEFAULT_REQUIRE_MENTION if require_mention is None else bool(require_mention)
    config = {
        "mode": normalized_mode,
        "allowlist": normalized_allowlist,
        "require_mention": normalized_require_mention,
    }
    persisted = await _persist_agent_group_policy_config(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
        config=config,
    )
    if not persisted:
        return None
    return await _load_agent_group_policy_config(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
    )


def _group_policy_group_id(message: Dict[str, Any], *, remote_jid: str) -> str:
    """The identity group_policy's open|allowlist|disabled axis authorizes
    against: the group's own chat id (remote_jid — unlike dmPolicy's
    _dm_policy_sender_id, this is NEVER sender_jid; a group's membership
    list is irrelevant to "is this group itself allowed", only the group's
    own identity is)."""
    return str(remote_jid or "").strip()


async def _enforce_group_policy(
    *,
    registration: Dict[str, Any],
    channel_key: str,
    agent_id: str,
    message: Dict[str, Any],
    remote_jid: str,
) -> Dict[str, Any]:
    """THE group gate — replaces the inline `if is_group: if not
    is_mentioned and not is_reply_to_sage: skip` duplicated across the
    WhatsApp/Telegram/local-bridge/cloud handlers below. Combines the two
    axes documented above this function's constants.

    HARD CONSTRAINT: never reads message content/topic — only
    message["is_group"]/["is_mentioned"]/["is_reply_to_sage"] (platform-
    computed addressing facts a channel plugin derives from mention
    entities/reply-linkage, never from what the message says) and the
    group's own chat id.

    Self-chat bypass: self-chat (message["is_self_chat"]) is, by
    construction, never a group message on any channel (Saved
    Messages/Note-to-Self is always a private 1:1 with the owner) — the
    is_group short-circuit below already means self-chat is never gated.
    This explicit check is defense-in-depth so that remains true even if a
    future bridge ever mis-set both flags at once, matching the "owner's
    self-chat is NEVER gated by either policy" hard constraint literally
    rather than as an implied consequence.

    Returns {"allowed": bool, "reason": Optional[str], "mode": Optional[str],
    "require_mention": Optional[bool], "group_id": Optional[str],
    "was_addressed": Optional[bool]}. `reason` is GROUP_GATE_REASON_NO_MENTION
    or GROUP_GATE_REASON_POLICY_DENIED when blocked.

    was_addressed: the HONEST, real "was this message actually addressed"
    fact (explicit mention or reply-to-agent) — None for a DM/self-chat
    (not applicable; addressed is meaningless outside a group), otherwise
    the resolver's effective_was_mentioned, computed independently of
    require_mention. THIS MUST BE THREADED THROUGH to whatever builds the
    reply and feeds personal_channel_sage_bridge_service's InboundEnvelope
    (see _build_personal_channel_envelope's `addressed` field) — with
    requireMention defaulting OFF, allowed=True no longer implies "this was
    addressed" the way it used to when this gate hard-blocked every
    unaddressed message; callers that skip threading this through would
    silently tell the model "you were addressed directly" for a message
    that was not, breaking Ruling A's own "informed judgment" mechanism.
    """
    if bool(message.get("is_self_chat")) or not bool(message.get("is_group")):
        return {
            "allowed": True, "reason": None, "mode": None, "require_mention": None,
            "group_id": None, "was_addressed": None,
        }

    group_id = _group_policy_group_id(message, remote_jid=remote_jid)
    tenant_id = str(registration.get("tenant_id") or "default").strip() or "default"
    workspace_id = str(registration.get("workspace_id") or "default").strip() or "default"
    config = await _load_agent_group_policy_config(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key,
    )
    mode = config["mode"]
    require_mention = bool(config["require_mention"])

    # Computed regardless of require_mention (the formula doesn't depend on
    # it) so was_addressed is always the honest fact, whether or not this
    # policy would actually enforce it.
    mention_decision = mention_gating_service.resolve_inbound_mention_decision(
        facts=mention_gating_service.mention_facts_from_message(message),
        policy={"is_group": True, "require_mention": require_mention},
    )
    was_addressed = bool(mention_decision["effective_was_mentioned"])

    if mode == GROUP_POLICY_DISABLED:
        return {
            "allowed": False, "reason": GROUP_GATE_REASON_POLICY_DENIED, "mode": mode,
            "require_mention": require_mention, "group_id": group_id, "was_addressed": was_addressed,
        }
    if mode == GROUP_POLICY_ALLOWLIST and (not group_id or group_id not in config["allowlist"]):
        return {
            "allowed": False, "reason": GROUP_GATE_REASON_POLICY_DENIED, "mode": mode,
            "require_mention": require_mention, "group_id": group_id, "was_addressed": was_addressed,
        }
    if mention_decision["should_skip"]:
        return {
            "allowed": False, "reason": GROUP_GATE_REASON_NO_MENTION, "mode": mode,
            "require_mention": require_mention, "group_id": group_id, "was_addressed": was_addressed,
        }
    return {
        "allowed": True, "reason": None, "mode": mode,
        "require_mention": require_mention, "group_id": group_id, "was_addressed": was_addressed,
    }


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_record_list(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _manifest_channel_key(item: Dict[str, Any]) -> str:
    return str(item.get("channel_key") or item.get("channelKey") or "").strip()


def _records_by_channel(items: Any) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for item in _as_record_list(items):
        channel_key = _manifest_channel_key(item)
        if channel_key:
            records[channel_key] = item
    return records


def _boolish(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"true", "1", "yes", "y", "on"}:
        return True
    if token in {"false", "0", "no", "n", "off"}:
        return False
    return default


# Holds strong references to the fire-and-forget asyncio.Task objects
# _ensure_agent_channel_binding_enabled schedules below -- asyncio only
# weakly tracks a bare create_task() via the event loop, so without this
# set the task can be garbage-collected mid-run with no warning (the same
# footgun documented for the compaction background job,
# docs/PLATFORM-MAP.md Part 31.1). Entries remove themselves via
# add_done_callback the moment each task finishes, so this never grows
# unbounded. See docs/design/audit-silent-failures.md C2.
_CHANNEL_BINDING_ENABLE_TASKS: set[asyncio.Task] = set()


def _track_channel_binding_enable_task(task: asyncio.Task) -> None:
    _CHANNEL_BINDING_ENABLE_TASKS.add(task)

    def _on_done(finished: asyncio.Task) -> None:
        _CHANNEL_BINDING_ENABLE_TASKS.discard(finished)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            # Defense in depth: _enable()'s own try/except below already
            # logs+audits every failure it can see. This callback only
            # fires if something escapes that (e.g. a bug in the handler
            # itself, or a CancelledError-adjacent edge case) -- so a
            # background-task crash still can never vanish with zero trace.
            from server_modules import durability_signal

            durability_signal.capture_durability_failure(
                "channel binding enable task (uncaught)",
                exc=exc,
                event_class="channel_binding_enable_task_crashed",
            )

    task.add_done_callback(_on_done)


def _ensure_agent_channel_binding_enabled(
    *,
    agent_id: str,
    channel_key: str,
    registration: Dict[str, Any],
) -> None:
    """The Channels-tab pill's own gate (connection_catalog_service.
    agent_status_items) requires BOTH the underlying session state AND an
    ENABLED agent_channel_bindings row before it will ever show "connected"
    for a specific agent — that binding is how the UI distinguishes "this
    agent's channel" from "some other agent's, on the same catalog item."
    Other channel types (OAuth-based connectors) create that binding as
    part of their own connect flow; the full-account personal-channel wizard
    never did. Fire-and-forget from here, the moment a real (non-legacy)
    agent's session reaches "connected" — pairing an account for an agent
    should be sufficient to enable it for that agent, no separate manual
    toggle.

    Best-effort: a failure here must never break the state sync that's
    actually reporting the real session status -- but "best-effort" no
    longer means silent. `agent_bindings_repository.py` documents a live
    DB-level unique constraint (uq_agent_channel_bindings_inbound_owner_v2)
    that can genuinely fire here (re-pairing the same WhatsApp/Telegram
    account to a different agent is the ordinary way to trigger it); until
    this fix, that exception vanished into a bare `except Exception: pass`
    with zero logging, permanently stranding the Channels-tab pill in a
    not-connected state with no diagnostics and no manual recovery path
    (docs/design/audit-silent-failures.md C2). Every failure here is now
    logged with full context (exc_info=True) and raised as a durability
    signal (ERROR log + Sentry + an operator-visible activity-ledger
    dead-letter event via durability_signal.capture_durability_failure), and
    the scheduling task is tracked so it can't be GC'd mid-flight either.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — nothing to schedule onto (shouldn't happen; all callers are async)

    tenant_id = str(registration.get("tenant_id") or "").strip() or "default"
    workspace_id = str(registration.get("workspace_id") or "").strip() or "default"

    async def _enable() -> None:
        try:
            from server_modules import agent_bindings_repository as bindings
            await bindings.upsert_channel_binding(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                agent_install_id=normalized_agent_id,
                channel_key=channel_key,
                enabled=True,
            )
        except Exception as exc:
            from server_modules import durability_signal

            _logger.exception(
                "_ensure_agent_channel_binding_enabled: upsert_channel_binding failed "
                "agent_id=%s channel_key=%s tenant_id=%s workspace_id=%s -- the "
                "Channels-tab pill for this agent will misreport as not-connected "
                "until this binding is enabled.",
                normalized_agent_id, channel_key, tenant_id, workspace_id,
            )
            durability_signal.capture_durability_failure(
                f"channel binding enable for agent={normalized_agent_id} channel={channel_key}",
                exc=exc,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                channel=channel_key,
                event_class="channel_binding_enable_failed",
                action="upsert_channel_binding",
                summary=(
                    f"Session for agent {normalized_agent_id} on channel {channel_key} "
                    "connected, but enabling its agent_channel_bindings row failed "
                    f"({exc.__class__.__name__}) -- the Channels-tab pill will show "
                    "not-connected until this is manually retried."
                ),
            )

    task = loop.create_task(_enable())
    _track_channel_binding_enable_task(task)


def _claim_agent_channel_state(
    *,
    gateway_id: str,
    channel_key: str,
    agent_id: str,
    registration: Dict[str, Any],
) -> None:
    """Called at the START of configure_*_personal_gateway, before the
    Gateway is ever dispatched to. Seeds a row under the REAL agent_id if
    one doesn't already exist for this (gateway_id, channel_key, agent_id) —
    a no-op if this agent already has a row (don't clobber a connected
    session's status back to "connecting" on a retry/refresh call). This is
    what makes _resolve_agent_id_for_inbound correct even for a mid-pairing
    status (code_required, qr_required, ...): by the time ANY response
    comes back from the Gateway, the row already exists under the right
    agent_id, not the legacy/unscoped sentinel."""
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        return  # nothing to claim — caller is using the pre-existing unscoped path
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        existing = personal_channels_repository.get_whatsapp_state(
            str(gateway_id or "").strip(), channel_key=channel_key, agent_id=normalized_agent_id,
        )
        if existing is not None:
            return
        personal_channels_repository.upsert_whatsapp_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=channel_key,
            agent_id=normalized_agent_id,
            provider=WHATSAPP_PERSONAL_PROVIDER,
            status="connecting",
        )
    elif channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        existing = personal_channels_repository.get_telegram_state(
            str(gateway_id or "").strip(), channel_key=channel_key, agent_id=normalized_agent_id,
        )
        if existing is not None:
            return
        personal_channels_repository.upsert_telegram_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=channel_key,
            agent_id=normalized_agent_id,
            provider=TELEGRAM_PERSONAL_PROVIDER,
            status="connecting",
        )
    elif channel_key in LOCAL_BRIDGE_PERSONAL_CHANNELS:
        # Local-bridge twin — used by an explicit owner action that already
        # names a real agent_id (today: recheck_imessage_personal_gateway /
        # install_imessage_imsg_gateway, whose routes accept agent_id as a
        # query param). This is a MORE authoritative identity signal than
        # _resolve_local_bridge_agent_id's own preferred_gateway_id
        # fallback (an explicit action by the agent's own owner, not an
        # inference), so it's checked first by that function's fast path
        # via the same row.
        existing = personal_channels_repository.get_local_bridge_state(
            str(gateway_id or "").strip(), channel_key=channel_key, agent_id=normalized_agent_id,
        )
        if existing is not None:
            return
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=channel_key,
            agent_id=normalized_agent_id,
            provider=LOCAL_BRIDGE_PERSONAL_CHANNELS.get(channel_key, {}).get("provider", channel_key),
            status="connecting",
            metadata={"resolved_via": "explicit_owner_action"},
        )


def _resolve_agent_id_for_inbound(gateway_id: str, channel_key: str) -> str:
    """Which agent's session an inbound event belongs to. The Gateway's
    session runtime doesn't know about Empyralis agent ids (see
    personal_channels_repository.find_agent_id_for_telegram_session's
    docstring) so this is a reverse lookup by gateway_id+channel_key against
    whichever agent has a currently-connected session there. Returns
    LEGACY_UNSCOPED_AGENT_ID (empty string) when that's ambiguous or there
    is none — execute_sage_turn_for_channel already treats that as "run as
    Sage," the pre-existing behavior, so an ambiguous lookup never breaks a
    turn, it just doesn't get specialist-scoped.

    This is the FAST path only — an indexed row lookup, no I/O beyond
    SQLite. For Signal/iMessage/WeChat-personal (LOCAL_BRIDGE_PERSONAL_CHANNELS)
    it only finds a row once something has already claimed one; see
    _resolve_local_bridge_agent_id (async, does the actual claiming) for the
    real inbound-handler entry point for those three channels — this
    synchronous function alone is not enough for them the way it is for
    WhatsApp/Telegram, which get an explicit agent_id at configure time
    (_claim_agent_channel_state)."""
    normalized_gateway_id = str(gateway_id or "").strip()
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        return personal_channels_repository.find_agent_id_for_whatsapp_session(
            normalized_gateway_id, channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        )
    if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        return personal_channels_repository.find_agent_id_for_telegram_session(
            normalized_gateway_id, channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        )
    if channel_key in LOCAL_BRIDGE_PERSONAL_CHANNELS:
        return personal_channels_repository.find_agent_id_for_local_bridge_session(
            normalized_gateway_id, channel_key=channel_key,
        )
    return personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID


async def agents_sharing_gateway(
    *,
    tenant_id: str,
    workspace_id: str,
    gateway_id: str,
) -> List[str]:
    """Every ENABLED agent install in this (tenant, workspace) whose own
    install_metadata.preferred_gateway_id points at this gateway_id — i.e.
    every agent legitimately placed on this box (CLAUDE.md: "AN AGENT
    BELONGS TO ITS PROJECT AND WORKS ONLY THERE" is about project scope, not
    hardware — one owner's several agents can share one gateway by design).

    Extracted from what was inline in _resolve_local_bridge_agent_id's slow
    path so the SAME "who is on this box" answer feeds both inbound
    resolution (this module) and provisioning-conflict detection
    (openclaw_provisioning_service.build_openclaw_channel_policies) — two
    readers computing this independently would be exactly the "second
    opinion that can disagree" shape this file warns about elsewhere.

    Fails closed to an empty list on any read error or a blank gateway_id,
    matching every other fail-closed reader on this path — a caller seeing
    zero sharing agents falls back to its own single-agent behavior, never
    to "assume nobody else is here" when the read simply failed."""
    normalized_gateway_id = str(gateway_id or "").strip()
    if not normalized_gateway_id:
        return []
    try:
        from server_modules import agent_registry_repository as _repo

        installs = await _repo.list_workspace_agent_installs(tenant_id=tenant_id, workspace_id=workspace_id)
    except Exception:
        _logger.warning(
            "agents_sharing_gateway: preferred_gateway_id lookup failed for gateway_id=%s",
            normalized_gateway_id, exc_info=True,
        )
        return []
    return sorted({
        str(install.get("id") or "").strip()
        for install in installs
        if isinstance(install, dict)
        and bool(install.get("enabled"))
        and str((install.get("metadata") or {}).get("preferred_gateway_id") or "").strip() == normalized_gateway_id
        and str(install.get("id") or "").strip()
    })


async def agents_bound_to_channel(
    *,
    tenant_id: str,
    workspace_id: str,
    candidate_agent_ids: List[str],
    channel_key: str,
) -> List[str]:
    """Which of candidate_agent_ids holds an ENABLED agent_channel_bindings
    row for this exact channel_key — the channel-specific narrowing that
    agents_sharing_gateway alone cannot provide (two agents can share a box
    without sharing a channel). Order of candidate_agent_ids is preserved
    among matches. Fails closed per-agent: a read error for one candidate
    excludes only that candidate rather than aborting the whole answer,
    matching channels_in_use's own "fewer channels, never more" posture."""
    if not candidate_agent_ids:
        return []
    from server_modules import agent_bindings_repository

    normalized_channel_key = str(channel_key or "").strip()
    owners: List[str] = []
    for candidate_id in candidate_agent_ids:
        try:
            bindings = await agent_bindings_repository.list_agent_channel_bindings(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                agent_install_id=candidate_id,
                enabled_only=True,
            )
        except Exception:
            _logger.warning(
                "agents_bound_to_channel: binding lookup failed for agent_id=%s channel=%s",
                candidate_id, normalized_channel_key, exc_info=True,
            )
            continue
        if any(str((row or {}).get("key") or "").strip() == normalized_channel_key for row in bindings or []):
            owners.append(candidate_id)
    return owners


class AgentNotPlacedOnGatewayError(ValueError):
    """Raised by assert_agent_placed_on_gateway below. A ValueError subclass
    (not a bare RuntimeError) so it falls straight into the `except
    ValueError` -> 400 handling every WhatsApp/Telegram/iMessage personal-
    channel route already has, with zero route changes needed; the OpenClaw
    provisioning path re-wraps it into OpenClawProvisioningError(status_code
    =403, reason_code="agent_not_placed_on_gateway") — see
    openclaw_provisioning_service.provision_openclaw_gateway.

    str(this) is already the owner-facing sentence — no "gateway_id",
    "install_metadata", or "preferred_gateway_id" anywhere in it, matching
    OpenClawProvisioningConflictError's own no-mechanism rule elsewhere in
    this codebase."""

    def __init__(self, *, agent_label: str, gateway_id: str, preferred_gateway_id: str) -> None:
        self.gateway_id = gateway_id
        self.preferred_gateway_id = preferred_gateway_id
        if preferred_gateway_id:
            message = (
                f"{agent_label} is placed on a different computer than this one. A channel "
                f"runs on the computer its agent is placed on — open {agent_label}'s Hardware "
                "tab to move it here, or set this channel up from that agent's own Channels panel."
            )
        else:
            message = (
                f"{agent_label} isn't placed on any computer yet. Open its Hardware tab and "
                "assign it a computer before setting up a channel for it."
            )
        super().__init__(message)


async def assert_agent_placed_on_gateway(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    gateway_id: str,
) -> None:
    """THE enforcement point for CLAUDE.md's execution-locality doctrine
    applied to channels. Founder, 2026-08-14: "I clearly connected my agent
    to a hardware and channel came from there as well... If X agent is
    connected to Z hardware, gateway and channel must run there as well."

    Before this, every write that binds one agent's channel state to a
    gateway_id (configure_whatsapp_personal_gateway,
    configure_telegram_personal_gateway, recheck/install_imessage_*,
    openclaw_provisioning_service.provision_openclaw_gateway) accepted ANY
    gateway_id the caller's OWNER role could reach in the workspace — never
    checked against which box the named agent is actually placed on. A
    gateway's per-channel software CAPABILITY (e.g. channel.telegram.
    personal, advertised unconditionally by every gateway process that
    bundles the runtime — see get_gateway_personal_channel_surfaces's own
    doc comment on `stage`/`proven_live`) was the only thing that looked
    like a gate, and it says nothing about binding: three gateways in one
    workspace can all legitimately advertise `telegram: True` in software
    while only one of them is where a given agent's tools actually run.

    install_metadata.preferred_gateway_id is the single field already
    treated as authoritative for "where does this agent's hardware run" —
    resolveHardwarePlacement (frontend), _resolve_local_bridge_agent_id,
    agents_sharing_gateway, and routes_fleet.fleet_agent_channels's
    selected_gateway_id all key off it already. This is that same fact
    enforced at every channel WRITE, not just read — one authoritative
    source, checked at the narrow waist each mutation already passes
    through, rather than a defensive re-check invented per route.

    Fails CLOSED: an unreadable install bundle raises rather than allowing
    the write through — the cost of a false positive here (a channel wired
    to hardware the agent was never placed on) is a cross-box credential
    leak or a repeat of the group-ban incident CLAUDE.md already documents;
    the cost of a false negative is a retryable error."""
    normalized_agent_id = str(agent_id or "").strip()
    normalized_gateway_id = str(gateway_id or "").strip()
    if not normalized_agent_id or not normalized_gateway_id:
        # Nothing to enforce: an unscoped call (no agent_id) is the
        # pre-existing "route as Sage" behavior _claim_agent_channel_state
        # already tolerates, and a blank gateway_id never reaches a real
        # mutation (the route's own gateway lookup 404s first).
        return
    from server_modules import agent_registry_repository as _repo

    try:
        bundle = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
        )
    except Exception:
        _logger.warning(
            "assert_agent_placed_on_gateway: install lookup failed for agent_id=%s gateway_id=%s",
            normalized_agent_id, normalized_gateway_id, exc_info=True,
        )
        bundle = None
    if not isinstance(bundle, dict):
        raise AgentNotPlacedOnGatewayError(
            agent_label=normalized_agent_id,
            gateway_id=normalized_gateway_id,
            preferred_gateway_id="",
        )
    metadata = bundle.get("install_metadata") if isinstance(bundle.get("install_metadata"), dict) else bundle.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    preferred = str(metadata.get("preferred_gateway_id") or "").strip()
    if preferred and preferred == normalized_gateway_id:
        return
    label = str(bundle.get("label") or bundle.get("agent_definition_name") or "").strip() or normalized_agent_id
    raise AgentNotPlacedOnGatewayError(
        agent_label=label,
        gateway_id=normalized_gateway_id,
        preferred_gateway_id=preferred,
    )


async def handle_agent_hardware_relocated(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    old_gateway_id: str,
    new_gateway_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Called as a best-effort side effect of fleet_tools.fleet_configure_agent
    whenever an agent's own install_metadata.preferred_gateway_id changes
    away from a real, previously-set gateway (a MOVE or an unbind — never
    fires on the FIRST placement, where old_gateway_id is blank).

    WHY THIS EXISTS: assert_agent_placed_on_gateway above stops new writes
    from landing on the wrong box, but a channel that was ALREADY connected
    on the OLD box before the move does not become disconnected just
    because Empyralis's own metadata changed — the founder's own scenario
    ("channels already paired elsewhere") demands this actively do
    something, not silently leave a channel answering from hardware the
    product now says the agent isn't on. CLAUDE.md's outcome-honesty law
    ("after an action, the product must tell the person what actually
    happened") applies here too: this never raises past the caller, and its
    return value is always a real report, not a swallowed None.

    WhatsApp/Telegram personal: these are single-session-per-box channels
    (find_agent_id_for_telegram_session/_whatsapp_session resolve by "most
    recently touched row for this gateway+channel", not per-agent) — the
    live session credential lives ON the old box's process memory/disk, so
    the only honest way to actually end it is the SAME disconnect capability
    the Channels panel's own "Disconnect" button already calls
    (disconnect_whatsapp_personal_gateway/disconnect_telegram_personal_gateway).
    Reused here rather than re-implemented, and best-effort: an offline old
    box means the session simply logs out the next time it can, exactly
    like any other disconnect attempt against an unreachable gateway.

    OpenClaw-transported / local-bridge channels: there is no per-channel
    disconnect RPC (CLAUDE.md: these are OS-level bridges or a shared
    OpenClaw config, not something Empyralis logs in and out of). The
    honest action there is to re-push the old gateway's OpenClaw policy —
    build_openclaw_channel_policies recomputes ownership fresh from CURRENT
    preferred_gateway_id on every call, so once this agent's own metadata
    has already moved, provisioning the OLD gateway (as whichever OTHER
    agent still shares it, if any) naturally stops including this agent's
    settings. Skipped, honestly, when no other agent remains on the old box
    to provision as — there is nothing left to push, and the old box's
    config will keep whatever it had until it is next reached, which is
    reported rather than hidden.
    """
    normalized_agent_id = str(agent_id or "").strip()
    normalized_old_gateway_id = str(old_gateway_id or "").strip()
    normalized_new_gateway_id = str(new_gateway_id or "").strip()
    report: Dict[str, Any] = {
        "old_gateway_id": normalized_old_gateway_id or None,
        "new_gateway_id": normalized_new_gateway_id or None,
        "released_channels": [],
        "notes": [],
    }
    if not normalized_agent_id or not normalized_old_gateway_id:
        return report
    if normalized_old_gateway_id == normalized_new_gateway_id:
        return report

    old_registration = gateway_state_repository.get_gateway_registration(normalized_old_gateway_id)
    if not old_registration:
        report["notes"].append("The previous computer's pairing record is gone; nothing to release there.")
        return report

    # WhatsApp / Telegram personal (Baileys/gramjs) had their own per-channel
    # disconnect RPC here (disconnect_whatsapp_personal_gateway/
    # disconnect_telegram_personal_gateway) — DELETED 2026-08-14 (full
    # OpenClaw channel cutover) along with those functions and the runtimes
    # they disconnected. WhatsApp and Telegram are now OpenClaw-transported
    # exactly like Signal/iMessage/WeChat always were, so they now fall
    # entirely under the OpenClaw-transported re-provisioning block below —
    # there is no first-party session left to explicitly disconnect.

    # OpenClaw-transported / local-bridge channels — re-push the OLD
    # gateway's policy under whichever OTHER agent still shares it, so
    # ownership recomputes without this agent. Nothing to push if this was
    # the only agent on that box.
    try:
        remaining_agents = [
            a for a in await agents_sharing_gateway(
                tenant_id=tenant_id, workspace_id=workspace_id, gateway_id=normalized_old_gateway_id,
            )
            if a != normalized_agent_id
        ]
    except Exception:
        remaining_agents = []
    if remaining_agents:
        try:
            from server_modules import openclaw_provisioning_service as _openclaw

            await _openclaw.provision_openclaw_gateway(
                gateway_id=normalized_old_gateway_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                agent_id=remaining_agents[0],
                actor_id=actor_id,
            )
            report["notes"].append(
                "OpenClaw-transported channels on the previous computer were re-provisioned "
                "under the agent(s) still placed there."
            )
        except Exception as exc:
            report["notes"].append(
                f"Could not re-provision the previous computer's OpenClaw transport right now "
                f"({exc}); it will pick up the change the next time anyone provisions it."
            )
    else:
        report["notes"].append(
            "No other agent is placed on the previous computer, so its OpenClaw-transported "
            "channel policy was left as-is; it will refresh the next time that computer is "
            "provisioned."
        )
    return report


async def _resolve_local_bridge_agent_id(
    *,
    gateway_id: str,
    channel_key: str,
    registration: Dict[str, Any],
) -> str:
    """The real inbound-resolution entry point for Signal/iMessage/
    WeChat-personal — _resolve_agent_id_for_inbound's fast path alone isn't
    enough for these three, because unlike WhatsApp/Telegram there is no
    configure_*_personal_gateway step that names a real agent_id up front
    (_claim_agent_channel_state). These are OS-level bridges authenticated
    entirely outside Empyralis (signal-cli, Messages.app) — there is no
    "the owner just told us who this is" moment for most of them.

    What DOES already unambiguously answer "which agent owns this gateway's
    local-bridge channel" is the SAME mechanism the product already uses in
    the opposite direction for these exact channels: install_metadata.
    preferred_gateway_id. FleetAgentDetail.tsx's own doc comment on
    CHANNEL_DOORS states it directly — "'Full account' doors bind to THIS
    agent's own preferred_gateway_id ... never to a workspace-wide/
    Sage-routed session" — Signal/iMessage's full-account doors are already
    product-documented as belonging to whichever agent points its own
    preferred_gateway_id at this box. Reusing it here for inbound
    resolution is the existing convention, not a new one.

    Fast path: _resolve_agent_id_for_inbound's indexed row lookup, populated
    by a PRIOR call to this function (or, for iMessage specifically, by an
    explicit owner action — see recheck_imessage_personal_gateway/
    install_imessage_imsg_gateway, which call _claim_agent_channel_state
    directly with the agent_id their own route already receives, a more
    authoritative signal than this fallback and checked first via the same
    row).

    Slow path (only runs until the fast path has something to find):
    reverse-scans this gateway's own tenant/workspace fleet for agent
    installs whose preferred_gateway_id equals this gateway_id
    (agents_sharing_gateway), THEN narrows by which of those agents actually
    holds an enabled binding for THIS channel_key (agents_bound_to_channel).

    The narrowing step is the fix for a real regression this shipped with:
    two agents legitimately sharing one box (agent A on Telegram, agent B on
    Discord — the ordinary, working multi-agent-per-box case) used to make
    EVERY local-bridge/OpenClaw channel on that box permanently ambiguous
    the moment the second agent's preferred_gateway_id was set, because the
    old check only asked "how many agents prefer this gateway" and never
    "which of them actually use this channel" — so adding an unrelated
    second agent to a box silently broke the first agent's already-working
    channel, forever (ambiguous resolves to unscoped, which claims nothing,
    so the NEXT message hits the same broken check again). Narrowing by
    channel-specific binding first, and only falling back to the broader
    "who shares this box" set when nobody has an enabled binding yet (a
    genuinely fresh, unclaimed channel), fixes that without weakening the
    real conflict case: if two DIFFERENT agents both hold an enabled binding
    for the SAME channel_key — the one configuration OpenClaw's own schema
    genuinely cannot express (a channel node is a single account, verified
    against the pinned build's own config schema, never a named-accounts
    map) — this still resolves ambiguous and fails closed, exactly as
    before.

    Exactly one match claims and persists a row
    (personal_channel_local_bridge_states), so every later message on this
    gateway+channel hits the fast path instead. Zero or multiple matches is
    genuinely ambiguous (no agent has claimed this channel, or more than one
    conflicting agent has) — returns LEGACY_UNSCOPED_AGENT_ID and claims
    nothing, so a later, unambiguous state doesn't have a wrong row to
    override.

    UNLIKE WhatsApp/Telegram's own ambiguous-lookup fallback (which only
    costs specialist-scoping — the turn still runs as Sage), an unresolved
    result here also denies the group/DM gates by construction (see
    _unresolved_identity_group_policy_config / _unresolved_identity_dm_policy_config)
    rather than defaulting open — there is no "the owner already configured
    this specific agent" story to lean on for a channel nobody has claimed,
    or that more than one agent claims."""
    resolved = _resolve_agent_id_for_inbound(gateway_id, channel_key)
    if resolved and resolved != personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return resolved
    normalized_gateway_id = str(gateway_id or "").strip()
    if not normalized_gateway_id:
        return personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID
    tenant_id = str(registration.get("tenant_id") or "default").strip() or "default"
    workspace_id = str(registration.get("workspace_id") or "default").strip() or "default"
    matches = await agents_sharing_gateway(
        tenant_id=tenant_id, workspace_id=workspace_id, gateway_id=normalized_gateway_id,
    )
    if len(matches) > 1:
        # Narrow "shares this box" down to "actually uses this channel" —
        # see the docstring above for why this step exists. An empty result
        # here means nobody has an enabled binding for this channel_key yet
        # (a fresh, unclaimed channel on a shared box); keep the broader set
        # in that case so the pre-existing ambiguity-denial below still
        # applies rather than silently resolving to nothing.
        channel_owners = await agents_bound_to_channel(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            candidate_agent_ids=matches,
            channel_key=channel_key,
        )
        if channel_owners:
            matches = channel_owners
    if len(matches) != 1:
        if len(matches) > 1:
            _logger.warning(
                "local_bridge: gateway_id=%s channel=%s has %d conflicting agent claims (%s) — "
                "ambiguous, leaving identity unresolved (fails closed rather than guessing which "
                "agent owns it). OpenClaw's own config schema cannot express two accounts on one "
                "channel node; this is a real configuration conflict, not a false positive from "
                "box-sharing alone.",
                normalized_gateway_id, channel_key, len(matches), matches,
            )
        return personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID
    resolved_agent_id = matches[0]
    provider = LOCAL_BRIDGE_PERSONAL_CHANNELS.get(channel_key, {}).get("provider", channel_key)
    personal_channels_repository.upsert_local_bridge_state(
        gateway_id=normalized_gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=str(registration.get("user_id") or "").strip(),
        channel_key=channel_key,
        agent_id=resolved_agent_id,
        provider=provider,
        status="linked",
        metadata={"resolved_via": "preferred_gateway_id"},
    )
    return resolved_agent_id


def _personal_channel_state(gateway_id: str, channel_key: str) -> Optional[Dict[str, Any]]:
    normalized_gateway_id = str(gateway_id or "").strip()
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        return personal_channels_repository.get_whatsapp_state(
            normalized_gateway_id,
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        )
    if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        return personal_channels_repository.get_telegram_state(
            normalized_gateway_id,
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        )
    return None


def _recent_message_count(gateway_id: str, channel_key: str) -> int:
    if channel_key not in {WHATSAPP_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_CHANNEL_KEY}:
        return 0
    return len(
        personal_channels_repository.list_recent_gateway_messages(
            str(gateway_id or "").strip(),
            channel_key=channel_key,
        )
    )


def _safe_state_summary(state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(state, dict):
        return None
    metadata = _as_mapping(state.get("metadata"))
    summary = {
        "status": str(state.get("status") or "").strip() or None,
        "linked_name": str(state.get("linked_name") or "").strip() or None,
        "linked_username": str(state.get("linked_username") or "").strip() or None,
        "connected_at": str(state.get("connected_at") or "").strip() or None,
        "updated_at": metadata.get("updated_at"),
        "retryable": bool(metadata.get("retryable")),
    }
    return secret_redaction_service.sanitize_mapping({key: value for key, value in summary.items() if value is not None})


def _connected_identity_label(channel_key: str, state: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(state, dict):
        return None
    for key in ("linked_name", "linked_username"):
        value = str(state.get(key) or "").strip()
        if value:
            return secret_redaction_service.redact_text(value)
    status = str(state.get("status") or "").strip().lower()
    if status == "connected":
        if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
            return "Linked Telegram account"
        if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
            return "Linked WhatsApp account"
    return None


def _personal_channel_transport_descriptor(
    channel_key: str,
    platform: Dict[str, Any],
) -> Dict[str, Any]:
    """Which transport carries this channel, and — for OpenClaw — which of
    their channels it is.

    Derived per channel from the registry, never a lookup table: a channel
    OpenClaw adds gets a correct descriptor with no edit here.
    """
    channel = openclaw_channel_registry.channel_for_key(channel_key)
    if channel is None:
        return {"kind": "first_party"}
    return {
        "kind": openclaw_channel_registry.OWNER_OPENCLAW,
        "openclaw_channel_id": channel.id,
        "openclaw_version": openclaw_channel_registry.OPENCLAW_VERSION,
        # False for the handful of catalogued channels whose `channels.<id>`
        # config node only exists once their plugin is installed. Provisioning
        # leaves those OFF with a stated reason rather than writing a node
        # OpenClaw would reject — worth surfacing, because a channel that is
        # declared but cannot carry a policy is not the same as one that is
        # simply not connected.
        "policy_expressible": bool(channel.config_schema_present),
        "superseded_by": platform.get("superseded_by"),
    }


def get_gateway_personal_channel_surfaces(gateway_id: str) -> Dict[str, Any]:
    """Return safe per-channel capability/status projection for a paired Agent Computer.

    This is intentionally broader than the WhatsApp/Telegram detail endpoints:
    it includes local-bridge personal channels such as Signal, iMessage, and
    WeChat, while keeping them scoped to Sage/Agent Computer rather than Studio
    business/customer connectors.
    """

    normalized_gateway_id = str(gateway_id or "").strip()
    registration = gateway_state_repository.get_gateway_registration(normalized_gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")

    metadata = _as_mapping(registration.get("metadata"))
    manifests_by_key = _records_by_channel(metadata.get("personal_channel_manifests"))
    health_by_key = _records_by_channel(metadata.get("personal_channel_health"))
    catalog_by_key = {item["channel_key"]: dict(item) for item in channel_lane_contract_service.personal_channel_catalog()}
    platform_by_key = {
        item["channel_key"]: dict(item)
        for item in channel_lane_contract_service.platform_channel_catalog("sage")
        if channel_lane_contract_service.is_personal_channel_key(str(item.get("channel_key") or ""))
    }

    items: list[Dict[str, Any]] = []
    for channel_key, catalog in catalog_by_key.items():
        spec = channel_lane_contract_service.assert_personal_gateway_channel(channel_key)
        manifest = manifests_by_key.get(channel_key, {})
        health = health_by_key.get(channel_key, {})
        platform = platform_by_key.get(channel_key, {})
        state = _personal_channel_state(normalized_gateway_id, channel_key)
        state_status = str((state or {}).get("status") or "").strip()
        health_status = str(health.get("status") or "").strip()
        manifest_status = str(manifest.get("status") or "").strip()
        status = state_status or health_status or manifest_status or str(platform.get("status") or catalog.get("stage") or "agent_computer_bridge").strip()
        catalog_live_capable = _boolish(
            platform.get("live_capable", spec.get("live_capable")),
            default=str(spec.get("live_capable") or "").strip().lower() == "true",
        )
        advertised_live_capable = _boolish(
            manifest.get("live_capable")
            if "live_capable" in manifest
            else manifest.get("liveCapable")
            if "liveCapable" in manifest
            else platform.get("live_capable", spec.get("live_capable")),
            default=str(spec.get("live_capable") or "").strip().lower() == "true",
        )
        live_capable = bool(catalog_live_capable and advertised_live_capable)
        connected = bool(state_status == "connected" or health.get("connected") is True)
        running = bool(health.get("running") is True)
        items.append(
            {
                "channel_key": channel_key,
                "label": str(manifest.get("label") or platform.get("label") or catalog.get("label") or channel_key).strip(),
                "provider": str(manifest.get("provider") or platform.get("provider") or spec.get("provider") or "").strip(),
                "runtime_lane": str(manifest.get("runtime_lane") or manifest.get("runtimeLane") or spec.get("runtime_lane") or "").strip(),
                "stage": str(platform.get("stage") or catalog.get("stage") or manifest.get("stage") or "").strip(),
                "status": status,
                "status_label": _LOCAL_BRIDGE_PERSONAL_CHANNEL_COPY.get(channel_key, {}).get("status_label"),
                "live_capable": live_capable,
                "requires_agent_computer": _boolish(
                    manifest.get("requires_agent_computer")
                    if "requires_agent_computer" in manifest
                    else manifest.get("requiresAgentComputer")
                    if "requiresAgentComputer" in manifest
                    else platform.get("requires_agent_computer", True),
                    default=True,
                ),
                "connected": connected,
                "running": running,
                "connected_identity": _connected_identity_label(channel_key, state),
                "recent_message_count": _recent_message_count(normalized_gateway_id, channel_key),
                "capabilities": secret_redaction_service.sanitize_value(
                    manifest.get("capabilities")
                    if isinstance(manifest.get("capabilities"), list)
                    else platform.get("capabilities", []),
                ),
                "chat_types": secret_redaction_service.sanitize_value(
                    manifest.get("chat_types")
                    if isinstance(manifest.get("chat_types"), list)
                    else [],
                ),
                "media": secret_redaction_service.sanitize_mapping(_as_mapping(manifest.get("media"))),
                # `safety` here is the GATEWAY's own static manifest claim,
                # passed through unmodified (see empyralis-gateway's
                # PersonalChannelCapabilityManifest.safety). `dm_policy` below
                # is what the SERVER actually enforces for this channel —
                # see _enforce_dm_policy. This endpoint has no agent_id
                # parameter (a gateway can host more than one agent's
                # session per channel), so this is the channel-level
                # default/enforcement description, not one specific agent's
                # live allowlist — fetch that per-agent via a future
                # settings surface once one exists (see this build's report).
                "safety": secret_redaction_service.sanitize_mapping(_as_mapping(manifest.get("safety"))),
                # default_mode/modes are LANE-SPECIFIC, not global: an
                # OpenClaw-transported channel defaults to allowlist and can
                # be set to nothing else (see
                # DEFAULT_OPENCLAW_DM_POLICY_MODE). Reporting the first-party
                # default here would describe a state this channel is never
                # in, and reporting all four modes would invite a UI to
                # render three that the write path refuses.
                "dm_policy": {
                    "default_mode": _default_dm_policy_mode_for_channel(channel_key),
                    "modes": sorted(
                        OPENCLAW_SETTABLE_DM_POLICY_MODES
                        if _is_openclaw_transported_channel(channel_key)
                        else DM_POLICY_MODES
                    ),
                    "enforced_server_side": True,
                },
                # group_policy: the SAME channel-level default/enforcement
                # description as dm_policy above, for the separate group
                # axes (see _enforce_group_policy). require_mention's
                # default is OFF (Ruling A, see-and-decide) — an owner can
                # opt any agent back into a hard mention-only gate per
                # channel; see that constant's own doc comment for why.
                "group_policy": {
                    "default_mode": DEFAULT_GROUP_POLICY_MODE,
                    "modes": sorted(GROUP_POLICY_MODES),
                    "default_require_mention": DEFAULT_REQUIRE_MENTION,
                    "enforced_server_side": True,
                },
                "manifest": secret_redaction_service.sanitize_mapping(manifest),
                "health": secret_redaction_service.sanitize_mapping(health),
                "state": _safe_state_summary(state),
                "detail": _LOCAL_BRIDGE_PERSONAL_CHANNEL_COPY.get(channel_key, {}).get("detail"),
                "next_step": _LOCAL_BRIDGE_PERSONAL_CHANNEL_COPY.get(channel_key, {}).get("next_step"),
                # HOW "available through the transport, not yet proven live"
                # is expressed — and why it is NOT a hand-maintained list.
                #
                #   stage        "preview" for every OpenClaw channel. A
                #                property of the transport, uniform, so there
                #                is no per-channel judgement to keep honest.
                #   proven_live  OBSERVED, never declared: this gateway has a
                #                real connection on this channel right now.
                #                Read off gateway state, so it becomes true the
                #                moment a channel is genuinely driven and false
                #                again if it stops — which is the only version
                #                of "promote it from a real message, not from a
                #                code reading" that cannot rot.
                #
                # A UI shows "Preview — not connected" until proven_live, then
                # the same live treatment every first-party channel gets. No
                # list anywhere says which channels have made it.
                "proven_live": bool(connected and running),
                "transport": _personal_channel_transport_descriptor(channel_key, platform),
            }
        )

    return {
        "gateway_id": normalized_gateway_id,
        "items": items,
    }


def sync_gateway_personal_channel_state(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    payload: Dict[str, Any],
    agent_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """agent_id: pass it explicitly when the caller already knows it (the
    inbound-message handlers below, which resolve it from the message
    itself). Leave it None for the raw gateway.state.update push (no
    message to resolve from) — this falls back to "whoever most recently
    touched this gateway+channel" via the repository's reverse lookup,
    which configure_telegram_personal_gateway/configure_whatsapp_personal_gateway
    keep correct by claiming a row up front, before the Gateway ever
    reports back (see personal_channels_repository.find_agent_id_for_telegram_session's
    docstring)."""
    personal_channels = payload.get("personal_channels") if isinstance(payload.get("personal_channels"), dict) else {}
    synced_state: Optional[Dict[str, Any]] = None

    # A BOX MAY REPORT A CHANNEL THIS BUILD NO LONGER CARRIES, AND THAT IS
    # NOT AN ERROR — it is the ordinary state of a fleet mid-rollout.
    #
    # This runs on EVERY `gateway.state.update` frame
    # (gateway_protocol_service.py:2960). The 2026-08-14 cutover retired the
    # first-party whatsapp_personal / telegram_personal lanes, but a gateway
    # installed before it keeps advertising them in its state payload until
    # it takes an update — and `assert_personal_gateway_channel` RAISES on a
    # key the lane contract no longer knows. Observed on production minutes
    # after this deploy, on a real customer box:
    #
    #   sync_gateway_personal_channel_state
    #     -> assert_personal_gateway_channel("whatsapp_personal")
    #     -> ValueError: Channel lane contract rejected non-personal channel
    #
    # An old box must never be able to raise inside the frame handler for
    # every state update it sends. The contract assertion is still the right
    # gate for a key we intend to WRITE; it is the wrong response to a key
    # somebody else merely mentioned. Skip what this build does not carry,
    # and say so once per key rather than silently — a channel quietly
    # vanishing from sync is exactly the kind of thing this file's own
    # history says takes weeks to notice.
    def _carried(channel_key: str) -> bool:
        try:
            channel_lane_contract_service.assert_personal_gateway_channel(
                channel_key,
                str((personal_channels.get(channel_key) or {}).get("provider") or "").strip() or None,
            )
            return True
        except Exception:
            _logger.info(
                "gateway state update mentions channel %s, which this build no longer carries — skipping its sync (gateway_id=%s)",
                channel_key,
                gateway_id,
            )
            return False

    whatsapp_state = (
        personal_channels.get(WHATSAPP_PERSONAL_CHANNEL_KEY)
        if isinstance(personal_channels.get(WHATSAPP_PERSONAL_CHANNEL_KEY), dict)
        else {}
    )
    if whatsapp_state and _carried(WHATSAPP_PERSONAL_CHANNEL_KEY):
        whatsapp_spec = channel_lane_contract_service.assert_personal_gateway_channel(
            WHATSAPP_PERSONAL_CHANNEL_KEY,
            str(whatsapp_state.get("provider") or WHATSAPP_PERSONAL_PROVIDER).strip() or WHATSAPP_PERSONAL_PROVIDER,
        )
        resolved_whatsapp_agent_id = (
            str(agent_id).strip() if agent_id is not None
            else _resolve_agent_id_for_inbound(gateway_id, WHATSAPP_PERSONAL_CHANNEL_KEY)
        )
        synced_state = personal_channels_repository.upsert_whatsapp_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id=resolved_whatsapp_agent_id,
            provider=whatsapp_spec["provider"],
            status=str(whatsapp_state.get("status") or "idle").strip() or "idle",
            qr_code=str(whatsapp_state.get("qr_code") or "").strip() or None,
            linked_jid=str(whatsapp_state.get("linked_jid") or "").strip() or None,
            linked_name=str(whatsapp_state.get("linked_name") or "").strip() or None,
            connected_at=str(whatsapp_state.get("connected_at") or "").strip() or None,
            metadata={
                "retryable": bool(whatsapp_state.get("retryable")),
                "login_hint": str(whatsapp_state.get("login_hint") or "").strip() or None,
                "pairing_code": str(whatsapp_state.get("pairing_code") or "").strip() or None,
                "pairing_code_generated_at": str(
                    whatsapp_state.get("pairing_code_generated_at") or ""
                ).strip()
                or None,
                "last_disconnect_reason": str(whatsapp_state.get("last_disconnect_reason") or "").strip() or None,
                "last_disconnect_code": whatsapp_state.get("last_disconnect_code"),
                "updated_at": whatsapp_state.get("updated_at"),
            },
        )
        if str((synced_state or {}).get("status") or "").strip() == "connected":
            _ensure_agent_channel_binding_enabled(
                agent_id=resolved_whatsapp_agent_id,
                channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
                registration=registration,
            )
    telegram_state = (
        personal_channels.get(TELEGRAM_PERSONAL_CHANNEL_KEY)
        if isinstance(personal_channels.get(TELEGRAM_PERSONAL_CHANNEL_KEY), dict)
        else {}
    )
    if telegram_state and _carried(TELEGRAM_PERSONAL_CHANNEL_KEY):
        telegram_spec = channel_lane_contract_service.assert_personal_gateway_channel(
            TELEGRAM_PERSONAL_CHANNEL_KEY,
            str(telegram_state.get("provider") or TELEGRAM_PERSONAL_PROVIDER).strip() or TELEGRAM_PERSONAL_PROVIDER,
        )
        resolved_telegram_agent_id = (
            str(agent_id).strip() if agent_id is not None
            else _resolve_agent_id_for_inbound(gateway_id, TELEGRAM_PERSONAL_CHANNEL_KEY)
        )
        synced_state = personal_channels_repository.upsert_telegram_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id=resolved_telegram_agent_id,
            provider=telegram_spec["provider"],
            status=str(telegram_state.get("status") or "idle").strip() or "idle",
            login_hint=str(telegram_state.get("login_hint") or "").strip() or None,
            linked_user_id=str(telegram_state.get("linked_user_id") or "").strip() or None,
            linked_username=str(telegram_state.get("linked_username") or "").strip() or None,
            linked_phone=str(telegram_state.get("linked_phone") or "").strip() or None,
            linked_name=str(telegram_state.get("linked_name") or "").strip() or None,
            connected_at=str(telegram_state.get("connected_at") or "").strip() or None,
            metadata={
                "retryable": bool(telegram_state.get("retryable")),
                "code_requested_at": str(telegram_state.get("code_requested_at") or "").strip() or None,
                "last_disconnect_reason": str(telegram_state.get("last_disconnect_reason") or "").strip() or None,
                "last_disconnect_code": telegram_state.get("last_disconnect_code"),
                "updated_at": telegram_state.get("updated_at"),
            },
        )
        if str((synced_state or {}).get("status") or "").strip() == "connected":
            _ensure_agent_channel_binding_enabled(
                agent_id=resolved_telegram_agent_id,
                channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
                registration=registration,
            )
    return synced_state


def sync_gateway_channel_outbound_result(
    *,
    gateway_id: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    channel_key = str(payload.get("channel_key") or "").strip()
    if not channel_key:
        return None
    channel_lane_contract_service.assert_personal_gateway_channel(
        channel_key,
        str(payload.get("provider") or "").strip() or None,
    )
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return None
    if bool(payload.get("delivered")):
        return personal_channels_repository.mark_outbound_delivered(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            idempotency_key=idempotency_key,
            external_message_id=str(payload.get("external_message_id") or "").strip() or None,
            metadata={"dispatch_result": dict(payload or {})},
        )
    return personal_channels_repository.get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=idempotency_key,
    )


def _iter_personal_channel_records(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            candidates = value.get("items") or []
        else:
            candidates = []
            for key, item in value.items():
                if isinstance(item, dict):
                    enriched = dict(item)
                    enriched.setdefault("channel_key", str(key or "").strip())
                    candidates.append(enriched)
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []
    return [item for item in candidates if isinstance(item, dict)]


def _record_channel_key(record: Dict[str, Any]) -> str:
    return str(
        record.get("channel_key")
        or record.get("channelKey")
        or record.get("key")
        or record.get("id")
        or ""
    ).strip()


def _record_provider(record: Dict[str, Any]) -> str:
    return str(record.get("provider") or record.get("runtime_provider") or "").strip()


def _registration_capabilities(registration: Dict[str, Any]) -> list[str]:
    raw = registration.get("capabilities")
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return []


def _registration_advertises_personal_channel(
    registration: Dict[str, Any],
    channel_key: str,
) -> bool:
    normalized_channel = str(channel_key or "").strip().lower()
    if not normalized_channel:
        return False
    provider_prefix = normalized_channel.removesuffix("_personal")
    expected_fragments = {
        normalized_channel,
        normalized_channel.replace("_", "."),
        f"channel.{normalized_channel}",
        f"channel.{normalized_channel.replace('_', '.')}",
    }
    if provider_prefix and provider_prefix != normalized_channel:
        expected_fragments.add(f"channel.{provider_prefix}.personal")
    for capability in _registration_capabilities(registration):
        normalized_capability = capability.strip().lower()
        if normalized_capability in expected_fragments:
            return True
    return False


def _assert_gateway_advertised_personal_channel(
    *,
    registration: Dict[str, Any],
    channel_key: str,
    provider: str,
) -> None:
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    manifests = _iter_personal_channel_records(metadata.get("personal_channel_manifests"))
    manifest = next((item for item in manifests if _record_channel_key(item) == channel_key), None)
    if manifest is None:
        if not _registration_advertises_personal_channel(registration, channel_key):
            raise ValueError(f"Gateway has not advertised personal channel support for {channel_key}.")
    else:
        manifest_provider = _record_provider(manifest)
        if manifest_provider and provider and manifest_provider != provider:
            raise ValueError(f"Gateway advertised {channel_key} with provider {manifest_provider}, not {provider}.")
        if manifest.get("liveCapable") is False or manifest.get("live_capable") is False:
            raise ValueError(f"Gateway personal channel {channel_key} is not live-capable.")
        manifest_status = str(manifest.get("status") or manifest.get("stage") or "").strip().lower()
        if manifest_status in {"unsupported", "unavailable", "disabled", "planned", "locked"}:
            raise ValueError(f"Gateway personal channel {channel_key} is {manifest_status}.")
    health_records = _iter_personal_channel_records(metadata.get("personal_channel_health"))
    health = next((item for item in health_records if _record_channel_key(item) == channel_key), None)
    if health is not None:
        health_status = str(health.get("status") or health.get("health") or "").strip().lower()
        if health_status in {"offline", "error", "failed", "disconnected", "unavailable", "disabled"}:
            raise ValueError(f"Gateway personal channel {channel_key} health is {health_status}.")


# ── Media pipeline (Feature B): inbound fetch/store/transcribe ────────
#
# THE CONTRACT: a channel.inbound message MAY carry an optional `media`
# array; each item is {kind: "image"|"voice"|"audio"|"video"|"file",
# media_id: "<gateway-fetchable id>", mime_type, filename?, size_bytes?,
# duration_sec?}. See gateway_protocol_service.fetch_channel_media's
# docstring for the exact fetch RPC, and
# personal_channel_media_store_service's module docstring for the
# storage/attachment-context contract.


async def _process_inbound_media_for_turn(
    *,
    gateway_id: str,
    channel_key: str,
    provider: str,
    registration: Dict[str, Any],
    agent_id: str,
    text: str,
    media_items: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]]]:
    """Fetch/store any media on this inbound message, splice voice/audio
    transcripts into the text (`[Voice message]: <transcript>`, or the
    fixed "transcription unavailable" placeholder), and return image/file
    attachments ready for the turn context. Never raises: any failure here
    degrades to the original text with no attachments rather than dropping
    the turn — matching this feature's graceful-degradation contract.
    """
    if not media_items:
        return text, []

    workspace_id = str(registration.get("workspace_id") or "default").strip() or "default"
    try:
        from server_modules import personal_channel_media_store_service as _media_store

        result = await _media_store.process_inbound_media(
            gateway_id=gateway_id,
            channel_key=channel_key,
            provider=provider,
            workspace_id=workspace_id,
            agent_id=agent_id,
            media=media_items,
        )
    except Exception:
        _logger.warning(
            "media pipeline: process_inbound_media failed channel=%s gateway=%s",
            channel_key, gateway_id, exc_info=True,
        )
        return text, []

    effective_text = text
    for voice_record in result.get("voice_records") or []:
        try:
            from server_modules import personal_channel_transcription_service as _stt

            transcription = await _stt.transcribe_voice_bytes(
                workspace_id=workspace_id,
                audio_bytes=voice_record.get("raw_bytes") or b"",
                mime_type=str(voice_record.get("mime_type") or "audio/ogg"),
                filename=str(voice_record.get("filename") or "voice-message"),
                agent_id=agent_id,
            )
            voice_text = _stt.format_voice_message_text(transcription)
        except Exception:
            _logger.warning(
                "media pipeline: voice transcription failed channel=%s gateway=%s",
                channel_key, gateway_id, exc_info=True,
            )
            voice_text = "[Voice message — transcription unavailable]"
        effective_text = f"{effective_text}\n{voice_text}".strip() if effective_text else voice_text

    attachments = list(result.get("attachments") or [])
    if not effective_text.strip():
        # Media-only message with nothing text-like to anchor the turn on
        # (e.g. an image with no caption, or an unfetchable voice note) —
        # the attachment context block (if any) still carries the image.
        effective_text = "[Media message]"

    return effective_text, attachments


async def handle_gateway_channel_inbound(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_key = str(payload.get("channel_key") or "").strip()
    provider = str(payload.get("provider") or "").strip()
    channel_lane_contract_service.assert_personal_gateway_channel(
        channel_key,
        provider or None,
    )
    _assert_gateway_advertised_personal_channel(
        registration=registration,
        channel_key=channel_key,
        provider=provider,
    )
    # Generate or preserve trace_id once at the inbound boundary
    trace_id = str(payload.get("trace_id") or "").strip() or f"channel-{channel_key}-{uuid4().hex[:16]}"
    payload["trace_id"] = trace_id
    handler = _handler_registry.get(channel_key)
    return await handler.handle_inbound(
        gateway_id=gateway_id,
        registration=registration,
        payload=payload,
    )


# _deliver_whatsapp_personal_reply, _handle_whatsapp_gateway_channel_inbound and
# _handle_telegram_gateway_channel_inbound DELETED 2026-08-14 (full OpenClaw
# channel cutover) along with the WhatsApp Baileys / Telegram gramjs gateway
# runtimes they served. WhatsApp and Telegram inbound now flows through
# _OpenClawPersonalChannelHandler.handle_inbound -> the SAME
# _handle_local_bridge_gateway_channel_inbound every other OpenClaw-
# transported channel already uses (see _handler_registry below). Git
# history has the deleted functions if a first-party revival is ever needed.


async def _deliver_local_bridge_personal_reply(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    inbound: Dict[str, Any],
    remote_jid: str,
    external_message_id: str,
    text: str,
    push_name: Optional[str],
    duplicate: bool,
    channel_key: str,
    provider: str,
    label: str,
    # REQUIRED, deliberately without a default (unlike the WhatsApp/Telegram
    # twins' `agent_id: str = ""`). This function omitted the parameter
    # entirely until 2026-08-08, so every mark_inbound_processed below
    # defaulted to LEGACY_UNSCOPED_AGENT_ID while
    # _handle_local_bridge_gateway_channel_inbound recorded the row under the
    # REAL agent_id resolved by _resolve_local_bridge_agent_id. The UPDATE is
    # scoped by (gateway_id, channel_key, agent_id, external_message_id), so
    # it matched zero rows and reply_idempotency_key stayed NULL forever.
    #
    # Consequence, and why this is a safety fix and not bookkeeping: the
    # no-reply marker written below IS the "the agent saw this and
    # deliberately said nothing" record, and the guard at the top of this
    # function is its only reader. `channel.inbound` is at-least-once by
    # design end to end (ws-client.publishEvent re-enqueues into a replayable
    # outbox when the socket is down; local-bridge-runtime's seen-event set
    # is in-memory and dies with the process; the OpenClaw bridge plugin
    # retries through its own durable BoundedRetryQueue). A redelivered
    # message therefore found a blank marker, re-ran the turn, and could
    # answer where the first pass had chosen SILENCE — the same
    # "silence is a decision, not a failure" invariant
    # personal_channel_sage_bridge_service enforces one layer down, defeated
    # from the persistence layer instead.
    agent_id: str,
    trace_id: str = "",
    attachments: Optional[List[Dict[str, Any]]] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
    sender_id: str = "",
    was_addressed: Optional[bool] = None,
) -> Dict[str, Any]:
    no_reply_prefix = f"{channel_key}:noreply:"
    reply_idempotency_key = str(inbound.get("reply_idempotency_key") or "").strip() or None
    if reply_idempotency_key and reply_idempotency_key.startswith(no_reply_prefix):
        return {"duplicate": duplicate, "inbound": inbound, "outbound": None}

    outbound: Optional[Dict[str, Any]] = None
    idempotency_key = reply_idempotency_key or f"{channel_key}:{external_message_id}"
    # OUTBOUND rows for this channel family stay under the legacy unscoped
    # agent id ON PURPOSE, and that is not the same defect as the inbound one
    # fixed here. Inbound was a genuine read/write MISMATCH — written scoped,
    # updated unscoped. Outbound is uniformly unscoped on every local-bridge
    # write path: send_local_bridge_personal_message (the explicit
    # POST .../messages route) has no agent_id to pass at all, and it must
    # share one idempotency namespace with these auto-replies or a manual
    # retry of the same key would stop deduping against a reply already sent.
    # Do not "finish the job" by scoping only these three calls.
    if reply_idempotency_key:
        outbound = personal_channels_repository.get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            idempotency_key=reply_idempotency_key,
        )
    if outbound and str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            agent_id=agent_id,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": duplicate, "inbound": inbound, "outbound": outbound}

    if outbound is None:
        # ── Shared command dispatcher (the one waist every personal-channel
        # delivery path crosses — see _dispatch_personal_channel_command).
        # outbound_agent_id is deliberately omitted: this channel family's
        # outbound rows stay unscoped on purpose (see this function's own
        # comment on its outbound calls below). ──
        command_result = await _dispatch_personal_channel_command(
            gateway_id=gateway_id,
            registration=registration,
            inbound=inbound,
            channel_key=channel_key,
            provider=provider,
            capability_id=f"{channel_key}.send",
            agent_id=agent_id,
            external_message_id=external_message_id,
            remote_jid=remote_jid,
            text=text,
            duplicate=duplicate,
            trace_id=trace_id,
        )
        if command_result is not None:
            return command_result

        reply = await personal_channel_sage_bridge_service.build_personal_channel_reply_async(
            surface_channel=channel_key,
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            gateway_id=str(gateway_id or "").strip(),
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            sender_id=sender_id,
            fallback_label=label,
            source_event_id=external_message_id,
            attachments=attachments,
            is_owner=is_owner,
            is_group=is_group,
            chat_label=chat_label,
            was_addressed=was_addressed,
            # The agent this conversation belongs to, already resolved above by
            # _resolve_local_bridge_agent_id. Without it the turn runs as the
            # workspace master and lands in the shared "sage-main" thread, so
            # the conversation is invisible to GET /api/threads?agent_id=...
            agent_id=agent_id,
        )
        # ABSOLUTE RULE: no hardcoded platform status/error message may EVER
        # be sent into a channel (DM or group). resolve_channel_reply_outcome
        # applies filter_channel_outbound_reply internally as the backstop AND
        # separates "the agent chose silence" from "the turn never completed"
        # — see its docstring for why collapsing those two lost people's
        # messages outright.
        outcome = channel_adapter.resolve_channel_reply_outcome(reply, is_owner=is_owner)
        reply_media = list(outcome.media)
        _safe_reply_text = outcome.text
        # A media-only reply (send_image/generate_image queued an attachment
        # but the model had nothing more to say, or its text was filtered
        # above) still has something to deliver — only skip when there is
        # genuinely neither safe text nor media.
        if not _safe_reply_text and not reply_media:
            _undelivered = outcome.is_undelivered
            # The no-reply marker is written ONLY for a genuine silence
            # decision. On an UNDELIVERED turn it is deliberately NOT
            # written: it would record a message the platform failed to
            # answer as answered, and it is the exact row the redelivery
            # guard at the top of this function reads — so writing it also
            # cancels the at-least-once retry that is this message's last
            # remaining chance to be answered at all.
            refreshed_inbound = None
            if not _undelivered:
                no_reply_idempotency_key = f"{no_reply_prefix}{external_message_id}"
                # THE load-bearing write: this is the durable record that the
                # agent was asked and chose not to answer. See agent_id's own
                # comment on this function's signature.
                refreshed_inbound = personal_channels_repository.mark_inbound_processed(
                    gateway_id=str(gateway_id or "").strip(),
                    channel_key=channel_key,
                    agent_id=agent_id,
                    external_message_id=external_message_id,
                    reply_idempotency_key=no_reply_idempotency_key,
                )
            _emit_automatic_reply_audit(
                action=f"personal_channel.{channel_key.split('_', 1)[0]}.automatic_reply",
                status="failed" if _undelivered else "skipped",
                registration=registration,
                gateway_id=gateway_id,
                channel_key=channel_key,
                provider=provider,
                detail=(
                    f"Automatic {label} personal reply was NOT delivered: the turn did not complete "
                    f"({outcome.status_code}). The inbound message is left unprocessed so a redelivery retries it."
                    if _undelivered
                    else f"Automatic {label} personal reply was skipped because the agent returned no reply."
                ),
                metadata={
                    "remote_jid": remote_jid,
                    "inbound_external_message_id": external_message_id,
                    "undelivered": _undelivered,
                    "status_code": outcome.status_code or None,
                },
                trace_id=trace_id,
                idempotency_key=(
                    f"personal_channel.{channel_key}.automatic_reply."
                    f"{'failed' if _undelivered else 'skipped'}:{gateway_id}:{external_message_id}"
                ),
            )
            return {"duplicate": duplicate, "inbound": refreshed_inbound or inbound, "outbound": None}

        outbound, _ = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            idempotency_key=idempotency_key,
            remote_jid=remote_jid,
            text=_safe_reply_text,
            reply_to_external_message_id=external_message_id,
            metadata={
                "reply_source": (
                    "platform_status_owner_only"
                    if outcome.is_undelivered
                    else str((reply or {}).get("source") or "").strip() or None
                ),
                "media": reply_media or None,
                "undelivered_status_code": outcome.status_code or None,
            },
        )

    if str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            agent_id=agent_id,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": duplicate, "inbound": inbound, "outbound": outbound}

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=f"{str(channel_key or '').strip()}.send",
        request_id=str(idempotency_key or "").strip(),
    )
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        provider=provider,
        remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
        text=str(outbound.get("text") or "").strip(),
        idempotency_key=idempotency_key,
        # Auto-replies dispatch as normal messages, not forced quote-reply
        # bubbles: always threading a reply to the triggering message reads
        # as robotic. An explicit "reply to X" send
        # (send_local_bridge_personal_message) still honors a caller-supplied id.
        reply_to_external_message_id=None,
        # Read back from the stored outbound row (not the `reply` var above)
        # so this also carries media on the idempotent-replay path, where
        # `outbound` came from get_outbound_message() and `reply` was never
        # rebuilt this call.
        media=list((outbound.get("metadata") or {}).get("media") or []),
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=idempotency_key,
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        agent_id=agent_id,
        external_message_id=external_message_id,
        reply_idempotency_key=idempotency_key,
    )
    _emit_automatic_reply_audit(
        action=f"personal_channel.{channel_key.split('_', 1)[0]}.automatic_reply",
        status="delivered",
        registration=registration,
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        detail=f"Automatic {label} personal reply was dispatched immediately.",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "reply_text_length": len(str(outbound.get("text") or "")),
            "dispatched": True,
            "dispatch_external_message_id": str(dispatch_result.get("external_message_id") or "").strip() or None,
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.{channel_key.split('_', 1)[0]}.automatic_reply.delivered:{gateway_id}:{idempotency_key}",
    )
    return {"duplicate": duplicate, "inbound": inbound, "outbound": delivered or outbound}


async def _handle_local_bridge_gateway_channel_inbound(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    payload: Dict[str, Any],
    channel_key: str,
    provider: str,
    label: str,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        channel_key,
        str(payload.get("provider") or provider).strip() or provider,
    )
    trace_id = str(payload.get("trace_id") or "").strip()
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    external_message_id = str(message.get("external_message_id") or "").strip()
    remote_jid = str(message.get("remote_jid") or "").strip()
    text = str(message.get("text") or "").strip()
    media_items = [item for item in (message.get("media") or []) if isinstance(item, dict)]
    if not external_message_id or not remote_jid or (not text and not media_items):
        raise ValueError("channel.inbound requires external_message_id, remote_jid, and (text or media).")
    # A self-chat message (the owner messaging their own Signal "Note to
    # Self" conversation — the local-bridge analog of WhatsApp's/Telegram's
    # is_self_chat command channel) is always from_me too, since only the
    # linked account can post into its own self-conversation. Let it through
    # here exactly like the WhatsApp/Telegram handlers (`from_me and not
    # is_self_chat`) instead of ignoring every from_me message
    # unconditionally — _is_owner_message's is_self_chat shortcut (above)
    # already treats this as the owner regardless of channel, so no other
    # plumbing is needed once a bridge sets it. An ordinary outgoing message
    # to someone else (or to a group) still has is_self_chat=False and is
    # still ignored here. Whether is_self_chat is ever true depends on the
    # specific bridge actually computing it — see signal-cli-bridge.ts's
    # mapSignalCliReceiveNotification for the reference implementation
    # (BlueBubbles/WeChat bridges don't compute it yet, so iMessage/WeChat
    # keep the previous from_me-always-ignored behavior until they do).
    if bool(message.get("from_me")) and not bool(message.get("is_self_chat")):
        return {"ignored": True, "reason": "from_me", "channel_key": channel_key}
    # Resolved BEFORE the group gate (needed by it — group_policy is
    # per-agent config) exactly like the WhatsApp/Telegram handlers resolve
    # agent_id before their own group gate. Unlike those two, there is no
    # in-app configure step that already claimed a row for most local-bridge
    # channels, so this can genuinely still come back unresolved (ambiguous
    # or unclaimed preferred_gateway_id) — see _resolve_local_bridge_agent_id's
    # own docstring for the full contract, including why that now fails
    # CLOSED (denies) rather than open.
    agent_id = await _resolve_local_bridge_agent_id(
        gateway_id=gateway_id, channel_key=channel_key, registration=registration,
    )
    # Group gate: _enforce_group_policy — the ONE shared resolver, replacing
    # what used to be an inline `if is_group and not is_mentioned and not
    # is_reply_to_sage: skip` here — identical contract to the WhatsApp/
    # Telegram handlers. Whether is_group/is_mentioned/is_reply_to_sage are
    # ever true here depends on the specific bridge (signal-cli-bridge.ts,
    # bluebubbles-bridge.ts, or a third-party WeChat bridge) actually
    # computing them — see local-bridge-runtime.ts's mapInboundEvent.
    group_decision = await _enforce_group_policy(
        registration=registration,
        channel_key=channel_key,
        agent_id=agent_id,
        message=message,
        remote_jid=remote_jid,
    )
    if not group_decision["allowed"]:
        return {
            "ignored": True,
            "reason": group_decision["reason"],
            "channel_key": channel_key,
        }
    inbound, created = personal_channels_repository.record_inbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        agent_id=agent_id,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        sender_jid=str(message.get("sender_jid") or "").strip() or None,
        push_name=str(message.get("push_name") or "").strip() or None,
        text=text,
        metadata={
            "provider": provider,
            "received_at": str(message.get("received_at") or "").strip() or None,
            "from_me": bool(message.get("from_me")),
            "agent_computer_bridge": True,
            "media_kinds": [str(item.get("kind") or "").strip() for item in media_items] or None,
        },
    )
    # ── dmPolicy gate ── MUST run before any reply (including a
    # control-command reply) is generated. See _enforce_dm_policy's
    # docstring.
    #
    # existing_state used to be hardcoded None here, with a comment saying
    # local-bridge channels populate no equivalent of WhatsApp's linked_jid
    # so _channel_owner_linked_id returns "" regardless. That was true and
    # it was half the bug: is_self_chat is hardcoded False on the OpenClaw
    # transport ON PURPOSE (normalize_openclaw_gate_facts — a forged one
    # would bypass both gates), so with the state never loaded there was no
    # remaining route by which ANY sender on ANY live channel could be the
    # owner. Loading the row is the other half of the fix; the column it
    # reads is only ever written by a deliberate owner action.
    #
    # Scoped to the SAME (gateway_id, channel_key, agent_id) tuple the row
    # was claimed under a few lines above — never a gateway-wide read, or a
    # second agent on the same box would inherit the first agent's owner.
    # When agent_id is still unresolved (see the comment above), this
    # evaluates through _unresolved_identity_dm_policy_config's hardcoded
    # owner_only with no owner signal available, i.e. every sender is
    # blocked — strictly SAFER than the pre-dmPolicy behavior (reply to
    # everyone, unconditionally), never worse.
    dm_decision = await _enforce_dm_policy(
        registration=registration,
        channel_key=channel_key,
        agent_id=agent_id,
        message=message,
        remote_jid=remote_jid,
        existing_state=personal_channels_repository.get_local_bridge_state(
            str(gateway_id or "").strip(), channel_key=channel_key, agent_id=agent_id,
        ),
        label=label,
    )
    if not dm_decision["allowed"]:
        return await _handle_dm_policy_blocked(
            gateway_id=gateway_id,
            registration=registration,
            inbound=inbound,
            channel_key=channel_key,
            provider=provider,
            agent_id=agent_id,
            external_message_id=external_message_id,
            remote_jid=remote_jid,
            duplicate=not created,
            decision=dm_decision,
            trace_id=trace_id,
        )
    blocked_result = await _control_command_block_result(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        channel_key=channel_key,
        provider=provider,
        agent_id=agent_id,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        text=text,
        is_owner=bool(dm_decision.get("is_owner")),
        duplicate=not created,
        no_reply_prefix=f"{channel_key}:noreply:",
        trace_id=trace_id,
    )
    if blocked_result is not None:
        return blocked_result

    effective_text, attachments = await _process_inbound_media_for_turn(
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        registration=registration,
        agent_id=agent_id,
        text=text,
        media_items=media_items,
    )
    return await _deliver_local_bridge_personal_reply(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        remote_jid=remote_jid,
        external_message_id=external_message_id,
        text=effective_text,
        push_name=str(message.get("push_name") or "").strip() or None,
        duplicate=not created,
        channel_key=channel_key,
        provider=provider,
        label=label,
        # The SAME agent_id record_inbound_message wrote this row under, a
        # few lines above — see this parameter's comment on the callee.
        agent_id=agent_id,
        trace_id=trace_id,
        attachments=attachments,
        is_owner=bool(dm_decision.get("is_owner")),
        # Same forward-compatible defaults as Telegram above: the local
        # bridge (Signal/iMessage/WeChat on Agent Computer) doesn't resolve
        # is_group/chat_title today, so this reads as False/None until that
        # bridge is upgraded — a safe no-op today, not a regression.
        is_group=bool(message.get("is_group")),
        chat_label=str(message.get("chat_title") or "").strip() or None,
        # The specific participant who sent this message — differs from
        # remote_jid inside a group. Feeds ONLY the canonical
        # InboundEnvelope's sender.id (see
        # personal_channel_sage_bridge_service._build_personal_channel_envelope);
        # present on Signal (signal-cli-bridge.ts's `source`) and iMessage
        # (imsg-imessage-runtime.ts's `event.sender_jid`) alike.
        sender_id=str(message.get("sender_jid") or "").strip(),
        # The REAL "was this message actually addressed" fact from the
        # group gate above — see _enforce_group_policy's own docstring.
        was_addressed=group_decision.get("was_addressed"),
    )


async def send_local_bridge_personal_message(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    channel_key: str,
    provider: str,
    remote_jid: str,
    text: str,
    idempotency_key: str,
    reply_to_external_message_id: Optional[str] = None,
    media: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(channel_key, provider)
    outbound, _ = personal_channels_repository.create_or_get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=str(idempotency_key or "").strip(),
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
        metadata={"source": "manual_api", "agent_computer_bridge": True, "media": list(media) if media else None},
    )
    if str(outbound.get("status") or "").strip() == "delivered":
        return outbound

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=f"{str(channel_key or '').strip()}.send",
        request_id=str(idempotency_key or "").strip(),
    )
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        provider=provider,
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        idempotency_key=str(idempotency_key or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
        media=media,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=str(idempotency_key or "").strip(),
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    return delivered or outbound


# recheck_imessage_personal_gateway, install_imessage_imsg_gateway,
# send/get_view/configure/disconnect_whatsapp_personal_gateway,
# dispatch_approved_personal_channel_outbound (already dead — zero
# production callers even before this change), send/get_view/configure/
# disconnect_telegram_personal_gateway, and the Telegram api_id/api_hash
# platform-credential-pool helpers (_platform_telegram_*, gramjs-only,
# meaningless to a Bot API credential) ALL DELETED 2026-08-14 (full
# OpenClaw channel cutover). Git history has them if a first-party
# revival is ever needed.

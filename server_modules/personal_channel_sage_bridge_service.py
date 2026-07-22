from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import logging as _logging
_logger = _logging.getLogger(__name__)

from server_modules.sage_command_dispatcher import (  # noqa: E402
    classify_error,
)
from server_modules.error_notification import classify_error_notification  # noqa: E402

from server_modules import authority_mandate_service
from server_modules import channel_lane_contract_service
from server_modules.channel_adapter import filter_outbound_reply
from server_modules.inbound_envelope import (
    EnvelopeChat,
    EnvelopeSender,
    InboundEnvelope,
    SurfaceKind,
)


def _build_error_reply_dict(
    exc: Exception,
    workspace_id: str,
    *,
    extra: dict | None = None,
) -> dict:
    """Build a SILENT error result for a personal channel turn.

    ABSOLUTE RULE: no hardcoded status/error message may EVER be sent into
    a channel (DM or group). "text" is intentionally left empty here — every
    caller in personal_channels_service.py already treats an empty "text" as
    "no reply" and skips the channel send (marking the inbound message
    processed with an audit event, status="skipped"). The classified message
    is kept under "error_text"/"notification" for logging and any future
    dashboard rendering only — callers must never resurrect a channel-bound
    string from those keys.

    The failure itself is still logged (server logs) and surfaced loudly via
    durability_signal (dashboard/activity feed) so it isn't silently lost —
    it just never reaches the human on the other end of the channel.
    """
    _exc_str = str(exc)
    _classified = classify_error(_exc_str, raw_error=_exc_str)
    _logger.warning(
        "personal channel turn failed for workspace=%s — suppressed from channel: %s",
        workspace_id, _exc_str,
    )
    try:
        from server_modules import durability_signal

        durability_signal.capture_durability_failure(
            f"personal channel turn error suppressed for workspace={workspace_id}",
            exc,
            workspace_id=workspace_id,
            event_class="channel_error_suppressed",
            action="turn_error",
            summary=_exc_str[:500],
        )
    except Exception:
        pass
    result: dict = {
        "text": "",
        "source": "error_classifier",
        "error_text": _classified,
        "notification": classify_error_notification(
            _exc_str, raw_error=_exc_str,
        ).as_dict(),
    }
    if extra:
        result.update(extra)
    return result


_NO_TOOL_RUNTIME_CALLBACKS = (
    "build_direct_chat_tools",
    "build_local_direct_chat_tools",
    "build_builtin_direct_chat_tools",
    "_build_direct_chat_tools",
    "_build_local_direct_chat_tools",
    "_build_builtin_direct_chat_tools",
)


def _personal_channel_no_tools_availability(runtime_context: Dict[str, Any]) -> Dict[str, Any]:
    availability = dict(runtime_context["availability"])
    availability.update(
        {
            "personal_channel_tool_profile": "external_no_tools",
            "tools_allowed": False,
            "tool_capabilities": [],
            "local_gateway_online": False,
            "runtime_ok": False,
            "capability_truth": {
                "my_computer": {
                    "local_tools_available": False,
                    "local_gateway_online": False,
                    "runtime_ok": False,
                },
                "connectors": [],
                "builtin_tools": [],
            },
        }
    )
    return availability


def _personal_channel_no_tools_session_ctx(
    *,
    runtime_context: Dict[str, Any],
    guarded: Any,
    is_owner: bool = False,
) -> Dict[str, Any]:
    session_ctx = dict(runtime_context["session_ctx"])
    session_ctx.update(
        {
            "personal_channel_tool_profile": "external_no_tools",
            "tools_allowed": False,
            # This fallback (mandate hardening report) used to hard-code
            # TIER_AUDIENCE unconditionally because it never resolved a live
            # sender identity. It now receives is_owner from the SAME
            # robust, non-spoofable check the primary unified path uses
            # (personal_channels_service._is_owner_message via
            # _enforce_dm_policy — self-chat or sender matching the
            # channel's linked owner id, NEVER a claimed name/message text)
            # — so a provably-owner turn gets TIER_OWNER here too. Still
            # fails safe: is_owner defaults False -> audience, and tools
            # stay hard-zeroed below regardless of tier (this is a no-tools
            # call either way), so this only ever affects tone/behavior
            # instructions inside handle_sage_chat, never tool access.
            "authority_tier": (
                authority_mandate_service.TIER_OWNER
                if is_owner
                else authority_mandate_service.TIER_AUDIENCE
            ),
        }
    )
    # Only genuinely external/unknown senders get the external_content_guard
    # audit block — an owner turn was never wrapped in the first place (see
    # the is_owner branch in _build_personal_reply below), so there is no
    # wrapper_id/suspicious_patterns to report here.
    if not is_owner and guarded is not None:
        session_ctx["external_content_guard"] = {
            "wrapper_id": guarded.wrapper_id,
            "suspicious_patterns": list(guarded.suspicious_patterns),
            "source": guarded.metadata.source,
            "channel": guarded.metadata.channel,
        }
    return session_ctx


def _owner_provenance_message(
    *,
    raw_text: str,
    display_name: Optional[str],
    channel_label: str,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> str:
    """Clean, unwrapped provenance header for a message ROBUSTLY identified
    as coming from the workspace OWNER's own identity — self-chat, or a
    sender matching the channel's linked owner id (see
    personal_channels_service._is_owner_message). Deliberately NOT the
    same shape as external_content_guard.wrap_external_content: no
    "SECURITY NOTICE", no <<<EXTERNAL_UNTRUSTED_CONTENT>>> markers. The
    owner is not an external/untrusted party — wrapping their own message
    as untrusted data (the bug this fixes) taught the model to distrust its
    own owner's instructions. This is presentation only, never a trust
    boundary: display_name comes from the channel's own push_name field on
    a message already robustly confirmed to be from the owner's linked
    identity, so it cannot be spoofed by a stranger to claim ownership.

    is_group / chat_label: part of the "family group" bug fix. This
    function used to hardcode "direct message" unconditionally, even when
    the caller had ALREADY correctly resolved is_group=True (an owner
    posting inside a group they're a member of — see
    _build_unified_sage_personal_reply_async's is_group docstring). The
    model was then told "From: <name> (owner) · <channel> · direct
    message" for what was actually a message in a shared group with other,
    non-owner participants watching — indistinguishable, from the model's
    point of view, from the owner privately DMing it. That framing is what
    made the agent treat a group turn exactly like a private 1:1 command.
    chat_label (the actual group/channel name, e.g. a Telegram supergroup's
    title) is untrusted, attacker-influenceable text — sanitized/truncated
    by the caller (see _sanitize_channel_label) before it ever reaches
    here; only ever used for a human-readable label, never a trust
    boundary.
    """
    name = str(display_name or "").strip() or "the workspace owner"
    label = str(channel_label or "").strip() or "this channel"
    if is_group:
        group_name = str(chat_label or "").strip()
        where = f'the "{group_name}" group chat' if group_name else "a group chat"
        return (
            f"From: {name} (owner) · {label} · message posted in {where}, "
            "visible to other participants who are NOT the workspace owner\n\n"
            f"{raw_text}"
        )
    return f"From: {name} (owner) · {label} · direct message\n\n{raw_text}"


def _owner_unified_conversation_key(agent_id: str) -> str:
    """Fixed agent_conversation_memory conversation_key for the owner's own
    1:1 thread with this agent — shared across EVERY channel the owner DMs
    it from (Telegram, WhatsApp, WeChat, iMessage, ...) instead of the usual
    per-(channel,chat) silo. One per (workspace, agent): workspace scoping
    already comes from agent_conversation_memory's own directory layout
    (conversation_path nests workspace_id/agent_id/conversation_key.jsonl);
    agent_id is folded into the key literal too so it stays a single
    self-describing string independent of that directory nesting. Empty
    agent_id (the master Sage install) buckets under "_sage", matching
    agent_conversation_memory's own fallback for the directory segment.
    """
    return f"owner:direct:{str(agent_id or '').strip() or '_sage'}"


# Cap on a chat/group label baked into the owner-unified activity feed (see
# the mirrored "[sent to X · Y]" entries in _build_unified_sage_personal_reply_async).
_CHANNEL_LABEL_MAX_CHARS = 60


def _sanitize_channel_label(value: Optional[str]) -> str:
    """Best-effort-clean a human-readable chat/group label before it is
    baked into stored conversation content. Source is e.g. a WhatsApp group
    subject — settable by ANY group member/admin, not just the owner — so
    this is untrusted decorative text, never a trust boundary. Collapses
    newlines/control characters (so it cannot forge a fake line break or
    role marker inside the stored JSONL line) and truncates so one hostile
    group name can't bloat every future turn's context. Returns "" (never
    None) so callers can build a label suffix with a plain truthiness check.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text[:_CHANNEL_LABEL_MAX_CHARS].strip()


def _personal_channel_guard_metadata(
    *, remote_jid: str, is_group: bool, chat_label: Optional[str], include_group_context: bool = True,
) -> Dict[str, Any]:
    """Metadata threaded into external_content_guard.wrap_external_content
    for a non-owner personal-channel sender ("family group" bug fix, other
    half). _owner_provenance_message's is_group/chat_label doc covers the
    OWNER branch; this covers the EXTERNAL/non-owner branch, which needs
    the same explicit "you are one of possibly many people in a shared
    chat" signal — without it, a message that DOES pass the group gate
    (mentioned or a reply to Sage) still reached the model with zero
    indication it was a group message at all: Source/Sender/Channel lines
    only, the same as an ordinary 1:1 stranger DM. Chat-Type/Group-Name are
    deliberately Title-Cased (unlike "remote_jid") to render legibly
    alongside wrap_external_content's own Source:/Sender:/Channel: lines —
    external_content_guard._metadata_lines passes extra dict keys through
    unchanged, no automatic case conversion. chat_label is untrusted,
    attacker-influenceable text (any group member/admin can set a group's
    name) — sanitized/truncated via _sanitize_channel_label before it
    reaches the model, same as everywhere else chat_label is used.

    include_group_context: True (default) keeps the ORIGINAL behavior this
    docstring describes — the legacy no-tools _build_personal_reply()
    fallback still needs it, since that path never reaches
    execute_sage_turn and so has no envelope header to state the group
    context instead. _build_unified_sage_personal_reply_async (the primary
    path — every real personal-channel turn) passes False: the canonical
    InboundEnvelope's rendered header (execute_sage_turn's chokepoint)
    already states the group/channel name and "NOT your owner" once,
    structurally: adding it again here would be exactly the
    "is_group/chat_label text framing" duplication the inbound-envelope
    wiring removes. The SECURITY NOTICE/<<<EXTERNAL_UNTRUSTED_CONTENT>>>
    trust boundary itself, and the plain remote_jid/Sender:/Channel: lines,
    are UNCHANGED either way — this flag only ever affects the two extra
    Chat-Type/Group-Name lines.
    """
    metadata: Dict[str, Any] = {"remote_jid": str(remote_jid or "").strip()}
    if is_group and include_group_context:
        metadata["Chat-Type"] = "group"
        group_name = _sanitize_channel_label(chat_label)
        if group_name:
            metadata["Group-Name"] = group_name
    return metadata


def _build_personal_channel_envelope(
    *,
    surface_channel: str,
    remote_jid: str,
    sender_id: str,
    push_name: Optional[str],
    is_owner: bool,
    is_group: bool,
    chat_label: Optional[str],
) -> InboundEnvelope:
    """Construct the canonical InboundEnvelope (inbound_envelope.py) for a
    personal-channel turn, entirely from signals this bridge ALREADY
    receives from personal_channels_service — never a new/guessed signal.

    platform is surface_channel itself (e.g. "whatsapp_personal",
    "telegram_personal", "signal_personal", "imessage_personal") — already
    exactly the ChannelOrigin string inbound_envelope._PLATFORM_LABELS keys
    off, so no translation is needed.

    Surface derivation, specific to the PERSONAL-channel family (the
    owner's own connected account, never a bot with separate customer
    contacts):
      - is_group=True -> GROUP. addressed=True here is not a guess: every
        group message that reaches this function already passed the
        mention/reply-to-Sage gate in personal_channels_service's inbound
        handlers (_handle_whatsapp_gateway_channel_inbound,
        _handle_telegram_gateway_channel_inbound,
        _handle_local_bridge_gateway_channel_inbound all
        `return {"ignored": ..., "reason": "group_no_mention"}` before ever
        calling this code path), so "the agent was addressed" is an
        already-enforced fact by the time we get here, not an inference.
        Telegram broadcast-channel posts never reach this far either —
        runtime.ts's isBroadcastTelegramChat() hard-drops them gateway-side
        before sender resolution even runs (see the inbound-attribution
        audit) — so SurfaceKind.BROADCAST_CHANNEL is never constructed from
        this bridge; only Telegram's own gateway can ever originate it.
      - is_group=False, envelope-owner is True -> OWNER_SELF_CHAT. On a
        personal channel (the owner's own connected account, never a bot
        with separate contacts) a non-group owner-verified turn is
        structurally the owner's self-chat/Saved-Messages/Note-to-Self
        thread — see personal_channels_service._is_owner_message: an
        ordinary outgoing 1:1 message from that SAME account to a different
        contact never reaches this bridge at all, because every inbound
        handler's "from_me and not is_self_chat" guard drops it first.
      - is_group=False, envelope-owner is False or None -> DM. A
        stranger/contact 1:1 with the owner's own number, or (None) a
        sender this channel could not verify at all — never assumed to be
        a self-chat just because verification is unavailable.

    sender.id: sender_id when the caller resolved one (the actual
    participant — differs from remote_jid inside a group), else remote_jid
    (the 1:1 case, where they're the same JID anyway).

    is_owner is the SAME already-verified bool every other branch of this
    bridge uses (personal_channels_service._is_owner_message via
    _enforce_dm_policy) — never reinterpreted upward; a False here never
    becomes an envelope True. The ONE exception, scoped narrowly to
    iMessage: personal_channels_service's local-bridge dmPolicy gate can
    only ever report is_owner=True via message.get("is_self_chat"), and the
    imsg gateway runtime (empyralis-gateway/src/channels/
    imsg-imessage-runtime.ts, handleInboundNotification) never populates
    that field at all today — so an iMessage False here is not a verified
    "not the owner" signal, it is "this channel could not tell."
    InboundEnvelope.sender.is_owner is tri-state exactly for this case: it
    renders as "unverified, treat as NOT your owner" in the model-facing
    header instead of a flat "NOT your owner," which is the honest state of
    the world. Every gate (envelope_allows_owner_commands) treats False and
    None identically — fail-closed — so this changes nothing about what the
    turn is ALLOWED to do, only what the model is told about why. The
    moment the imsg bridge starts setting is_self_chat for a genuine
    self-chat turn, is_owner=True flows straight through unchanged, exactly
    like it already does for Signal/WhatsApp/Telegram, with no further
    change needed here.
    """
    if is_owner:
        envelope_is_owner: Optional[bool] = True
    elif str(surface_channel or "").strip() == "imessage_personal":
        envelope_is_owner = None
    else:
        envelope_is_owner = False

    resolved_sender_id = str(sender_id or "").strip() or str(remote_jid or "").strip()
    if is_group:
        surface = SurfaceKind.GROUP
        addressed: Optional[bool] = True
        chat = EnvelopeChat(id=str(remote_jid or "").strip(), title=str(chat_label or "").strip())
    else:
        surface = SurfaceKind.OWNER_SELF_CHAT if envelope_is_owner is True else SurfaceKind.DM
        addressed = None
        chat = EnvelopeChat()

    return InboundEnvelope(
        platform=str(surface_channel or "").strip() or "unknown",
        surface=surface,
        sender=EnvelopeSender(
            id=resolved_sender_id,
            display_name=str(push_name or "").strip(),
            is_owner=envelope_is_owner,
            is_bot=False,
        ),
        chat=chat,
        addressed=addressed,
    )


async def _execute_channel_turn_with_envelope(
    *,
    workspace_id: str,
    surface_channel: str,
    remote_jid: str,
    push_name: Optional[str],
    message: str,
    agent_id: str,
    attachments: Optional[List[dict]],
    channel_prior_messages: Optional[List[dict]],
    envelope: Optional[InboundEnvelope],
) -> Dict[str, Any]:
    """Run a personal-channel turn through the real chokepoint
    (sage_turn_adapter.execute_sage_turn) WITH the canonical InboundEnvelope.

    This duplicates sage_turn_adapter.execute_sage_turn_for_channel's own
    body (specialist_context resolution + the individual-parameter call
    into execute_sage_turn) ONLY because that function — frozen, owned by a
    separate build track — doesn't yet accept `envelope=` and forward it
    through. execute_sage_turn itself already fully supports `envelope=`
    (it's what renders the one-line attribution header and gates "/" owner
    commands). Once execute_sage_turn_for_channel grows an `envelope=`
    parameter, this helper can be deleted and personal-channel calls can go
    back to calling execute_sage_turn_for_channel(..., envelope=envelope)
    directly — the parameter mapping below is deliberately kept identical
    to execute_sage_turn_for_channel's so that swap is a pure deletion.
    """
    from server_modules.sage_agent_runtime_contract import SAGE_MODE
    from server_modules.sage_turn_adapter import execute_sage_turn

    normalized_agent_id = str(agent_id or "").strip()
    specialist_context = None
    if normalized_agent_id:
        from server_modules.specialist_runtime_context import resolve_specialist_runtime_context

        try:
            specialist_context = await resolve_specialist_runtime_context(
                workspace_id=workspace_id,
                tenant_id="default",
                active_agent_install_id=normalized_agent_id,
            )
        except Exception:
            specialist_context = None  # fail safe to Sage — same contract as execute_sage_turn_for_channel

    sage_result = await execute_sage_turn(
        workspace_id=workspace_id,
        tenant_id="",
        message=message,
        surface="chat",
        mode=SAGE_MODE,
        current_user=None,
        channel_origin=str(surface_channel or "").strip(),
        channel_sender_id=str(remote_jid or "").strip(),
        channel_sender_name=str(push_name or "").strip(),
        attachments=list(attachments) if attachments else None,
        specialist_context=specialist_context,
        channel_prior_messages=channel_prior_messages,
        envelope=envelope,
    )
    return sage_result.as_dict()


@contextmanager
def _without_direct_chat_runtime_tools(runtime_exports: Any):
    saved = {
        name: getattr(runtime_exports, name)
        for name in _NO_TOOL_RUNTIME_CALLBACKS
        if hasattr(runtime_exports, name)
    }
    for name in saved:
        if "builtin" in name:
            setattr(runtime_exports, name, lambda: [])
        elif "local" in name:
            setattr(runtime_exports, name, lambda _availability: [])
        else:
            setattr(runtime_exports, name, lambda _tool_capabilities: [])
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(runtime_exports, name, value)


def _build_personal_reply(
    *,
    surface_channel: str,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    fallback_label: str,
    source_event_id: Optional[str] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return None
    guarded = None
    if is_owner:
        # OWNER, robustly identified by the caller — clean provenance, no
        # SECURITY NOTICE, no <<<EXTERNAL_UNTRUSTED_CONTENT>>> wrapper. See
        # _build_unified_sage_personal_reply_async's matching branch (the
        # primary path) for the full rationale; this legacy no-tools
        # fallback needs the same fix so a silent-turn retry doesn't
        # re-wrap the owner's own message as untrusted external content.
        # is_group/chat_label: same "family group" bug fix as the primary
        # path — see _owner_provenance_message's docstring.
        turn_message = _owner_provenance_message(
            raw_text=normalized_text, display_name=push_name, channel_label=fallback_label,
            is_group=is_group, chat_label=chat_label,
        )
    else:
        # EXTERNAL / non-owner / unknown sender — UNCHANGED prompt-injection
        # boundary; metadata gained an explicit chat-type/group-name signal
        # (see _personal_channel_guard_metadata's docstring).
        guarded = channel_lane_contract_service.guard_personal_gateway_inbound_message(
            surface_channel=surface_channel,
            text=normalized_text,
            sender=push_name or remote_jid,
            source_event_id=source_event_id,
            metadata=_personal_channel_guard_metadata(
                remote_jid=remote_jid, is_group=is_group, chat_label=chat_label,
            ),
        )
        turn_message = guarded.text
    runtime_context = channel_lane_contract_service.build_personal_gateway_runtime_context(
        surface_channel=surface_channel,
        workspace_id=str(workspace_id or "default").strip() or "default",
        gateway_id=str(gateway_id or "").strip(),
        remote_jid=str(remote_jid or "").strip(),
    )
    try:
        from server_modules import direct_chat_runtime_exports

        with _without_direct_chat_runtime_tools(direct_chat_runtime_exports):
            result = direct_chat_runtime_exports.collect_direct_operator_reply(
                message=turn_message,
                workspace_id=str(workspace_id or "default").strip() or "default",
                requested_model="",
                requested_provider="",
                thread_id=str(runtime_context["thread_id"]),
                prior_messages=[],
                reasoning_effort="",
                availability=_personal_channel_no_tools_availability(runtime_context),
                approved_action=None,
                max_iterations=1,
                session_ctx=_personal_channel_no_tools_session_ctx(
                    runtime_context=runtime_context,
                    guarded=guarded,
                    is_owner=is_owner,
                ),
            )
        # Same [SILENT]/NO_REPLY suppression as the unified path above — this
        # legacy fallback builder must not leak the sentinel either.
        reply = filter_outbound_reply(str((result or {}).get("reply") or "").strip())
        if reply:
            return {
                "text": reply,
                "source": "direct_chat_runtime_exports",
                "raw": dict(result or {}),
            }
    except Exception as _exc:
        _logger.warning(
            "_build_personal_reply failed for channel=%s workspace=%s: %s",
            surface_channel, workspace_id, _exc,
        )
        return _build_error_reply_dict(_exc, workspace_id)
    return None


async def _build_unified_sage_personal_reply_async(
    *,
    surface_channel: str,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    sender_id: str = "",
    fallback_label: str,
    source_event_id: Optional[str] = None,
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Route personal channel messages through the unified Sage turn adapter.

    This ensures channel-originated Sage turns use the same execution path,
    safety rules, context loading, persistence, and audit as /api/sage/chat.
    Falls back to the legacy path on any error.

    agent_id: which specialist install this full-account session is bound
    to — empty means the pre-existing behavior (run as Sage). See
    _execute_channel_turn_with_envelope's own docstring.

    attachments: media-pipeline attachments (image/file kinds) already
    resolved+stored by personal_channel_media_store_service — forwarded
    as-is to _execute_channel_turn_with_envelope.

    sender_id: the specific participant who sent this message (e.g. a
    group's per-message sender_jid, which differs from remote_jid — the
    group's own id) when the caller resolved one; falls back to remote_jid
    (the ordinary 1:1 case, where they're the same JID) when omitted. Feeds
    ONLY the canonical InboundEnvelope's sender.id (see
    _build_personal_channel_envelope) — every other identity/routing use in
    this function still keys off remote_jid, unchanged.

    is_owner: True ONLY when the caller has ROBUSTLY established (see
    personal_channels_service._is_owner_message — self-chat, or a sender
    matching the channel's own linked owner id; NEVER a claimed name or
    message text, which is trivially spoofable) that this inbound message
    is from the workspace owner. Owner turns get a clean, unwrapped message
    (no ad-hoc "From: X (owner) · channel · direct message" prose prefix —
    that used to duplicate exactly what the canonical InboundEnvelope's
    rendered header now states once, at the execute_sage_turn chokepoint)
    instead of external_content_guard's SECURITY NOTICE/
    <<<EXTERNAL_UNTRUSTED_CONTENT>>> wrapper — the owner is not an
    untrusted external party. Every other sender (non-owner DM, group
    member, customer, or anything uncertain) keeps the EXACT prior
    behavior: full external_content_guard wrapping, unchanged. Defaults to
    False so any caller that hasn't threaded a real signal fails to the
    safe/guarded path.

    is_group: True when this turn came from a group/channel chat rather
    than a private 1:1 — caller-resolved (e.g. WhatsApp's
    message.is_group). Together with is_owner this decides conversation
    MEMORY routing (see the agent_conversation_memory block below): only
    an owner turn in a NON-group chat — a real 1:1 DM — uses the
    owner-unified thread; every group turn, even one from the owner,
    keeps the existing per-(channel,chat) silo, because a group is a
    shared space with other, non-owner participants (the same
    prompt-injection boundary commit 2ae01498 hardened for provenance
    above), never folded into the owner's private thread. Defaults to
    False — a caller passing is_owner=True MUST also correctly resolve
    is_group, or a group turn from the owner would be mis-routed into
    the unified thread.

    chat_label: human-readable chat/group name (e.g. a WhatsApp group's
    subject), used only to make the owner-unified activity feed's
    mirrored "[sent to X · Y]" entries legible. Never a trust boundary —
    treated as untrusted, attacker-influenceable text (sanitized +
    truncated before storage, see _sanitize_channel_label). None when
    unavailable; the mirror entry then falls back to fallback_label alone.
    """
    # The CLEAN raw message — never wrapped, never provenance-prefixed.
    # This is what agent_conversation_memory persists below, regardless of
    # which branch builds the actual turn_message sent to the model:
    # storing the SECURITY NOTICE/wrapper-laden guarded.text bloated and
    # garbled the durable conversation history (problem 2 of
    # fix/owner-aware-provenance).
    raw_text = str(text or "").strip()

    if is_owner:
        # OWNER — clean, UNPREFIXED message. This used to call
        # _owner_provenance_message() to hand-build a "From: {name} (owner)
        # · {channel} · direct message"/group-chat prose header — that
        # duplicated exactly the facts the canonical InboundEnvelope (built
        # below) now states once, structurally, in the one-line header
        # execute_sage_turn prepends at its own chokepoint. Removing the
        # duplicate ad-hoc prefix here is the actual envelope wiring; the
        # legacy no-tools _build_personal_reply() fallback above still
        # calls _owner_provenance_message() unchanged, because that path
        # never reaches execute_sage_turn and so has no envelope header to
        # rely on instead.
        turn_message = raw_text
    else:
        # EXTERNAL / non-owner / unknown sender — this is the
        # prompt-injection boundary. The SECURITY NOTICE/wrapper itself is
        # UNCHANGED from prior behavior. The Chat-Type/Group-Name lines
        # _personal_channel_guard_metadata used to add for a group turn are
        # dropped here (include_group_context=False) — the canonical
        # InboundEnvelope built below already states the group/chat name
        # and "NOT your owner" once, in the header execute_sage_turn
        # prepends; duplicating it inside the guard wrapper too is exactly
        # the ad-hoc "is_group/chat_label text framing" this wiring
        # removes. See _personal_channel_guard_metadata's own docstring.
        guarded = channel_lane_contract_service.guard_personal_gateway_inbound_message(
            surface_channel=surface_channel,
            text=raw_text,
            sender=push_name or remote_jid,
            source_event_id=source_event_id,
            metadata=_personal_channel_guard_metadata(
                remote_jid=remote_jid, is_group=is_group, chat_label=chat_label,
                include_group_context=False,
            ),
        )
        turn_message = guarded.text
    if not str(turn_message or "").strip():
        return None

    # Canonical inbound attribution (inbound_envelope.py) — see
    # _build_personal_channel_envelope's own docstring for the surface/
    # is_owner derivation. Rendered into the one-line header and consulted
    # by the code-level owner-command gate at the execute_sage_turn
    # chokepoint (_execute_channel_turn_with_envelope below), never here.
    envelope = _build_personal_channel_envelope(
        surface_channel=surface_channel,
        remote_jid=remote_jid,
        sender_id=sender_id,
        push_name=push_name,
        is_owner=is_owner,
        is_group=is_group,
        chat_label=chat_label,
    )

    # ── Durable per-agent conversation memory (agent_conversation_memory) ──
    # Load recent history BEFORE the turn and hand it to the runtime; persist
    # this exchange AFTER. Per-agent JSONL, fsync'd, survives restarts — the
    # fix for the control-plane thread store being dead under SQLite-fallback
    # prod (every backend restart wiped the in-memory-only turns since
    # ~2026-06-24, so agents "forgot" everything mid-conversation).
    from server_modules import agent_conversation_memory
    _mem_ws = str(workspace_id or "default").strip() or "default"
    _mem_agent = str(agent_id or "").strip()
    _owner_unified_key = _owner_unified_conversation_key(_mem_agent)
    # Owner-unified 1:1 key (fix/unified-owner-memory): a message ROBUSTLY
    # identified as the owner's own (is_owner — see this function's
    # docstring) AND NOT inside a group uses ONE fixed, channel-agnostic key
    # instead of the usual per-(channel,chat) silo, so the owner's
    # DM-with-agent on Telegram/WhatsApp/WeChat/iMessage is a single
    # continuous thread the agent can recall from any of them — this is the
    # fix for "the agent denies its own actions when asked from a different
    # channel/DM": before this, every (channel, chat) was a disjoint JSONL
    # file with no shared owner thread at all. Groups stay isolated even
    # when the owner is the sender (a group is a shared space with other,
    # non-owner participants — never folded into the owner's private
    # thread), and every non-owner sender keeps the EXACT prior per-silo
    # behavior. Gate for LOAD is enforced by construction here: a non-owner
    # turn's _mem_key can only ever resolve to the per-silo branch below, so
    # it never loads the owner-unified key as context (never leaks the
    # owner's private cross-channel thread to a stranger).
    _is_owner_direct_dm = bool(is_owner) and not bool(is_group)
    _mem_key = (
        _owner_unified_key
        if _is_owner_direct_dm
        else f"{surface_channel}:{str(remote_jid or '').strip()}"
    )
    try:
        _mem_prior = agent_conversation_memory.load_recent_turns(
            workspace_id=_mem_ws, agent_id=_mem_agent, conversation_key=_mem_key,
        )
    except Exception:
        _mem_prior = []

    try:
        result = await _execute_channel_turn_with_envelope(
            workspace_id=_mem_ws,
            surface_channel=surface_channel,
            remote_jid=str(remote_jid or "").strip(),
            push_name=push_name,
            message=turn_message,
            agent_id=_mem_agent,
            attachments=list(attachments) if attachments else None,
            channel_prior_messages=_mem_prior,
            envelope=envelope,
        )
        # Suppress the runtime's [SILENT]/NO_REPLY sentinels via the shared
        # filter — the personal-channel path (unlike direct_chat/hosted)
        # never applied it, so a "stay quiet" turn was shipped verbatim,
        # posting a literal "[SILENT]" into the chat (esp. group chats where
        # most messages aren't for the agent). None here → every handler's
        # existing empty-reply skip path fires (no outbound, no dispatch).
        reply = filter_outbound_reply(str((result or {}).get("message") or "").strip())
        # Outbound attachments queued by send_image / generate_image's
        # auto-attach this turn (see sage_agent_runtime_service.py's
        # session_ctx["pending_outbound_media"] and SageTurnResult.media).
        # Threaded through even on an otherwise-empty reply — a media-only
        # turn ("send me that photo back") must not be treated as silence.
        media = list((result or {}).get("media") or [])
        # Record the turn so the NEXT message has continuity — the user's
        # message always (even on a silent turn), the assistant reply only when
        # it actually spoke. Best-effort: a memory write must never sink a reply.
        # Stores raw_text (the CLEAN original message) — never turn_message,
        # which for an external sender carries the SECURITY NOTICE and
        # <<<EXTERNAL_UNTRUSTED_CONTENT>>> wrapper markers. Persisting the
        # wrapped form bloated/garbled the durable history on every replay
        # (problem 2 of fix/owner-aware-provenance); the model still sees
        # the appropriately-provenanced/guarded turn_message for THIS turn,
        # only the stored memory changes.
        # Sender attribution for GROUP-silo storage only: prefix the stored
        # user turn with "{push_name}: " so a replayed group transcript
        # shows who said what — a bare per-(channel,chat) silo otherwise has
        # no record of which of several group participants sent a given
        # line. Never applied to a 1:1 (owner-unified or per-silo), where
        # there's exactly one counterpart and a prefix would just be noise.
        _mem_user_content = raw_text
        if is_group:
            _push_name_clean = str(push_name or "").strip()
            if _push_name_clean:
                _mem_user_content = f"{_push_name_clean}: {raw_text}"
        try:
            agent_conversation_memory.append_turn(
                workspace_id=_mem_ws, agent_id=_mem_agent,
                conversation_key=_mem_key, role="user", content=_mem_user_content,
                # Per-turn provenance (audit Part 3 #1 — the cheapest unlock):
                # thread the SAME canonical InboundEnvelope built above into
                # append_turn's previously-unused `metadata` param, via its own
                # to_metadata() serializer, so a recalled turn from
                # load_recent_turns carries WHO sent it (platform/surface/
                # sender/is_owner) instead of only the group-silo's hand-built
                # "{push_name}: " text prefix. Only the inbound/user turn gets
                # this — the assistant's own reply below isn't "said by" the
                # channel's sender, so tagging it with the SAME envelope would
                # misattribute the agent's own words as the sender's.
                metadata=envelope.to_metadata(),
            )
            # A media-only turn (no text, but send_image/generate_image
            # queued an attachment — see `media` above) is still a real sent
            # turn and must still be recorded: gating this purely on `reply`
            # (as before outbound media existed) silently dropped every
            # media-only send from BOTH this channel's own continuity memory
            # and the owner-unified activity log below — directly
            # undermining the "every send is mirrored" / "the agent knows
            # its own actions" guarantee this whole block exists for.
            # content falls back to a plain attachment summary so the stored
            # line is never blank.
            if reply or media:
                _mem_assistant_content = reply or (
                    "[sent " + ", ".join(sorted({str(item.get("kind") or "file") for item in media})) + "]"
                )
                agent_conversation_memory.append_turn(
                    workspace_id=_mem_ws, agent_id=_mem_agent,
                    conversation_key=_mem_key, role="assistant", content=_mem_assistant_content,
                )
                # Mirror every outbound send into the owner-unified key,
                # tagged by destination — but only when this turn's OWN
                # storage isn't already that key (an owner-direct-DM turn
                # IS the unified thread already; mirroring it back into
                # itself would just duplicate the line right below it).
                # This is what turns the owner's own thread into a standing
                # activity log of every reply the agent sent anywhere — a
                # group, a stranger's DM, WeChat/iMessage/Signal — so the
                # owner can ask from ANY of their own channels "what did you
                # send in X" and the answer is in their own confirmed
                # history. Only ever the ASSISTANT'S OWN reply text — never
                # another participant's inbound message — so this can never
                # leak a group member's or stranger's content into the
                # owner's feed, only a record of what the agent itself did.
                # Safe regardless of who triggered THIS turn (is_owner True
                # or False): logging the agent's own output is not the
                # sender's private data to protect, and the LOAD gate above
                # already ensures only an owner-identified turn ever reads
                # this key back out — a stranger's turn writes here (as the
                # destination of an agent reply, e.g. a DM to that same
                # stranger) but can never load it.
                if _mem_key != _owner_unified_key:
                    _label = _sanitize_channel_label(chat_label)
                    _destination = f"{fallback_label}{' · ' + _label if _label else ''}"
                    agent_conversation_memory.append_turn(
                        workspace_id=_mem_ws, agent_id=_mem_agent,
                        conversation_key=_owner_unified_key, role="assistant",
                        content=f"[sent to {_destination}] {_mem_assistant_content}",
                    )
        except Exception:
            pass
        # A media-only turn (no text, but send_image/generate_image queued an
        # attachment) still counts as "something to deliver" — only a truly
        # empty turn (no text AND no media) falls through to None/skipped.
        # `reply or ""`: filter_outbound_reply returns None (not "") for
        # empty/suppressed text, and every downstream reader of this dict's
        # "text" key expects a string it can safely .strip() — a media-only
        # reply is the one new case that can reach here with reply is None.
        if reply or media:
            return {
                "text": reply or "",
                "source": "sage_turn_adapter",
                "trace_id": (result or {}).get("trace_id", ""),
                "media": media,
                "raw": dict(result or {}),
            }
    except Exception as _exc:
        _logger.warning(
            "_build_unified_sage_personal_reply_async failed for workspace=%s channel=%s: %s",
            workspace_id, surface_channel, _exc
        )
        return _build_error_reply_dict(_exc, workspace_id)

    return None


def _build_unified_sage_personal_reply(
    *,
    surface_channel: str,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    sender_id: str = "",
    fallback_label: str,
    source_event_id: Optional[str] = None,
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _build_unified_sage_personal_reply_async(
                surface_channel=surface_channel,
                workspace_id=workspace_id,
                gateway_id=gateway_id,
                remote_jid=remote_jid,
                text=text,
                push_name=push_name,
                sender_id=sender_id,
                fallback_label=fallback_label,
                source_event_id=source_event_id,
                agent_id=agent_id,
                attachments=attachments,
                is_owner=is_owner,
                is_group=is_group,
                chat_label=chat_label,
            )
        )

    holder: dict[str, Any] = {"result": None, "error": None}

    def _runner() -> None:
        try:
            holder["result"] = asyncio.run(
                _build_unified_sage_personal_reply_async(
                    surface_channel=surface_channel,
                    workspace_id=workspace_id,
                    gateway_id=gateway_id,
                    remote_jid=remote_jid,
                    text=text,
                    push_name=push_name,
                    sender_id=sender_id,
                    fallback_label=fallback_label,
                    source_event_id=source_event_id,
                    agent_id=agent_id,
                    attachments=attachments,
                    is_owner=is_owner,
                    is_group=is_group,
                    chat_label=chat_label,
                )
            )
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if holder["error"] is not None:
        raise holder["error"]
    return holder["result"]


async def build_whatsapp_personal_reply_async(
    *,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    sender_id: str = "",
    source_event_id: Optional[str] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        unified = await _build_unified_sage_personal_reply_async(
            surface_channel="whatsapp_personal",
            workspace_id=workspace_id,
            gateway_id=gateway_id,
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            sender_id=sender_id,
            fallback_label="WhatsApp",
            source_event_id=source_event_id,
            is_owner=is_owner,
            is_group=is_group,
            chat_label=chat_label,
        )
        return unified
    except Exception as _exc:
        # ABSOLUTE RULE: no hardcoded status/error message may EVER be sent
        # into a channel. This used to also fire a direct cloud-session
        # dispatch of the classified error text — that bypassed every
        # reply object and every filter and sent a canned string straight
        # into the channel (DM or group). _build_error_reply_dict() below
        # already logs + surfaces this on the dashboard/activity feed and
        # returns text="" so the caller treats it as no-reply. Do not
        # resurrect a channel send here.
        return _build_error_reply_dict(_exc, workspace_id)


async def build_telegram_personal_reply_async(
    *,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    sender_id: str = "",
    source_event_id: Optional[str] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        unified = await _build_unified_sage_personal_reply_async(
            surface_channel="telegram_personal",
            workspace_id=workspace_id,
            gateway_id=gateway_id,
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            sender_id=sender_id,
            fallback_label="Telegram",
            source_event_id=source_event_id,
            is_owner=is_owner,
            is_group=is_group,
            chat_label=chat_label,
        )
        return unified
    except Exception as _exc:
        # ABSOLUTE RULE: no hardcoded status/error message may EVER be sent
        # into a channel. This used to also fire a direct cloud-session
        # dispatch of SAGE_ERROR_REPLY — that bypassed every reply object
        # and every filter and sent a canned string straight into the
        # channel (DM or group). Log + surface on the dashboard/activity
        # feed instead; the channel gets nothing.
        _build_error_reply_dict(_exc, workspace_id)
        return None


async def build_discord_personal_reply_async(
    *,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    sender_id: str = "",
    source_event_id: Optional[str] = None,
    linked_user_name: Optional[str] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a Sage reply for a Discord personal DM.

    Follows the same unified path as WhatsApp and Telegram personal channels.
    linked_user_name is accepted for future identity-context injection but
    not yet threaded into _build_unified_sage_personal_reply_async.

    is_owner: no live caller resolves this for Discord personal DMs today
    (see personal_channels_service — Discord DMs currently route through
    discord_connector.py's own execute_sage_turn path, not this bridge) so
    it defaults to False/guarded. Accepted here for signature parity with
    the other build_*_personal_reply_async functions and so a future caller
    that DOES resolve Discord owner identity can thread it through with no
    further changes to this function.
    """
    try:
        unified = await _build_unified_sage_personal_reply_async(
            surface_channel="discord_personal",
            workspace_id=workspace_id,
            gateway_id=gateway_id,
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            sender_id=sender_id,
            fallback_label="Discord",
            source_event_id=source_event_id,
            is_owner=is_owner,
            is_group=is_group,
            chat_label=chat_label,
        )
        return unified
    except Exception as _exc:
        _logger.warning(
            "Discord DM turn failed for workspace=%s: %s",
            workspace_id, _exc
        )
        return _build_error_reply_dict(_exc, workspace_id)


async def build_personal_channel_reply_async(
    *,
    surface_channel: str,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    sender_id: str = "",
    fallback_label: str = "channel",
    source_event_id: Optional[str] = None,
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        unified = await _build_unified_sage_personal_reply_async(
            surface_channel=surface_channel,
            workspace_id=workspace_id,
            gateway_id=gateway_id,
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            sender_id=sender_id,
            fallback_label=fallback_label,
            source_event_id=source_event_id,
            attachments=attachments,
            is_owner=is_owner,
            is_group=is_group,
            chat_label=chat_label,
        )
        return unified
    except Exception as _exc:
        _logger.warning(
            "Personal channel turn failed for channel=%s workspace=%s: %s",
            surface_channel, workspace_id, _exc
        )
        return _build_error_reply_dict(_exc, workspace_id)


def build_whatsapp_personal_reply(
    *,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    sender_id: str = "",
    source_event_id: Optional[str] = None,
    linked_user_name: Optional[str] = None,
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a reply for a WhatsApp personal DM — as the specialist agent_id
    names (see _execute_channel_turn_with_envelope), or as Sage when
    agent_id is empty (pre-existing behavior).

    linked_user_name is accepted for future identity-context injection but
    not yet threaded into _build_unified_sage_personal_reply (same as
    build_discord_personal_reply_async's linked_user_name parameter above).

    attachments: media-pipeline attachments (image/file kinds) already
    resolved+stored by personal_channel_media_store_service.

    sender_id: the specific participant's jid (differs from remote_jid
    inside a group) — see _build_personal_channel_envelope's docstring;
    falls back to remote_jid when omitted.

    is_owner: caller-resolved via personal_channels_service._is_owner_message
    (self-chat, or sender matching this channel's linked owner id) — see
    _build_unified_sage_personal_reply_async's docstring for the full
    contract. Defaults to False (guarded/external), matching every other
    build_*_personal_reply* entry point.

    is_group / chat_label: caller-resolved group signal + human-readable
    chat/group label — see _build_unified_sage_personal_reply_async's
    docstring for the owner-unified-memory contract they feed.
    """
    unified = _build_unified_sage_personal_reply(
        surface_channel="whatsapp_personal",
        workspace_id=workspace_id,
        gateway_id=gateway_id,
        remote_jid=remote_jid,
        text=text,
        push_name=push_name,
        sender_id=sender_id,
        fallback_label="WhatsApp",
        source_event_id=source_event_id,
        agent_id=agent_id,
        attachments=attachments,
        is_owner=is_owner,
        is_group=is_group,
        chat_label=chat_label,
    )
    if unified is not None:
        return unified
    return _build_personal_reply(
        surface_channel="whatsapp_personal",
        workspace_id=workspace_id,
        gateway_id=gateway_id,
        remote_jid=remote_jid,
        text=text,
        push_name=push_name,
        fallback_label="WhatsApp",
        source_event_id=source_event_id,
        is_owner=is_owner,
        is_group=is_group,
        chat_label=chat_label,
    )


def build_telegram_personal_reply(
    *,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    sender_id: str = "",
    source_event_id: Optional[str] = None,
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a reply for a Telegram personal DM — see build_whatsapp_personal_reply's
    docstring for the agent_id, sender_id, is_owner, is_group and chat_label
    contracts."""
    unified = _build_unified_sage_personal_reply(
        surface_channel="telegram_personal",
        workspace_id=workspace_id,
        gateway_id=gateway_id,
        remote_jid=remote_jid,
        text=text,
        push_name=push_name,
        sender_id=sender_id,
        fallback_label="Telegram",
        source_event_id=source_event_id,
        agent_id=agent_id,
        attachments=attachments,
        is_owner=is_owner,
        is_group=is_group,
        chat_label=chat_label,
    )
    if unified is not None:
        return unified
    return _build_personal_reply(
        surface_channel="telegram_personal",
        workspace_id=workspace_id,
        gateway_id=gateway_id,
        remote_jid=remote_jid,
        text=text,
        push_name=push_name,
        fallback_label="Telegram",
        source_event_id=source_event_id,
        is_owner=is_owner,
        is_group=is_group,
        chat_label=chat_label,
    )

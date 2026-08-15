from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import logging as _logging
_logger = _logging.getLogger(__name__)

from server_modules.sage_command_dispatcher import (  # noqa: E402
    classify_error,
)
from server_modules.error_notification import classify_error_notification  # noqa: E402

from server_modules import channel_lane_contract_service
from server_modules.channel_adapter import (
    DELIVERY_FAILED_CODE_KEY,
    DELIVERY_FAILED_KEY,
    filter_outbound_reply,
)
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
    """Build a FAILED (not silent) error result for a personal channel turn.

    ABSOLUTE RULE: no hardcoded status/error message may EVER be sent into
    a channel (DM or group). "text" is intentionally left empty here — no
    delivery seam may ever resurrect a channel-bound string from
    "error_text"/"notification" (the latter carries
    ErrorNotification.raw_detail, i.e. the RAW exception text).

    But an empty "text" alone used to be a LIE by omission. Every delivery
    seam read it as "the agent had nothing to say" and wrote the
    `<channel>:noreply:` marker — the durable record that the agent was
    asked and DELIBERATELY chose silence. It had not: the turn never
    completed. Two things followed from that one conflation:

        - the person got nothing and was told nothing, and
        - the marker disarmed the ONLY retry the message would ever get.
          `channel.inbound` is at-least-once on every leg (ws-client's
          replayable outbox, local-bridge-runtime's in-memory seen-set, the
          OpenClaw plugin's durable BoundedRetryQueue) and that marker is
          exactly what the redelivery guard at the top of
          _deliver_local_bridge_personal_reply reads. A message the platform
          failed to answer was therefore permanently recorded as answered.

    So the result now also carries channel_adapter.DELIVERY_FAILED_KEY, and
    channel_adapter.resolve_channel_reply_outcome() turns that into
    kind="undelivered" — no no-reply marker, an honest audit row, a retry
    that can still happen, and (for the OWNER only) a frozen
    CHANNEL_EXECUTION_FAILED literal so their own agent does not read as
    simply ignoring them. See that function and
    platform_event.CHANNEL_OWNER_SAFE_CODES.

    The failure is still logged (server logs) and surfaced loudly via
    durability_signal (dashboard/activity feed), unchanged.
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
    from server_modules import platform_event as _platform_event

    result: dict = {
        "text": "",
        "source": "error_classifier",
        "error_text": _classified,
        "notification": classify_error_notification(
            _exc_str, raw_error=_exc_str,
        ).as_dict(),
        # "the turn did not complete" — NOT "the agent chose silence".
        # A stable CODE, never the classified prose: the owner-visible
        # literal is looked up from the frozen PlatformEvent registry by
        # this code (platform_event.owner_channel_text_for_code), so no part
        # of _exc_str, _classified, or notification["raw_detail"] can reach
        # a channel through it.
        DELIVERY_FAILED_KEY: True,
        DELIVERY_FAILED_CODE_KEY: _platform_event.CHANNEL_EXECUTION_FAILED.code,
    }
    if extra:
        result.update(extra)
    return result


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
    *, remote_jid: str, is_group: bool, chat_label: Optional[str],
) -> Dict[str, Any]:
    """Metadata threaded into external_content_guard.wrap_external_content
    for a non-owner personal-channel sender. chat_label is untrusted,
    attacker-influenceable text (any group member/admin can set a group's
    name) — sanitized/truncated via _sanitize_channel_label before it
    reaches the model, same as everywhere else chat_label is used.

    The group signal itself (Chat-Type / Group-Name) is deliberately NOT
    added here any more: the canonical InboundEnvelope's rendered header
    (execute_sage_turn's chokepoint) already states the group/channel name
    and "NOT your owner" once, structurally, and duplicating it inside the
    guard wrapper is exactly the ad-hoc text framing the inbound-envelope
    wiring removed. An `include_group_context=True` branch used to keep the
    old behavior for the legacy no-tools _build_personal_reply() fallback;
    that fallback is gone (it was overriding the agent's decision to stay
    silent with a second, unguarded LLM turn), so the flag had exactly one
    caller passing exactly one value and has been collapsed away. The
    SECURITY NOTICE / <<<EXTERNAL_UNTRUSTED_CONTENT>>> trust boundary and
    the plain remote_jid/Sender:/Channel: lines are UNCHANGED.
    """
    return {"remote_jid": str(remote_jid or "").strip()}


def _build_personal_channel_envelope(
    *,
    surface_channel: str,
    remote_jid: str,
    sender_id: str,
    push_name: Optional[str],
    is_owner: bool,
    is_group: bool,
    chat_label: Optional[str],
    was_addressed: Optional[bool] = None,
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
      - is_group=True -> GROUP. addressed=was_addressed, the caller-resolved
        REAL fact (personal_channels_service._enforce_group_policy's own
        result — an explicit mention or reply-to-agent), never a blanket
        True.
        UPDATED 2026-07-23 (group_policy build): before this build,
        addressed=True here WAS a safe hardcode — every group message that
        reached this function had already passed a hard, non-configurable
        mention/reply-to-Sage gate in personal_channels_service's inbound
        handlers (`return {"ignored": ..., "reason": "group_no_mention"}`
        before ever calling this code path), so "the agent was addressed"
        was an already-enforced fact, not an inference. That invariant no
        longer holds: requireMention now defaults OFF (Ruling A, "see-and-
        decide"), so an unaddressed group message routinely reaches this
        function too — was_addressed must be the caller's REAL, honest
        fact so the model's own judgment (Ruling A's actual mechanism,
        rendered via inbound_envelope.render_envelope_header's "you were
        not addressed — observe..." branch) has accurate group context to
        decide from, not a false "you were addressed directly" claim.
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
        addressed: Optional[bool] = was_addressed
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
    from server_modules.sage_reply_dispatcher import acquire_channel_turn_lock

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

    # Serialize per (workspace, personal-channel thread) so only ONE turn
    # executes at a time for a given DM/group — the SAME protection
    # dispatch_sage_reply already gives hosted-bot/WeChat-official channels
    # (sage_reply_dispatcher._CHANNEL_TURN_LOCKS), which personal channels
    # never had: this is the sole chokepoint every personal-channel reply
    # (WhatsApp, Telegram-personal, Discord DMs, local-bridge/OpenClaw)
    # crosses before calling execute_sage_turn, so patching here closes it
    # for the whole family at once. Without this, two messages arriving
    # close together on one thread started two concurrent turns reading the
    # same thread history — the torn-history bug. Personal channels have no
    # explicit thread_id of their own; (surface_channel, remote_jid)
    # together are what actually identify a conversation here, namespaced
    # under "personal:" so this can never collide with a hosted-bot
    # thread_id (typically the literal string "sage-main") in the same
    # shared lock dict for the same workspace.
    thread_lock_key = f"personal:{str(surface_channel or '').strip()}:{str(remote_jid or '').strip()}"
    lock = await acquire_channel_turn_lock(workspace_id, thread_lock_key)
    async with lock:
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
    was_addressed: Optional[bool] = None,
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

    was_addressed: the REAL, caller-resolved "was this group message
    actually addressed" fact (personal_channels_service._enforce_group_policy's
    own result — an explicit mention or reply-to-agent) — feeds ONLY the
    canonical InboundEnvelope's `addressed` field (see
    _build_personal_channel_envelope's docstring). None for a non-group
    turn (not applicable) or when the caller hasn't resolved a real fact
    (renders as "unverified" in the header, never as a false "you were
    addressed directly" claim). Defaults to None deliberately — unlike
    is_owner/is_group, there is no safe non-None default here: with
    requireMention OFF by default, a group turn reaching this function no
    longer implies it was addressed.
    """
    # The CLEAN raw message — never wrapped, never provenance-prefixed.
    # This is what agent_conversation_memory persists below, regardless of
    # which branch builds the actual turn_message sent to the model:
    # storing the SECURITY NOTICE/wrapper-laden guarded.text bloated and
    # garbled the durable conversation history (problem 2 of
    # fix/owner-aware-provenance).
    raw_text = str(text or "").strip()

    if is_owner:
        # OWNER — clean, UNPREFIXED message. This used to hand-build a
        # "From: {name} (owner) · {channel} · direct message"/group-chat
        # prose header — that duplicated exactly the facts the canonical
        # InboundEnvelope (built below) now states once, structurally, in
        # the one-line header execute_sage_turn prepends at its own
        # chokepoint. Removing the duplicate ad-hoc prefix here is the
        # actual envelope wiring.
        turn_message = raw_text
    else:
        # EXTERNAL / non-owner / unknown sender — this is the
        # prompt-injection boundary. The SECURITY NOTICE/wrapper itself is
        # UNCHANGED from prior behavior. The Chat-Type/Group-Name lines
        # _personal_channel_guard_metadata used to add for a group turn are
        # dropped — the canonical InboundEnvelope built below already states
        # the group/chat name and "NOT your owner" once, in the header
        # execute_sage_turn prepends; duplicating it inside the guard
        # wrapper too is exactly the ad-hoc "is_group/chat_label text
        # framing" this wiring removes. See _personal_channel_guard_metadata.
        guarded = channel_lane_contract_service.guard_personal_gateway_inbound_message(
            surface_channel=surface_channel,
            text=raw_text,
            sender=push_name or remote_jid,
            source_event_id=source_event_id,
            metadata=_personal_channel_guard_metadata(
                remote_jid=remote_jid, is_group=is_group, chat_label=chat_label,
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
        was_addressed=was_addressed,
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


# build_whatsapp_personal_reply_async / build_whatsapp_personal_reply DELETED
# 2026-08-15. Both hardcoded `surface_channel="whatsapp_personal"`, a key the
# 2026-08-14 OpenClaw cutover removed from the lane contract along with the
# Baileys runtime, every WhatsApp inbound handler and the catalog entry — and
# `cloud-session-manager/`, the one other producer of a first-party personal
# key at the time, was telegram-only (and is itself deleted as of 2026-08-15,
# see the note below), so nothing anywhere can produce that key. They had
# ZERO callers and were fully unit-tested: exactly
# the "built, tested, and never wired" shape CLAUDE.md names as this
# codebase's most common defect, one step worse than usual because the key
# they were wired to no longer exists. Their real subject — a WhatsApp turn
# through the unified path — is now asserted against
# `build_personal_channel_reply_async` with the live `openclaw_whatsapp` key,
# which is the function personal_channels_service actually calls.
#
# Deleted rather than repointed at `openclaw_whatsapp`: a per-platform builder
# IS the thing the generic builder replaced. Nine more would be needed the day
# OpenClaw ships nine more channels, which is the hand-maintained channel list
# this transport was adopted to stop writing.


# build_telegram_personal_reply_async / build_telegram_personal_reply DELETED
# 2026-08-15, together with the sync `_build_unified_sage_personal_reply`
# wrapper above whose only caller the second one was.
#
# Both hardcoded `surface_channel="telegram_personal"`, and the ONLY thing
# that still produced that key was the cloud-session lane — a second,
# cloud-hosted gramjs Telegram ACCOUNT runtime (`cloud-session-manager/`) the
# 2026-08-14 OpenClaw cutover missed because nothing traced it. That lane was
# deleted whole on 2026-08-15 (route, handler, outbound dispatcher, both
# proactive callers, config flag, directory), so these two are in exactly the
# position their WhatsApp twins were in above: pointed at a key nothing can
# produce.
#
# The sync one had ALREADY had zero production callers before that, and its
# own docstring said to delete it together with the sync unified wrapper once
# the ~20 tests driving the unified path through it were retargeted onto the
# async builder. They have been.
#
# Deleted rather than repointed at `openclaw_telegram`, same reasoning as the
# WhatsApp pair: a per-platform builder IS the thing the generic builder
# replaced, and `build_personal_channel_reply_async` is what
# personal_channels_service actually calls for every OpenClaw channel.


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
    was_addressed: Optional[bool] = None,
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

    was_addressed: no live caller resolves this for Discord yet either
    (no group_policy/mention-gating equivalent wired up for Discord) — see
    _build_personal_channel_envelope's docstring for the contract once one is
    (it used to point at build_telegram_personal_reply, deleted 2026-08-15).
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
            was_addressed=was_addressed,
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
    was_addressed: Optional[bool] = None,
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
            was_addressed=was_addressed,
        )
        return unified
    except Exception as _exc:
        _logger.warning(
            "Personal channel turn failed for channel=%s workspace=%s: %s",
            surface_channel, workspace_id, _exc
        )
        return _build_error_reply_dict(_exc, workspace_id)

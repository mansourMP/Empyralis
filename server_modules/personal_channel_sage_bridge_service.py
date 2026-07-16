from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import logging as _logging
_logger = _logging.getLogger(__name__)

from server_modules.sage_command_dispatcher import (  # noqa: E402
    SAGE_ERROR_REPLY,
    classify_error,
)
from server_modules.error_notification import classify_error_notification  # noqa: E402

from server_modules import authority_mandate_service
from server_modules import channel_lane_contract_service
from server_modules.channel_adapter import filter_outbound_reply


def _build_error_reply_dict(
    exc: Exception,
    workspace_id: str,
    *,
    extra: dict | None = None,
) -> dict:
    """Build a classified error return dict with notification payload."""
    _exc_str = str(exc)
    result: dict = {
        "text": classify_error(_exc_str, raw_error=_exc_str),
        "source": "error_classifier",
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
    """
    name = str(display_name or "").strip() or "the workspace owner"
    label = str(channel_label or "").strip() or "this channel"
    return f"From: {name} (owner) · {label} · direct message\n\n{raw_text}"


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
        turn_message = _owner_provenance_message(
            raw_text=normalized_text, display_name=push_name, channel_label=fallback_label,
        )
    else:
        # EXTERNAL / non-owner / unknown sender — UNCHANGED prompt-injection
        # boundary.
        guarded = channel_lane_contract_service.guard_personal_gateway_inbound_message(
            surface_channel=surface_channel,
            text=normalized_text,
            sender=push_name or remote_jid,
            source_event_id=source_event_id,
            metadata={"remote_jid": str(remote_jid or "").strip()},
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
    fallback_label: str,
    source_event_id: Optional[str] = None,
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Route personal channel messages through the unified Sage turn adapter.

    This ensures channel-originated Sage turns use the same execution path,
    safety rules, context loading, persistence, and audit as /api/sage/chat.
    Falls back to the legacy path on any error.

    agent_id: which specialist install this full-account session is bound
    to — empty means the pre-existing behavior (run as Sage). See
    sage_turn_adapter.execute_sage_turn_for_channel's own docstring.

    attachments: media-pipeline attachments (image/file kinds) already
    resolved+stored by personal_channel_media_store_service — forwarded
    as-is to execute_sage_turn_for_channel.

    is_owner: True ONLY when the caller has ROBUSTLY established (see
    personal_channels_service._is_owner_message — self-chat, or a sender
    matching the channel's own linked owner id; NEVER a claimed name or
    message text, which is trivially spoofable) that this inbound message
    is from the workspace owner. Owner turns get a clean, unwrapped
    provenance header instead of external_content_guard's SECURITY
    NOTICE/<<<EXTERNAL_UNTRUSTED_CONTENT>>> wrapper — the owner is not an
    untrusted external party. Every other sender (non-owner DM, group
    member, customer, or anything uncertain) keeps the EXACT prior
    behavior: full external_content_guard wrapping, unchanged. Defaults to
    False so any caller that hasn't threaded a real signal fails to the
    safe/guarded path.
    """
    from server_modules.sage_turn_adapter import execute_sage_turn_for_channel

    # The CLEAN raw message — never wrapped, never provenance-prefixed.
    # This is what agent_conversation_memory persists below, regardless of
    # which branch builds the actual turn_message sent to the model:
    # storing the SECURITY NOTICE/wrapper-laden guarded.text bloated and
    # garbled the durable conversation history (problem 2 of
    # fix/owner-aware-provenance).
    raw_text = str(text or "").strip()

    if is_owner:
        # OWNER — clean provenance + full trust. No SECURITY NOTICE, no
        # untrusted-content wrapper markers.
        turn_message = _owner_provenance_message(
            raw_text=raw_text, display_name=push_name, channel_label=fallback_label,
        )
    else:
        # EXTERNAL / non-owner / unknown sender — this is the
        # prompt-injection boundary. UNCHANGED from prior behavior.
        guarded = channel_lane_contract_service.guard_personal_gateway_inbound_message(
            surface_channel=surface_channel,
            text=raw_text,
            sender=push_name or remote_jid,
            source_event_id=source_event_id,
            metadata={"remote_jid": str(remote_jid or "").strip()},
        )
        turn_message = guarded.text
    if not str(turn_message or "").strip():
        return None

    # ── Durable per-agent conversation memory (agent_conversation_memory) ──
    # Load recent history BEFORE the turn and hand it to the runtime; persist
    # this exchange AFTER. Per-agent JSONL, fsync'd, survives restarts — the
    # fix for the control-plane thread store being dead under SQLite-fallback
    # prod (every backend restart wiped the in-memory-only turns since
    # ~2026-06-24, so agents "forgot" everything mid-conversation).
    from server_modules import agent_conversation_memory
    _mem_ws = str(workspace_id or "default").strip() or "default"
    _mem_agent = str(agent_id or "").strip()
    _mem_key = f"{surface_channel}:{str(remote_jid or '').strip()}"
    try:
        _mem_prior = agent_conversation_memory.load_recent_turns(
            workspace_id=_mem_ws, agent_id=_mem_agent, conversation_key=_mem_key,
        )
    except Exception:
        _mem_prior = []

    try:
        result = await execute_sage_turn_for_channel(
            workspace_id=_mem_ws,
            surface_channel=surface_channel,
            gateway_id=str(gateway_id or "").strip(),
            remote_jid=str(remote_jid or "").strip(),
            message=turn_message,
            push_name=push_name,
            source_event_id=source_event_id,
            agent_id=_mem_agent,
            attachments=list(attachments) if attachments else None,
            channel_prior_messages=_mem_prior,
        )
        # Suppress the runtime's [SILENT]/NO_REPLY sentinels via the shared
        # filter — the personal-channel path (unlike direct_chat/hosted)
        # never applied it, so a "stay quiet" turn was shipped verbatim,
        # posting a literal "[SILENT]" into the chat (esp. group chats where
        # most messages aren't for the agent). None here → every handler's
        # existing empty-reply skip path fires (no outbound, no dispatch).
        reply = filter_outbound_reply(str((result or {}).get("message") or "").strip())
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
        try:
            agent_conversation_memory.append_turn(
                workspace_id=_mem_ws, agent_id=_mem_agent,
                conversation_key=_mem_key, role="user", content=raw_text,
            )
            if reply:
                agent_conversation_memory.append_turn(
                    workspace_id=_mem_ws, agent_id=_mem_agent,
                    conversation_key=_mem_key, role="assistant", content=reply,
                )
        except Exception:
            pass
        if reply:
            return {
                "text": reply,
                "source": "sage_turn_adapter",
                "trace_id": (result or {}).get("trace_id", ""),
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
    fallback_label: str,
    source_event_id: Optional[str] = None,
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
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
                fallback_label=fallback_label,
                source_event_id=source_event_id,
                agent_id=agent_id,
                attachments=attachments,
                is_owner=is_owner,
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
                    fallback_label=fallback_label,
                    source_event_id=source_event_id,
                    agent_id=agent_id,
                    attachments=attachments,
                    is_owner=is_owner,
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
    source_event_id: Optional[str] = None,
    is_owner: bool = False,
) -> Optional[Dict[str, Any]]:
    try:
        unified = await _build_unified_sage_personal_reply_async(
            surface_channel="whatsapp_personal",
            workspace_id=workspace_id,
            gateway_id=gateway_id,
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            fallback_label="WhatsApp",
            source_event_id=source_event_id,
            is_owner=is_owner,
        )
        return unified
    except Exception as _exc:
        _logger.warning(
            "WhatsApp turn failed for workspace=%s: %s",
            workspace_id, _exc
        )
        # Try cloud-session dispatch if available (same pattern as Telegram),
        # otherwise return classified error for Gateway delivery.
        _session_id = str(gateway_id or "").replace("cloud:", "", 1).strip()
        if _session_id and remote_jid:
            try:
                from server_modules.personal_channels_service import dispatch_cloud_channel_outbound as _cs_dispatch
                await _cs_dispatch(
                    session_id=_session_id,
                    text=classify_error(str(_exc), raw_error=str(_exc)),
                    remote_jid=remote_jid,
                )
            except Exception:
                pass
        return _build_error_reply_dict(_exc, workspace_id)


async def build_telegram_personal_reply_async(
    *,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    source_event_id: Optional[str] = None,
    is_owner: bool = False,
) -> Optional[Dict[str, Any]]:
    try:
        unified = await _build_unified_sage_personal_reply_async(
            surface_channel="telegram_personal",
            workspace_id=workspace_id,
            gateway_id=gateway_id,
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            fallback_label="Telegram",
            source_event_id=source_event_id,
            is_owner=is_owner,
        )
        return unified
    except Exception as _exc:
        _logger.warning(
            "CSM turn failed for workspace=%s: %s",
            workspace_id, _exc
        )
        # Extract session_id from gateway_id (format: "cloud:{session_id}")
        _session_id = str(gateway_id or "").replace("cloud:", "", 1).strip()
        if _session_id and remote_jid:
            try:
                from server_modules.personal_channels_service import dispatch_cloud_channel_outbound as _cs_dispatch
                await _cs_dispatch(
                    session_id=_session_id,
                    text=SAGE_ERROR_REPLY,
                    remote_jid=remote_jid,
                )
            except Exception:
                pass
        return None


async def build_discord_personal_reply_async(
    *,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    source_event_id: Optional[str] = None,
    linked_user_name: Optional[str] = None,
    is_owner: bool = False,
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
            fallback_label="Discord",
            source_event_id=source_event_id,
            is_owner=is_owner,
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
    fallback_label: str = "channel",
    source_event_id: Optional[str] = None,
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
) -> Optional[Dict[str, Any]]:
    try:
        unified = await _build_unified_sage_personal_reply_async(
            surface_channel=surface_channel,
            workspace_id=workspace_id,
            gateway_id=gateway_id,
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            fallback_label=fallback_label,
            source_event_id=source_event_id,
            attachments=attachments,
            is_owner=is_owner,
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
    source_event_id: Optional[str] = None,
    linked_user_name: Optional[str] = None,
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
) -> Optional[Dict[str, Any]]:
    """Build a reply for a WhatsApp personal DM — as the specialist agent_id
    names (see execute_sage_turn_for_channel), or as Sage when agent_id is
    empty (pre-existing behavior).

    linked_user_name is accepted for future identity-context injection but
    not yet threaded into _build_unified_sage_personal_reply (same as
    build_discord_personal_reply_async's linked_user_name parameter above).

    attachments: media-pipeline attachments (image/file kinds) already
    resolved+stored by personal_channel_media_store_service.

    is_owner: caller-resolved via personal_channels_service._is_owner_message
    (self-chat, or sender matching this channel's linked owner id) — see
    _build_unified_sage_personal_reply_async's docstring for the full
    contract. Defaults to False (guarded/external), matching every other
    build_*_personal_reply* entry point.
    """
    unified = _build_unified_sage_personal_reply(
        surface_channel="whatsapp_personal",
        workspace_id=workspace_id,
        gateway_id=gateway_id,
        remote_jid=remote_jid,
        text=text,
        push_name=push_name,
        fallback_label="WhatsApp",
        source_event_id=source_event_id,
        agent_id=agent_id,
        attachments=attachments,
        is_owner=is_owner,
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
    )


def build_telegram_personal_reply(
    *,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
    text: str,
    push_name: Optional[str] = None,
    source_event_id: Optional[str] = None,
    agent_id: str = "",
    attachments: Optional[List[dict]] = None,
    is_owner: bool = False,
) -> Optional[Dict[str, Any]]:
    """Build a reply for a Telegram personal DM — see build_whatsapp_personal_reply's
    docstring for the agent_id and is_owner contracts."""
    unified = _build_unified_sage_personal_reply(
        surface_channel="telegram_personal",
        workspace_id=workspace_id,
        gateway_id=gateway_id,
        remote_jid=remote_jid,
        text=text,
        push_name=push_name,
        fallback_label="Telegram",
        source_event_id=source_event_id,
        agent_id=agent_id,
        attachments=attachments,
        is_owner=is_owner,
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
    )

"""
Studio-connector channel router.

route_inbound_channel_message is the single entry point Studio connectors
(Slack Guild, Discord Guild, GitHub, Telegram Hosted) use to reach the main
Sage agent pipeline — connectors_actions.py's webhook handlers,
agent_registry_api.py, and the connectors/*_ingress_service.py modules all
call it. _resolve_agent_for_inbound is its Stage 4B helper (which channel
binding, if any, owns this inbound message) and is also called directly by
tests. ChannelIngressValidationError / ChannelOwnerNotFoundError /
ChannelSecurityDeniedError are re-exported here (from channel_errors.py)
because several callers (connectors_actions.py, agent_registry_api.py)
catch them as `agent_channel_router.ChannelXxxError` — that is the module
surface this file promises, independent of where the classes are actually
defined.

History (systemic backend-safety task, FIX 4): this file used to ALSO carry
a second, self-contained "Personal gateway channel router — Path B"
implementation — a full, parallel copy of every WhatsApp/Telegram/local-
bridge inbound handler, reply-delivery, state-sync, and configure/send
function personal_channels_service.py already implements and is the one
actually wired to live traffic (gateway_protocol_service.py ->
personal_channels_service.handle_gateway_channel_inbound;
routes_personal_channels.py -> personal_channels_service.
handle_cloud_channel_inbound — the latter deleted 2026-08-15 with the
cloud-session lane, so the gateway entry is the only one left). That second
implementation was never called
by anything: confirmed via a full-repo grep for every symbol in it (every
handler, every helper, the module-level constants, the lazy
gateway_protocol_service wrapper, and the file's own independent
PersonalChannelHandler registry/subclasses) before deleting it, then a full
test-suite run to confirm nothing that passed before still passes after.
The one test file that exercised it directly
(test_agent_channel_router.py's AgentChannelRouterNaturalReplyTests) was a
byte-for-byte duplicate of test_personal_channels_service_natural_reply.py
already covering the same behavior against the real, live code — removed
with it, no coverage lost. See that commit for the full symbol list and the
grep evidence.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

from server_modules.channel_errors import (  # noqa: E402
    ChannelIngressValidationError,
    ChannelOwnerNotFoundError,
    ChannelSecurityDeniedError,
)
from server_modules.inbound_envelope import InboundEnvelope  # noqa: E402

# Re-exported (not directly referenced below) so `agent_channel_router.
# ChannelIngressValidationError` etc. resolve for callers that catch them
# that way — see the module docstring's History note. Neither
# route_inbound_channel_message nor _resolve_agent_for_inbound currently
# raises any of the three (both fail safe / return an error dict instead),
# so this import exists purely to keep the module's promised attribute
# surface honest, not because it's exercised by a call in this file today.
_ = (ChannelIngressValidationError, ChannelOwnerNotFoundError, ChannelSecurityDeniedError)


# ── Stage 4B: multi-agent channel binding resolution ─────────────────────

async def _resolve_agent_for_inbound(
    *,
    channel_type: str,
    endpoint_key: str,
    tenant_id: str,
    workspace_id: str,
) -> str:
    """Resolve which agent owns an inbound channel message.

    Matches channel_type + endpoint_key against each agent's real,
    persisted channel binding (the ``agent_channel_bindings`` table via
    agent_bindings_repository) — the same table Discord's BYO bot binding
    already writes and validates against
    (discord_bot_provisioning_service.assign_agent_discord). Replaces an
    earlier (Stage 4B) design that matched against a ``channel_bindings``
    JSONB column on workspace_agent_installs — confirmed via a full-
    codebase grep to have zero writers anywhere for the shape it expected
    (`{channel_type, bot_token_hash}`), so it could never actually match a
    real agent no matter what called it.

    Returns:
        agent_install_id if a matching, enabled binding exists, else "".
        "" means: fall through to Sage, exactly like the pre-existing
        no-match behavior — one router, no per-channel forks, unmatched
        never falls back to another specialist.
    """
    channel_type = str(channel_type or "").strip().lower()
    endpoint_key = str(endpoint_key or "").strip()
    if not channel_type or not endpoint_key:
        return ""

    from server_modules import agent_bindings_repository as bindings

    try:
        rows = await bindings.list_workspace_channel_bindings(
            tenant_id=str(tenant_id or "default").strip() or "default",
            workspace_id=str(workspace_id or "").strip(),
            enabled_only=True,
        )
    except Exception:
        return ""  # fail safe to Sage, never block the turn on a lookup error

    for row in rows:
        if str(row.get("key") or "").strip().lower() != channel_type:
            continue
        binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
        if str(binding.get("endpoint_key") or "").strip().lower() == endpoint_key.lower():
            return str(row.get("agent_install_id") or "").strip()

    return ""


# ──────────────────────────────────────────────────────────────────────────────
# Studio connector channel routing
# ──────────────────────────────────────────────────────────────────────────────

# Channel keys that route through the main Sage agent pipeline.
#
# FIX (channel-audit Defect 2): "whatsapp" was missing entirely.
# whatsapp_ingress_service.py's _dispatch_public_deployed_agent_envelope
# (the WhatsApp Business "public deployed agent" mode — a workspace's own
# Twilio-backed WhatsApp number replying to any customer, the WhatsApp
# analog of "telegram" -> telegram_hosted below) has always called
# route_inbound_channel_message(channel_key="whatsapp", ...), but with no
# entry here channel_origin resolved to None, so every call fell through to
# the "Unimplemented channels" branch below and returned
# {"status": "channel_unavailable"} — the turn never reached
# execute_sage_turn. This mode has been silently dead since it shipped.
#
# Origin-specific handling checked for parity before adding this entry (see
# channel_adapter.ChannelOrigin.WHATSAPP_TWILIO's own comment for the naming
# precedent): entitlements enforcement
# (entitlements_service.enforce_channel_surface_access_for_workspace_id) and
# any Gate 1 dmPolicy/owner-pairing gate are both — by the SAME design, not
# an oversight only WhatsApp has — absent from every public-deployed-agent
# dispatch path (confirmed against telegram_ingress_service.py's own
# _dispatch_public_deployed_agent_envelope, which skips both identically).
# That's correct for this specific mode: "public deployed agent" traffic is
# customer-facing by product design — anyone messaging a workspace's own
# published WhatsApp Business / Telegram bot number is expected traffic,
# not a candidate for the owner-only personal-channel DM gate, which exists
# for the OWNER's own personal number instead
# (personal_channels_service._enforce_dm_policy — a different surface
# entirely, unaffected by this fix). No caller on this path constructs an
# InboundEnvelope either (WhatsApp or Telegram), so envelope-based owner-
# command gating is likewise consistently absent for both, not a WhatsApp-
# specific gap.
_SAGE_CHANNEL_ORIGIN_MAP: dict[str, str] = {
    "slack": "slack_guild",
    "discord": "discord_guild",
    "github": "github",
    "telegram": "telegram_hosted",
    "sms": "sms",
    "whatsapp": "whatsapp_twilio",
}


def _coerce_customer_message_text(customer_message: Any) -> str:
    """Extract a plain-text message from *customer_message*.

    Callers in connectors_actions.py pass a bare string (the goal).
    Callers in agent_registry_api.py may pass a dict from a JSON body.
    """
    if customer_message is None:
        return ""
    if isinstance(customer_message, str):
        return customer_message.strip()
    if isinstance(customer_message, dict):
        # Try common text fields in priority order.
        for key in ("text", "goal", "message", "body", "content"):
            val = customer_message.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        # Fallback: JSON-serialise the dict so the agent can see it.
        import json as _json
        try:
            return _json.dumps(customer_message, ensure_ascii=False, default=str)
        except Exception:
            return str(customer_message)
    return str(customer_message).strip()


_STUDIO_CHANNEL_LABEL_MAX_CHARS = 80


def _sanitize_studio_channel_label(value: Any) -> str:
    """Best-effort-clean a human-readable channel/group label from
    connector-supplied metadata before it is baked into the message text a
    model turn consumes. Untrusted, connector/attacker-influenceable text
    (e.g. a Slack channel name any member can rename) — never a trust
    boundary. Collapses whitespace/newlines (so it cannot forge a fake line
    break inside the turn message) and truncates so one hostile channel
    name can't bloat every turn — the same treatment
    personal_channel_sage_bridge_service._sanitize_channel_label gives the
    equivalent personal-channel field; kept as a small local copy here
    rather than imported, so this generic Studio-connector router doesn't
    take on a dependency on the personal-channel module's naming domain for
    a two-line string helper."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:_STUDIO_CHANNEL_LABEL_MAX_CHARS].strip()


def _studio_channel_context_prefix(*, channel_key: str, metadata: Optional[Dict[str, Any]]) -> str:
    """Render is_group/chat_type/chat_label from a Studio-connector's
    inbound `metadata` into a short context line prepended to the message
    text the model turn actually consumes.

    FIX (systemic backend-safety task): `metadata` used to be accepted by
    route_inbound_channel_message and never read again anywhere in this
    function -- a connector could compute a real is_group/chat_type/
    chat_label signal (mirroring the same convention
    personal_channel_sage_bridge_service already uses for personal
    channels -- _owner_provenance_message / _personal_channel_guard_metadata,
    the "family group" bug fix) and it would be silently discarded here:
    the model never learned whether a Slack/Discord/GitHub/Telegram-hosted
    turn came from a shared, multi-person channel, or which one.
    execute_sage_turn has no dedicated group-context parameter --
    channel_origin (_SAGE_CHANNEL_ORIGIN_MAP) is a fixed system enum, a
    "which platform" signal, not a per-message "which room" one -- so this
    is threaded the same way an owner-provenance header is: prefixed onto
    the message text itself, the one thing every downstream consumer of a
    Sage turn actually reads.

    Returns "" (add nothing) when metadata carries NONE of these three
    keys -- the real shape every current connectors_actions.py webhook
    handler sends today (confirmed by inspection: the slack/discord/github
    webhook handlers' metadata all carry connector- and provider-specific
    IDs -- slack_channel_id, discord_guild_id, github_repository -- never a
    generic is_group/chat_type/chat_label key), so this is forward-
    compatible plumbing against a future connector update, not a behavior
    change against today's real traffic -- same framing as this file's
    other "safe no-op today" comments.

    Deliberately does NOT attempt to infer is_group from a provider-
    specific id shape itself (e.g. a Slack channel id's D/C/G prefix) --
    that is connector domain knowledge that belongs in the connector
    computing metadata (out of this router's scope), not guessed at here.
    """
    meta = metadata if isinstance(metadata, dict) else {}
    if "is_group" not in meta and "chat_type" not in meta and "chat_label" not in meta:
        return ""
    chat_type = _sanitize_studio_channel_label(meta.get("chat_type")) or str(channel_key or "").strip() or "channel"
    chat_label = _sanitize_studio_channel_label(meta.get("chat_label"))
    is_group = meta.get("is_group")
    where = f'{chat_type} "{chat_label}"' if chat_label else chat_type
    if is_group is None:
        group_note = ""
    elif bool(is_group):
        group_note = ", a shared channel visible to other participants"
    else:
        group_note = ", a private conversation"
    return f"[Posted in {where}{group_note}]\n\n"


async def route_inbound_channel_message(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    channel_key: str = "",
    endpoint_key: Optional[str] = None,
    customer_message: Optional[Dict[str, Any]] = None,
    session_key: Optional[str] = None,
    message_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_display_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    allow_master_fallback: bool = False,
    privileged_runtime_approved: bool = False,
    trace_id: Optional[str] = None,
    agent_installs: Optional[list] = None,
    sage_agent_id: str = "",
    # Canonical inbound attribution (inbound_envelope.py) — who sent this,
    # from where, verified by the calling connector. None = unwired caller
    # (the pre-existing behavior: execute_sage_turn treats a None envelope
    # exactly like a legacy caller — no header, no owner-command gating
    # change). Callers that can confidently compute DM/group/owner signals
    # (see connectors_actions.slack_events_webhook,
    # discord_bot_runtime_service.handle_parsed_event,
    # discord_connector._handle_dm_via_gateway) pass one through.
    envelope: Optional[InboundEnvelope] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Route an inbound studio-connector message to the agent pipeline.

    Wired channels (Slack Guild, Discord Guild, GitHub, Telegram) route through
    :func:`execute_sage_turn` — the same unified pipeline used by every
    other channel.  Remaining channels return ``channel_unavailable``
    until their specialist routing is built.

    Stage 4B: resolves the target agent via its real, persisted channel
    binding (endpoint_key) before falling back to Sage. `agent_installs`/
    `sage_agent_id` are accepted for backward compatibility but unused —
    no real caller ever passed them (confirmed by grep), and the earlier
    resolution they fed had zero writers for the shape it matched on, so
    it could never have worked regardless.

    metadata: connector-supplied context for this inbound message
    (connector_id, delivery_source, and provider-specific ids like
    slack_channel_id/discord_guild_id/github_repository today — see
    connectors_actions.py's webhook handlers). Three keys, if present,
    are threaded into the model turn: is_group (bool), chat_type (str),
    chat_label (str) — see _studio_channel_context_prefix's docstring for
    the fix this is (`metadata` used to be accepted here and never read
    again at all) and exactly why "the message text itself" is the
    threading point, not a dedicated execute_sage_turn parameter.
    """
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_channel_key = str(channel_key or "").strip().lower()

    # ── Stage 4B: resolve agent via its real, persisted channel binding ──
    resolved_agent_id = ""
    specialist_context = None
    if endpoint_key:
        resolved_agent_id = await _resolve_agent_for_inbound(
            channel_type=resolved_channel_key,
            endpoint_key=str(endpoint_key or "").strip(),
            tenant_id=tenant_id or "default",
            workspace_id=resolved_workspace_id,
        )
        if resolved_agent_id:
            # A specialist agent matched its own channel binding (e.g. a
            # bound Slack/Discord bot) -- resolve its runtime identity so
            # the turn below actually runs AS that agent (its persona,
            # model/provider binding, memory namespace), not as Sage with
            # the reply merely attributed to it. Same resolution every
            # other channel funnels through -- see
            # specialist_runtime_context.resolve_specialist_runtime_context's
            # docstring for the two guarantees this carries. Resolution
            # failures fail safe to Sage (unchanged pre-existing behavior),
            # never to an error.
            try:
                from server_modules.specialist_runtime_context import resolve_specialist_runtime_context

                specialist_context = await resolve_specialist_runtime_context(
                    workspace_id=resolved_workspace_id,
                    tenant_id=tenant_id or "default",
                    active_agent_install_id=resolved_agent_id,
                )
            except Exception:
                specialist_context = None

    # ── Sage-routed channels ───────────────────────────────────────────
    channel_origin = _SAGE_CHANNEL_ORIGIN_MAP.get(resolved_channel_key)
    if channel_origin is not None:
        message_text = _coerce_customer_message_text(customer_message)
        if not message_text:
            logger.warning(
                "route_inbound_channel_message: channel=%s empty message from actor=%s",
                resolved_channel_key,
                actor_id or "unknown",
            )
            return {
                "ok": False,
                "status": "empty_message",
                "error": "No message text could be extracted from the inbound payload.",
                "run_id": None,
                "reply": None,
            }

        run_id = trace_id or f"chan-{uuid4().hex[:12]}"

        # is_group/chat_type/chat_label context (see
        # _studio_channel_context_prefix's docstring for the fix this is).
        # Skipped for a directive/command message (leading "/", e.g.
        # "/model claude" or "/new") -- execute_sage_turn's own directive
        # parser (command_registry.process_message) matches on the message
        # literally starting with "/"; prefixing context text here would
        # silently break every Studio-connector directive the moment a
        # connector starts sending this metadata. A directive is a
        # structured command, not a conversational turn the model
        # free-form interprets, so it never needed the framing anyway --
        # same reasoning personal_channel_sage_bridge_service's provenance
        # header is applied to the free-form message, never a command.
        context_prefix = (
            "" if message_text.startswith("/")
            else _studio_channel_context_prefix(channel_key=resolved_channel_key, metadata=metadata)
        )
        effective_message = f"{context_prefix}{message_text}" if context_prefix else message_text

        # ── Thread-id resolution (docs/design/audit-history-memory.md gap
        # #4 — "the sage-main collapse"): without this, every Studio-
        # connector sender/room on a given channel_origin shares the SAME
        # SQL thread (execute_sage_turn's own frozen fallback: a resolved
        # specialist keys per (agent, sender) already via
        # agent_sender_thread_id, but the common "no specialist bound, runs
        # as Sage" case falls through to get_active_thread's single
        # workspace+channel-type pointer, which defaults to the literal
        # "sage-main" for every channel that never set an override — no
        # channel does). Reuses agent_sender_thread_id's exact
        # deterministic (agent, key) pattern the WeChat fix
        # (wechat_official_service.py) already established for the same
        # bug, generalized here for every Studio connector that supplies a
        # canonical envelope: a turn whose envelope names a ROOM
        # (envelope.chat.id — a Slack channel, a Discord guild channel, a
        # GitHub repository) keys per ROOM, since every participant in that
        # room already shares its context by design (same as a group
        # chat); a turn with no room (a DM, or any surface that never sets
        # chat.id) keys per SENDER (envelope.sender.id) instead, so two
        # different senders on the same channel type never interleave.
        # room-over-sender (not surface-over-sender) is the actual signal:
        # GitHub's envelope below is surface=API (not GROUP/
        # BROADCAST_CHANNEL — a webhook has no "member" concept) but still
        # wants per-repo, not per-actor, scoping — chat.id presence is what
        # the target model (docs/design/audit-history-memory.md §2.2) means
        # by "room", independent of which surface enum enclosed it. Gated
        # on `envelope is not None` so an envelope-less caller (WhatsApp/
        # Telegram ingress, the direct /agent-registry/channels/inbound API
        # route — neither constructs one today) keeps its EXACT
        # pre-existing behavior, unchanged: empty string here means
        # execute_sage_turn's own (frozen, untouched) thread-resolution
        # fallback runs exactly as before.
        resolved_thread_id = ""
        if envelope is not None:
            _thread_agent_token = resolved_agent_id or "sage"
            _room_or_sender = str(envelope.chat.id or "").strip() or str(envelope.sender.id or "").strip()
            if not _room_or_sender:
                _room_or_sender = str(actor_id or "").strip() or str(session_key or "").strip()
            if _room_or_sender:
                from server_modules.sage_command_dispatcher import agent_sender_thread_id
                resolved_thread_id = agent_sender_thread_id(_thread_agent_token, _room_or_sender)

        try:
            from server_modules.sage_turn_adapter import execute_sage_turn

            sage_result = await execute_sage_turn(
                workspace_id=resolved_workspace_id,
                tenant_id=tenant_id or "",
                message=effective_message,
                surface="chat",
                channel_origin=channel_origin,
                channel_sender_id=str(actor_id or ""),
                channel_sender_name=str(actor_display_name or ""),
                request_id=message_id or run_id,
                specialist_context=specialist_context,
                envelope=envelope,
                thread_id=resolved_thread_id,
            )

            reply_text = str(sage_result.message or "")
            return {
                "ok": True,
                "status": "completed",
                "run_id": run_id,
                "reply": reply_text,
                "trace_id": str(sage_result.trace_id or ""),
                "provider": str(sage_result.provider or ""),
                "model": sage_result.model,
            }
        except Exception as exc:
            logger.warning(
                "route_inbound_channel_message: channel=%s execute_sage_turn failed: %s",
                resolved_channel_key,
                exc,
            )
            return {
                "ok": False,
                "status": "execution_error",
                "error": f"Agent turn failed: {exc}",
                "run_id": run_id,
                "reply": None,
            }

    # ── Unimplemented channels ─────────────────────────────────────────
    logger.warning(
        "agent_channel_router.route_inbound_channel_message: channel=%s is not "
        "available — studio connector routing is not yet implemented. "
        "Message from actor=%s at endpoint=%s dropped safely.",
        channel_key,
        actor_id or "unknown",
        endpoint_key or "unknown",
    )
    return {
        "ok": False,
        "status": "channel_unavailable",
        "error": (
            f"The {channel_key} channel is not available yet. "
            "Studio connector routing has not been implemented."
        ),
        "run_id": None,
        "reply": None,
    }

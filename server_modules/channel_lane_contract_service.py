from __future__ import annotations

from typing import Any, Dict

from server_modules import external_content_guard, openclaw_channel_registry


PERSONAL_GATEWAY_RUNTIME_LANE = "personal_gateway"
STUDIO_CONNECTOR_RUNTIME_LANE = "studio_business_connector"
DIRECT_CHAT_MEMORY_SURFACE = "direct_chat"

PERSONAL_ROUTE_PREFIX = "/personal-channels/"

# The one channel key the cloud session manager puts on the wire, and the one
# name every module that has to reason about that lane should import rather
# than retype. See PERSONAL_CHANNEL_SPECS's own comment immediately below for
# why this survived the 2026-08-14 cutover when its on-box twin did not.
CLOUD_SESSION_TELEGRAM_CHANNEL_KEY = "telegram_personal"
CLOUD_SESSION_TELEGRAM_PROVIDER = "telegram_gramjs"

# 2026-08-14 full OpenClaw channel cutover: whatsapp_personal, telegram_personal,
# signal_personal, imessage_personal and wechat_personal are DELETED from this
# dict, not merely superseded — their first-party gateway runtimes (Baileys,
# gramjs, and the local-bridge family) are deleted in the same change, so
# leaving these keys declared here would be exactly the "declared but nothing
# serves it" dishonesty CLAUDE.md warns about: a customer would see a setup
# panel for a channel that can never again connect. The live replacements
# (openclaw_whatsapp, openclaw_telegram, openclaw_signal, openclaw_imessage,
# openclaw_openclaw-weixin) are added below via OPENCLAW_PERSONAL_CHANNEL_SPECS,
# derived from OPENCLAW_CUT_OVER_CHANNEL_IDS — see openclaw_channel_registry.py.
# discord_personal stays: it is a cloud_connector (bot-token) channel with no
# Agent Computer runtime at all, not a platform OpenClaw's hardware-bound
# transport would improve on — see that module's own comment on why discord/
# slack/sms were deliberately excluded from the cutover.
#
# CORRECTION, 2026-08-15: the telegram_personal deletion above was OVER-BROAD,
# and it took the cloud lane down silently for a day. There are TWO gramjs
# Telegram runtimes in this product, not one. The cutover deleted the ON-BOX
# one (empyralis-gateway/src/channels/telegram/runtime.ts) — correctly. It did
# NOT delete the CLOUD one (`cloud-session-manager/`, real gramjs, its own
# HTTP relay), which is still wired at three live seams and sends this exact
# channel_key on the wire:
#
#   inbound   POST /personal-channels/cloud/inbound  (HMAC, routes_personal_
#             channels.py) -> personal_channels_service.handle_cloud_channel_
#             inbound, whose `channel_key` is hardcoded "telegram_personal" by
#             cloud-session-manager/src/telegram/hmac.js::buildSignedInbound.
#   outbound  personal_channels_service.dispatch_cloud_channel_outbound
#   proactive runtime_heartbeat_service + connectors/channel_delivery_outbox_
#             service, both via resolve_cloud_telegram_session_id()
#
# Deleting the key while leaving that lane running produced the worst possible
# split: proactive OUTBOUND still worked, while every INBOUND message died in
# assert_personal_gateway_channel below, was caught as a generic turn failure,
# and came back as an empty reply — the person got silence and nothing
# anywhere said why. So the key is declared again, telling the truth about
# WHICH runtime serves it.
#
# It is declared HERE ONLY, and deliberately NOT in PERSONAL_CHANNEL_ROADMAP
# or CHANNEL_PLATFORM_CATALOG: those two are what the UI reads, and there is
# no self-serve way to create a cloud session (no frontend caller for
# cloud-session-manager/src/api/routes.js's creation endpoints), so a setup
# panel would be exactly the "declared but nothing serves it" dishonesty the
# comment above rejects. A lane contract answers "may this message be
# processed"; a catalog answers "may a customer start this". Different
# questions.
#
# NOT openclaw_telegram, and this is not a naming preference: openclaw_telegram
# is a BOT channel whose replies leave over the gateway WebSocket to a box,
# while this is the owner's own ACCOUNT whose replies leave over HTTP to the
# cloud session manager. Routing one through the other would misattribute the
# identity in the envelope and silently merge two different Telegram threads'
# conversation memory.
#
# THIS IS NOT AN ENDORSEMENT OF THE LANE. The gramjs account channel is
# ban-risk and the founder retired its on-box twin on purpose; whether the
# cloud twin should be deleted outright (route + service + heartbeat/outbox
# callers, one coherent change) is his call, not a side effect of this fix.
# Until he makes it, the lane runs and reports honestly instead of failing in
# silence.
PERSONAL_CHANNEL_SPECS: Dict[str, Dict[str, str]] = {
    "discord_personal": {
        "provider": "discord_bot",
        "runtime_lane": "cloud_connector",  # Uses bot token, not user-account session (Discord ToS prohibits self-bots)
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        "stage": "live",
        "live_capable": "true",
    },
    CLOUD_SESSION_TELEGRAM_CHANNEL_KEY: {
        "provider": CLOUD_SESSION_TELEGRAM_PROVIDER,
        # cloud_connector, NOT personal_gateway: nothing about this lane runs
        # on an Agent Computer. Same reasoning as discord_personal above.
        "runtime_lane": "cloud_connector",
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        "stage": "live",
        "live_capable": "true",
    },
}

# ── OpenClaw-transported channels ────────────────────────────────────────
#
# CHANNEL-ADOPTION-PLAN.md step 2. An OpenClaw gateway runs as a channel
# TRANSPORT ONLY on the customer's own machine, next to the Empyralis
# gateway; its brain/memory/skills/UI are off. Inbound arrives at our
# bridge plugin's `message_received` tap, is POSTed to the Empyralis
# gateway's loopback intake, and is republished on the SAME
# `channel.inbound` event through the SAME three gates every other
# personal-gateway channel uses. That makes these members of the existing
# personal-gateway lane by construction, not a new lane.
#
# ONE PROVIDER FOR ALL OF THEM, ON PURPOSE. The transport is OpenClaw; the
# platform is the channel_key suffix. A per-platform provider string would
# imply Empyralis speaks each protocol itself, which it does not.
#
# THE SET IS DERIVED, NOT LISTED. It used to be a five-entry tuple written
# out here by hand, against an upstream that ships twenty-seven channels and
# an Empyralis side with no per-channel code at all. Every channel now comes
# from `openclaw_channel_registry`, which reads a manifest generated from the
# pinned OpenClaw. Adding a channel upstream needs no edit here — only a
# regeneration. See that module for why a checked-in manifest rather than a
# CLI shell-out, and for how the verbatim-id invariant became automatic.
OPENCLAW_TRANSPORT_PROVIDER = "openclaw"

# The derivation itself lives below STUDIO_CHANNEL_ROADMAP, because resolving
# which platforms Empyralis already owns has to read BOTH first-party catalogs
# — the personal lane and the Studio lane — and the Studio one is defined after
# this point. Search for OPENCLAW_TRANSPORT_OWNERSHIP.

# 2026-08-14 full OpenClaw channel cutover: telegram_personal, whatsapp_personal,
# signal_personal, imessage_personal and wechat_personal are DELETED (not
# superseded-and-kept) — see PERSONAL_CHANNEL_SPECS's comment above for why.
# discord_personal stays for the same reason it stays there.
PERSONAL_CHANNEL_ROADMAP: tuple[Dict[str, str], ...] = (
    {
        "channel_key": "discord_personal",
        "label": "Discord",
        "provider": "discord_bot",
        # Uses a bot token, not a paired-gateway user-account session (Discord
        # ToS prohibits self-bots) — matches PERSONAL_CHANNEL_SPECS above and
        # session_owner below; this previously said personal_gateway, which
        # contradicted both.
        "runtime_lane": "cloud_connector",
        "stage": "live",
        "live_capable": "true",
        "family": "personal",
        "session_owner": "cloud_connector",
    },
)

STUDIO_CHANNEL_ROADMAP: tuple[Dict[str, str], ...] = (
    {
        "channel_key": "web_chat",
        "label": "Web Chat",
        "provider": "web_widget",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "stage": "roadmap",
        "status": "roadmap",
        "live_capable": "false",
        "launch_allowed": "false",
        "family": "studio_business",
        "session_owner": "cloud_connector",
    },
    {
        "channel_key": "email",
        "label": "Email",
        "provider": "workspace_mailbox",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "stage": "partial",
        "status": "partial",
        "live_capable": "false",
        "launch_allowed": "false",
        "family": "studio_business",
        "session_owner": "cloud_connector",
    },
    {
        "channel_key": "telegram_bot",
        "label": "Telegram Bot",
        "provider": "telegram_bot_api",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": "true",
        "launch_allowed": "true",
        "family": "studio_business",
        "session_owner": "cloud_connector",
    },
    {
        "channel_key": "whatsapp_twilio",
        "label": "WhatsApp Business",
        "provider": "twilio_whatsapp",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "stage": "out_of_scope",
        "status": "out_of_scope",
        "live_capable": "false",
        "launch_allowed": "false",
        "family": "studio_business",
        "session_owner": "cloud_connector",
    },
    {
        # Plain SMS via a dedicated Twilio number per agent — reuses the same
        # Twilio Messages API plumbing as whatsapp_twilio, minus the
        # `whatsapp:` address prefix (see WhatsAppTransportService.send_sms).
        "channel_key": "sms_twilio",
        "label": "SMS (Twilio)",
        "provider": "twilio_sms",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": "true",
        "launch_allowed": "true",
        "family": "studio_business",
        "session_owner": "cloud_connector",
    },
    {
        "channel_key": "apple_messages_business",
        "label": "Apple Messages for Business",
        "provider": "apple_messages_business_msp",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "stage": "roadmap",
        "status": "roadmap",
        "live_capable": "false",
        "launch_allowed": "false",
        "family": "studio_business",
        "session_owner": "cloud_connector",
    },
    {
        "channel_key": "slack",
        "label": "Slack",
        "provider": "slack_events",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": "true",
        "launch_allowed": "true",
        "family": "studio_business",
        "session_owner": "cloud_connector",
    },
    {
        "channel_key": "discord_bot",
        "label": "Discord",
        "provider": "discord_webhook",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": "true",
        "launch_allowed": "true",
        "family": "studio_business",
        "session_owner": "cloud_connector",
    },
)

# ── OpenClaw transport: the derived channel set ──────────────────────────
#
# Placed here, not beside OPENCLAW_TRANSPORT_PROVIDER above, because it has
# to read BOTH first-party catalogs to know which platforms Empyralis
# already owns.
# The platforms Empyralis already implements itself, as bare tokens. Derived
# from the first-party catalogs above rather than typed out, so a first-party
# channel added later is automatically considered when resolving overlap with
# OpenClaw instead of quietly producing a second live implementation of one
# platform. `_personal`/`_bot`/`_twilio` are the suffixes those catalogs use
# to say HOW a platform is reached; the platform itself is what remains.
_FIRST_PARTY_LANE_SUFFIXES = ("_personal", "_bot", "_twilio")


def _first_party_platform_token(channel_key: str) -> str:
    token = str(channel_key or "").strip().lower()
    for suffix in _FIRST_PARTY_LANE_SUFFIXES:
        if token.endswith(suffix):
            return token[: -len(suffix)]
    return token


FIRST_PARTY_PLATFORM_TOKENS: frozenset[str] = frozenset(
    _first_party_platform_token(channel_key) for channel_key in PERSONAL_CHANNEL_SPECS
) | frozenset(
    _first_party_platform_token(str(entry.get("channel_key") or ""))
    for entry in STUDIO_CHANNEL_ROADMAP
)

# channel id -> "first_party" | "openclaw". Computed; see
# openclaw_channel_registry.resolve_transport_ownership.
OPENCLAW_TRANSPORT_OWNERSHIP: Dict[str, str] = openclaw_channel_registry.resolve_transport_ownership(
    FIRST_PARTY_PLATFORM_TOKENS
)

# The OpenClaw channels that ARE the live implementation of their platform.
# Only these enter PERSONAL_CHANNEL_SPECS, get advertised by the gateway, get
# provisioned into OpenClaw's config, and can accept inbound — so declaring
# all 27 cannot produce two runtimes on one account.
OPENCLAW_ACTIVE_CHANNELS: tuple[Any, ...] = tuple(
    channel
    for channel in openclaw_channel_registry.CHANNELS
    if OPENCLAW_TRANSPORT_OWNERSHIP.get(channel.id) == openclaw_channel_registry.OWNER_OPENCLAW
)

# The OpenClaw channels a first-party Empyralis runtime still owns. Declared
# and visible (so the UI can say "carried by the transport, superseded here"),
# never live.
OPENCLAW_SUPERSEDED_CHANNELS: tuple[Any, ...] = tuple(
    channel
    for channel in openclaw_channel_registry.CHANNELS
    if OPENCLAW_TRANSPORT_OWNERSHIP.get(channel.id) != openclaw_channel_registry.OWNER_OPENCLAW
)

if not OPENCLAW_ACTIVE_CHANNELS:
    raise RuntimeError(
        "Every OpenClaw channel resolved to a first-party owner, leaving the transport with "
        "nothing to carry. That is a derivation failure, not a product decision — a silently "
        "empty transport looks exactly like a working one until a customer's channel goes quiet."
    )

OPENCLAW_PERSONAL_CHANNEL_SPECS: Dict[str, Dict[str, str]] = {
    channel.channel_key: {
        "provider": OPENCLAW_TRANSPORT_PROVIDER,
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        # "preview", not "live", for EVERY channel on this transport — a
        # property of the transport itself, not a per-channel judgement, so
        # there is no list to keep honest. No platform here has been driven
        # with real credentials yet (CHANNEL-ADOPTION-PLAN.md step 5).
        #
        # Promotion does not happen in this file. "Proven live" is an
        # OBSERVED fact — a real message through a real connection — and it
        # is reported per-gateway by
        # personal_channels_service.get_gateway_personal_channel_surfaces
        # (`connected` / `running` / `proven_live`), read off gateway state
        # rather than declared here. That is the only shape of "promote it
        # from a real message" that cannot rot into a stale hand-written
        # claim.
        "stage": "preview",
        "live_capable": "true",
        # Whether the pinned OpenClaw has a `channels.<id>` config node at
        # all. Four catalogued channels only contribute one once their plugin
        # is installed; provisioning must say so rather than write a node
        # OpenClaw will reject. Carried here so every reader of the lane
        # contract sees the same honest answer.
        "policy_expressible": "true" if channel.config_schema_present else "false",
    }
    for channel in OPENCLAW_ACTIVE_CHANNELS
}

PERSONAL_CHANNEL_SPECS.update(OPENCLAW_PERSONAL_CHANNEL_SPECS)


CHANNEL_PLATFORM_CATALOG: tuple[Dict[str, Any], ...] = (
    {
        "channel_key": "web_chat",
        "binding_channel_key": "web_chat",
        "label": "Web Chat / Widget",
        "provider": "web_widget",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "roadmap",
        "status": "roadmap",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": False,
        "account_provider": None,
        "connector_id": None,
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "public_widget"],
    },
    {
        "channel_key": "gmail",
        "binding_channel_key": "email",
        "label": "Email via Gmail / Google Workspace",
        "provider": "google_workspace",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "partial",
        "status": "partial",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": False,
        "account_provider": "google_workspace",
        "connector_id": "google_workspace",
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "mailbox"],
    },
    {
        "channel_key": "smtp_imap",
        "binding_channel_key": "email",
        "label": "Email via SMTP/IMAP",
        "provider": "smtp",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "partial",
        "status": "partial",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": False,
        "account_provider": "smtp",
        "connector_id": "smtp",
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "mailbox"],
    },
    {
        "channel_key": "telegram_bot",
        "binding_channel_key": "telegram",
        "label": "Telegram Bot API",
        "provider": "telegram_bot_api",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "telegram_bot",
        "connector_id": "telegram_bot",
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "commands"],
    },
    {
        "channel_key": "discord_bot",
        "binding_channel_key": "discord",
        "label": "Discord Bot",
        "provider": "discord_bot",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "discord_bot",
        "connector_id": "discord_bot",
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "slash_commands"],
    },
    {
        "channel_key": "slack",
        "binding_channel_key": "slack",
        "label": "Slack App",
        "provider": "slack",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "slack",
        "connector_id": "slack",
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "mentions"],
    },
    {
        "channel_key": "whatsapp_business",
        "binding_channel_key": "whatsapp",
        "label": "WhatsApp Business / Cloud API / Twilio",
        "provider": "twilio_whatsapp",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "roadmap",
        "status": "roadmap",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": False,
        "account_provider": "whatsapp_twilio",
        "connector_id": "whatsapp_twilio",
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "business_messaging"],
    },
    {
        # "Each agent gets its own phone number to text with." Cloud webhook
        # channel (no gateway/hardware pairing) on the same Studio connector
        # lane as Slack/Discord bot. The platform holds ONE master Twilio
        # account (no customer keys) — provisioning searches + buys a number
        # under it and points the number's SmsUrl at
        # /channels/sms/twilio/webhook. Gated on TWILIO_ACCOUNT_SID /
        # TWILIO_AUTH_TOKEN; unset ⇒ "not configured on this deployment".
        "channel_key": "sms_twilio",
        "binding_channel_key": "sms",
        "label": "SMS via Twilio",
        "provider": "twilio_sms",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "sms_twilio",
        "connector_id": "sms_twilio",
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "sms", "dedicated_number"],
        # Per-message + monthly-number cost is a Twilio pass-through. These
        # are indicative Twilio US list prices (subject to change by Twilio;
        # carrier A2P fees extra) surfaced so the cost is never hidden — they
        # are NOT yet metered against workspace credits. The exact billing
        # hook point is documented in sms_twilio_provisioning_service.py.
        "pricing": {
            "model": "usage_metered_passthrough",
            "currency": "USD",
            "number_rental_per_month": 1.15,
            "outbound_per_segment": 0.0079,
            "inbound_per_segment": 0.0079,
            "metered": False,
            "provider": "twilio",
        },
    },
    {
        "channel_key": "apple_messages_business",
        "binding_channel_key": "apple_messages_business",
        "label": "Apple Messages for Business",
        "provider": "apple_messages_business_msp",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "roadmap",
        "status": "roadmap",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": False,
        "account_provider": "apple_messages_business",
        "connector_id": "apple_messages_business",
        "surface_support": ["sage", "studio"],
        "capabilities": [
            "inbound",
            "outbound",
            "business_messaging",
            "ai_disclosure_required",
            "human_handoff_required",
            "user_initiated",
        ],
    },
    {
        "channel_key": "microsoft_365",
        "binding_channel_key": "microsoft_365",
        "label": "Microsoft 365 / Outlook",
        "provider": "microsoft_365",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "partial",
        "status": "partial",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": False,
        "account_provider": "microsoft_365",
        "connector_id": "microsoft_365",
        "surface_support": ["studio"],
        "capabilities": ["mail", "calendar", "files"],
    },
    {
        "channel_key": "teams",
        "binding_channel_key": "teams",
        "label": "Microsoft Teams",
        "provider": "microsoft_teams",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "roadmap",
        "status": "roadmap",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": False,
        "account_provider": "microsoft_365",
        "connector_id": "microsoft_365",
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "teams_bot"],
    },
    {
        "channel_key": "matrix",
        "binding_channel_key": "matrix",
        "label": "Matrix",
        "provider": "matrix",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "customer_chat",
        "stage": "roadmap",
        "status": "roadmap",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": False,
        "account_provider": None,
        "connector_id": None,
        "surface_support": ["studio"],
        "capabilities": ["inbound", "outbound", "rooms"],
    },
    {
        "channel_key": "github",
        "binding_channel_key": "github",
        "label": "GitHub Issues/PRs",
        "provider": "github",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "github",
        "connector_id": "github",
        "surface_support": ["studio"],
        "capabilities": ["issues", "pull_requests", "webhooks"],
    },
    {
        "channel_key": "linear",
        "binding_channel_key": "linear",
        "label": "Linear",
        "provider": "linear",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "linear",
        "connector_id": "linear",
        "surface_support": ["studio"],
        "capabilities": ["issues", "projects"],
    },
    {
        "channel_key": "notion",
        "binding_channel_key": "notion",
        "label": "Notion",
        "provider": "notion",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "notion",
        "connector_id": "notion",
        "surface_support": ["studio"],
        "capabilities": ["pages", "databases"],
    },
    {
        "channel_key": "dropbox",
        "binding_channel_key": "dropbox",
        "label": "Dropbox",
        "provider": "dropbox",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "dropbox",
        "connector_id": "dropbox",
        "surface_support": ["studio"],
        "capabilities": ["files", "folders", "shared_links"],
    },
    {
        "channel_key": "s3",
        "binding_channel_key": "s3",
        "label": "Amazon S3",
        "provider": "s3",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "s3",
        "connector_id": "s3",
        "surface_support": ["studio"],
        "capabilities": ["buckets", "objects", "presigned_urls"],
    },
    {
        "channel_key": "smtp",
        "binding_channel_key": "smtp",
        "label": "SMTP / IMAP Email",
        "provider": "smtp",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "smtp",
        "connector_id": "smtp",
        "surface_support": ["studio"],
        "capabilities": ["mailbox_fetch", "mailbox_send"],
    },
    {
        "channel_key": "wechat_work",
        "binding_channel_key": "wechat_work",
        "label": "WeChat Work",
        "provider": "wechat_work",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "wechat_work",
        "connector_id": "wechat_work",
        "surface_support": ["studio"],
        "capabilities": ["webhook_messages"],
    },
    {
        "channel_key": "instagram_business",
        "binding_channel_key": "instagram_business",
        "label": "Instagram Business",
        "provider": "instagram_business",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
        "category": "work_system",
        "stage": "live",
        "status": "working_when_configured",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "instagram_business",
        "connector_id": "instagram_business",
        "surface_support": ["studio"],
        "capabilities": ["comments", "direct_messages"],
    },
    # telegram_personal (gramjs) and whatsapp_personal (Baileys) DELETED
    # 2026-08-14 (full OpenClaw channel cutover) — see PERSONAL_CHANNEL_SPECS's
    # comment above. The live replacements are openclaw_telegram/
    # openclaw_whatsapp, added to this same tuple below via
    # OPENCLAW_CHANNEL_PLATFORM_CATALOG.
    {
        "channel_key": "discord_personal",
        "binding_channel_key": "discord_personal",
        "label": "Discord Personal DM",
        "provider": "discord_bot",
        # Bot-token-backed, not a paired-gateway session — see
        # PERSONAL_CHANNEL_SPECS's discord_personal entry for why.
        "runtime_lane": "cloud_connector",
        "category": "personal_runtime",
        "stage": "live",
        "status": "personal_dm",
        "live_capable": True,
        "launch_allowed": True,
        "requires_agent_computer": False,
        "account_provider": "discord_bot",
        "connector_id": "discord_bot",
        "surface_support": ["sage"],
        "capabilities": ["inbound", "outbound", "personal_dm"],
    },
    # signal_personal, imessage_personal, wechat_personal (the first-party
    # local-bridge family) DELETED 2026-08-14 (full OpenClaw channel
    # cutover) — see PERSONAL_CHANNEL_SPECS's comment above. The live
    # replacements are openclaw_signal/openclaw_imessage/openclaw_openclaw-
    # weixin, added to this same tuple below via
    # OPENCLAW_CHANNEL_PLATFORM_CATALOG.
)


# ── OpenClaw channels in the catalogs the UI reads ───────────────────────
#
# Until now the OpenClaw channels were in PERSONAL_CHANNEL_SPECS but in
# NEITHER catalog, so `get_gateway_personal_channel_surfaces` — which
# iterates `personal_channel_catalog()` — never listed one. They were wired
# end to end and invisible: the "built, tested, and never wired" shape
# CLAUDE.md names, one level up from the code.
#
# Both catalogs are extended from the SAME derived channel set, so a channel
# OpenClaw adds shows up in the product without an edit here.
#
# WHAT THE UI CAN TELL APART, WITHOUT A HAND-MAINTAINED LIST:
#
#   stage "preview"                every OpenClaw channel, uniformly — the
#                                  transport is wired and gated, nothing has
#                                  been driven with real credentials yet.
#   status "openclaw_transport"    live implementation of its platform.
#   status "superseded_by_first_party"
#                                  Empyralis already implements this platform
#                                  itself; carried by the transport, not used.
#                                  `superseded_by` names the owner.
#   live_capable                   False for superseded channels — they are
#                                  never advertised, provisioned, or handled.
#   proven_live                    NOT here. It is an observed per-gateway
#                                  fact (a real connection, a real message)
#                                  reported by
#                                  get_gateway_personal_channel_surfaces, and
#                                  a catalog is the wrong place to claim it.

# `openclaw channel id -> the Empyralis channel_key that owns that platform`.
# Both lanes, because `slack` and `sms` are owned by Studio connectors while
# telegram/whatsapp/signal/imessage/wechat/discord are owned by personal ones.
# Computed from the same catalogs the ownership resolution reads, so it cannot
# name an owner the resolution disagrees with.
_OPENCLAW_FIRST_PARTY_OWNER_BY_ID: Dict[str, str] = {}
for _owner_channel_key in (
    *PERSONAL_CHANNEL_SPECS,
    *(str(_entry.get("channel_key") or "") for _entry in STUDIO_CHANNEL_ROADMAP),
):
    _owner_channel_id = openclaw_channel_registry.openclaw_id_for_platform_token(
        _first_party_platform_token(_owner_channel_key)
    )
    if (
        _owner_channel_id
        and OPENCLAW_TRANSPORT_OWNERSHIP.get(_owner_channel_id)
        == openclaw_channel_registry.OWNER_FIRST_PARTY
    ):
        _OPENCLAW_FIRST_PARTY_OWNER_BY_ID.setdefault(_owner_channel_id, _owner_channel_key)


def _openclaw_catalog_entry(channel: Any) -> Dict[str, Any]:
    active = OPENCLAW_TRANSPORT_OWNERSHIP.get(channel.id) == openclaw_channel_registry.OWNER_OPENCLAW
    superseded_by = _OPENCLAW_FIRST_PARTY_OWNER_BY_ID.get(channel.id)
    return {
        "channel_key": channel.channel_key,
        "binding_channel_key": channel.channel_key,
        # OpenClaw's own display name, from its own catalog — never a
        # parallel hand-written label map.
        "label": channel.label,
        "provider": OPENCLAW_TRANSPORT_PROVIDER,
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "category": "personal_runtime",
        "stage": "preview",
        "status": "openclaw_transport" if active else "superseded_by_first_party",
        "superseded_by": superseded_by,
        "live_capable": active,
        # Never launchable from the product surface: an OpenClaw channel is
        # credentialed inside OpenClaw on the owner's own machine, so a
        # "Connect" button here would be a dead control (product law).
        "launch_allowed": False,
        "requires_agent_computer": True,
        "account_provider": None,
        "connector_id": None,
        "openclaw_channel_id": channel.id,
        "policy_expressible": bool(channel.config_schema_present),
        "surface_support": ["sage"],
        "capabilities": ["manifest", "health", "inbound", "outbound", "text"] if active else [],
    }


OPENCLAW_CHANNEL_PLATFORM_CATALOG: tuple[Dict[str, Any], ...] = tuple(
    _openclaw_catalog_entry(channel) for channel in openclaw_channel_registry.CHANNELS
)

CHANNEL_PLATFORM_CATALOG = CHANNEL_PLATFORM_CATALOG + OPENCLAW_CHANNEL_PLATFORM_CATALOG

# The personal roadmap only ever describes channels on the live personal
# lane, so it carries the ACTIVE OpenClaw channels — a superseded one has no
# runtime to describe, and listing it here would put a second "Telegram" in
# front of a customer with no way to tell which one answers.
PERSONAL_CHANNEL_ROADMAP = PERSONAL_CHANNEL_ROADMAP + tuple(
    {
        "channel_key": channel.channel_key,
        "label": channel.label,
        "provider": OPENCLAW_TRANSPORT_PROVIDER,
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "stage": "preview",
        "live_capable": "true",
        "family": "personal",
        "session_owner": "paired_gateway",
    }
    for channel in OPENCLAW_ACTIVE_CHANNELS
)

if len(CHANNEL_PLATFORM_CATALOG) <= len(OPENCLAW_CHANNEL_PLATFORM_CATALOG):
    raise RuntimeError("First-party channels vanished from CHANNEL_PLATFORM_CATALOG.")


RESERVED_PRIVATE_RUNTIME_CHANNELS: tuple[Dict[str, str], ...] = (
    {"channel_key": "voice_wake", "label": "Voice/Wake", "status": "reserved_private_runtime"},
    {"channel_key": "mobile_nodes", "label": "Mobile nodes", "status": "reserved_private_runtime"},
    {"channel_key": "plugin_marketplace", "label": "Plugin marketplace", "status": "reserved_private_runtime"},
)

PUBLIC_STUDIO_WEBHOOK_ROUTES: Dict[str, Dict[str, str]] = {
    "/channels/whatsapp/twilio/webhook": {
        "provider": "twilio_whatsapp",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
    },
    "/channels/sms/twilio/webhook": {
        "provider": "twilio_sms",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
    },
    "/channels/telegram/webhook": {
        "provider": "telegram_bot_api",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
    },
    "/channels/slack/events": {
        "provider": "slack_events",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
    },
    "/channels/github/webhook": {
        "provider": "github_webhook",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
    },
    "/connectors/discord/webhook": {
        "provider": "discord_webhook",
        "runtime_lane": STUDIO_CONNECTOR_RUNTIME_LANE,
    },
}

_PERSONAL_FORBIDDEN_SESSION_KEYS = frozenset(
    {
        "responder_install_id",
        "master_install_id",
        "deployed_agent_id",
        "deployed_agent",
        "deployment_id",
        "connector_id",
        "session_key",
        "channel_key",
        "external_user_id",
    }
)


def _normalize_route_path(path: str) -> str:
    normalized_path = str(path or "").strip()
    if normalized_path == "/api":
        return "/"
    if normalized_path.startswith("/api/"):
        return normalized_path[4:]
    return normalized_path


def is_personal_channel_key(channel_key: str) -> bool:
    return str(channel_key or "").strip() in PERSONAL_CHANNEL_SPECS


def is_personal_route_path(path: str) -> bool:
    return _normalize_route_path(path).startswith(PERSONAL_ROUTE_PREFIX)


def is_public_studio_webhook_path(path: str) -> bool:
    candidate = _normalize_route_path(path)
    return candidate in PUBLIC_STUDIO_WEBHOOK_ROUTES


def personal_channel_catalog() -> list[Dict[str, str]]:
    return [dict(item) for item in PERSONAL_CHANNEL_ROADMAP]


def studio_channel_catalog() -> list[Dict[str, str]]:
    return [dict(item) for item in STUDIO_CHANNEL_ROADMAP]


def _catalog_surface_taxonomy(item: Dict[str, Any]) -> Dict[str, Any]:
    category = str(item.get("category") or "").strip().lower()
    runtime_lane = str(item.get("runtime_lane") or "").strip()
    if runtime_lane == PERSONAL_GATEWAY_RUNTIME_LANE:
        return {
            "surface_kind": "messaging_channel",
            "product_surface": "personal_messaging",
            "navigation_group": "personal_messaging",
            "extension_kind": "channel_adapter",
            "ownership_boundary": "agent_computer",
            "conversation_capable": True,
            "work_system_capable": False,
        }
    if category == "work_system":
        return {
            "surface_kind": "connected_app",
            "product_surface": "connected_app",
            "navigation_group": "connected_apps",
            "extension_kind": "app_connector",
            "ownership_boundary": "workspace_account",
            "conversation_capable": False,
            "work_system_capable": True,
        }
    return {
        "surface_kind": "messaging_channel",
        "product_surface": "business_channel",
        "navigation_group": "business_channels",
        "extension_kind": "channel_adapter",
        "ownership_boundary": "cloud_connector",
        "conversation_capable": True,
        "work_system_capable": False,
    }


def platform_channel_catalog(surface: str | None = None) -> list[Dict[str, Any]]:
    normalized_surface = str(surface or "").strip().lower()
    out: list[Dict[str, Any]] = []
    for item in CHANNEL_PLATFORM_CATALOG:
        payload = dict(item)
        payload.update(_catalog_surface_taxonomy(payload))
        support = payload.get("surface_support")
        if normalized_surface and isinstance(support, list) and normalized_surface not in support:
            continue
        out.append(payload)
    return out


def reserved_private_runtime_channel_catalog() -> list[Dict[str, str]]:
    return [dict(item) for item in RESERVED_PRIVATE_RUNTIME_CHANNELS]


def personal_bridge_preflight(channel_key: str) -> Dict[str, Any]:
    spec = assert_personal_gateway_channel(channel_key)
    live_capable = str(spec.get("live_capable") or "").strip().lower() == "true"
    if live_capable:
        return {
            "channel_key": channel_key,
            "provider": spec["provider"],
            "runtime_lane": spec["runtime_lane"],
            "status": "pass",
            "launch_allowed": True,
            "reason": "live_personal_gateway_runtime",
        }
    return {
        "channel_key": channel_key,
        "provider": spec["provider"],
        "runtime_lane": spec["runtime_lane"],
        "status": "blocked",
        "launch_allowed": False,
        "reason": "bridge_contract_not_live_enabled",
    }


def assert_personal_gateway_channel(channel_key: str, provider: str | None = None) -> Dict[str, str]:
    normalized_channel_key = str(channel_key or "").strip()
    spec = PERSONAL_CHANNEL_SPECS.get(normalized_channel_key)
    if not spec:
        raise ValueError(f"Channel lane contract rejected non-personal channel: {normalized_channel_key or 'unknown'}.")
    normalized_provider = str(provider or "").strip()
    expected_provider = spec["provider"]
    if normalized_provider and normalized_provider != expected_provider:
        raise ValueError(
            "Channel lane contract rejected mismatched personal provider "
            f"{normalized_provider!r} for {normalized_channel_key!r}."
        )
    payload = dict(spec)
    payload["channel_key"] = normalized_channel_key
    payload["provider"] = expected_provider
    return payload


def assert_personal_route_path(path: str) -> str:
    normalized_path = _normalize_route_path(path)
    if not is_personal_route_path(normalized_path):
        raise ValueError(f"Channel lane contract rejected non-personal route path: {normalized_path or 'unknown'}.")
    return normalized_path


def assert_public_studio_webhook_path(path: str) -> Dict[str, str]:
    normalized_path = _normalize_route_path(path)
    spec = PUBLIC_STUDIO_WEBHOOK_ROUTES.get(normalized_path)
    if not spec:
        raise ValueError(
            f"Channel lane contract rejected webhook route outside the Studio connector lane: {normalized_path or 'unknown'}."
        )
    payload = dict(spec)
    payload["path"] = normalized_path
    return payload


def build_personal_gateway_runtime_context(
    *,
    surface_channel: str,
    workspace_id: str,
    gateway_id: str,
    remote_jid: str,
) -> Dict[str, Any]:
    spec = assert_personal_gateway_channel(surface_channel)
    thread_id = f"{surface_channel}:{str(gateway_id or '').strip()}:{str(remote_jid or '').strip()}"
    session_ctx = {
        "workspace_id": str(workspace_id or "default").strip() or "default",
        "thread_id": thread_id,
        "surface_channel": surface_channel,
        "source": surface_channel,
        "runtime_lane": spec["runtime_lane"],
        "memory_surface": spec["memory_surface"],
    }
    assert_personal_runtime_session_ctx(session_ctx)
    return {
        "thread_id": thread_id,
        "availability": {
            "surface_channel": surface_channel,
            "source": surface_channel,
            "runtime_lane": spec["runtime_lane"],
            "memory_surface": spec["memory_surface"],
        },
        "session_ctx": session_ctx,
    }


def guard_personal_gateway_inbound_message(
    *,
    surface_channel: str,
    text: str,
    sender: str | None = None,
    source_event_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> external_content_guard.GuardedExternalContent:
    spec = assert_personal_gateway_channel(surface_channel)
    return external_content_guard.wrap_external_content(
        text,
        source="personal_channel",
        sender=sender,
        channel=surface_channel,
        source_event_id=source_event_id,
        metadata={"provider": spec["provider"], **dict(metadata or {})},
    )


def assert_personal_runtime_session_ctx(session_ctx: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(session_ctx or {})
    for forbidden_key in _PERSONAL_FORBIDDEN_SESSION_KEYS:
        if payload.get(forbidden_key):
            raise ValueError(
                "Channel lane contract rejected Studio deployment state in the personal runtime lane "
                f"via {forbidden_key!r}."
            )
    surface_channel = str(payload.get("surface_channel") or "").strip()
    if not is_personal_channel_key(surface_channel):
        raise ValueError(
            f"Channel lane contract rejected non-personal surface channel in personal session context: {surface_channel or 'unknown'}."
        )
    if str(payload.get("runtime_lane") or "").strip() != PERSONAL_GATEWAY_RUNTIME_LANE:
        raise ValueError("Channel lane contract rejected personal session context without the personal gateway runtime lane.")
    if str(payload.get("memory_surface") or "").strip() != DIRECT_CHAT_MEMORY_SURFACE:
        raise ValueError("Channel lane contract rejected personal session context without direct-chat memory isolation.")
    return payload

from __future__ import annotations

from typing import Any, Dict

from server_modules import external_content_guard


PERSONAL_GATEWAY_RUNTIME_LANE = "personal_gateway"
STUDIO_CONNECTOR_RUNTIME_LANE = "studio_business_connector"
DIRECT_CHAT_MEMORY_SURFACE = "direct_chat"

PERSONAL_ROUTE_PREFIX = "/personal-channels/"

PERSONAL_CHANNEL_SPECS: Dict[str, Dict[str, str]] = {
    "whatsapp_personal": {
        "provider": "whatsapp_baileys",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        "stage": "live",
        "live_capable": "true",
    },
    "telegram_personal": {
        "provider": "telegram_gramjs",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        "stage": "live",
        "live_capable": "true",
    },
    "signal_personal": {
        "provider": "signal_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        "stage": "live",
        "live_capable": "true",
    },
    "imessage_personal": {
        "provider": "bluebubbles_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        "stage": "live",
        "live_capable": "true",
    },
    "wechat_personal": {
        "provider": "wechat_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        "stage": "planned",
        "live_capable": "false",
    },
    "discord_personal": {
        "provider": "discord_bot",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "memory_surface": DIRECT_CHAT_MEMORY_SURFACE,
        "stage": "live",
        "live_capable": "true",
    },
}

PERSONAL_CHANNEL_ROADMAP: tuple[Dict[str, str], ...] = (
    {
        "channel_key": "telegram_personal",
        "label": "Telegram",
        "provider": "telegram_gramjs",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "stage": "live",
        "live_capable": "true",
        "family": "personal",
        "session_owner": "paired_gateway",
    },
    {
        "channel_key": "whatsapp_personal",
        "label": "WhatsApp",
        "provider": "whatsapp_baileys",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "stage": "live",
        "live_capable": "true",
        "family": "personal",
        "session_owner": "paired_gateway",
    },
    {
        "channel_key": "signal_personal",
        "label": "Signal",
        "provider": "signal_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "stage": "planned",
        "live_capable": "false",
        "family": "personal",
        "session_owner": "paired_gateway",
    },
    {
        "channel_key": "imessage_personal",
        "label": "iMessage",
        "provider": "bluebubbles_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "stage": "planned",
        "live_capable": "false",
        "family": "personal",
        "session_owner": "paired_gateway",
    },
    {
        "channel_key": "wechat_personal",
        "label": "WeChat",
        "provider": "wechat_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "stage": "planned",
        "live_capable": "false",
        "family": "personal",
        "session_owner": "paired_gateway",
    },
    {
        "channel_key": "discord_personal",
        "label": "Discord",
        "provider": "discord_bot",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
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
    {
        "channel_key": "telegram_personal",
        "binding_channel_key": "telegram_personal",
        "label": "Telegram Personal through Agent Computer",
        "provider": "telegram_gramjs",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "category": "personal_runtime",
        "stage": "live",
        "status": "agent_computer_only",
        "live_capable": True,
        "launch_allowed": False,
        "requires_agent_computer": True,
        "account_provider": "telegram_personal",
        "connector_id": None,
        "surface_support": ["sage"],
        "capabilities": ["inbound", "outbound", "personal_session"],
    },
    {
        "channel_key": "whatsapp_personal",
        "binding_channel_key": "whatsapp_personal",
        "label": "WhatsApp Personal through Agent Computer",
        "provider": "whatsapp_baileys",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "category": "personal_runtime",
        "stage": "live",
        "status": "agent_computer_only",
        "live_capable": True,
        "launch_allowed": False,
        "requires_agent_computer": True,
        "account_provider": "whatsapp_personal",
        "connector_id": None,
        "surface_support": ["sage"],
        "capabilities": ["inbound", "outbound", "personal_session"],
    },
    {
        "channel_key": "discord_personal",
        "binding_channel_key": "discord_personal",
        "label": "Discord Personal DM",
        "provider": "discord_bot",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
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
    {
        "channel_key": "signal_personal",
        "binding_channel_key": "signal_personal",
        "label": "Signal Personal through Agent Computer",
        "provider": "signal_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "category": "personal_runtime",
        "stage": "live",
        "status": "agent_computer_bridge",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": True,
        "account_provider": "signal_personal",
        "connector_id": None,
        "surface_support": ["sage"],
        "capabilities": ["manifest", "health", "inbound", "outbound", "text"],
    },
    {
        "channel_key": "imessage_personal",
        "binding_channel_key": "imessage_personal",
        "label": "iMessage Personal through Agent Computer",
        "provider": "bluebubbles_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "category": "personal_runtime",
        "stage": "live",
        "status": "agent_computer_bridge",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": True,
        "account_provider": "imessage_personal",
        "connector_id": None,
        "surface_support": ["sage"],
        "capabilities": ["manifest", "health", "inbound", "outbound", "text"],
    },
    {
        "channel_key": "wechat_personal",
        "binding_channel_key": "wechat_personal",
        "label": "WeChat Personal through Agent Computer",
        "provider": "wechat_local_bridge",
        "runtime_lane": PERSONAL_GATEWAY_RUNTIME_LANE,
        "category": "personal_runtime",
        "stage": "live",
        "status": "agent_computer_bridge",
        "live_capable": False,
        "launch_allowed": False,
        "requires_agent_computer": True,
        "account_provider": "wechat_personal",
        "connector_id": None,
        "surface_support": ["sage"],
        "capabilities": ["manifest", "health", "inbound", "outbound", "text"],
    },
)

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

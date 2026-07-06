from __future__ import annotations
import os

from copy import deepcopy
from typing import Any, Dict, Iterable, Optional

from server_modules import (
    channel_lane_contract_service,
    connection_oauth_service,
    connection_readiness_service,
    gateway_registry_service,
    gateway_state_repository,
    personal_channels_repository,
    runtime_common,
    sage_agent_computer_selection_service,
)
from server_modules.runtime_config import CONNECTOR_CATALOG, CHANNEL_REGISTRY


LANE_SAGE_PERSONAL_CHANNEL = "sage_personal_channel"
LANE_STUDIO_BUSINESS_CHANNEL = "studio_business_channel"
LANE_WORK_APP_CONNECTOR = "work_app_connector"
LANE_AGENT_COMPUTER = "agent_computer"
LANE_APPLICATION = "application"
LANE_MCP_PLUGIN = "mcp_plugin"
LANE_AI = "ai"
LANE_SKILL = "skill"

LAUNCH_LIVE = "live"
LAUNCH_LIVE_WHEN_CONFIGURED = "live_when_configured"
LAUNCH_PARTIAL = "partial"
LAUNCH_PLANNED = "planned"
LAUNCH_LOCKED = "locked"

_USABLE_LAUNCH_STATUSES = {LAUNCH_LIVE, LAUNCH_LIVE_WHEN_CONFIGURED}
_GENERIC_CONNECTION_TEST_RUNNER = "not_implemented"
_OAUTH_SETUP_KINDS = {"oauth", "oauth_or_app_install", "oauth_mailbox"}
DISPLAY_CONNECTED = "connected"
DISPLAY_CONNECTABLE = "connectable"
DISPLAY_REQUIRES_SETUP = "requires_setup"
DISPLAY_REQUIRES_AGENT_COMPUTER = "requires_agent_computer"
DISPLAY_UNAVAILABLE = "unavailable"


def _text(value: Any, fallback: str = "") -> str:
    candidate = str(value or "").strip()
    return candidate or fallback


def _token(value: Any) -> str:
    return _text(value).lower()


def _gateway_is_online(gateway: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(gateway, dict):
        return False
    connection_status = _token(gateway.get("connection_status"))
    if connection_status in {"online", "connected"}:
        return True
    if bool(gateway.get("heartbeat_fresh")) and _token(gateway.get("status")) == "active":
        return True
    return False


def _media(*, text: bool = False, images: bool = False, files: bool = False, voice: bool = False) -> Dict[str, bool]:
    return {
        "text": bool(text),
        "images": bool(images),
        "files": bool(files),
        "voice": bool(voice),
    }


def _item(
    *,
    connection_id: str,
    display_name: str,
    lane: str,
    surfaces: Iterable[str],
    setup_kind: str,
    launch_status: str,
    description: str,
    requires_gateway: bool = False,
    supports_inbound: bool = False,
    supports_outbound: bool = False,
    media_support: Optional[Dict[str, bool]] = None,
    approval_policy: str = "none",
    health_check: str = "none",
    test_action: Optional[str] = None,
    connector_ids: Optional[list[str]] = None,
    provider: Optional[str] = None,
    runtime_provider: Optional[str] = None,
    vault_provider: Optional[str] = None,
    connector_id: Optional[str] = None,
    account_provider: Optional[str] = None,
    setup_available: Optional[bool] = None,
    runtime_usable: Optional[bool] = None,
) -> Dict[str, Any]:
    normalized_status = _token(launch_status) or LAUNCH_LOCKED
    usable = normalized_status in _USABLE_LAUNCH_STATUSES if runtime_usable is None else bool(runtime_usable)
    setup = usable if setup_available is None else bool(setup_available)
    normalized_connector_id = connector_id or connection_id
    normalized_account_provider = account_provider or normalized_connector_id
    normalized_runtime_provider = runtime_provider or provider or connection_id
    normalized_vault_provider = vault_provider or normalized_account_provider
    auth_catalog = CONNECTOR_CATALOG.get(normalized_vault_provider) or CONNECTOR_CATALOG.get(normalized_connector_id) or CHANNEL_REGISTRY.get(normalized_vault_provider) or CHANNEL_REGISTRY.get(normalized_connector_id) or {}
    auth_required_fields = list(auth_catalog.get("auth") or [])
    launch_blockers = _static_launch_blockers(
        launch_status=normalized_status,
        setup_available=setup,
        runtime_usable=usable,
    )
    return connection_readiness_service.decorate_catalog_item({
        "id": connection_id,
        "display_name": display_name,
        "lane": lane,
        "surface": list(dict.fromkeys(_text(surface) for surface in surfaces if _text(surface))),
        "setup_kind": setup_kind,
        "launch_status": normalized_status,
        "description": description,
        "requires_gateway": bool(requires_gateway),
        "supports_inbound": bool(supports_inbound),
        "supports_outbound": bool(supports_outbound),
        "media_support": dict(media_support or _media()),
        "approval_policy": approval_policy,
        "health_check": health_check,
        "test_action": test_action,
        "connector_ids": list(connector_ids or [normalized_connector_id]),
        "provider": normalized_runtime_provider,
        "runtime_provider": normalized_runtime_provider,
        "vault_provider": normalized_vault_provider,
        "connector_id": normalized_connector_id,
        "account_provider": normalized_account_provider,
        "setup_available": setup,
        "runtime_usable": usable,
        "launchable": usable and setup and not launch_blockers,
        "launch_blockers": launch_blockers,
        "proof_blockers": ["generic_connection_test_not_implemented"] if test_action else [],
        "auth_required_fields": auth_required_fields,
        "test_runner": _GENERIC_CONNECTION_TEST_RUNNER if test_action else "not_required",
    })


def _static_launch_blockers(
    *,
    launch_status: str,
    setup_available: bool,
    runtime_usable: bool,
) -> list[str]:
    blockers: list[str] = []
    normalized_status = _token(launch_status)
    if normalized_status not in _USABLE_LAUNCH_STATUSES:
        blockers.append(f"launch_status:{normalized_status or LAUNCH_LOCKED}")
    if not setup_available:
        blockers.append("setup_unavailable")
    if not runtime_usable:
        blockers.append("runtime_unusable")
    return blockers


_CATALOG: tuple[Dict[str, Any], ...] = (
    _item(
        connection_id="agent_computer",
        display_name="Agent Computer",
        lane=LANE_AGENT_COMPUTER,
        surfaces=("sage", "studio", "agent_computer"),
        setup_kind="gateway_pairing",
        launch_status=LAUNCH_LIVE,
        description="Gateway, Supervisor, permissions, and local runtime for hardware-backed work.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True, images=True, files=True),
        approval_policy="hardware_policy",
        health_check="gateway_registration",
        test_action="health_check",
    ),
    _item(
        connection_id="telegram_personal",
        display_name="Your Telegram",
        lane=LANE_SAGE_PERSONAL_CHANNEL,
        surfaces=("sage", "agent_computer"),
        setup_kind="phone_code_2fa",
        launch_status=LAUNCH_LIVE,
        description="Personal Telegram account through the selected Agent Computer or cloud session manager.",
        requires_gateway=not os.environ.get("CLOUD_SESSION_MANAGER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on"),
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="owner_approval_required",
        health_check="personal_channel_state",
        test_action="send_text",
        connector_ids=["telegram_personal", "telegram_gramjs"],
        provider="telegram_gramjs",
        runtime_provider="telegram_gramjs",
        connector_id="telegram_personal",
        account_provider="telegram_personal",
        vault_provider="telegram_personal",
    ),
    _item(
        connection_id="whatsapp_personal",
        display_name="Your WhatsApp",
        lane=LANE_SAGE_PERSONAL_CHANNEL,
        surfaces=("sage", "agent_computer"),
        setup_kind="qr_pairing",
        launch_status=LAUNCH_LIVE,
        description="Personal WhatsApp account through the selected Agent Computer.",
        requires_gateway=True,
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="owner_approval_required",
        health_check="personal_channel_state",
        test_action="send_text",
        connector_ids=["whatsapp_personal", "whatsapp_baileys"],
        provider="whatsapp_baileys",
        runtime_provider="whatsapp_baileys",
        connector_id="whatsapp_personal",
        account_provider="whatsapp_personal",
        vault_provider="whatsapp_personal",
    ),
    _item(
        connection_id="signal_personal",
        display_name="Signal",
        lane=LANE_SAGE_PERSONAL_CHANNEL,
        surfaces=("sage", "agent_computer"),
        setup_kind="local_bridge",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Private Signal through the selected Agent Computer bridge using signal-cli.",
        requires_gateway=True,
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="owner_approval_required",
        health_check="bridge_runtime",
        provider="signal_local_bridge",
        runtime_provider="signal_local_bridge",
        connector_id="signal_personal",
        account_provider="signal_personal",
        vault_provider="signal_personal",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="imessage_personal",
        display_name="iMessage",
        lane=LANE_SAGE_PERSONAL_CHANNEL,
        surfaces=("sage", "agent_computer"),
        setup_kind="mac_bridge",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Private iMessage through the selected Agent Computer bridge using BlueBubbles.",
        requires_gateway=True,
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="owner_approval_required",
        health_check="bridge_runtime",
        provider="bluebubbles_local_bridge",
        runtime_provider="bluebubbles_local_bridge",
        connector_id="imessage_personal",
        account_provider="imessage_personal",
        vault_provider="imessage_personal",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="wechat_personal",
        display_name="WeChat",
        lane=LANE_SAGE_PERSONAL_CHANNEL,
        surfaces=("sage", "agent_computer"),
        setup_kind="local_bridge",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Planned private WeChat through a selected Agent Computer bridge. Not launch-ready until the local bridge runtime is certified.",
        requires_gateway=True,
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="owner_approval_required",
        health_check="bridge_runtime",
        provider="wechat_local_bridge",
        runtime_provider="wechat_local_bridge",
        connector_id="wechat_personal",
        account_provider="wechat_personal",
        vault_provider="wechat_personal",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="telegram_bot",
        display_name="Telegram Bot",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("sage", "studio"),
        setup_kind="bot_token",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Cloud Telegram bot channel. Separate from your personal Telegram on Agent Computer.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="studio_channel_policy",
        health_check="bot_token_verify",
        test_action="send_text",
        connector_ids=["telegram_bot"],
        provider="telegram_bot_api",
        runtime_provider="telegram_bot_api",
        connector_id="telegram_bot",
        account_provider="telegram_bot",
        vault_provider="telegram_bot",
    ),
    _item(
        connection_id="sage_telegram_hosted",
        display_name="Sage on Telegram",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("sage",),
        setup_kind="first_party_bot_pairing",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Official Empyralis Telegram bot. Send a pairing code from your Empyralis settings to connect.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="channel_policy",
        health_check="first_party_bot_health",
        provider="sage_telegram_hosted_bot",
        runtime_provider="sage_telegram_hosted_bot",
        connector_id="sage_telegram_hosted",
        account_provider="sage_telegram_hosted",
        vault_provider="sage_telegram_hosted",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="web_chat",
        display_name="Web Chat",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("studio",),
        setup_kind="web_widget",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Planned customer web chat channel.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="studio_channel_policy",
        health_check="webhook_health",
        runtime_usable=True,
        setup_available=True,
    ),
    _item(
        connection_id="email",
        display_name="Email",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("sage", "studio"),
        setup_kind="oauth_mailbox",
        launch_status=LAUNCH_PARTIAL,
        description="Use Google Workspace or Microsoft 365 before exposing mailbox setup.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="channel_policy",
        health_check="mailbox_credential",
        connector_ids=["email", "smtp", "google_workspace", "microsoft_365"],
        provider="workspace_mailbox",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="slack",
        display_name="Slack",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("sage", "studio"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Install Slack with OAuth and route signed mentions or DMs into Sage/Studio channels.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="channel_policy",
        health_check="oauth_credential",
        provider="slack_events",
        runtime_provider="slack_events",
        connector_id="slack",
        account_provider="slack",
        vault_provider="slack",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="discord_bot",
        display_name="Discord",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("sage", "studio"),
        setup_kind="oauth_or_app_install",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Install a Discord bot and route signed messages or interactions into Sage/Studio channels.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="channel_policy",
        health_check="bot_health",
        provider="discord_webhook",
        runtime_provider="discord_webhook",
        connector_id="discord_bot",
        account_provider="discord_bot",
        vault_provider="discord_bot",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="whatsapp_twilio",
        display_name="WhatsApp Business",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("sage", "studio"),
        setup_kind="provider_webhook",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Planned business WhatsApp provider channel.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="channel_policy",
        health_check="webhook_health",
        provider="twilio_whatsapp",
        runtime_provider="twilio_whatsapp",
        connector_id="whatsapp_twilio",
        account_provider="whatsapp_twilio",
        vault_provider="whatsapp_twilio",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="apple_messages_business",
        display_name="Apple Messages for Business",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("sage", "studio"),
        setup_kind="msp_business_messaging",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Planned official Apple Messages for Business channel through an approved messaging service provider. Separate from private iMessage on Agent Computer.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="business_messaging_policy",
        health_check="msp_webhook_health",
        provider="apple_messages_business_msp",
        runtime_provider="apple_messages_business_msp",
        connector_id="apple_messages_business",
        account_provider="apple_messages_business",
        vault_provider="apple_messages_business",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="google_workspace",
        display_name="Google Workspace",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="One Google connection for Gmail, Calendar, and Drive.",
        supports_inbound=False,
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        test_action="credential_health",
        connector_ids=["google_workspace", "gmail", "google_calendar", "google_drive"],
        provider="google_workspace",
        runtime_provider="google_workspace",
        connector_id="google_workspace",
        account_provider="google_workspace",
        vault_provider="google_workspace",
    ),
    _item(
        connection_id="microsoft_365",
        display_name="Microsoft 365",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="One Microsoft connection for Outlook mail, Calendar, and OneDrive.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        test_action="credential_health",
        provider="microsoft_365",
        runtime_provider="microsoft_365",
        connector_id="microsoft_365",
        account_provider="microsoft_365",
        vault_provider="microsoft_365",
    ),
    _item(
        connection_id="github",
        display_name="GitHub",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth_or_app_install",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect GitHub for repository actions and signed Issues/PR/push webhooks.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        provider="github",
        runtime_provider="github",
        connector_id="github",
        account_provider="github",
        vault_provider="github",
        setup_available=True,
        runtime_usable=True,
    ),
    _item(
        connection_id="notion",
        display_name="Notion",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Notion for page, database, and approved write actions.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="notion",
        connector_id="notion",
        account_provider="notion",
        vault_provider="notion",
    ),
    _item(
        connection_id="linear",
        display_name="Linear",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Linear for teams, issues, projects, and approved issue updates.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="linear",
        connector_id="linear",
        account_provider="linear",
        vault_provider="linear",
    ),
    _item(
        connection_id="dropbox",
        display_name="Dropbox",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Dropbox for folder, file, shared-link, and approved storage actions.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="dropbox",
        connector_id="dropbox",
        account_provider="dropbox",
        vault_provider="dropbox",
    ),
    _item(
        connection_id="figma",
        display_name="Figma",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Figma for design files, metadata, and comment context.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="figma",
        connector_id="figma",
        account_provider="figma",
        vault_provider="figma",
    ),
    _item(
        connection_id="todoist",
        display_name="Todoist",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Todoist for personal tasks, projects, and approved task updates.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="todoist",
        connector_id="todoist",
        account_provider="todoist",
        vault_provider="todoist",
    ),
    _item(
        connection_id="airtable",
        display_name="Airtable",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Airtable for bases, records, schema, and approved record updates.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="airtable",
        connector_id="airtable",
        account_provider="airtable",
        vault_provider="airtable",
    ),
    _item(
        connection_id="canva",
        display_name="Canva",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Canva for design metadata, folders, and asset context.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="canva",
        connector_id="canva",
        account_provider="canva",
        vault_provider="canva",
    ),
    _item(
        connection_id="asana",
        display_name="Asana",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Asana for tasks, projects, workspaces, and approved project updates.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="asana",
        connector_id="asana",
        account_provider="asana",
        vault_provider="asana",
    ),
    _item(
        connection_id="hubspot",
        display_name="HubSpot",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect HubSpot for CRM contacts, companies, deals, and approved sales follow-up.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="hubspot",
        connector_id="hubspot",
        account_provider="hubspot",
        vault_provider="hubspot",
    ),
    _item(
        connection_id="zoom",
        display_name="Zoom",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Zoom for meeting profile, meeting context, and approved meeting workflows.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="zoom",
        connector_id="zoom",
        account_provider="zoom",
        vault_provider="zoom",
    ),
    _item(
        connection_id="calendly",
        display_name="Calendly",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Calendly for scheduling links, event types, and booking context.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="calendly",
        connector_id="calendly",
        account_provider="calendly",
        vault_provider="calendly",
    ),
    _item(
        connection_id="clickup",
        display_name="ClickUp",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect ClickUp for tasks, lists, workspaces, and approved project updates.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="clickup",
        connector_id="clickup",
        account_provider="clickup",
        vault_provider="clickup",
    ),
    _item(
        connection_id="jira",
        display_name="Jira",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Jira for issues, projects, comments, and approved project updates.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="jira",
        connector_id="jira",
        account_provider="jira",
        vault_provider="jira",
    ),
    _item(
        connection_id="stripe",
        display_name="Stripe",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Stripe for account, payment, customer, invoice, and revenue context.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="stripe",
        connector_id="stripe",
        account_provider="stripe",
        vault_provider="stripe",
    ),
    _item(
        connection_id="salesforce",
        display_name="Salesforce",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Salesforce for CRM records, leads, accounts, and approved sales workflows.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="salesforce",
        connector_id="salesforce",
        account_provider="salesforce",
        vault_provider="salesforce",
    ),
    _item(
        connection_id="webflow",
        display_name="Webflow",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Webflow for sites, pages, CMS, assets, and form context.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="webflow",
        connector_id="webflow",
        account_provider="webflow",
        vault_provider="webflow",
    ),
    _item(
        connection_id="monday",
        display_name="monday.com",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect monday.com for boards, updates, workspaces, and approved project workflows.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="monday",
        connector_id="monday",
        account_provider="monday",
        vault_provider="monday",
    ),
    _item(
        connection_id="box",
        display_name="Box",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Box for enterprise files, folders, and approved document workflows.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="box",
        connector_id="box",
        account_provider="box",
        vault_provider="box",
    ),
    _item(
        connection_id="gitlab",
        display_name="GitLab",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect GitLab for repositories, merge requests, issues, and approved engineering workflows.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="gitlab",
        connector_id="gitlab",
        account_provider="gitlab",
        vault_provider="gitlab",
    ),
    _item(
        connection_id="confluence",
        display_name="Confluence",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Confluence for spaces, pages, knowledge, and approved content workflows.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="confluence",
        connector_id="confluence",
        account_provider="confluence",
        vault_provider="confluence",
    ),
    _item(
        connection_id="miro",
        display_name="Miro",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Miro for boards, whiteboards, and approved planning workflows.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="miro",
        connector_id="miro",
        account_provider="miro",
        vault_provider="miro",
    ),
    _item(
        connection_id="intercom",
        display_name="Intercom",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Intercom for conversations, users, tickets, and approved support workflows.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="intercom",
        connector_id="intercom",
        account_provider="intercom",
        vault_provider="intercom",
    ),
    _item(
        connection_id="docusign",
        display_name="Docusign",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Docusign for envelopes, signing status, and approved document workflows.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="docusign",
        connector_id="docusign",
        account_provider="docusign",
        vault_provider="docusign",
    ),
    _item(
        connection_id="square",
        display_name="Square",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Square for seller profile, customers, orders, payments, and invoices.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="square",
        connector_id="square",
        account_provider="square",
        vault_provider="square",
    ),
    _item(
        connection_id="typeform",
        display_name="Typeform",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Typeform for forms, responses, accounts, and approved form workflows.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="typeform",
        connector_id="typeform",
        account_provider="typeform",
        vault_provider="typeform",
    ),
    _item(
        connection_id="vercel",
        display_name="Vercel",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="oauth",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Vercel for account identity, projects, deployments, and approved developer workflows.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="oauth_credential",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="vercel",
        connector_id="vercel",
        account_provider="vercel",
        vault_provider="vercel",
    ),
    _item(
        connection_id="s3",
        display_name="Amazon S3",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="access_key",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Amazon S3 for bucket, object, presigned URL, and approved storage actions.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="credential_health",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="s3",
        connector_id="s3",
        account_provider="s3",
        vault_provider="s3",
    ),
    _item(
        connection_id="smtp",
        display_name="SMTP / IMAP Email",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="smtp_imap_credentials",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect a custom mailbox for direct send and fetch actions. Durable email-as-channel routing remains separate.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="workspace_app_policy",
        health_check="credential_health",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="smtp",
        connector_id="smtp",
        account_provider="smtp",
        vault_provider="smtp",
    ),
    _item(
        connection_id="wechat_work",
        display_name="WeChat Work",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="webhook_url",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect WeChat Work for approved outbound workspace messages through a webhook.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="webhook_health",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="wechat_work",
        connector_id="wechat_work",
        account_provider="wechat_work",
        vault_provider="wechat_work",
    ),
    _item(
        connection_id="instagram_business",
        display_name="Instagram Business",
        lane=LANE_WORK_APP_CONNECTOR,
        surfaces=("sage", "studio", "apps"),
        setup_kind="graph_api_token",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Connect Instagram Business for approved comment replies and direct-message sends.",
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="workspace_app_policy",
        health_check="credential_health",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="instagram_business",
        connector_id="instagram_business",
        account_provider="instagram_business",
        vault_provider="instagram_business",
    ),
    _item(
        connection_id="webhook",
        display_name="Webhook",
        lane=LANE_STUDIO_BUSINESS_CHANNEL,
        surfaces=("studio", "apps"),
        setup_kind="advanced_custom_api",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Custom API and webhook workflows use the Advanced setup surface.",
        supports_inbound=True,
        supports_outbound=True,
        media_support=_media(text=True),
        approval_policy="channel_policy",
        health_check="webhook_health",
        setup_available=True,
        runtime_usable=True,
        runtime_provider="webhook",
        connector_id="custom_api",
        account_provider="custom_api",
        vault_provider="custom_api",
    ),
    _item(
        connection_id="mcp",
        display_name="MCP",
        lane=LANE_MCP_PLUGIN,
        surfaces=("sage", "studio"),
        setup_kind="mcp_server",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="MCP servers and plugin packages.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="tool_policy",
        health_check="mcp_server_health",
        test_action="tool_discovery",
        connector_ids=["mcp", "mcp_plugin"],
        runtime_provider="mcp",
        connector_id="mcp",
        account_provider="mcp",
        vault_provider="mcp",
    ),
    _item(
        connection_id="applications",
        display_name="Applications",
        lane=LANE_APPLICATION,
        surfaces=("sage", "applications"),
        setup_kind="hosted_application",
        launch_status=LAUNCH_LIVE_WHEN_CONFIGURED,
        description="Mini-apps hosted inside Empyralis.",
        supports_outbound=True,
        media_support=_media(text=True, files=True),
        approval_policy="application_policy",
        health_check="app_launch_health",
        test_action="launch_health",
        connector_ids=["applications", "mini_apps"],
        runtime_provider="applications",
        connector_id="applications",
        account_provider="applications",
        vault_provider="applications",
    ),
)


def _matches_surface(item: Dict[str, Any], surface: Optional[str]) -> bool:
    normalized = _token(surface)
    if not normalized:
        return True
    surfaces = item.get("surface") if isinstance(item.get("surface"), list) else []
    return normalized in {_token(surface_item) for surface_item in surfaces}


def catalog_items(*, surface: Optional[str] = None) -> list[Dict[str, Any]]:
    return [deepcopy(item) for item in _CATALOG if _matches_surface(item, surface)]


def catalog_item(connection_id: str) -> Optional[Dict[str, Any]]:
    normalized = _token(connection_id)
    for item in _CATALOG:
        if _token(item.get("id")) == normalized:
            return deepcopy(item)
    for item in _CATALOG:
        if normalized in {_token(alias) for alias in item.get("connector_ids", []) if _text(alias)}:
            return deepcopy(item)
    return None


def _selected_gateway(
    *,
    workspace_id: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    selected_gateway_id: Optional[str] = None,
) -> tuple[Optional[Dict[str, Any]], list[Dict[str, Any]], Optional[str]]:
    requested = _text(selected_gateway_id)
    if not requested and user_id:
        selection = sage_agent_computer_selection_service.get_selection(
            workspace_id=workspace_id,
            user_id=user_id,
        )
        requested = _text((selection or {}).get("selected_gateway_id"))
    try:
        registrations = gateway_state_repository.list_workspace_gateway_registrations(
            workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            include_revoked=False,
        )
    except Exception:
        return None, [], requested or None
    public_registrations = [gateway_registry_service.gateway_registration_public_payload(item) for item in registrations]
    if requested:
        return next((item for item in public_registrations if _text(item.get("gateway_id")) == requested), None), public_registrations, requested
    return None, public_registrations, None


def _vault_connector_ids(workspace_id: str, agent_install_id: Optional[str] = None) -> set[str]:
    """Provider/connector tokens that have a vault credential in the workspace.

    When `agent_install_id` is given, only credentials scoped to THAT agent are
    counted — workspace/global-scoped (unassigned legacy) credentials, like the
    ws-1 GitHub credential, are excluded from every agent's set."""
    try:
        rows = runtime_common.list_vault_connectors(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
        )
    except Exception:
        return set()
    out: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("connector", "provider"):
            token = _token(row.get(key))
            if token:
                out.add(token)
    return out


def _vault_credential_provider_tokens(workspace_id: str) -> Dict[str, str]:
    """credential_id -> provider/connector token, for every vault credential
    that actually exists in the workspace (regardless of which agent owns it).
    Used to verify a binding's binding.credential_id still points at a live
    credential — under the project-scoped reuse model (UI Phase 1), a binding
    can legitimately reference a credential owned by a DIFFERENT agent, so
    checking "this agent's own vault rows" is no longer sufficient (or correct)."""
    try:
        rows = runtime_common.list_vault_connectors(workspace_id=workspace_id)
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cid = _text(row.get("id"))
        token = _token(row.get("connector") or row.get("provider"))
        if cid and token:
            out[cid] = token
    return out


def _connector_aliases(item: Dict[str, Any]) -> set[str]:
    """The set of tokens (item id + connector/provider aliases) used to match a
    catalog item against vault credential provider tokens and binding keys."""
    item_id = _text(item.get("id"))
    aliases = {_token(alias) for alias in item.get("connector_ids", []) if _text(alias)}
    aliases.add(_token(item_id))
    for key in ("connector_id", "account_provider", "vault_provider", "provider", "runtime_provider"):
        token = _token(item.get(key))
        if token:
            aliases.add(token)
    aliases.discard("")
    return aliases


def _personal_channel_state(connection_id: str, gateway_id: str) -> Optional[Dict[str, Any]]:
    if connection_id == "telegram_personal":
        return personal_channels_repository.get_telegram_state(gateway_id, channel_key="telegram_personal")
    if connection_id == "whatsapp_personal":
        return personal_channels_repository.get_whatsapp_state(gateway_id, channel_key="whatsapp_personal")
    return None


def _records_by_channel(value: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(value, dict):
        iterable = value.values()
    elif isinstance(value, list):
        iterable = value
    else:
        iterable = []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        channel_key = _token(item.get("channel_key") or item.get("channelKey"))
        if channel_key:
            out[channel_key] = dict(item)
    return out


def _selected_gateway_local_bridge_state(connection_id: str, selected_gateway: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if connection_id not in {"signal_personal", "imessage_personal", "wechat_personal"}:
        return None
    metadata = selected_gateway.get("metadata") if isinstance(selected_gateway, dict) else {}
    if not isinstance(metadata, dict):
        return None
    manifests_by_key = _records_by_channel(metadata.get("personal_channel_manifests"))
    health_by_key = _records_by_channel(metadata.get("personal_channel_health"))
    manifest = manifests_by_key.get(connection_id, {})
    health = health_by_key.get(connection_id, {})
    if not manifest and not health:
        return None

    status = _token(health.get("status") or health.get("health") or manifest.get("status") or "not_configured")
    connected = bool(health.get("connected") is True or status == "connected")
    configured = connected or status not in {"", "not_configured", "missing", "unsupported", "planned", "locked"}
    return {
        "status": "connected" if connected else status or "not_configured",
        "connected": connected,
        "configured": configured,
        "last_error": health.get("last_error") or health.get("lastError"),
        "metadata": {
            "manifest": manifest,
            "health": health,
        },
    }


def _next_action(
    item: Dict[str, Any],
    *,
    connected: bool,
    configured: bool,
    selected_gateway: Optional[Dict[str, Any]],
    selected_gateway_online: bool,
) -> str:
    if connected and _token(item.get("id")) in {"signal_personal", "imessage_personal", "wechat_personal"}:
        return "manage"
    if _token(item.get("launch_status")) not in _USABLE_LAUNCH_STATUSES or not bool(item.get("runtime_usable")):
        return "locked"
    if bool(item.get("requires_gateway")) and not selected_gateway:
        return "choose_agent_computer"
    if bool(item.get("requires_gateway")) and not selected_gateway_online:
        return "gateway_offline"
    if connected:
        return "manage"
    if configured and item.get("test_action"):
        # Generic /connections/{id}/test is not a real test runner yet. Keep
        # unconnected configured items on the setup/manage surface instead of
        # advertising a dead primary action.
        return "connect"
    return "connect" if item.get("setup_available") else "locked"


def _display_state(
    item: Dict[str, Any],
    *,
    connected: bool,
    selected_gateway: Optional[Dict[str, Any]],
    selected_gateway_online: bool,
    health_status: str,
    next_action: str,
) -> str:
    if connected:
        return DISPLAY_CONNECTED
    if _token(item.get("launch_status")) not in _USABLE_LAUNCH_STATUSES or not bool(item.get("runtime_usable")):
        return DISPLAY_UNAVAILABLE
    if bool(item.get("requires_gateway")) and (not selected_gateway or not selected_gateway_online):
        return DISPLAY_REQUIRES_AGENT_COMPUTER
    if not bool(item.get("setup_available")):
        return DISPLAY_REQUIRES_SETUP if _token(health_status) == "setup_missing" else DISPLAY_UNAVAILABLE
    if _token(next_action) == "connect":
        return DISPLAY_CONNECTABLE
    return DISPLAY_REQUIRES_SETUP


def _oauth_setup_unconfigured(item: Dict[str, Any]) -> bool:
    if _token(item.get("setup_kind")) not in _OAUTH_SETUP_KINDS:
        return False
    try:
        return not connection_oauth_service.oauth_connection_configured(_text(item.get("id")))
    except Exception:
        return True


def status_items(
    *,
    workspace_id: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    surface: Optional[str] = None,
    selected_gateway_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    selected_gateway_result = _selected_gateway(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        selected_gateway_id=selected_gateway_id,
    )
    if len(selected_gateway_result) == 2:
        selected_gateway, registrations = selected_gateway_result
        requested_gateway_id = None
    else:
        selected_gateway, registrations, requested_gateway_id = selected_gateway_result
    selected_gateway_id_value = _text((selected_gateway or {}).get("gateway_id")) or _text(requested_gateway_id) or None
    selected_gateway_missing = bool(selected_gateway_id_value and not selected_gateway)
    selected_gateway_online = _gateway_is_online(selected_gateway)
    online_gateways = [
        registration
        for registration in registrations
        if _gateway_is_online(registration)
    ]
    online_gateway_candidate = online_gateways[0] if len(online_gateways) == 1 else None
    vault_connector_ids = _vault_connector_ids(workspace_id)
    out: list[Dict[str, Any]] = []
    for item in catalog_items(surface=surface):
        effective_item = deepcopy(item)
        item_id = _text(item.get("id"))
        lane = _token(item.get("lane"))
        connected = False
        configured = False
        health_status = "not_configured"
        test_status = "not_tested"
        last_error = None

        if lane == LANE_AGENT_COMPUTER:
            configured = bool(registrations)
            connected = bool(selected_gateway_online)
            health_status = "healthy" if connected else "gateway_missing" if selected_gateway_missing else "offline" if configured else "missing"
        elif lane == LANE_SAGE_PERSONAL_CHANNEL:
            configured = bool(selected_gateway_id_value)
            health_status = "gateway_missing" if not selected_gateway_id_value or selected_gateway_missing else "not_configured"
            if selected_gateway_id_value and not selected_gateway_missing:
                state = _personal_channel_state(item_id, selected_gateway_id_value)
                if state is None:
                    state = _selected_gateway_local_bridge_state(item_id, selected_gateway)
                state_status = _token((state or {}).get("status"))
                connected = state_status == "connected" or bool((state or {}).get("connected") is True)
                configured = connected or bool((state or {}).get("configured")) or (
                    state is not None and state_status not in {"", "not_configured", "missing"}
                )
                health_status = "healthy" if connected else state_status or health_status
                last_error = (state or {}).get("last_error")
        elif lane in {LANE_WORK_APP_CONNECTOR, LANE_STUDIO_BUSINESS_CHANNEL}:
            aliases = _connector_aliases(item)
            connected = bool(aliases & vault_connector_ids)
            configured = connected
            health_status = "healthy" if connected else "not_configured"
            # First-party bot pairing (e.g. hosted Telegram) — check in-memory
            # pairing state rather than vault entries.
            if not connected and item.get("setup_kind") == "first_party_bot_pairing":
                try:
                    from server_modules.sage_telegram_hosted_service import is_workspace_paired
                    if is_workspace_paired(workspace_id):
                        connected = True
                        configured = True
                        health_status = "healthy"
                except Exception:
                    pass
            if not connected and _oauth_setup_unconfigured(item):
                effective_item["setup_available"] = False
                health_status = "setup_missing"
                last_error = "OAuth provider credentials are not configured."
        elif lane == LANE_MCP_PLUGIN:
            health_status = "available"
        elif lane == LANE_APPLICATION:
            health_status = "available"

        next_action = _next_action(
            effective_item,
            connected=connected,
            configured=configured,
            selected_gateway=selected_gateway,
            selected_gateway_online=selected_gateway_online,
        )
        display_state = _display_state(
            effective_item,
            connected=connected,
            selected_gateway=selected_gateway,
            selected_gateway_online=selected_gateway_online,
            health_status=health_status,
            next_action=next_action,
        )
        payload = deepcopy(effective_item)
        payload.update({
            "connected": connected,
            "configured": configured,
            "display_state": display_state,
            "test_status": test_status,
            "health_status": health_status,
            "selected_gateway_id": selected_gateway_id_value,
            "selected_gateway_missing": selected_gateway_missing,
            "gateway_count": len(registrations),
            "online_gateway_count": len(online_gateways),
            "online_gateway_candidate": deepcopy(online_gateway_candidate) if online_gateway_candidate else None,
            "last_error": last_error,
            "next_action": next_action,
        })
        payload = connection_readiness_service.decorate_status_item(payload)
        out.append(payload)
    return out


async def agent_status_items(
    *,
    workspace_id: str,
    agent_id: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    surface: Optional[str] = None,
    selected_gateway_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """Agent-scoped connection status. `agent_id` (an agent install id) is
    REQUIRED — there is no workspace-wide fallback here; for the workspace view
    use status_items()/workspace_connection_summary() instead.

    Truth for connectors and channels:

        connected(agent, X) == (an enabled binding row exists)
                               AND (its binding.credential_id points at a
                                    credential that actually exists)

    UI Phase 1: a credential lives at PROJECT scope and agents SUBSCRIBE to it
    via the binding — the binding is the one source of truth for "is this
    agent using this connector," not whether the credential's own
    agent_install_id happens to match (it may be owned by a different agent in
    the same project). A workspace/global-scoped ("unassigned legacy")
    credential with no agent binding is therefore NOT connected for any agent.
    """
    resolved_agent = str(agent_id or "").strip()
    if not resolved_agent:
        raise ValueError("agent_id is required for agent-scoped connection status.")
    resolved_tenant = str(tenant_id or "").strip() or "default"

    # Base items carry the catalog + gateway/display state (workspace view).
    base = status_items(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        surface=surface,
        selected_gateway_id=selected_gateway_id,
    )

    # Every vault credential that actually exists in the workspace, by id —
    # guards against a binding whose credential_id points at something deleted.
    credential_tokens_by_id = _vault_credential_provider_tokens(workspace_id)

    # Enabled binding rows for this agent — the subscription itself.
    from server_modules import agent_bindings_repository as bindings
    connector_binding_rows = await bindings.list_agent_connector_bindings(
        tenant_id=resolved_tenant, workspace_id=workspace_id,
        agent_install_id=resolved_agent, enabled_only=True,
    )
    channel_binding_rows = await bindings.list_agent_channel_bindings(
        tenant_id=resolved_tenant, workspace_id=workspace_id,
        agent_install_id=resolved_agent, enabled_only=True,
    )
    enabled_connector_keys = {_token(b.get("key")) for b in connector_binding_rows}
    enabled_channel_keys = {_token(b.get("key")) for b in channel_binding_rows}
    # Connector keys whose binding still resolves to a live credential.
    subscribed_live_keys = {
        _token(b.get("key"))
        for b in connector_binding_rows
        if str((b.get("binding") or {}).get("credential_id") or "").strip() in credential_tokens_by_id
    }

    for item in base:
        lane = _token(item.get("lane"))
        aliases = _connector_aliases(item)
        if lane == LANE_WORK_APP_CONNECTOR:
            has_binding = bool(aliases & enabled_connector_keys)
            has_live_credential = bool(aliases & subscribed_live_keys)
            item["connected"] = has_binding and has_live_credential
            item["configured"] = item["connected"]
            item["health_status"] = "healthy" if item["connected"] else "not_configured"
        elif lane in {LANE_SAGE_PERSONAL_CHANNEL, LANE_STUDIO_BUSINESS_CHANNEL}:
            # Channel truth is gated by the agent's own enabled channel binding
            # on top of the underlying workspace channel/pairing state.
            has_binding = bool(aliases & enabled_channel_keys)
            item["connected"] = bool(item.get("connected")) and has_binding
            item["configured"] = bool(item.get("configured")) and has_binding
        item["agent_id"] = resolved_agent
    return base


async def workspace_connection_summary(
    *,
    workspace_id: str,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Honest workspace-aggregate counters for the Fleet Home strip.

    numerator = sum of ENABLED bindings across the workspace's agents;
    denominator = catalog size for that lane. (Computers are counted separately
    on the client from gateway registrations.)
    """
    resolved_tenant = str(tenant_id or "").strip() or "default"
    from server_modules import agent_bindings_repository as bindings
    connector_bindings = await bindings.list_workspace_connector_bindings(
        tenant_id=resolved_tenant, workspace_id=workspace_id, enabled_only=True,
    )
    channel_bindings = await bindings.list_workspace_channel_bindings(
        tenant_id=resolved_tenant, workspace_id=workspace_id, enabled_only=True,
    )
    connector_total = sum(
        1 for item in catalog_items(surface="apps")
        if _token(item.get("lane")) == LANE_WORK_APP_CONNECTOR
    )
    channel_total = sum(
        1 for item in catalog_items(surface="sage")
        if _token(item.get("lane")) in {LANE_SAGE_PERSONAL_CHANNEL, LANE_STUDIO_BUSINESS_CHANNEL}
    )
    return {
        "connectors": {"connected": len(connector_bindings), "total": connector_total},
        "channels": {"connected": len(channel_bindings), "total": channel_total},
    }


def list_catalog_payload(*, surface: Optional[str] = None) -> Dict[str, Any]:
    items = catalog_items(surface=surface)
    groups: Dict[str, int] = {}
    for item in items:
        lane = _text(item.get("lane"), "unknown")
        groups[lane] = groups.get(lane, 0) + 1
    return {
        "items": items,
        "count": len(items),
        "groups": groups,
    }


def list_status_payload(
    *,
    workspace_id: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    surface: Optional[str] = None,
    selected_gateway_id: Optional[str] = None,
) -> Dict[str, Any]:
    items = status_items(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        surface=surface,
        selected_gateway_id=selected_gateway_id,
    )
    groups: Dict[str, int] = {}
    for item in items:
        lane = _text(item.get("lane"), "unknown")
        groups[lane] = groups.get(lane, 0) + 1
    return {
        "items": items,
        "count": len(items),
        "groups": groups,
    }


def reject_if_unusable(connection_id: str) -> Dict[str, Any]:
    item = catalog_item(connection_id)
    if not item:
        raise ValueError("connection_not_found")
    if _token(item.get("launch_status")) not in _USABLE_LAUNCH_STATUSES or not bool(item.get("setup_available")):
        raise PermissionError("connection_not_launch_ready")
    return item


def studio_channel_catalog_items(surface: Optional[str] = None) -> list[Dict[str, Any]]:
    normalized_surface = _token(surface)
    connection_by_id = {_token(item.get("id")): item for item in catalog_items()}
    items: list[Dict[str, Any]] = []
    for raw in channel_lane_contract_service.platform_channel_catalog(normalized_surface or None):
        item = dict(raw)
        candidates = [
            _token(item.get("channel_key")),
            _token(item.get("connector_id")),
            _token(item.get("account_provider")),
        ]
        connection = next((connection_by_id[candidate] for candidate in candidates if candidate in connection_by_id), None)
        if connection:
            item["setup_kind"] = connection.get("setup_kind")
            item["health_check"] = connection.get("health_check")
            item["test_action"] = connection.get("test_action")
            item["media_support"] = connection.get("media_support")
            item["launch_status"] = connection.get("launch_status")
            item["runtime_usable"] = connection.get("runtime_usable")
            item["setup_available"] = connection.get("setup_available")
            item["runtime_provider"] = connection.get("runtime_provider") or item.get("provider")
            item["vault_provider"] = connection.get("vault_provider") or item.get("account_provider") or item.get("connector_id")
        else:
            item.setdefault("runtime_provider", item.get("provider"))
            item.setdefault("vault_provider", item.get("account_provider") or item.get("connector_id"))
        items.append(item)
    return items

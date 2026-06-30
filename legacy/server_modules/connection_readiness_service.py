from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


READINESS_PLANNED = "planned"
READINESS_IMPLEMENTATION_READY = "implementation_ready"
READINESS_LAUNCH_CERTIFIED = "launch_certified"
READINESS_DISABLED = "disabled"

_USABLE_LAUNCH_STATUSES = {"live", "live_when_configured"}
_PROVEN_STATIC_CONNECTION_IDS = {
    "agent_computer",
    "telegram_personal",
    "whatsapp_personal",
}
_LOCAL_BRIDGE_CONNECTION_IDS = {
    "signal_personal",
    "imessage_personal",
    "wechat_personal",
}
_EXTERNAL_ACCOUNT_CONNECTION_IDS = {
    "apple_messages_business",
    "whatsapp_twilio",
    "telegram_bot",
    "discord_bot",
    "slack",
    "google_workspace",
    "microsoft_365",
    "github",
    "notion",
    "linear",
    "dropbox",
    "s3",
    "smtp",
    "wechat_work",
    "instagram_business",
}

_CERTIFICATION_REQUIREMENTS: Dict[str, list[str]] = {
    "signal_personal": [
        "Select an online Sage Agent Computer.",
        "Install and configure the Signal local bridge on that Agent Computer.",
        "Pass bridge health, inbound event, outbound send, restart, and replay checks.",
    ],
    "imessage_personal": [
        "Select an online Mac Sage Agent Computer.",
        "Configure the BlueBubbles or approved macOS Messages bridge on that Mac.",
        "Pass bridge health, inbound event, outbound send, restart, and replay checks.",
    ],
    "wechat_personal": [
        "Select an online Sage Agent Computer.",
        "Configure a WeChat local bridge URL and token on that Agent Computer.",
        "Pass bridge health, inbound event, outbound send, restart, and replay checks.",
    ],
    "apple_messages_business": [
        "Create or attach an approved Apple Messages for Business account through an MSP.",
        "Configure inbound webhook verification and outbound send through the MSP API.",
        "Provide AI disclosure, human handoff, transcript retention, and support escalation.",
        "Pass inbound, outbound, approval, handoff, and replay certification tests.",
    ],
    "whatsapp_twilio": [
        "Connect a WhatsApp Business provider account.",
        "Configure webhook verification, inbound delivery, outbound send, and template handling.",
        "Pass inbound, outbound, approval, rate-limit, and replay certification tests.",
    ],
    "web_chat": [
        "Configure the hosted web-chat widget, signed session mapping, and allowed origins.",
        "Pass inbound, outbound, transcript, identity, and abuse-control certification tests.",
    ],
    "email": [
        "Use Google Workspace, Microsoft 365, or SMTP / IMAP until the generic mailbox channel is certified.",
        "Pass mailbox identity, inbound polling/webhook, outbound send, attachment, and approval tests.",
    ],
    "sage_telegram_hosted": [
        "Provision the official Empyralis Telegram bot (set EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN).",
        "User pairing via 6-digit code exchange.",
        "Pass inbound, outbound, and rate-limit certification tests.",
    ],
}


def _text(value: Any, fallback: str = "") -> str:
    candidate = str(value or "").strip()
    return candidate or fallback


def _token(value: Any) -> str:
    return _text(value).lower()


def certification_requirements(connection_id: str) -> list[str]:
    return list(_CERTIFICATION_REQUIREMENTS.get(_token(connection_id), []))


def _catalog_readiness(item: Dict[str, Any]) -> tuple[str, str]:
    connection_id = _token(item.get("id"))
    launch_status = _token(item.get("launch_status"))
    setup_available = bool(item.get("setup_available"))
    runtime_usable = bool(item.get("runtime_usable"))
    launchable = bool(item.get("launchable"))

    if launch_status == "locked":
        return READINESS_DISABLED, "connection_locked"
    if connection_id in _PROVEN_STATIC_CONNECTION_IDS and launch_status == "live" and setup_available and runtime_usable:
        return READINESS_LAUNCH_CERTIFIED, "runtime_certified"
    if launchable and launch_status in _USABLE_LAUNCH_STATUSES:
        return READINESS_IMPLEMENTATION_READY, "runtime_available_needs_account_certification"
    if launch_status in {"planned", "partial"}:
        return READINESS_PLANNED, f"launch_status:{launch_status}"
    return READINESS_PLANNED, "not_launch_ready"


def decorate_catalog_item(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(item)
    readiness_status, reason = _catalog_readiness(payload)
    connection_id = _token(payload.get("id"))
    requirements = certification_requirements(connection_id)
    payload.update({
        "readiness_status": readiness_status,
        "readiness_reason": reason,
        "launch_certified": readiness_status == READINESS_LAUNCH_CERTIFIED,
        "certification_required": readiness_status != READINESS_LAUNCH_CERTIFIED,
        "certification_requirements": requirements,
        "requires_external_account": connection_id in _EXTERNAL_ACCOUNT_CONNECTION_IDS,
        "requires_local_bridge": connection_id in _LOCAL_BRIDGE_CONNECTION_IDS,
    })
    return payload


def decorate_status_item(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = decorate_catalog_item(item)
    connection_id = _token(payload.get("id"))
    connected = bool(payload.get("connected"))
    launch_status = _token(payload.get("launch_status"))
    launchable = bool(payload.get("launchable"))
    health_status = _token(payload.get("health_status"))

    if connected and (launchable or connection_id in _LOCAL_BRIDGE_CONNECTION_IDS):
        payload.update({
            "readiness_status": READINESS_LAUNCH_CERTIFIED,
            "readiness_reason": "connected_runtime_certified",
            "launch_certified": True,
            "certification_required": False,
        })
    elif health_status == "setup_missing" and launch_status in _USABLE_LAUNCH_STATUSES:
        requirements = list(payload.get("certification_requirements") or [])
        if "Configure provider credentials before account setup." not in requirements:
            requirements.insert(0, "Configure provider credentials before account setup.")
        payload.update({
            "readiness_status": READINESS_IMPLEMENTATION_READY,
            "readiness_reason": "provider_setup_missing",
            "certification_required": True,
            "certification_requirements": requirements,
        })
    return payload

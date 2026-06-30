from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import HTTPException

from server_modules import auth as auth_module
from server_modules import gateway_state_repository
from server_modules import personal_channels_repository
from server_modules import run_state_repository
from server_modules import runtime_common
from server_modules import runtime_config
from server_modules import shared as shared_module
from server_modules.connectors.autopilot_runtime_exports import (
    handle_telegram_autopilot_status,
    handle_whatsapp_autopilot_status,
)
from server_modules.runtime_common import _safe_read_json


CHANNEL_PROVIDERS = ("telegram", "whatsapp")
PAIRING_FAILURE_ACTIONS = {"pairing_required", "pairing_failed"}
PERSONAL_MODE_PROVIDER = {
    "telegram": "telegram_gramjs",
    "whatsapp": "whatsapp_baileys",
}
QUICK_MODE_PROVIDER = {
    "telegram": "telegram_bot_api",
    "whatsapp": "twilio_whatsapp",
}
QUICK_MODE_CONNECTOR = {
    "telegram": "telegram_bot",
    "whatsapp": "whatsapp_twilio",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _normalize_workspace_token(value: Any) -> str:
    return str(value or "").strip()


def _normalize_channel_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_status_token(value: Any) -> str:
    return str(value or "").strip().lower() or "unknown"


def _parse_utc_ts(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except Exception:
        return None


def _issue_severity(issue: Dict[str, Any]) -> str:
    token = str(issue.get("severity") or "").strip().lower()
    if token in {"setup_needed", "degraded"}:
        return token
    return "degraded"


def _latest_workspace_gateway_registration(workspace_id: str) -> Dict[str, Any] | None:
    items = gateway_state_repository.list_workspace_gateway_registrations(
        workspace_id,
        include_revoked=False,
    )
    for item in items:
        if str(item.get("status") or "").strip().lower() == "active":
            return item
    return items[0] if items else None


def _personal_state_for_family(family: str, gateway_id: str | None) -> Dict[str, Any]:
    if not gateway_id:
        return {}
    if family == "telegram":
        return personal_channels_repository.get_telegram_state(
            str(gateway_id or "").strip(),
            channel_key="telegram_personal",
        ) or {}
    return personal_channels_repository.get_whatsapp_state(
        str(gateway_id or "").strip(),
        channel_key="whatsapp_personal",
    ) or {}


def _personal_linked_identity(family: str, state: Dict[str, Any]) -> str | None:
    if family == "telegram":
        for key in ("linked_name", "linked_username", "linked_phone", "linked_user_id"):
            token = str(state.get(key) or "").strip()
            if token:
                return token
        return None
    for key in ("linked_name", "linked_jid"):
        token = str(state.get(key) or "").strip()
        if token:
            return token
    return None


def _personal_mode_hint(family: str, registration: Dict[str, Any] | None, state: Dict[str, Any]) -> str:
    if not registration:
        return f"Pair a gateway to use your real {family.title()} account."
    status = _normalize_status_token(state.get("status"))
    if status == "connected":
        identity = _personal_linked_identity(family, state)
        return (
            f"Linked as {identity} on the paired gateway."
            if identity
            else f"{family.title()} personal is live on the paired gateway."
        )
    if status == "pairing_code_required":
        return "Enter the pairing code from the gateway on your phone in Linked Devices."
    if status == "qr_required":
        return "Scan the gateway QR from your phone to finish linking the personal account."
    if status == "authorization_required":
        login_hint = _normalize_status_token(
            state.get("login_hint") or _coerce_dict(state.get("metadata")).get("login_hint")
        )
        if family == "telegram":
            mapping = {
                "api_credentials_required": "Telegram personal needs API ID and API hash.",
                "phone_number_required": "Telegram personal needs your phone number.",
                "password_required": "Telegram personal is waiting for your Telegram 2FA password.",
            }
            return mapping.get(login_hint, "Telegram personal needs login inputs before it can connect.")
        if login_hint == "phone_number_invalid":
            return "WhatsApp personal needs a valid digits-only phone number with country code."
        return "WhatsApp personal needs a valid local login path before it can connect."
    if status == "code_required":
        return "Telegram personal is waiting for the login code sent to your phone."
    if status == "connecting":
        return f"{family.title()} personal is opening the local session through the gateway."
    if status in {"disconnected", "logged_out"}:
        detail = str(_coerce_dict(state.get("metadata")).get("last_disconnect_reason") or "").strip()
        return detail or f"{family.title()} personal disconnected and needs relink or retry."
    if status == "idle":
        return f"{family.title()} personal is enabled on the gateway but not linked yet."
    return f"{family.title()} personal is waiting on the paired gateway."


def _quick_mode_hint(family: str, provider_payload: Dict[str, Any]) -> str:
    workspace_status = _normalize_status_token(provider_payload.get("workspace_status"))
    connectors = [
        _coerce_dict(item) for item in _coerce_list(provider_payload.get("connectors"))
    ]
    issues = [_coerce_dict(item) for item in _coerce_list(provider_payload.get("issues"))]
    if workspace_status == "live":
        return f"{family.title()} quick mode is live through the Studio connector stack."
    if not connectors:
        if family == "telegram":
            return "Create a Telegram bot connector for the fastest setup path."
        return "Create a Twilio WhatsApp connector for the fastest setup path."
    first_issue = str((issues[0] or {}).get("message") or "").strip() if issues else ""
    return first_issue or f"{family.title()} quick mode needs connector attention before it is fully live."


def _build_channel_family_modes(
    *,
    family: str,
    workspace_id: str,
    provider_payload: Dict[str, Any],
) -> Dict[str, Any]:
    registration = _latest_workspace_gateway_registration(workspace_id)
    gateway_id = str((registration or {}).get("gateway_id") or "").strip() or None
    personal_state = _personal_state_for_family(family, gateway_id)
    personal_status = _normalize_status_token(
        personal_state.get("status") or ("gateway_required" if not registration else "idle")
    )
    quick_status = _normalize_status_token(
        provider_payload.get("workspace_status") or provider_payload.get("status") or "setup_needed"
    )
    personal_mode = {
        "id": f"{family}_personal",
        "family": family,
        "label": "Personal mode",
        "mode_kind": "personal",
        "strategy": "recommended_for_sage",
        "setup_weight": "advanced",
        "provider": PERSONAL_MODE_PROVIDER[family],
        "runtime_lane": "personal_gateway",
        "configured": bool(registration) and personal_status not in {"gateway_required", "unknown"},
        "current_status": personal_status,
        "description": f"Use your real {family.title()} account through the paired local gateway.",
        "best_for": "Sage acting as you from your own account and device-resident session.",
        "gateway_id": gateway_id,
        "linked_identity": _personal_linked_identity(family, personal_state),
        "setup_hint": _personal_mode_hint(family, registration, personal_state),
    }
    quick_mode = {
        "id": f"{family}_quick",
        "family": family,
        "label": "Quick mode",
        "mode_kind": "quick",
        "strategy": "quick_start",
        "setup_weight": "light",
        "provider": QUICK_MODE_PROVIDER[family],
        "runtime_lane": "studio_business_connector",
        "configured": bool(provider_payload.get("workspace_configured")),
        "current_status": quick_status,
        "description": (
            "Use a BotFather bot token through the Studio connector stack."
            if family == "telegram"
            else "Use Twilio WhatsApp through the Studio connector stack."
        ),
        "best_for": "Fast onboarding, cloud-managed reliability, and business-facing deployment flows.",
        "connector_count": len(_coerce_list(provider_payload.get("connectors"))),
        "setup_hint": _quick_mode_hint(family, provider_payload),
    }
    active_mode = (
        "personal"
        if personal_status == "connected"
        else "quick"
        if quick_status == "live"
        else None
    )
    return {
        "family": family,
        "label": family.title(),
        "summary": (
            "Choose between your own account on the local gateway and a fast bot-based Studio connector."
            if family == "telegram"
            else "Choose between your own account on the local gateway and a fast provider-managed Studio connector."
        ),
        "active_mode": active_mode,
        "modes": [personal_mode, quick_mode],
    }


def _workspace_status_from_issues(
    *,
    base_status: str,
    connectors: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
) -> str:
    normalized_base = str(base_status or "").strip().lower()
    if normalized_base == "disabled":
        if not connectors:
            return "disabled"
        if any(_issue_severity(issue) == "setup_needed" for issue in issues):
            return "setup_needed"
        if issues:
            return "degraded"
        return "setup_needed"
    if not connectors:
        return "setup_needed"
    if any(_issue_severity(issue) == "setup_needed" for issue in issues):
        return "setup_needed"
    if issues:
        return "degraded"
    return "live"


def _workspace_quick_vault_connectors(*, provider: str, workspace_id: str) -> List[Dict[str, Any]]:
    connector_id = QUICK_MODE_CONNECTOR.get(provider)
    if not connector_id:
        return []
    try:
        rows = runtime_common.list_vault_connectors(workspace_id)
    except Exception:
        return []
    items: List[Dict[str, Any]] = []
    for raw_item in rows:
        item = _coerce_dict(raw_item)
        if str(item.get("connector") or "").strip().lower() != connector_id:
            continue
        items.append(
            {
                "id": str(item.get("id") or "").strip(),
                "label": item.get("label"),
                "workspace_id": _normalize_workspace_token(item.get("workspace_id")),
                "profile_status": "configured",
                "profile_issue_code": None,
                "profile_issue": None,
                "last_processed_at": item.get("updated_at") or item.get("created_at"),
                "last_error": None,
                "last_error_category": None,
                "last_error_at": None,
                "webhook_path": None,
                "webhook_url": None,
            }
        )
    return items


def _workspace_relevant_provider_issues(
    *,
    provider: str,
    connectors: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    connectors_present = bool(connectors)
    for raw_issue in issues:
        issue = _coerce_dict(raw_issue)
        code = str(issue.get("code") or "").strip().lower()
        message = str(issue.get("message") or "").strip().lower()
        if connectors_present and code == f"{provider}_workspace_connector_missing":
            continue
        if connectors_present and "not scoped to an explicit workspace" in message:
            continue
        filtered.append(issue)
    return filtered


def _workspace_channel_payload(
    *,
    provider: str,
    workspace_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    payload_connectors = [
        _coerce_dict(item)
        for item in _coerce_list(payload.get("connectors"))
        if _normalize_workspace_token(_coerce_dict(item).get("workspace_id")) == workspace_id
    ]
    connectors_by_id = {
        str(item.get("id") or "").strip(): item
        for item in payload_connectors
        if str(item.get("id") or "").strip()
    }
    for item in _workspace_quick_vault_connectors(provider=provider, workspace_id=workspace_id):
        connector_id = str(item.get("id") or "").strip()
        if connector_id and connector_id not in connectors_by_id:
            connectors_by_id[connector_id] = item
    connectors = list(connectors_by_id.values())
    issues = [_coerce_dict(item) for item in _coerce_list(payload.get("issues")) if isinstance(item, dict)]
    if not connectors:
        issues.append(
            {
                "code": f"{provider}_workspace_connector_missing",
                "severity": "setup_needed",
                "message": f"No {provider.title()} connector is configured for this workspace.",
            }
        )
    for connector in connectors:
        profile_issue = str(connector.get("profile_issue") or "").strip()
        if profile_issue:
            issues.append(
                {
                    "code": str(connector.get("profile_issue_code") or f"{provider}_connector_profile_issue").strip(),
                    "severity": (
                        "setup_needed"
                        if str(connector.get("profile_status") or "").strip().lower() == "setup_needed"
                        else "degraded"
                    ),
                    "message": profile_issue,
                    "connector_id": str(connector.get("id") or "").strip() or None,
                }
            )
        last_error = str(connector.get("last_error") or "").strip()
        if last_error:
            issues.append(
                {
                    "code": str(connector.get("last_error_category") or f"{provider}_connector_error").strip(),
                    "severity": "degraded",
                    "message": last_error,
                    "connector_id": str(connector.get("id") or "").strip() or None,
                    "occurred_at": connector.get("last_error_at"),
                }
            )

    issues = _workspace_relevant_provider_issues(
        provider=provider,
        connectors=connectors,
        issues=issues,
    )

    workspace_status = _workspace_status_from_issues(
        base_status=str(payload.get("status") or "").strip().lower(),
        connectors=connectors,
        issues=issues,
    )

    return {
        "provider": provider,
        "status": str(payload.get("status") or "").strip().lower() or "setup_needed",
        "workspace_status": workspace_status,
        "webhook": _coerce_dict(payload.get("webhook")),
        "autopilot": _coerce_dict(payload.get("autopilot")),
        "connectors": connectors,
        "issues": issues,
        "workspace_configured": bool(connectors),
        "vault_error": payload.get("vault_error"),
    }


def _filter_recent_channel_events(
    *,
    workspace_id: str,
    limit: int,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 0), 200))
    with shared_module.CHANNEL_EVENTS_LOCK:
        snapshot = list(shared_module.CHANNEL_EVENTS)
    items: List[Dict[str, Any]] = []
    for raw_item in snapshot:
        item = _coerce_dict(raw_item)
        if _normalize_workspace_token(item.get("workspace_id")) != workspace_id:
            continue
        if _normalize_channel_token(item.get("channel")) not in CHANNEL_PROVIDERS:
            continue
        items.append(item)
        if len(items) >= safe_limit:
            break
    return items


def _filter_pairing_failures(items: List[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 0), 100))
    failures = [
        item
        for item in items
        if str(item.get("action") or "").strip().lower() in PAIRING_FAILURE_ACTIONS
    ]
    return failures[:safe_limit]


def _workspace_dead_letters(*, workspace_id: str, limit: int) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 0), 200))
    payload = _safe_read_json(runtime_config.ORION_CHANNEL_DEAD_LETTER_FILE, {"version": 1, "items": []})
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    filtered: List[Dict[str, Any]] = []
    for raw_item in items:
        item = _coerce_dict(raw_item)
        if _normalize_workspace_token(item.get("workspace_id")) != workspace_id:
            continue
        if _normalize_channel_token(item.get("channel")) not in CHANNEL_PROVIDERS:
            continue
        filtered.append(item)
        if len(filtered) >= safe_limit:
            break
    return filtered


def _workspace_pending_delivery_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_channel: Dict[str, int] = {}
    poisoned_by_channel: Dict[str, int] = {}
    last_delivery_error: Dict[str, Any] | None = None
    retry_total = 0
    max_retry = 0
    repeated_failure_count = 0
    stuck_count = 0
    with_receipt_count = 0
    now = datetime.now(timezone.utc)
    for item in items:
        payload = _coerce_dict(item.get("payload"))
        channel = _normalize_channel_token(payload.get("channel")) or "unknown"
        target_bucket = poisoned_by_channel if str(item.get("poisoned_at") or "").strip() else by_channel
        target_bucket[channel] = target_bucket.get(channel, 0) + 1
        retry_count = int(item.get("retry_count") or 0)
        retry_total += retry_count
        max_retry = max(max_retry, retry_count)
        if retry_count >= 3:
            repeated_failure_count += 1
        delivery = _coerce_dict(payload.get("delivery"))
        if isinstance(delivery.get("receipt"), dict):
            with_receipt_count += 1
        next_attempt_at = _parse_utc_ts(item.get("next_attempt_at"))
        last_attempted_at = _parse_utc_ts(item.get("last_attempted_at"))
        created_at = _parse_utc_ts(item.get("created_at"))
        reference = next_attempt_at or last_attempted_at or created_at
        if not item.get("poisoned_at") and reference is not None and (now - reference).total_seconds() >= 60:
            stuck_count += 1
        if not last_delivery_error and str(item.get("last_delivery_error") or "").strip():
            last_delivery_error = {
                "event_id": str(item.get("event_id") or "").strip(),
                "message": str(item.get("last_delivery_error") or "").strip(),
                "last_attempted_at": item.get("last_attempted_at"),
                "retry_count": retry_count,
            }
    return {
        "pending_count": sum(by_channel.values()),
        "poisoned_count": sum(poisoned_by_channel.values()),
        "retry_count_total": retry_total,
        "max_retry_count": max_retry,
        "pending_by_channel": by_channel,
        "poisoned_by_channel": poisoned_by_channel,
        "repeated_failure_count": repeated_failure_count,
        "stuck_count": stuck_count,
        "with_receipt_count": with_receipt_count,
        "last_delivery_error": last_delivery_error,
    }


def _enforce_workspace_operator_access(
    *,
    current_user: Dict[str, Any] | None,
    workspace_id: str,
) -> str:
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    raw_workspace_access = _coerce_dict((current_user or {}).get("workspace_access"))
    workspace_access = auth_module.workspace_access_map(current_user)
    workspace_entry = _coerce_dict(raw_workspace_access.get(resolved_workspace_id)) or _coerce_dict(
        workspace_access.get(resolved_workspace_id)
    )
    workspace_role = str(
        workspace_entry.get("role")
        or auth_module.workspace_role(current_user, resolved_workspace_id)
        or ""
    ).strip().lower()
    if workspace_role in {"owner", "admin"}:
        return resolved_workspace_id
    if bool((current_user or {}).get("is_admin")):
        return resolved_workspace_id
    raise HTTPException(
        status_code=403,
        detail=f"Admin or owner role required for workspace '{resolved_workspace_id}'.",
    )


async def build_workspace_channel_operations(
    *,
    current_user: Dict[str, Any] | None,
    workspace_id: str,
) -> Dict[str, Any]:
    resolved_workspace_id = _enforce_workspace_operator_access(
        current_user=current_user,
        workspace_id=workspace_id,
    )

    telegram_status_payload, whatsapp_status_payload = await handle_telegram_autopilot_status(), await handle_whatsapp_autopilot_status()
    recent_events = _filter_recent_channel_events(workspace_id=resolved_workspace_id, limit=40)
    pairing_failures = _filter_pairing_failures(recent_events, limit=20)
    dead_letters = _workspace_dead_letters(workspace_id=resolved_workspace_id, limit=20)

    runtime_outbox_summary = await run_state_repository.get_outbox_delivery_status()
    pending_outbox_items = await run_state_repository.list_undelivered_outbox_events(
        older_than_seconds=0,
        limit=200,
    )
    poisoned_outbox_items = await run_state_repository.list_poisoned_outbox_events(limit=200)
    workspace_pending_deliveries = [
        item
        for item in pending_outbox_items
        if _normalize_workspace_token(item.get("workspace_id")) == resolved_workspace_id
        and str(item.get("event_type") or "").strip() == "channel_run_delivery"
        and _normalize_channel_token(_coerce_dict(item.get("payload")).get("channel")) in CHANNEL_PROVIDERS
    ]
    workspace_poisoned_deliveries = [
        item
        for item in poisoned_outbox_items
        if _normalize_workspace_token(item.get("workspace_id")) == resolved_workspace_id
        and str(item.get("event_type") or "").strip() == "channel_run_delivery"
        and _normalize_channel_token(_coerce_dict(item.get("payload")).get("channel")) in CHANNEL_PROVIDERS
    ]

    channels = {
        "telegram": _workspace_channel_payload(
            provider="telegram",
            workspace_id=resolved_workspace_id,
            payload=_coerce_dict(telegram_status_payload),
        ),
        "whatsapp": _workspace_channel_payload(
            provider="whatsapp",
            workspace_id=resolved_workspace_id,
            payload=_coerce_dict(whatsapp_status_payload),
        ),
    }

    return {
        "ok": True,
        "workspace_id": resolved_workspace_id,
        "generated_at": _utc_now_iso(),
        "channels": channels,
        "channel_families": {
            "telegram": _build_channel_family_modes(
                family="telegram",
                workspace_id=resolved_workspace_id,
                provider_payload=channels["telegram"],
            ),
            "whatsapp": _build_channel_family_modes(
                family="whatsapp",
                workspace_id=resolved_workspace_id,
                provider_payload=channels["whatsapp"],
            ),
        },
        "delivery": {
            "runtime_summary": _coerce_dict(runtime_outbox_summary),
            "workspace_summary": _workspace_pending_delivery_summary(
                workspace_pending_deliveries + workspace_poisoned_deliveries
            ),
            "pending": workspace_pending_deliveries,
            "poisoned": workspace_poisoned_deliveries,
            "dead_letters": dead_letters,
        },
        "events": {
            "recent": recent_events,
            "pairing_failures": pairing_failures,
        },
    }

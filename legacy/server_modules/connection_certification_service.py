from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from fastapi import HTTPException

from server_modules import (
    connection_catalog_service,
    connection_readiness_service,
    connection_verify_service,
)


_PROVIDER_VERIFY_CONNECTION_IDS = {"telegram_bot", "discord_bot"}
_LOCAL_BRIDGE_CONNECTION_IDS = {"signal_personal", "imessage_personal", "wechat_personal"}


def _text(value: Any, fallback: str = "") -> str:
    candidate = str(value or "").strip()
    return candidate or fallback


def _token(value: Any) -> str:
    return _text(value).lower()


def _status_item(
    *,
    connection_id: str,
    workspace_id: str,
    tenant_id: Optional[str],
    user_id: Optional[str],
    surface: Optional[str],
    selected_gateway_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    items = connection_catalog_service.status_items(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        surface=surface,
        selected_gateway_id=selected_gateway_id,
    )
    normalized = _token(connection_id)
    return next((deepcopy(item) for item in items if _token(item.get("id")) == normalized), None)


def _provider(item: Dict[str, Any]) -> str:
    return _text(
        item.get("vault_provider")
        or item.get("account_provider")
        or item.get("connector_id")
        or item.get("id")
    )


def _doctor_checks(item: Dict[str, Any]) -> list[Dict[str, Any]]:
    checks = [
        {
            "id": "catalog_launch_status",
            "status": "pass" if _token(item.get("launch_status")) in {"live", "live_when_configured"} else "block",
            "message": f"Launch status is {_text(item.get('launch_status'), 'unknown')}.",
        },
        {
            "id": "setup_available",
            "status": "pass" if bool(item.get("setup_available")) else "block",
            "message": "Setup is available." if item.get("setup_available") else "Setup is not available for launch users yet.",
        },
        {
            "id": "runtime_usable",
            "status": "pass" if bool(item.get("runtime_usable")) else "block",
            "message": "Runtime is usable." if item.get("runtime_usable") else "Runtime is not launch usable yet.",
        },
    ]
    if item.get("requires_gateway"):
        gateway_selected = bool(item.get("selected_gateway_id")) and not bool(item.get("selected_gateway_missing"))
        health_status = _token(item.get("health_status"))
        checks.append({
            "id": "gateway_selected",
            "status": "pass" if gateway_selected else "block",
            "message": "A Sage Agent Computer is selected." if gateway_selected else "Select a Sage Agent Computer first.",
        })
        checks.append({
            "id": "gateway_channel_health",
            "status": "pass" if health_status == "healthy" or bool(item.get("connected")) else "block",
            "message": (
                "Selected Agent Computer reports this channel healthy."
                if health_status == "healthy" or item.get("connected")
                else f"Selected Agent Computer channel health is {health_status or 'unknown'}."
            ),
        })
    elif item.get("requires_external_account"):
        checks.append({
            "id": "external_account",
            "status": "pass" if bool(item.get("connected")) else "block",
            "message": "External account is connected." if item.get("connected") else "Connect and verify the external account.",
        })
    return checks


def doctor_connection(
    *,
    connection_id: str,
    workspace_id: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    surface: Optional[str] = None,
    selected_gateway_id: Optional[str] = None,
) -> Dict[str, Any]:
    item = _status_item(
        connection_id=connection_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        surface=surface,
        selected_gateway_id=selected_gateway_id,
    )
    if item is None:
        catalog_item = connection_catalog_service.catalog_item(connection_id)
        if not catalog_item:
            raise HTTPException(status_code=404, detail="Connection was not found.")
        item = connection_readiness_service.decorate_status_item({
            **catalog_item,
            "connected": False,
            "configured": False,
            "display_state": "unavailable",
            "test_status": "not_tested",
            "health_status": "not_configured",
            "selected_gateway_id": selected_gateway_id,
            "selected_gateway_missing": False,
            "gateway_count": 0,
            "online_gateway_count": 0,
            "online_gateway_candidate": None,
            "last_error": None,
            "next_action": "locked",
        })
    return {
        "connection": item,
        "connection_id": item.get("id") or connection_id,
        "display_name": item.get("display_name"),
        "readiness_status": item.get("readiness_status"),
        "readiness_reason": item.get("readiness_reason"),
        "launch_certified": bool(item.get("launch_certified")),
        "certification_required": bool(item.get("certification_required")),
        "certification_requirements": list(item.get("certification_requirements") or []),
        "doctor_checks": _doctor_checks(item),
        "next_action": item.get("next_action"),
    }


def certify_connection(
    *,
    connection_id: str,
    workspace_id: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    surface: Optional[str] = None,
    selected_gateway_id: Optional[str] = None,
) -> Dict[str, Any]:
    doctor = doctor_connection(
        connection_id=connection_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        surface=surface,
        selected_gateway_id=selected_gateway_id,
    )
    item = dict(doctor.get("connection") or {})
    normalized = _token(item.get("id") or connection_id)

    if normalized in _PROVIDER_VERIFY_CONNECTION_IDS:
        result = connection_verify_service.verify_connection(
            connection_id=normalized,
            provider=_provider(item),
            workspace_id=workspace_id,
            tenant_id=tenant_id,
        )
        return {
            "ok": True,
            "certified": True,
            "certification_kind": "provider_verify",
            "connection_id": normalized,
            "provider": _provider(item),
            "readiness_status": connection_readiness_service.READINESS_LAUNCH_CERTIFIED,
            "readiness_reason": "provider_identity_verified",
            **result,
        }

    if normalized in _LOCAL_BRIDGE_CONNECTION_IDS or bool(item.get("requires_gateway")):
        if not item.get("selected_gateway_id") or item.get("selected_gateway_missing"):
            raise HTTPException(status_code=409, detail="Select an online Sage Agent Computer before certification.")
        if not bool(item.get("connected")) or _token(item.get("health_status")) != "healthy":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Connection is not healthy enough to certify.",
                    "requirements": doctor.get("certification_requirements") or [],
                    "health_status": item.get("health_status"),
                },
            )
        return {
            "ok": True,
            "certified": True,
            "certification_kind": "agent_computer_bridge_health",
            "connection_id": normalized,
            "selected_gateway_id": item.get("selected_gateway_id"),
            "readiness_status": connection_readiness_service.READINESS_LAUNCH_CERTIFIED,
            "readiness_reason": "agent_computer_bridge_health_passed",
        }

    if bool(item.get("connected")) and _token(item.get("readiness_status")) in {
        connection_readiness_service.READINESS_IMPLEMENTATION_READY,
        connection_readiness_service.READINESS_LAUNCH_CERTIFIED,
    }:
        return {
            "ok": True,
            "certified": True,
            "certification_kind": "account_connected",
            "connection_id": normalized,
            "provider": _provider(item),
            "readiness_status": connection_readiness_service.READINESS_LAUNCH_CERTIFIED,
            "readiness_reason": "account_connected",
        }

    raise HTTPException(
        status_code=409,
        detail={
            "message": "Connection cannot be certified yet.",
            "readiness_status": doctor.get("readiness_status"),
            "requirements": doctor.get("certification_requirements") or [],
            "doctor_checks": doctor.get("doctor_checks") or [],
        },
    )

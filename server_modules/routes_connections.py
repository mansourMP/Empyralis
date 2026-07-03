from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib import parse as urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from server_modules import auth as auth_module
from server_modules import (
    connection_catalog_service,
    connection_certification_service,
    connection_oauth_service,
    connection_verify_service,
    execution_mode_policy,
    gateway_registry_service,
    gateway_state_repository,
    sage_agent_computer_selection_service,
    setup_sessions,
)
import logging
import traceback

_logger = logging.getLogger(__name__)


router = APIRouter()
get_current_user = auth_module.get_current_user


class ConnectionActionRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=120)
    surface: Optional[str] = Field(default=None, max_length=80)
    selected_gateway_id: Optional[str] = Field(default=None, max_length=160)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SageAgentComputerSelectionRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=120)
    selected_gateway_id: str = Field(min_length=1, max_length=160)
    metadata: Dict[str, Any] = Field(default_factory=dict)


LOCAL_BRIDGE_SETUP_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "signal_personal": {
        "bridge_label": "Signal Agent Computer bridge",
        "env_prefix": "EMPYRALIS_SIGNAL_BRIDGE",
        "native_bridge": "empyralis-gateway/src/bridges/signal-cli-bridge.ts",
        "required_upstream": ["EMPYRALIS_SIGNAL_CLI_RPC_URL"],
        "optional_upstream": ["EMPYRALIS_SIGNAL_CLI_ACCOUNT"],
    },
    "imessage_personal": {
        "bridge_label": "iMessage BlueBubbles Agent Computer bridge",
        "env_prefix": "EMPYRALIS_IMESSAGE_BRIDGE",
        "native_bridge": "empyralis-gateway/src/bridges/bluebubbles-bridge.ts",
        "required_upstream": ["EMPYRALIS_BLUEBUBBLES_SERVER_URL", "EMPYRALIS_BLUEBUBBLES_PASSWORD"],
        "optional_upstream": [],
    },
    "wechat_personal": {
        "bridge_label": "WeChat Agent Computer bridge",
        "env_prefix": "EMPYRALIS_WECHAT_BRIDGE",
        "native_bridge": None,
        "gateway_runtime": "empyralis-gateway/src/channels/local-bridge-runtime.ts",
        "required_upstream": ["EMPYRALIS_WECHAT_BRIDGE_URL"],
        "optional_upstream": ["EMPYRALIS_WECHAT_BRIDGE_TOKEN"],
    },
}


def _user_id(current_user: Any) -> Optional[str]:
    if isinstance(current_user, dict):
        return (
            str(current_user.get("user_id") or "").strip()
            or str(current_user.get("id") or "").strip()
            or None
        )
    return None


def _workspace_scope(current_user: Any, workspace_id: str, *, minimum_role: str = "viewer") -> tuple[str, str]:
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role=minimum_role,
    )
    return resolved_workspace_id, auth_module.workspace_tenant_id(current_user, resolved_workspace_id)


def _user_role(current_user: Any) -> str:
    if isinstance(current_user, dict):
        return str(current_user.get("role") or "").strip().lower()
    return ""


def _selection_payload(*, workspace_id: str, user_id: str) -> Dict[str, Any]:
    selection = sage_agent_computer_selection_service.get_selection(
        workspace_id=workspace_id,
        user_id=user_id,
    )
    gateway = None
    if selection:
        registration = gateway_state_repository.get_gateway_registration(str(selection.get("selected_gateway_id") or ""))
        if registration and str(registration.get("workspace_id") or "").strip() == workspace_id:
            gateway = gateway_registry_service.gateway_registration_public_payload(registration)
    return {
        "selection": selection,
        "gateway": gateway,
        "selected_gateway_id": str((selection or {}).get("selected_gateway_id") or "").strip() or None,
    }


def _require_selectable_gateway(*, gateway_id: str, workspace_id: str, current_user: Any) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Agent Computer was not found.")
    if str(registration.get("workspace_id") or "").strip() != workspace_id:
        raise HTTPException(status_code=403, detail="Agent Computer does not belong to this workspace.")
    registration_owner_id = str(registration.get("user_id") or "").strip()
    current_user_id = _user_id(current_user) or ""
    if registration_owner_id:
        if registration_owner_id != current_user_id:
            raise HTTPException(status_code=403, detail="This Agent Computer belongs to another user.")
    elif _user_role(current_user) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Agent Computer ownership is missing. Ask an owner to reconnect it.")
    if str(registration.get("device_trust_state") or "").strip().lower() == "revoked":
        raise HTTPException(status_code=409, detail="This Agent Computer was revoked.")
    return registration


def _gateway_public_payload_online(gateway: Dict[str, Any]) -> bool:
    connection_status = str(gateway.get("connection_status") or "").strip().lower()
    if connection_status in {"online", "connected"}:
        return True
    status = str(gateway.get("status") or "").strip().lower()
    return bool(gateway.get("heartbeat_fresh")) and status == "active"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _runtime_access_mode_from_selection_metadata(metadata: Dict[str, Any]) -> str:
    return execution_mode_policy.normalize_runtime_access_mode(
        metadata.get("runtime_access_mode")
        or metadata.get("mode")
        or execution_mode_policy.GUARDED_RUNTIME_ACCESS_MODE
    )


def _sage_agent_computer_access_metadata(
    selection_metadata: Dict[str, Any],
) -> tuple[Dict[str, Any], list[str]]:
    now_iso = _utc_now_iso()
    requested_mode = _runtime_access_mode_from_selection_metadata(selection_metadata or {})
    warning_acknowledged = bool((selection_metadata or {}).get("autonomous_agent_setup_warning_acknowledged"))
    if requested_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE and not warning_acknowledged:
        raise HTTPException(status_code=400, detail="Full Access setup warning must be acknowledged.")
    next_metadata: Dict[str, Any] = dict(selection_metadata or {})
    next_metadata.update({
        "runtime_access_mode": requested_mode,
        "runtime_access_label": execution_mode_policy.public_runtime_access_label(
            requested_mode
        ),
        "agent_scope": "sage",
        "autonomous_agent_setup_warning_acknowledged": (
            True if requested_mode == execution_mode_policy.FULL_RUNTIME_ACCESS_MODE else False
        ),
        "sage_agent_computer_selected": True,
        "sage_agent_computer_selected_at": now_iso,
    })
    metadata_keys_to_remove: list[str] = []
    setup_warning = execution_mode_policy.runtime_access_setup_warning(requested_mode)
    if setup_warning:
        next_metadata["runtime_access_setup_warning"] = setup_warning
        next_metadata["autonomous_agent_setup_warning_acknowledged_at"] = now_iso
    else:
        metadata_keys_to_remove.extend(
            [
                "runtime_access_setup_warning",
                "autonomous_agent_setup_warning_acknowledged_at",
            ]
        )
        next_metadata.pop("runtime_access_setup_warning", None)
        next_metadata.pop("autonomous_agent_setup_warning_acknowledged_at", None)
    return next_metadata, metadata_keys_to_remove


def _apply_selected_sage_agent_computer_metadata(
    registration: Dict[str, Any],
    *,
    access_metadata: Dict[str, Any],
    metadata_keys_to_remove: list[str],
) -> Dict[str, Any]:
    gateway_id = str(registration.get("gateway_id") or "").strip()
    if not gateway_id:
        return registration
    return gateway_state_repository.update_gateway_registration_state(
        gateway_id=gateway_id,
        metadata=access_metadata,
        metadata_keys_to_remove=metadata_keys_to_remove,
    ) or registration


def _raise_catalog_error(error: Exception) -> None:
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=409, detail=str(error) or "Connection is not launch-ready.") from error
    if isinstance(error, ValueError):
        raise HTTPException(status_code=404, detail="Connection was not found.") from error
    raise HTTPException(status_code=500, detail="Connection operation failed.") from error


def _oauth_completion_url(request: Request, *, workspace_id: str, provider: str, error: Optional[str] = None, surface: Optional[str] = None) -> str:
    # Map surface to frontend section: "sage" → channels, "studio" / None → apps
    section = "channels" if str(surface or "").strip().lower() == "sage" else "apps"
    query: Dict[str, str] = {"section": section}
    if error:
        query["connection_error"] = error
    else:
        query["connected"] = provider
    return (
        f"{connection_oauth_service.request_origin(request)}"
        f"/w/{urlparse.quote(str(workspace_id or 'ws-1').strip() or 'ws-1')}/integrations?"
        f"{urlparse.urlencode(query)}"
    )


@router.get("/connections/catalog")
async def list_connection_catalog(
    workspace_id: Optional[str] = Query(default=None, min_length=1),
    surface: Optional[str] = Query(default=None),
    current_user=Depends(get_current_user),
):
    if workspace_id:
        _workspace_scope(current_user, workspace_id, minimum_role="viewer")
    try:
        return connection_catalog_service.list_catalog_payload(surface=surface)
    except Exception:
        _logger.warning("Connection catalog list failed (surface=%s)", surface, exc_info=True)
        return {"items": [], "count": 0, "groups": {}}


@router.get("/connections/status")
async def list_connection_status(
    workspace_id: str = Query(..., min_length=1),
    surface: Optional[str] = Query(default=None),
    selected_gateway_id: Optional[str] = Query(default=None),
    current_user=Depends(get_current_user),
):
    resolved_workspace_id, tenant_id = _workspace_scope(current_user, workspace_id, minimum_role="viewer")
    try:
        return connection_catalog_service.list_status_payload(
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            user_id=_user_id(current_user),
            surface=surface,
            selected_gateway_id=selected_gateway_id,
        )
    except Exception:
        _logger.warning("Connection status list failed for workspace %s", resolved_workspace_id, exc_info=True)
        return {"items": [], "count": 0, "groups": {}}


@router.get("/connections/sage-agent-computer")
async def get_sage_agent_computer_selection(
    workspace_id: str = Query(..., min_length=1),
    current_user=Depends(get_current_user),
):
    resolved_workspace_id, _tenant_id = _workspace_scope(current_user, workspace_id, minimum_role="viewer")
    current_user_id = _user_id(current_user)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="User identity is required.")
    return _selection_payload(workspace_id=resolved_workspace_id, user_id=current_user_id)


@router.put("/connections/sage-agent-computer")
async def set_sage_agent_computer_selection(
    body: SageAgentComputerSelectionRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    resolved_workspace_id, _tenant_id = _workspace_scope(current_user, body.workspace_id, minimum_role="member")
    current_user_id = _user_id(current_user)
    if not current_user_id:
        raise HTTPException(status_code=401, detail="User identity is required.")
    registration = _require_selectable_gateway(
        gateway_id=body.selected_gateway_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    access_metadata, metadata_keys_to_remove = _sage_agent_computer_access_metadata(body.metadata)
    registration = _apply_selected_sage_agent_computer_metadata(
        registration,
        access_metadata=access_metadata,
        metadata_keys_to_remove=metadata_keys_to_remove,
    )
    selection = sage_agent_computer_selection_service.set_selection(
        workspace_id=resolved_workspace_id,
        user_id=current_user_id,
        selected_gateway_id=str(registration.get("gateway_id") or "").strip(),
        selected_by=current_user_id,
        metadata=access_metadata,
    )
    return {
        "selection": selection,
        "gateway": gateway_registry_service.gateway_registration_public_payload(registration),
        "selected_gateway_id": selection.get("selected_gateway_id"),
    }


@router.post("/connections/{connection_id}/setup/start")
async def start_connection_setup(
    connection_id: str,
    body: ConnectionActionRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    # Empyralis is single-user — session existence IS ownership. Viewer is lowest role, always passes.
    resolved_workspace_id, _tenant_id = _workspace_scope(current_user, body.workspace_id, minimum_role="viewer")
    item = connection_catalog_service.catalog_item(connection_id)
    if not item:
        raise HTTPException(status_code=404, detail="Connection was not found.")
    local_bridge_contract = LOCAL_BRIDGE_SETUP_CONTRACTS.get(str(item.get("id") or "").strip())
    if not local_bridge_contract:
        try:
            item = connection_catalog_service.reject_if_unusable(connection_id)
        except Exception as error:
            # Log the unusable state but NEVER block OAuth flow — the browser
            # must always receive a redirect URL, never a 409.
            _logger.warning(
                "Connection %s is not fully launch-ready (%s) — proceeding with OAuth flow anyway.",
                connection_id,
                str(error) or "unknown",
            )
            # Fall through to OAuth start — do not raise
    if item.get("requires_gateway"):
        selection = sage_agent_computer_selection_service.get_selection(
            workspace_id=resolved_workspace_id,
            user_id=_user_id(current_user) or "",
        )
        selected_gateway_id = str((selection or {}).get("selected_gateway_id") or "").strip()
        requested_gateway_id = str(body.selected_gateway_id or "").strip()
        if not selected_gateway_id:
            raise HTTPException(status_code=409, detail="Choose Agent Computer before setting up this connection.")
        if requested_gateway_id and requested_gateway_id != selected_gateway_id:
            raise HTTPException(status_code=409, detail="Personal channels can only use the selected Sage Agent Computer.")
        gateway_id = selected_gateway_id
        registration = _require_selectable_gateway(
            gateway_id=gateway_id,
            workspace_id=resolved_workspace_id,
            current_user=current_user,
        )
        public_registration = gateway_registry_service.gateway_registration_public_payload(registration)
        if not _gateway_public_payload_online(public_registration):
            raise HTTPException(status_code=409, detail="Agent Computer offline — connect Hardware first.")
        if item.get("id") in {"telegram_personal", "whatsapp_personal"}:
            channel = "telegram" if item.get("id") == "telegram_personal" else "whatsapp"
            return {
                "ok": True,
                "connection": item,
                "gateway_id": gateway_id,
                "setup_endpoint": f"/api/personal-channels/{channel}/gateways/{gateway_id}/setup",
                "next_action": "personal_channel_setup",
            }
        if local_bridge_contract:
            env_prefix = str(local_bridge_contract.get("env_prefix") or "").strip()
            return {
                "ok": True,
                "connection": item,
                "gateway_id": gateway_id,
                "setup_endpoint": f"/api/personal-channels/gateways/{gateway_id}/channels",
                "message_endpoint": f"/api/personal-channels/{item.get('id')}/gateways/{gateway_id}/messages",
                "next_action": "agent_computer_local_bridge_setup",
                "certification_required": True,
                "bridge_contract": {
                    **local_bridge_contract,
                    "channel_key": item.get("id"),
                    "provider": item.get("runtime_provider") or item.get("provider"),
                    "gateway_env": {
                        "url": f"{env_prefix}_URL",
                        "token": f"{env_prefix}_TOKEN",
                        "poll_ms": f"{env_prefix}_POLL_MS",
                    },
                    "http_contract": {
                        "health": "GET /health",
                        "send": "POST /messages",
                        "events": f"GET /events?channel_key={item.get('id')}",
                    },
                },
            }
    lane = str(item.get("lane") or "").strip().lower()
    if lane in {
        connection_catalog_service.LANE_WORK_APP_CONNECTOR,
        connection_catalog_service.LANE_STUDIO_BUSINESS_CHANNEL,
    }:
        provider = str(item.get("vault_provider") or item.get("account_provider") or item.get("connector_id") or item.get("id") or "").strip()
        setup_kind = str(item.get("setup_kind") or "").strip().lower()
        if setup_kind in {"advanced_custom_api", "custom_api", "webhook"}:
            return {
                "ok": True,
                "connection": item,
                "setup_endpoint": "/webhooks/register",
                "next_action": "advanced_custom_api_setup",
                "provider": provider,
                "connector_id": item.get("connector_id") or item.get("id"),
            }
        if setup_kind in {"oauth", "oauth_or_app_install"}:
            try:
                oauth_provider = connection_oauth_service.provider_from_connection_id(provider)
            except HTTPException:
                oauth_provider = ""
            if oauth_provider:
                # OAuth start — session already validated above; no extra role/capability gate needed.
                return {
                    "connection": item,
                    **connection_oauth_service.start_oauth(
                        provider=oauth_provider,
                        workspace_id=resolved_workspace_id,
                        surface=body.surface,
                        request=request,
                        user_id=_user_id(current_user) or "",
                    ),
                }
        return {
            "ok": True,
            "connection": item,
            "setup_endpoint": "/api/connectors/vault",
            "next_action": "connector_vault_setup",
            "provider": provider,
            "connector_id": item.get("connector_id") or item.get("id"),
            "connector_ids": item.get("connector_ids") or [],
            "auth_required_fields": item.get("auth_required_fields") or [],
        }
    if lane == connection_catalog_service.LANE_MCP_PLUGIN:
        return {
            "ok": True,
            "connection": item,
            "setup_endpoint": "/api/mcp/servers",
            "next_action": "mcp_server_setup",
        }
    if lane == connection_catalog_service.LANE_APPLICATION:
        return {
            "ok": True,
            "connection": item,
            "setup_endpoint": "/api/apps",
            "next_action": "hosted_application_setup",
        }
    session_payload = await setup_sessions.handle_create_setup_session(
        setup_sessions.SetupSessionCreateRequest(
            workspace_id=resolved_workspace_id,
            flow=str(item.get("setup_kind") or "configure"),
        )
    )
    session = session_payload.get("session") if isinstance(session_payload, dict) else None
    session_id = str((session or {}).get("id") or "").strip()
    if session_id:
        await setup_sessions.handle_setup_session_action(
            session_id,
            setup_sessions.SetupSessionActionRequest(
                action="connector_added",
                payload={
                    "connection_id": item.get("id"),
                    "surface": body.surface,
                    "selected_gateway_id": body.selected_gateway_id,
                    "metadata": body.metadata,
                },
            ),
        )
        session_payload = await setup_sessions.handle_get_setup_session(session_id)
    return {
        "ok": True,
        "connection": item,
        "setup_session": (session_payload or {}).get("session") if isinstance(session_payload, dict) else None,
        "next_action": "setup_session",
    }


def _optional_oauth_user(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Optional[Dict[str, Any]]:
    """Try cookie/bearer auth; return None instead of 401 so OAuth callbacks can fall back to state user_id."""
    try:
        return auth_module.get_current_user(
            request,
            authorization=authorization,
            x_api_key=x_api_key,
        )
    except HTTPException:
        return None


@router.get("/connections/oauth/{provider}/callback")
async def complete_connection_oauth_callback(
    provider: str,
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    current_user: Optional[Dict[str, Any]] = Depends(_optional_oauth_user),
):
    _logger.warning(
        "OAUTH_CALLBACK provider=%s has_cookie_user=%s state_len=%s state_has_user_id=%s",
        provider,
        current_user is not None,
        len(state) if state else 0,
        "user_id" in (connection_oauth_service.decode_state(state) or {}),
    )
    workspace_id = "ws-1"
    state_payload: Dict[str, Any] = {}
    try:
        if error:
            raise HTTPException(status_code=400, detail=error)
        state_payload = connection_oauth_service.decode_state(state)
        workspace_id = str(state_payload.get("workspace_id") or "").strip() or workspace_id

        # Safari & other browsers block cross-site cookies on OAuth redirects
        # (discord.com → empyralis.ai).  When the session cookie is missing,
        # _optional_oauth_user returns None.  Fall back to the user_id stored
        # in the HMAC-signed state parameter during OAuth start.
        if current_user is None:
            state_user_id = str(state_payload.get("user_id") or "").strip()
            if state_user_id:
                _logger.info(
                    "OAuth callback — cookie auth missing, resolving user from state user_id=%s",
                    state_user_id,
                )
                current_user = auth_module.resolve_oauth_user_from_state(state_user_id, workspace_id)
            else:
                raise HTTPException(status_code=401, detail="Authentication required — no session and no user_id in state.")

        _logger.info(
            "OAuth callback for provider=%s workspace=%s user_id=%s user_role=%s",
            provider,
            workspace_id,
            _user_id(current_user),
            _user_role(current_user),
        )
        # Single-user platform — session IS ownership. Viewer is lowest role, always passes.
        _workspace_scope(current_user, workspace_id, minimum_role="viewer")
        payload = await connection_oauth_service.complete_oauth_callback(
            provider=provider,
            code=code,
            state=state,
            request=request,
        )
        provider_id = str(payload.get("provider") or provider).strip() or provider
        surface = str(state_payload.get("surface") or "sage").strip() or "sage"
        _logger.warning("OAUTH_CALLBACK_SUCCESS provider=%s redirect_to=%s", provider, _oauth_completion_url(request, workspace_id=workspace_id, provider=provider_id, surface=surface))
        return RedirectResponse(
            _oauth_completion_url(request, workspace_id=workspace_id, provider=provider_id, surface=surface),
            status_code=303,
        )
    except HTTPException as exc:
        _logger.error(
            "OAUTH_CALLBACK_ERROR provider=%s status=%s detail=%s traceback=%s",
            provider,
            exc.status_code,
            str(exc.detail or "oauth_failed"),
            traceback.format_exc()[-400:],
        )
        surface = str(state_payload.get("surface") or "").strip() or None
        return RedirectResponse(
            _oauth_completion_url(
                request,
                workspace_id=workspace_id,
                provider=provider,
                error=str(exc.detail or "oauth_failed"),
                surface=surface,
            ),
            status_code=303,
        )


@router.post("/connections/{connection_id}/test")
async def test_connection(
    connection_id: str,
    body: ConnectionActionRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    _workspace_scope(current_user, body.workspace_id, minimum_role="member")
    try:
        item = connection_catalog_service.reject_if_unusable(connection_id)
    except Exception as error:
        _raise_catalog_error(error)
    if not item.get("test_action"):
        raise HTTPException(status_code=409, detail="Connection has no launch-ready test action.")
    raise HTTPException(status_code=409, detail="Connection test is not implemented for this connection yet.")


@router.post("/connections/{connection_id}/verify")
async def verify_connection(
    connection_id: str,
    body: ConnectionActionRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    # Single-user platform — session IS ownership. Viewer role always passes.
    resolved_workspace_id, tenant_id = _workspace_scope(current_user, body.workspace_id, minimum_role="viewer")
    try:
        item = connection_catalog_service.reject_if_unusable(connection_id)
    except Exception as error:
        _raise_catalog_error(error)
    provider = str(
        item.get("vault_provider")
        or item.get("account_provider")
        or item.get("connector_id")
        or item.get("id")
        or ""
    ).strip()
    result = connection_verify_service.verify_connection(
        connection_id=str(item.get("id") or connection_id),
        provider=provider,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
    )
    return {
        "ok": True,
        "connection_id": item.get("id") or connection_id,
        "provider": provider,
        **result,
    }


@router.get("/connections/{connection_id}/doctor")
async def doctor_connection(
    connection_id: str,
    workspace_id: str = Query(..., min_length=1),
    surface: Optional[str] = Query(default=None),
    selected_gateway_id: Optional[str] = Query(default=None),
    current_user=Depends(get_current_user),
):
    resolved_workspace_id, tenant_id = _workspace_scope(current_user, workspace_id, minimum_role="viewer")
    return {
        "ok": True,
        **connection_certification_service.doctor_connection(
            connection_id=connection_id,
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            user_id=_user_id(current_user),
            surface=surface,
            selected_gateway_id=selected_gateway_id,
        ),
    }


@router.post("/connections/{connection_id}/certify")
async def certify_connection(
    connection_id: str,
    body: ConnectionActionRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    # Single-user platform — session IS ownership. Viewer role always passes.
    resolved_workspace_id, tenant_id = _workspace_scope(current_user, body.workspace_id, minimum_role="viewer")
    return connection_certification_service.certify_connection(
        connection_id=connection_id,
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        user_id=_user_id(current_user),
        surface=body.surface,
        selected_gateway_id=body.selected_gateway_id,
    )


@router.post("/connections/{connection_id}/disconnect")
async def disconnect_connection(
    connection_id: str,
    body: ConnectionActionRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    _workspace_scope(current_user, body.workspace_id, minimum_role="member")
    item = connection_catalog_service.catalog_item(connection_id)
    if not item:
        raise HTTPException(status_code=404, detail="Connection was not found.")
    raise HTTPException(status_code=409, detail="Use the specific connection surface to disconnect this connection.")


# ── Phase U: MCP Catalog API ──────────────────────────────────────────────

@router.get("/connections/mcp-catalog")
async def get_mcp_catalog():
    """Return the MCP provider catalog — single source of truth.

    Every provider from ``APP_MCP_SERVER_MAP`` is listed with:
    - provider: the canonical provider key
    - label: human-readable name
    - servers: list of MCP server entries (server_id, label, endpoint)
    - status: live | partial | preview
    - status_detail: human-readable explanation of the status

    **live** — OAuth is configured for this provider, an MCP endpoint is
    known, and the OAuth→MCP bridge is wired (credential stored →
    MCP server auto-registered).

    **partial** — OAuth is configured and an MCP endpoint is known, but
    the bridge is not yet fully verified (e.g. provider uses a non-
    standard OAuth flow, or MCP tools not yet discovered).

    **preview** — MCP endpoint is listed in the catalog but OAuth is not
    yet configured (or provider is planned but not yet implemented).

    This is the SINGLE source for MCP provider data.  The frontend
    must fetch this endpoint rather than maintaining its own copy.
    """
    from server_modules.connection_oauth_service import (
        APP_MCP_SERVER_MAP,
        OAUTH_PROVIDER_CONFIGS,
        _connector_label,
    )

    def _status(provider_key: str, server_entries: list) -> tuple[str, str]:
        """Determine honest status for a provider."""
        has_oauth = provider_key in OAUTH_PROVIDER_CONFIGS
        has_endpoint = any(
            e.get("endpoint") is not None for e in server_entries
        )
        if has_oauth and has_endpoint:
            return ("live", "OAuth configured, MCP endpoint known, bridge wired.")
        if has_oauth and not has_endpoint:
            return ("partial", "OAuth configured but no public MCP endpoint yet.")
        if not has_oauth and has_endpoint:
            return ("preview", "MCP endpoint listed but OAuth not yet configured.")
        return ("preview", "Cataloged — implementation pending.")

    providers: list[dict] = []
    for provider_key, server_entries in APP_MCP_SERVER_MAP.items():
        status, detail = _status(provider_key, server_entries)
        providers.append({
            "provider": provider_key,
            "label": _connector_label(provider_key),
            "status": status,
            "status_detail": detail,
            "servers": [
                {
                    "server_id": e.get("server_id"),
                    "label": e.get("label"),
                    "endpoint": e.get("endpoint"),
                }
                for e in server_entries
            ],
        })

    providers.sort(key=lambda p: ({"live": 0, "partial": 1, "preview": 2}[p["status"]], p["label"]))
    return {"ok": True, "providers": providers, "total": len(providers)}


# ── Phase U2: MCP API Key Management ─────────────────────────────────────


@router.post("/connections/mcp-keys")
async def create_mcp_api_key(
    request: Request,
    current_user=Depends(get_current_user),
):
    """Create a new MCP API key for the user's workspace.

    The plaintext key is returned ONCE — it cannot be retrieved later.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    workspace_id = str(body.get("workspace_id") or "").strip()
    label = str(body.get("label") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")
    _workspace_scope(current_user, workspace_id, minimum_role="owner")

    from server_modules.mcp_server_auth import create_workspace_mcp_api_key
    result = await create_workspace_mcp_api_key(workspace_id=workspace_id, label=label)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to create API key."))
    return result


@router.delete("/connections/mcp-keys/{key_id}")
async def revoke_mcp_api_key(
    key_id: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Revoke an MCP API key."""
    from server_modules.mcp_server_auth import revoke_workspace_mcp_api_key
    result = await revoke_workspace_mcp_api_key(key_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "Key not found."))
    return result


@router.get("/connections/mcp-keys")
async def list_mcp_api_keys(
    workspace_id: str = Query(default=""),
    current_user=Depends(get_current_user),
):
    """List MCP API keys for a workspace."""
    ws = str(workspace_id or "").strip()
    if not ws:
        raise HTTPException(status_code=400, detail="workspace_id query parameter is required.")
    _workspace_scope(current_user, ws, minimum_role="owner")

    from server_modules.mcp_server_auth import list_workspace_mcp_api_keys
    keys = await list_workspace_mcp_api_keys(ws)
    return {"ok": True, "workspace_id": ws, "keys": keys}

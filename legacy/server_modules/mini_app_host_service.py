from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import importlib
import json
import os
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import HTTPException

from server_modules import app_bridge_service, external_content_guard
from server_modules.url_security import obvious_private_url_reason


DEFAULT_HOSTED_BRIDGE_MESSAGE_TYPE = "empyralis.hosted_app.bridge.request"
DEFAULT_HOSTED_BRIDGE_RESPONSE_TYPE = "empyralis.hosted_app.bridge.response"
DEFAULT_HOSTED_BRIDGE_READY_TYPE = "empyralis.hosted_app.bridge.ready"
_SUPPORTED_DELIVERY_MODES = {"structured", "hosted"}
_SUPPORTED_EMBED_KINDS = {"iframe", "webview"}
APP_PERMISSION_SUMMARY_READ = "app.summary.read"
APP_PERMISSION_RECORDS_READ_RAW = "app.records.read.raw"
APP_PERMISSION_RECORDS_WRITE = "app.records.write"
APP_PERMISSION_PROFILE_WRITE = "app.profile.write"
APP_PERMISSION_AI_INVOKE = "app.ai.invoke"
APP_PERMISSION_BRIDGE_SAGE_REQUEST = "app.bridge.sage.request"
APP_PERMISSION_BRIDGE_SPECIALIST_REQUEST = "app.bridge.specialist.request"
APP_PERMISSION_CONNECTOR_INVOKE = "app.connector.invoke"
_APP_PERMISSION_CLASSES = {
    APP_PERMISSION_SUMMARY_READ,
    APP_PERMISSION_RECORDS_READ_RAW,
    APP_PERMISSION_RECORDS_WRITE,
    APP_PERMISSION_PROFILE_WRITE,
    APP_PERMISSION_AI_INVOKE,
    APP_PERMISSION_BRIDGE_SAGE_REQUEST,
    APP_PERMISSION_BRIDGE_SPECIALIST_REQUEST,
    APP_PERMISSION_CONNECTOR_INVOKE,
}
_BRIDGE_KIND_REQUIRED_PERMISSION = {
    "app_to_sage": APP_PERMISSION_BRIDGE_SAGE_REQUEST,
    "app_to_specialist": APP_PERMISSION_BRIDGE_SPECIALIST_REQUEST,
    "app_to_connector_runtime": APP_PERMISSION_CONNECTOR_INVOKE,
}
_DEFAULT_IFRAME_SANDBOX = [
    "allow-forms",
    "allow-modals",
    "allow-scripts",
]
_DEFAULT_IFRAME_ALLOW: List[str] = []
_LAUNCH_TOKEN_TTL_SECONDS = 5 * 60


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_environment() -> str:
    return _normalized_text(
        os.getenv("EMPYRALIS_DEPLOY_ENV")
        or os.getenv("ORION_ENV")
        or os.getenv("ENV")
        or os.getenv("NODE_ENV")
    ).lower()


def _is_production_environment() -> bool:
    return _normalized_environment() in {"prod", "production"}


def _is_local_dev_environment() -> bool:
    if _is_production_environment():
        return False
    if _normalized_environment() in {"dev", "development", "local", "test"}:
        return True
    return _normalized_text(os.getenv("EMPYRALIS_ALLOW_LOCAL_MINI_APP_HTTP")).lower() in {"1", "true", "yes", "on"}


def _is_weak_launch_secret(secret: str) -> bool:
    token = _normalized_text(secret)
    if len(token) < 32:
        return True
    if token == "empyralis-mini-app-local-dev-secret":
        return True
    return len(set(token)) < 8


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    padding = "=" * ((4 - len(token) % 4) % 4)
    return base64.urlsafe_b64decode((token + padding).encode("ascii"))


def _launch_token_secret() -> bytes:
    secret = (
        os.getenv("EMPYRALIS_MINI_APP_LAUNCH_SECRET")
        or os.getenv("ORION_JWT_SECRET")
        or os.getenv("ORION_SECRET_KEY")
    )
    if not secret:
        if _is_production_environment():
            raise RuntimeError("CRITICAL: EMPYRALIS_MINI_APP_LAUNCH_SECRET must be configured in production.")
        secret = "empyralis-mini-app-local-dev-secret"
    if _is_production_environment() and _is_weak_launch_secret(secret):
        raise RuntimeError("CRITICAL: EMPYRALIS_MINI_APP_LAUNCH_SECRET must be at least 32 high-entropy characters in production.")
    return secret.encode("utf-8")


def issue_hosted_launch_token(
    *,
    workspace_id: str,
    app_id: str,
    user_id: str,
    origin: str,
    install_id: str = "",
    ttl_seconds: int = _LAUNCH_TOKEN_TTL_SECONDS,
) -> Dict[str, Any]:
    normalized_origin = _normalize_origin(origin)
    now = int(time.time())
    payload = {
        "workspace_id": _normalized_text(workspace_id) or "default",
        "app_id": _normalized_text(app_id),
        "user_id": _normalized_text(user_id),
        "install_id": _normalized_text(install_id),
        "origin": normalized_origin,
        "bridge_nonce": secrets.token_urlsafe(24),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + max(30, int(ttl_seconds or _LAUNCH_TOKEN_TTL_SECONDS)),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(payload_bytes)
    signature = hmac.new(_launch_token_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return {
        "token": f"{body}.{_b64url_encode(signature)}",
        "expires_at": payload["exp"],
        "bound_origin": normalized_origin,
        "bridge_nonce": payload["bridge_nonce"],
    }


def verify_hosted_launch_token(
    token: str,
    *,
    workspace_id: str,
    app_id: str,
    user_id: str,
    origin: str,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    raw_token = _normalized_text(token)
    if not raw_token or "." not in raw_token:
        raise PermissionError("Hosted mini app launch token is required.")
    body, signature = raw_token.split(".", 1)
    expected = _b64url_encode(hmac.new(_launch_token_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise PermissionError("Hosted mini app launch token signature is invalid.")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise PermissionError("Hosted mini app launch token is malformed.") from exc
    if not isinstance(payload, dict):
        raise PermissionError("Hosted mini app launch token payload is malformed.")
    normalized_origin = _normalize_origin(origin)
    expected_fields = {
        "workspace_id": _normalized_text(workspace_id) or "default",
        "app_id": _normalized_text(app_id),
        "user_id": _normalized_text(user_id),
        "origin": normalized_origin,
    }
    for key, expected_value in expected_fields.items():
        if _normalized_text(payload.get(key)) != expected_value:
            raise PermissionError(f"Hosted mini app launch token is not valid for this {key}.")
    expiry = int(payload.get("exp") or 0)
    if expiry <= int(now if now is not None else time.time()):
        raise PermissionError("Hosted mini app launch token has expired.")
    return payload


def _is_local_dev_host(hostname: str) -> bool:
    if not _is_local_dev_environment():
        return False
    normalized = _normalized_text(hostname).lower()
    if not normalized:
        return False
    if normalized in {"localhost", "127.0.0.1", "::1"} or normalized.endswith(".local"):
        return True
    try:
        parsed = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(parsed.is_loopback)


def _normalize_origin(value: Any) -> Optional[str]:
    token = _normalized_text(value)
    if not token:
        return None
    parsed = urlparse(token)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Hosted mini apps require absolute http(s) URLs and origins.")
    if parsed.scheme != "https" and not _is_local_dev_host(parsed.hostname or ""):
        raise ValueError("Hosted mini apps require https origins unless they target a local development host.")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _normalize_hosted_url(value: Any) -> Optional[str]:
    token = _normalized_text(value)
    if not token:
        return None
    parsed = urlparse(token)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("hosted_url must be an absolute http(s) URL.")
    if parsed.scheme != "https" and not _is_local_dev_host(parsed.hostname or ""):
        raise ValueError("hosted_url must use https unless it targets a local development host.")
    private_url_reason = obvious_private_url_reason(token)
    if private_url_reason and not _is_local_dev_host(parsed.hostname or ""):
        raise ValueError(f"hosted_url cannot target private or unsupported hosts ({private_url_reason}).")
    return token


def _normalize_delivery_mode(value: Any, *, hosted_url: Optional[str]) -> str:
    token = _normalized_text(value).lower() or ("hosted" if hosted_url else "structured")
    if token not in _SUPPORTED_DELIVERY_MODES:
        raise ValueError("delivery_mode must be either 'structured' or 'hosted'.")
    if token == "hosted" and not hosted_url:
        raise ValueError("hosted delivery_mode requires hosted_url.")
    return token


def _normalize_embed_kind(value: Any) -> str:
    token = _normalized_text(value).lower() or "iframe"
    if token not in _SUPPORTED_EMBED_KINDS:
        raise ValueError("embed_kind must be either 'iframe' or 'webview'.")
    return token


def _normalize_allowed_origins(value: Any, *, hosted_url: Optional[str]) -> List[str]:
    items: List[str] = []
    if hosted_url:
        origin = _normalize_origin(hosted_url)
        if origin:
            items.append(origin)
    raw_items = value if isinstance(value, list) else []
    for item in raw_items:
        origin = _normalize_origin(item)
        if origin and origin not in items:
            items.append(origin)
    return items


def _normalize_permission_tokens(
    value: Any,
    *,
    delivery_mode: str,
    bridge_contracts: Dict[str, List[str]],
) -> List[str]:
    permissions: List[str] = []
    for item in value if isinstance(value, list) else []:
        token = _normalized_text(item).lower()
        if not token:
            continue
        if token.startswith("bridge.app_to_sage."):
            token = APP_PERMISSION_BRIDGE_SAGE_REQUEST
        elif token.startswith("bridge.app_to_specialist."):
            token = APP_PERMISSION_BRIDGE_SPECIALIST_REQUEST
        elif token.startswith("bridge.app_to_connector_runtime."):
            token = APP_PERMISSION_CONNECTOR_INVOKE
        elif token.startswith("bridge.sage_to_app."):
            token = APP_PERMISSION_SUMMARY_READ
        if token not in _APP_PERMISSION_CLASSES:
            raise ValueError(f"Unsupported permission class '{token}'.")
        if token not in permissions:
            permissions.append(token)

    is_first_party = delivery_mode == "structured"
    if is_first_party and APP_PERMISSION_SUMMARY_READ not in permissions:
        permissions.append(APP_PERMISSION_SUMMARY_READ)

    for bridge_kind in bridge_contracts:
        required = _BRIDGE_KIND_REQUIRED_PERMISSION.get(_normalized_text(bridge_kind).lower())
        if required and required not in permissions:
            raise ValueError(
                f"Bridge kind '{bridge_kind}' requires explicit permission '{required}'."
            )
    return permissions


def _normalize_bridge_contracts(
    value: Any,
    *,
    app_id: str,
) -> Dict[str, List[str]]:
    allowed_map = app_bridge_service._bridge_contracts_map()
    normalized: Dict[str, List[str]] = {}
    raw_map = value if isinstance(value, dict) else {}
    for raw_kind, raw_types in raw_map.items():
        kind = _normalized_text(raw_kind).lower()
        if not kind:
            continue
        allowed_types = {
            _normalized_text(item).lower()
            for item in list(allowed_map.get(kind) or [])
            if _normalized_text(item)
        }
        normalized_types: List[str] = []
        for raw_type in raw_types if isinstance(raw_types, list) else []:
            bridge_type = _normalized_text(raw_type).lower()
            if not bridge_type:
                continue
            if allowed_types and bridge_type not in allowed_types:
                raise ValueError(f"Unsupported bridge_type '{bridge_type}' for bridge_kind '{kind}'.")
            if bridge_type not in normalized_types:
                normalized_types.append(bridge_type)
        if normalized_types:
            normalized[kind] = normalized_types
    return normalized


def _normalize_context_envelope(value: Any) -> Dict[str, List[str]]:
    envelope_map = app_bridge_service._context_envelope_map()
    allowed_default = {
        _normalized_text(item).lower()
        for item in list(envelope_map.get("default_classes") or [])
        if _normalized_text(item)
    }
    allowed_optional = {
        _normalized_text(item).lower()
        for item in list(envelope_map.get("optional_classes") or [])
        if _normalized_text(item)
    }
    payload = value if isinstance(value, dict) else {}
    normalized_default: List[str] = []
    normalized_optional: List[str] = []
    for item in payload.get("default_classes") if isinstance(payload.get("default_classes"), list) else []:
        token = _normalized_text(item).lower()
        if token and token in allowed_default and token not in normalized_default:
            normalized_default.append(token)
    for item in payload.get("optional_classes") if isinstance(payload.get("optional_classes"), list) else []:
        token = _normalized_text(item).lower()
        if token and token in allowed_optional and token not in normalized_optional:
            normalized_optional.append(token)
    return {
        "default_classes": normalized_default or sorted(allowed_default),
        "optional_classes": normalized_optional,
        "inherits_sage_memory_by_default": False,
        "inherits_specialist_memory_by_default": False,
    }


def _should_allow_same_origin_in_iframe(
    *,
    app_contract: Dict[str, Any],
    hosted_url: str,
) -> bool:
    parsed = urlparse(hosted_url)
    hostname = _normalized_text(parsed.hostname).lower()
    if _is_local_dev_host(hostname):
        return True
    policy = app_contract.get("verified_bridge_contract")
    if not isinstance(policy, dict):
        return False
    verification_status = _normalized_text(policy.get("verification_status")).lower()
    allow_same_origin_embed = bool(policy.get("allow_same_origin_embed"))
    same_origin_trusted = bool(policy.get("same_origin_trusted") or app_contract.get("same_origin_trusted"))
    return verification_status == "verified" and allow_same_origin_embed and same_origin_trusted


def normalize_hosted_app_fields(
    *,
    app_id: str,
    delivery_mode: Any = None,
    hosted_url: Any = None,
    embed_kind: Any = None,
    allowed_origins: Any = None,
    bridge_contracts: Any = None,
    permissions: Any = None,
    context_envelope: Any = None,
) -> Dict[str, Any]:
    normalized_hosted_url = _normalize_hosted_url(hosted_url)
    normalized_delivery_mode = _normalize_delivery_mode(delivery_mode, hosted_url=normalized_hosted_url)
    normalized_embed_kind = _normalize_embed_kind(embed_kind)
    normalized_bridge_contracts = _normalize_bridge_contracts(bridge_contracts, app_id=app_id)
    normalized_permissions = _normalize_permission_tokens(
        permissions,
        delivery_mode=normalized_delivery_mode,
        bridge_contracts=normalized_bridge_contracts,
    )
    normalized_context_envelope = _normalize_context_envelope(context_envelope)
    normalized_allowed_origins = _normalize_allowed_origins(
        allowed_origins,
        hosted_url=normalized_hosted_url if normalized_delivery_mode == "hosted" else None,
    )
    return {
        "delivery_mode": normalized_delivery_mode,
        "hosted_url": normalized_hosted_url,
        "embed_kind": normalized_embed_kind,
        "allowed_origins": normalized_allowed_origins,
        "bridge_contracts": normalized_bridge_contracts,
        "permissions": normalized_permissions,
        "context_envelope": normalized_context_envelope,
    }


def build_mini_app_csp_header(
    *,
    app_hosted_url: str,
    allowed_origins: Optional[List[str]] = None,
) -> str:
    origins = " ".join(
        str(origin).strip()
        for origin in (allowed_origins or [])
        if str(origin).strip()
    ) or "'none'"
    app_origin = "'none'"
    try:
        parsed = urlparse(str(app_hosted_url or "").strip())
        if parsed.scheme and parsed.netloc:
            app_origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    directives = [
        "default-src 'none'",
        f"script-src {app_origin}",
        f"style-src {app_origin} 'unsafe-inline'",
        f"img-src {app_origin} data: blob:",
        f"connect-src {app_origin} {origins}",
        f"frame-ancestors {origins}",
        "base-uri 'none'",
        "form-action 'none'",
    ]
    return "; ".join(directives)


def build_hosted_mini_app_manifest(
    *,
    workspace_id: str,
    app_contract: Dict[str, Any],
    launch_user_id: str = "",
    install_id: str = "",
) -> Optional[Dict[str, Any]]:
    if str(app_contract.get("delivery_mode") or "").strip().lower() != "hosted":
        return None
    hosted_url = _normalize_hosted_url(app_contract.get("hosted_url"))
    if not hosted_url:
        return None
    app_id = _normalized_text(app_contract.get("app_id"))
    bridge_contracts = app_contract.get("bridge_contracts") if isinstance(app_contract.get("bridge_contracts"), dict) else {}
    permissions = [token for token in list(app_contract.get("permissions") or []) if _normalized_text(token)]
    allowed_origins = _normalize_allowed_origins(app_contract.get("allowed_origins"), hosted_url=hosted_url)
    context_envelope = app_contract.get("context_envelope") if isinstance(app_contract.get("context_envelope"), dict) else {}
    iframe_sandbox = list(_DEFAULT_IFRAME_SANDBOX)
    if _should_allow_same_origin_in_iframe(app_contract=app_contract, hosted_url=hosted_url):
        iframe_sandbox.append("allow-same-origin")
    launch_token = (
        issue_hosted_launch_token(
            workspace_id=_normalized_text(workspace_id) or "default",
            app_id=app_id,
            user_id=launch_user_id,
            origin=allowed_origins[0],
            install_id=install_id,
        )
        if _normalized_text(launch_user_id) and allowed_origins
        else None
    )
    manifest = {
        "app_id": app_id,
        "workspace_id": _normalized_text(workspace_id) or "default",
        "delivery_mode": "hosted",
        "memory_scope": "none_by_default",
        "hosted_app": {
            "hosted_url": hosted_url,
            "allowed_origins": allowed_origins,
            "embed": {
                "kind": _normalize_embed_kind(app_contract.get("embed_kind")),
                "sandbox": iframe_sandbox,
                "allow": list(_DEFAULT_IFRAME_ALLOW),
                "referrer_policy": "origin",
            },
            "bridge": {
                "transport": "post_message",
                "request_type": DEFAULT_HOSTED_BRIDGE_MESSAGE_TYPE,
                "response_type": DEFAULT_HOSTED_BRIDGE_RESPONSE_TYPE,
                "ready_type": DEFAULT_HOSTED_BRIDGE_READY_TYPE,
                "endpoint": f"/api/workspaces/{_normalized_text(workspace_id) or 'default'}/mini-apps/{app_id}/bridge/messages",
                "allowed_contracts": bridge_contracts,
                "permissions": permissions,
                "context_envelope": context_envelope,
                "denied_by_default": list(app_bridge_service._app_denials()),
                "launch_token_required": True,
            },
        },
    }
    if launch_token:
        manifest["hosted_app"]["launch"] = launch_token
    return manifest


def _allowed_bridge_tokens(manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    hosted = manifest.get("hosted_app") if isinstance(manifest.get("hosted_app"), dict) else {}
    bridge = hosted.get("bridge") if isinstance(hosted.get("bridge"), dict) else {}
    allowed = bridge.get("allowed_contracts") if isinstance(bridge.get("allowed_contracts"), dict) else {}
    return {
        _normalized_text(kind).lower(): [
            _normalized_text(item).lower()
            for item in list(types)
            if _normalized_text(item)
        ]
        for kind, types in allowed.items()
        if _normalized_text(kind)
    }


def _required_permission_for_bridge_kind(bridge_kind: str) -> Optional[str]:
    return _BRIDGE_KIND_REQUIRED_PERMISSION.get(_normalized_text(bridge_kind).lower())


def _hosted_app_to_sage_enabled_by_verified_contract(app_contract: Dict[str, Any]) -> bool:
    policy = app_contract.get("verified_bridge_contract")
    if not isinstance(policy, dict):
        return False
    verification_status = _normalized_text(policy.get("verification_status")).lower()
    allow_app_to_sage = bool(policy.get("allow_hosted_app_to_sage"))
    return verification_status == "verified" and allow_app_to_sage


def _normalize_hosted_bridge_contract(
    *,
    app_id: str,
    bridge_kind: str,
    bridge_type: str,
    target: Optional[Dict[str, Any]] = None,
    context_envelope: Optional[Dict[str, Any]] = None,
    app_contract_context_envelope: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    kind = _normalized_text(bridge_kind).lower()
    bridge_token = _normalized_text(bridge_type).lower()
    allowed_map = app_bridge_service._bridge_contracts_map()
    if not kind or kind not in allowed_map:
        raise HTTPException(status_code=400, detail="Unsupported bridge_kind.")
    allowed_types = list(allowed_map.get(kind) or [])
    if not bridge_token or bridge_token not in allowed_types:
        raise HTTPException(status_code=400, detail="Unsupported bridge_type for this bridge_kind.")

    target_payload = dict(target or {}) if isinstance(target, dict) else {}
    app_bridge_service._assert_no_forbidden_bridge_keys(target_payload, root_label="bridge.target")
    if kind == "app_to_specialist" and not (
        _normalized_text(target_payload.get("target_install_id"))
        or _normalized_text(target_payload.get("target_capability"))
    ):
        raise HTTPException(status_code=400, detail="App -> specialist bridges require target_install_id or target_capability.")
    if kind == "sage_to_app":
        target_app_id = _normalized_text(target_payload.get("target_app_id") or app_id)
        if not target_app_id:
            raise HTTPException(status_code=400, detail="Sage -> app bridges require target_app_id.")
        target_payload["target_app_id"] = target_app_id
    if kind == "app_to_connector_runtime" and not (
        _normalized_text(target_payload.get("connector_id"))
        or _normalized_text(target_payload.get("workflow_id"))
        or _normalized_text(target_payload.get("route_key"))
    ):
        raise HTTPException(status_code=400, detail="App -> connector/runtime bridges require connector_id, workflow_id, or route_key.")

    normalized_metadata = dict(metadata or {}) if isinstance(metadata, dict) else {}
    app_bridge_service._assert_no_forbidden_bridge_keys(normalized_metadata, root_label="bridge.metadata")
    contract_context = _normalize_context_envelope(app_contract_context_envelope)
    normalized_envelope = app_bridge_service.normalize_app_context_envelope(
        context_envelope,
        contract={"context_envelope": contract_context},
    )
    return {
        "app_id": app_id,
        "bridge_kind": kind,
        "bridge_type": bridge_token,
        "target": target_payload,
        "context_envelope": normalized_envelope,
        "metadata": normalized_metadata,
        "denied_by_default": list(app_bridge_service._app_denials()),
        "allowed_bridge_types": allowed_types,
    }


async def process_hosted_bridge_request(
    *,
    workspace_id: str,
    tenant_id: str,
    current_user: Dict[str, Any],
    app_contract: Dict[str, Any],
    origin: str,
    bridge_kind: str,
    bridge_type: str,
    request_text: str = "",
    target: Optional[Dict[str, Any]] = None,
    context_envelope: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    manifest = build_hosted_mini_app_manifest(
        workspace_id=workspace_id,
        app_contract=app_contract,
    )
    if not manifest:
        raise ValueError("Hosted mini app manifest is not available for this app.")
    normalized_origin = _normalize_origin(origin)
    allowed_origins = set(manifest["hosted_app"]["allowed_origins"])
    if normalized_origin not in allowed_origins:
        raise PermissionError("Hosted mini app origin is not allowed.")

    kind = _normalized_text(bridge_kind).lower()
    bridge_token = _normalized_text(bridge_type).lower()
    allowed_tokens = _allowed_bridge_tokens(manifest)
    if kind not in allowed_tokens or bridge_token not in allowed_tokens.get(kind, []):
        raise ValueError("Hosted mini app bridge contract is not allowed.")
    required_permission = _required_permission_for_bridge_kind(kind)
    granted_permissions = {
        _normalized_text(item).lower()
        for item in list(app_contract.get("permissions") or [])
        if _normalized_text(item)
    }
    if required_permission and required_permission not in granted_permissions:
        raise PermissionError(
            f"Hosted mini app bridge '{kind}' requires explicit permission '{required_permission}'."
        )

    bridge = _normalize_hosted_bridge_contract(
        app_id=_normalized_text(app_contract.get("app_id")),
        bridge_kind=kind,
        bridge_type=bridge_token,
        target=dict(target or {}) if isinstance(target, dict) else None,
        context_envelope=dict(context_envelope or {}) if isinstance(context_envelope, dict) else None,
        app_contract_context_envelope=app_contract.get("context_envelope")
        if isinstance(app_contract.get("context_envelope"), dict)
        else {},
        metadata={
            **(dict(metadata or {}) if isinstance(metadata, dict) else {}),
            "request_origin": normalized_origin,
            "delivery_mode": "hosted",
            "request_text": _normalized_text(request_text) or None,
        },
    )

    guarded_request = external_content_guard.wrap_external_content(
        _normalized_text(request_text),
        source="hosted_mini_app",
        sender=_normalized_text(app_contract.get("app_id")) or "hosted-mini-app",
        channel="hosted_mini_app_bridge",
        source_event_id=_normalized_text((metadata or {}).get("request_id") if isinstance(metadata, dict) else "") or None,
        metadata={
            "workspace_id": _normalized_text(workspace_id),
            "bridge_kind": kind,
            "bridge_type": bridge_token,
            "origin": normalized_origin,
        },
    )
    guarded_request_text = guarded_request.text if _normalized_text(request_text) else ""

    rejection_reason = ""
    if kind == "app_to_sage" and not _hosted_app_to_sage_enabled_by_verified_contract(app_contract):
        rejection_reason = (
            "Hosted mini apps cannot invoke Sage turns by default. "
            "A verified bridge contract must explicitly enable app_to_sage execution."
        )

    turn_payload: Dict[str, Any] = {}
    if kind == "app_to_sage" and guarded_request_text and not rejection_reason:
        agent_registry_api = importlib.import_module("server_modules.agent_registry_api")

        master_install = await agent_registry_api.agent_registry_repository.get_workspace_master_agent_install(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            created_by_user_id=_normalized_text((current_user or {}).get("user_id")) or None,
        )
        if not isinstance(master_install, dict):
            raise HTTPException(status_code=500, detail="Workspace master agent is unavailable.")
        turn_result = await agent_registry_api.execute_install_agent_turn(
            install_id=_normalized_text(master_install.get("id")),
            current_user=current_user,
            message=guarded_request_text,
            channel="web",
            execution_mode="durable",
            response_mode="artifact",
            metadata_overrides={
                "source": "mini_apps.hosted_bridge",
                "app_id": bridge.get("app_id"),
                "app_bridge": bridge,
                "app_context_envelope": bridge.get("context_envelope"),
                "external_content_guard": {
                    "wrapper_id": guarded_request.wrapper_id,
                    "source": guarded_request.metadata.source,
                    "channel": guarded_request.metadata.channel,
                    "suspicious_patterns": list(guarded_request.suspicious_patterns),
                },
            },
        )
        if hasattr(turn_result, "model_dump"):
            turn_payload = turn_result.model_dump()
        elif hasattr(turn_result, "dict"):
            turn_payload = turn_result.dict()
        elif isinstance(turn_result, dict):
            turn_payload = dict(turn_result)

    audit = await app_bridge_service.record_app_bridge_audit(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_type="application",
        actor_id=_normalized_text(bridge.get("app_id")) or "hosted-mini-app",
        app_id=_normalized_text(bridge.get("app_id")) or "hosted-mini-app",
        bridge_kind=_normalized_text(bridge.get("bridge_kind")),
        bridge_type=_normalized_text(bridge.get("bridge_type")),
        target=bridge.get("target") if isinstance(bridge.get("target"), dict) else None,
        metadata={
            "source": "mini_apps.hosted_bridge",
            "origin": normalized_origin,
            "bridge_status": "rejected" if rejection_reason else "accepted",
            "bridge_rejection_reason": rejection_reason or None,
            "external_content_guard_wrapper_id": guarded_request.wrapper_id if guarded_request_text else None,
            "external_content_guard_suspicious_patterns": list(guarded_request.suspicious_patterns),
        },
    )
    if rejection_reason:
        raise PermissionError(rejection_reason)

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "origin": normalized_origin,
        "bridge": bridge,
        "audit": {"activity_event_id": _normalized_text((audit or {}).get("id")) or None},
        "turn_result": turn_payload,
        "run_id": _normalized_text(turn_payload.get("run_id")) or None,
        "thread_id": _normalized_text(turn_payload.get("thread_id")) or None,
        "session_id": _normalized_text(turn_payload.get("session_id")) or None,
    }

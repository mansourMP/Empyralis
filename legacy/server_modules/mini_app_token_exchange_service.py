from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any, Dict, Optional, Set

from server_modules import mini_app_host_service
from server_modules import mini_apps_service

EXCHANGE_TOKEN_TTL_SECONDS = 15 * 60
SCOPE_CATALOG: Set[str] = {
    "workspace:read",
    "records:write",
    "ai:invoke",
    "agent:create",
    "webhook:write",
}

_used_jti_cache: Dict[str, float] = {}
_JTI_CACHE_LOCK = __import__("threading").Lock()
_JTI_MAX_CACHE_SIZE = 10_000
_MAX_JTI_AGE_SECONDS = max(EXCHANGE_TOKEN_TTL_SECONDS, 30 * 60)


class ConfigurationError(RuntimeError):
    pass


def _exchange_secret() -> bytes:
    secret = os.getenv("EMPYRALIS_MINI_APP_EXCHANGE_SECRET")
    if not secret:
        raise ConfigurationError(
            "EMPYRALIS_MINI_APP_EXCHANGE_SECRET is required. Set this environment variable before issuing mini-app exchange tokens."
        )
    return secret.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    padding = "=" * ((4 - len(token) % 4) % 4)
    return base64.urlsafe_b64decode((token + padding).encode("ascii"))


def _normalize_scopes(scopes: Any) -> Set[str]:
    if not isinstance(scopes, list):
        return set()
    return {s for s in (str(item).strip() for item in scopes) if s in SCOPE_CATALOG}


def _prune_jti_cache(now: float) -> None:
    with _JTI_CACHE_LOCK:
        stale = [jti for jti, ts in _used_jti_cache.items() if now - ts > _MAX_JTI_AGE_SECONDS]
        for jti in stale:
            _used_jti_cache.pop(jti, None)
        if len(_used_jti_cache) > _JTI_MAX_CACHE_SIZE:
            oldest = sorted(_used_jti_cache.items(), key=lambda item: item[1])[: len(_used_jti_cache) // 4]
            for jti, _ts in oldest:
                _used_jti_cache.pop(jti, None)


def _check_jti_replay(jti: str) -> bool:
    now = time.time()
    _prune_jti_cache(now)
    with _JTI_CACHE_LOCK:
        if jti in _used_jti_cache:
            return False
        _used_jti_cache[jti] = now
        return True


def issue_access_token(
    *,
    workspace_id: str,
    app_id: str,
    user_id: str,
    scopes: Set[str],
    ttl_seconds: int = EXCHANGE_TOKEN_TTL_SECONDS,
) -> Dict[str, Any]:
    now = int(time.time())
    jti = uuid.uuid4().hex
    normalized_scopes = _normalize_scopes(list(scopes))
    payload = {
        "sub": app_id,
        "aud": "empyralis-api",
        "workspace_id": str(workspace_id or "default").strip() or "default",
        "user_id": str(user_id or "").strip(),
        "app_id": str(app_id or "").strip(),
        "scopes": sorted(normalized_scopes),
        "jti": jti,
        "iat": now,
        "exp": now + max(30, int(ttl_seconds or EXCHANGE_TOKEN_TTL_SECONDS)),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(payload_bytes)
    signature = hmac.new(_exchange_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return {
        "access_token": f"{body}.{_b64url_encode(signature)}",
        "token_type": "Bearer",
        "expires_at": payload["exp"],
        "scope": " ".join(sorted(normalized_scopes)),
        "jti": jti,
    }


def verify_access_token(token: str) -> Dict[str, Any]:
    raw_token = str(token or "").strip()
    if not raw_token or "." not in raw_token:
        raise PermissionError("Access token is required.")
    body, signature = raw_token.split(".", 1)
    expected = _b64url_encode(hmac.new(_exchange_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise PermissionError("Access token signature is invalid.")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise PermissionError("Access token is malformed.") from exc
    if not isinstance(payload, dict):
        raise PermissionError("Access token payload is malformed.")
    now = int(time.time())
    if int(payload.get("exp") or 0) <= now:
        raise PermissionError("Access token has expired.")
    return payload


def exchange_launch_token(
    *,
    workspace_id: str,
    app_id: str,
    launch_token: str,
    requested_scopes: Optional[list[str]] = None,
) -> Dict[str, Any]:
    verified = mini_app_host_service.verify_hosted_launch_token(launch_token)
    if verified.get("workspace_id", "").strip() != str(workspace_id or "default").strip():
        raise PermissionError("Launch token workspace mismatch.")
    if verified.get("app_id", "").strip() != str(app_id or "").strip():
        raise PermissionError("Launch token app mismatch.")
    jti = str(verified.get("jti") or "").strip()
    if jti and not _check_jti_replay(jti):
        raise PermissionError("Launch token has already been used.")
    try:
        contract = mini_apps_service.get_mini_app_contract(workspace_id, app_id)
    except KeyError:
        raise PermissionError("App not found.")
    allowed_scopes = _normalize_scopes(contract.get("permissions") or [])
    if requested_scopes:
        allowed_scopes = allowed_scopes & _normalize_scopes(requested_scopes)
    user_id = str(verified.get("user_id") or "").strip()
    return issue_access_token(
        workspace_id=workspace_id,
        app_id=app_id,
        user_id=user_id,
        scopes=allowed_scopes,
    )


def enforce_access_token_scope(token_payload: Dict[str, Any], required_scope: str) -> None:
    scopes = set(token_payload.get("scopes") or [])
    if required_scope not in scopes:
        raise PermissionError(f"Access token is missing required scope: {required_scope}")

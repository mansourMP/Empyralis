from __future__ import annotations

import base64
import asyncio
import copy
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Header, HTTPException, Request, Response
from server_modules import control_plane_repository
from server_modules import auth_store_repository
from server_modules import email_verification_service
from server_modules import entitlements_service
from server_modules import client_identity_service, quota_policy_service, quota_response_service
from server_modules import security_audit_service
from server_modules.downstream_resilience_service import (
    CircuitBreakerPolicy,
    DownstreamCircuitOpenError,
    RetryPolicy,
    call_with_retries,
)
from server_modules.direct_tool_config_service import run_async_tool_call
from server_modules.jwt_secret import resolve_jwt_secret
from server_modules.sqlite_helpers import connect_sqlite_rw

EMPYRALIS_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
AUTH_DB_FILE = (EMPYRALIS_STATE_HOME / "auth" / "users.db").expanduser()
LOGGER = logging.getLogger(__name__)
AUTH_LOCK = threading.Lock()
LOGIN_RATE_LIMIT_LOCK = threading.Lock()
LOGIN_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
USER_RATE_LIMIT_LOCK = threading.Lock()
USER_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
CSRF_FAILURE_RATE_LIMIT_LOCK = threading.Lock()
CSRF_FAILURE_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
REFRESH_RATE_LIMIT_LOCK = threading.Lock()
REFRESH_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
EMAIL_VERIFICATION_RATE_LIMIT_LOCK = threading.Lock()
EMAIL_VERIFICATION_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
JWT_EXP_SECONDS = int(os.getenv("ORION_JWT_EXP_SECONDS", "3600"))
MOBILE_JWT_EXP_SECONDS = int(os.getenv("ORION_MOBILE_JWT_EXP_SECONDS", str(60 * 60 * 24 * 30)))
MOBILE_REFRESH_EXP_SECONDS = int(os.getenv("ORION_MOBILE_REFRESH_EXP_SECONDS", str(60 * 60 * 24 * 180)))
ORION_PUBLIC_REGISTRATION_ENABLED = str(os.getenv("ORION_PUBLIC_REGISTRATION_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
ORION_ADMIN_USER_IDS = {item.strip() for item in str(os.getenv("ORION_ADMIN_USER_IDS", "")).split(",") if item.strip()}
ORION_ADMIN_EMAILS = {item.strip().lower() for item in str(os.getenv("ORION_ADMIN_EMAILS", "")).split(",") if item.strip()}
ORION_SERVICE_RATE_LIMIT_PER_MINUTE = int(os.getenv("ORION_SERVICE_RATE_LIMIT_PER_MINUTE", "600"))
ORION_AUTH_LOGIN_RATE_LIMIT_PER_MINUTE = int(os.getenv("ORION_AUTH_LOGIN_RATE_LIMIT_PER_MINUTE", "5"))
ORION_AUTHENTICATED_API_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_AUTHENTICATED_API_RATE_LIMIT_PER_MINUTE", "600")
)
ORION_MOBILE_AUTHENTICATED_API_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_MOBILE_AUTHENTICATED_API_RATE_LIMIT_PER_MINUTE", "600")
)
ORION_MODEL_INVOCATION_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_MODEL_INVOCATION_RATE_LIMIT_PER_MINUTE", "180")
)
ORION_MOBILE_MODEL_INVOCATION_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_MOBILE_MODEL_INVOCATION_RATE_LIMIT_PER_MINUTE", "120")
)
ORION_AUTH_CSRF_FAILURE_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_AUTH_CSRF_FAILURE_RATE_LIMIT_PER_MINUTE", "30")
)
ORION_AUTH_REFRESH_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_AUTH_REFRESH_RATE_LIMIT_PER_MINUTE", "10")
)
ORION_AUTH_EMAIL_VERIFICATION_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_AUTH_EMAIL_VERIFICATION_RATE_LIMIT_PER_MINUTE", "10")
)
ORION_MOBILE_BETA_AUTO_SIGNIN_ENABLED = str(
    os.getenv("ORION_MOBILE_BETA_AUTO_SIGNIN_ENABLED", "0")
).strip().lower() in {"1", "true", "yes", "on"}
RBAC_ROLE_ORDER = {"viewer": 0, "member": 1, "owner": 2}
WORKSPACE_CAPABILITY_ALL = "*"
AUTH_SESSION_CHANNELS = {"web", "desktop", "mobile", "local_runtime_companion"}
AUTH_SESSION_STATUSES = {"active", "revoked", "expired"}
DEVICE_LINK_STATUSES = {"active", "pending", "unlinked", "revoked"}
DEVICE_TRUST_STATES = {"unbound", "pending", "verified", "limited", "revoked"}
AUTH_ACCESS_COOKIE_NAME = str(os.getenv("EMPYRALIS_AUTH_ACCESS_COOKIE", "empyralis_access_token")).strip() or "empyralis_access_token"
AUTH_REFRESH_COOKIE_NAME = str(os.getenv("EMPYRALIS_AUTH_REFRESH_COOKIE", "empyralis_refresh_token")).strip() or "empyralis_refresh_token"
AUTH_CSRF_COOKIE_NAME = str(os.getenv("EMPYRALIS_AUTH_CSRF_COOKIE", "empyralis_csrf_token")).strip() or "empyralis_csrf_token"
AUTH_CSRF_HEADER_NAME = "x-csrf-token"
AUTH_COOKIE_PATH = "/"
AUTH_ACCESS_COOKIE_MAX_AGE_SECONDS = max(
    int(os.getenv("EMPYRALIS_AUTH_ACCESS_COOKIE_MAX_AGE_SECONDS", str(60 * 60 * 24))),
    60,
)
AUTH_REFRESH_COOKIE_MAX_AGE_SECONDS = max(
    int(os.getenv("EMPYRALIS_AUTH_REFRESH_COOKIE_MAX_AGE_SECONDS", str(60 * 60 * 24 * 30))),
    60,
)
BROWSER_AUTH_SESSION_CHANNELS = {"web", "desktop"}
PROVIDER_JWKS_CACHE_LOCK = threading.Lock()
PROVIDER_JWKS_CACHE: Dict[str, dict[str, Any]] = {}
PROVIDER_JWKS_CACHE_TTL_SECONDS = max(int(os.getenv("ORION_PROVIDER_JWKS_CACHE_TTL_SECONDS", "3600")), 60)
PROVIDER_JWKS_STALE_TTL_SECONDS = max(int(os.getenv("ORION_PROVIDER_JWKS_STALE_TTL_SECONDS", "86400")), PROVIDER_JWKS_CACHE_TTL_SECONDS)
PROVIDER_JWKS_RETRY_ATTEMPTS = max(int(os.getenv("ORION_PROVIDER_JWKS_RETRY_ATTEMPTS", "3")), 1)
PROVIDER_HTTP_TIMEOUT_SECONDS = max(float(os.getenv("ORION_PROVIDER_HTTP_TIMEOUT_SECONDS", "8")), 1.0)
AUTH_HOT_CACHE_LOCK = threading.Lock()
AUTH_HOT_CACHE_TTL_SECONDS = max(int(os.getenv("ORION_AUTH_HOT_CACHE_TTL_SECONDS", "15")), 5)
WORKSPACE_TENANT_CACHE: Dict[str, dict[str, Any]] = {}
TENANT_POLICY_CACHE: Dict[str, dict[str, Any]] = {}
WORKSPACE_POLICY_CACHE: Dict[str, dict[str, Any]] = {}
AUTH_DB_SCHEMA_READY_PATHS: set[str] = set()


def _auth_hot_cache_get(cache: Dict[str, dict[str, Any]], key: str) -> Any:
    now = time.time()
    with AUTH_HOT_CACHE_LOCK:
        cached = cache.get(key)
        if not isinstance(cached, dict):
            return None
        expires_at = float(cached.get("expires_at") or 0.0)
        if expires_at <= now:
            cache.pop(key, None)
            return None
        return copy.deepcopy(cached.get("value"))


def _auth_hot_cache_put(
    cache: Dict[str, dict[str, Any]],
    key: str,
    value: Any,
    *,
    ttl_seconds: Optional[int] = None,
) -> None:
    ttl = max(int(ttl_seconds or AUTH_HOT_CACHE_TTL_SECONDS), 1)
    with AUTH_HOT_CACHE_LOCK:
        cache[key] = {
            "value": copy.deepcopy(value),
            "expires_at": time.time() + ttl,
        }


def _auth_hot_cache_drop(cache: Dict[str, dict[str, Any]], key: str) -> None:
    with AUTH_HOT_CACHE_LOCK:
        cache.pop(key, None)


def _invalidate_workspace_auth_caches(
    *,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> None:
    if workspace_id:
        _auth_hot_cache_drop(WORKSPACE_TENANT_CACHE, workspace_id)
        _auth_hot_cache_drop(WORKSPACE_POLICY_CACHE, workspace_id)
    if tenant_id:
        _auth_hot_cache_drop(TENANT_POLICY_CACHE, tenant_id)


def _control_plane_call(coro: Any) -> Any:
    try:
        return run_async_tool_call(coro)
    except Exception:
        return None


_AUTH_STORE_CHOICE_LOGGED = False
_PG_NA = auth_store_repository.PG_NA


def _auth_store_pg(coro: Any) -> Any:
    """Bridge auth.py's sync functions to the async, Postgres-first auth-store
    ops. Returns auth_store_repository.PG_NA when the pool is unavailable (so
    the caller runs its unchanged SQLite fallback), the real result otherwise.

    Unlike _control_plane_call, this does NOT swallow errors: a genuine
    Postgres failure surfaces rather than silently diverging to SQLite.
    Emits a one-time loud line naming which store auth is actually using.
    """
    global _AUTH_STORE_CHOICE_LOGGED
    result = run_async_tool_call(coro)
    if not _AUTH_STORE_CHOICE_LOGGED:
        _AUTH_STORE_CHOICE_LOGGED = True
        if result is _PG_NA:
            LOGGER.warning(
                "AUTH STORE: SQLite fallback at %s — DATABASE_URL not set or "
                "Postgres unreachable; sessions, devices, policies, auth methods "
                "read/write SQLite only.",
                AUTH_DB_FILE,
            )
        else:
            LOGGER.warning(
                "AUTH STORE: Postgres (DATABASE_URL configured) — sessions, "
                "devices, policies, and auth methods read/write Postgres and are durable."
            )
    return result


def public_registration_enabled() -> bool:
    raw = os.getenv("ORION_PUBLIC_REGISTRATION_ENABLED")
    if raw is None:
        return bool(ORION_PUBLIC_REGISTRATION_ENABLED)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def browser_auth_session_channel(channel: Any) -> bool:
    return _normalize_auth_session_channel(channel, default="web") in BROWSER_AUTH_SESSION_CHANNELS


def _request_scheme(request: Request) -> str:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").strip().lower()
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip() or "http"
    try:
        return str(request.url.scheme or "").strip().lower() or "http"
    except Exception:
        return "http"


def _cookie_secure(request: Request) -> bool:
    if _environment_is_production():
        return True
    return _request_scheme(request) == "https"


def _cookie_domain() -> Optional[str]:
    raw_domain = str(os.getenv("EMPYRALIS_AUTH_COOKIE_DOMAIN") or "").strip()
    return raw_domain or None


def _cookie_samesite() -> str:
    raw_samesite = str(os.getenv("EMPYRALIS_AUTH_COOKIE_SAMESITE") or "").strip().lower()
    if raw_samesite in {"lax", "strict", "none"}:
        return raw_samesite
    return "strict" if _environment_is_production() else "lax"


def _bounded_cookie_max_age(raw_max_age: Any, *, hard_cap_seconds: int) -> int:
    try:
        max_age = int(raw_max_age)
    except Exception:
        max_age = 60
    if max_age < 60:
        max_age = 60
    if max_age > hard_cap_seconds:
        return hard_cap_seconds
    return max_age


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def auth_cookie_access_token(request: Request) -> Optional[str]:
    token = str(request.cookies.get(AUTH_ACCESS_COOKIE_NAME) or "").strip()
    return token or None


def auth_cookie_refresh_token(request: Request) -> Optional[str]:
    token = str(request.cookies.get(AUTH_REFRESH_COOKIE_NAME) or "").strip()
    return token or None


def set_auth_cookies(
    response: Response,
    payload: dict[str, Any],
    *,
    request: Request,
    channel: Any = "web",
) -> None:
    if not browser_auth_session_channel(channel):
        return
    access_token = str(payload.get("token") or "").strip()
    if not access_token:
        return
    token_payload = _decode_token_payload(access_token)
    now_ts = int(time.time())
    access_max_age = _bounded_cookie_max_age(
        int(token_payload.get("exp") or now_ts) - now_ts,
        hard_cap_seconds=AUTH_ACCESS_COOKIE_MAX_AGE_SECONDS,
    )

    refresh_payload = payload.get("session_recovery") if isinstance(payload.get("session_recovery"), dict) else {}
    refresh_token = str(refresh_payload.get("refresh_token") or "").strip()
    refresh_max_age = (
        _bounded_cookie_max_age(
            int(refresh_payload.get("refresh_expires_at") or now_ts) - now_ts,
            hard_cap_seconds=AUTH_REFRESH_COOKIE_MAX_AGE_SECONDS,
        )
        if refresh_token
        else None
    )
    csrf_token = issue_csrf_token()
    secure = _cookie_secure(request)
    domain = _cookie_domain()
    samesite = _cookie_samesite()

    response.set_cookie(
        key=AUTH_ACCESS_COOKIE_NAME,
        value=access_token,
        max_age=access_max_age,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path=AUTH_COOKIE_PATH,
        domain=domain,
    )
    if refresh_token and refresh_max_age is not None:
        response.set_cookie(
            key=AUTH_REFRESH_COOKIE_NAME,
            value=refresh_token,
            max_age=refresh_max_age,
            httponly=True,
            secure=secure,
            samesite=samesite,
            path=AUTH_COOKIE_PATH,
            domain=domain,
        )
    response.set_cookie(
        key=AUTH_CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=refresh_max_age or access_max_age,
        httponly=False,
        secure=secure,
        samesite=samesite,
        path=AUTH_COOKIE_PATH,
        domain=domain,
    )


def clear_auth_cookies(response: Response, *, request: Request) -> None:
    secure = _cookie_secure(request)
    domain = _cookie_domain()
    samesite = _cookie_samesite()
    # httponly must mirror what set_auth_cookies() used for each cookie: the
    # access/refresh cookies are HttpOnly, the CSRF cookie is intentionally
    # readable by JS. Starlette's delete_cookie() defaults httponly to False,
    # so this must be passed explicitly or the deletion Set-Cookie header for
    # the auth cookies is inconsistent with the cookie that was actually set.
    httponly_by_cookie = {
        AUTH_ACCESS_COOKIE_NAME: True,
        AUTH_REFRESH_COOKIE_NAME: True,
        AUTH_CSRF_COOKIE_NAME: False,
    }
    for cookie_name in (AUTH_ACCESS_COOKIE_NAME, AUTH_REFRESH_COOKIE_NAME, AUTH_CSRF_COOKIE_NAME):
        response.delete_cookie(
            key=cookie_name,
            path=AUTH_COOKIE_PATH,
            domain=domain,
            secure=secure,
            samesite=samesite,
            httponly=httponly_by_cookie[cookie_name],
        )


def _access_token_is_live(token: str) -> bool:
    """True only when the access cookie is a validly-signed, unexpired token.

    Used on the re-auth path to tell a real session apart from a stale cookie
    the browser is still echoing back. A dead token grants no authority, so it
    must not force CSRF and lock the user out of re-authenticating."""
    raw = str(token or "").strip()
    if not raw:
        return False
    try:
        payload = _decode_token_payload(raw)
    except Exception:
        return False
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return False
    if exp and exp < int(time.time()):
        return False
    return True


def _refresh_token_is_structurally_valid(token: str) -> bool:
    """Cheap structural check for the opaque ``esr_<session>.<secret>`` refresh
    token. A malformed/garbage value is treated as no session on the re-auth
    path — its true (DB-backed) expiry is validated by the refresh handler
    itself, which clears cookies on a dead token."""
    try:
        _decode_auth_session_refresh_token(token)
        return True
    except Exception:
        return False


def validate_csrf(request: Request, *, allow_expired_session: bool = False) -> bool:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return True
    access = auth_cookie_access_token(request)
    refresh = auth_cookie_refresh_token(request)
    if allow_expired_session:
        # Login/refresh/logout path: a stale cookie the browser is still
        # echoing back represents no live session. Treat a dead access token
        # and a malformed refresh token as absent so CSRF can't lock a user out
        # of recovering from an expired session. A *live* credential is still
        # honored below, so CSRF stays fully enforced for real sessions.
        if access and not _access_token_is_live(access):
            access = None
        if refresh and not _refresh_token_is_structurally_valid(refresh):
            refresh = None
    if not (access or refresh):
        return True
    csrf_cookie = str(request.cookies.get(AUTH_CSRF_COOKIE_NAME) or "").strip()
    csrf_header = str(request.headers.get(AUTH_CSRF_HEADER_NAME) or "").strip()
    if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
        _enforce_window_limit(
            request=request,
            buckets=CSRF_FAILURE_RATE_LIMIT_BUCKETS,
            lock=CSRF_FAILURE_RATE_LIMIT_LOCK,
            key=f"csrf:{_client_ip(request)}",
            limit=max(1, int(ORION_AUTH_CSRF_FAILURE_RATE_LIMIT_PER_MINUTE or 30)),
            profile_name=quota_policy_service.AUTH_CSRF_FAILURE_PROFILE.name,
        )
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    return True


def auth_provider_options() -> dict[str, Any]:
    google_enabled = bool(_configured_provider_audiences("google"))
    apple_enabled = bool(_configured_provider_audiences("apple"))
    return {
        "email": {"enabled": True},
        "google": {"enabled": google_enabled},
        "apple": {"enabled": apple_enabled},
        # Lets the signup form show/require the invite-code field before the
        # user even submits, instead of only learning via a 403 on submit.
        "invite_required": bool(str(os.getenv("EMPYRALIS_INVITE_CODE") or "").strip()),
    }


def _configured_provider_audiences(provider: str) -> list[str]:
    normalized_provider = str(provider or "").strip().lower()
    env_keys: tuple[str, ...]
    if normalized_provider == "google":
        env_keys = (
            "GOOGLE_AUDIENCES",
            "GOOGLE_AUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_CLIENT_ID",
            "GOOGLE_IOS_CLIENT_ID",
            "GOOGLE_ANDROID_CLIENT_ID",
            "GOOGLE_WEB_CLIENT_ID",
            "EXPO_PUBLIC_GOOGLE_CLIENT_ID",
            "EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID",
            "EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID",
            "EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID",
        )
    elif normalized_provider == "apple":
        env_keys = (
            "APPLE_AUDIENCES",
            "APPLE_CLIENT_ID",
            "APPLE_MOBILE_CLIENT_ID",
            "EMPYRALIS_IOS_BUNDLE_ID",
            "EXPO_PUBLIC_IOS_BUNDLE_ID",
        )
    else:
        env_keys = ()
    values: list[str] = []
    seen: set[str] = set()
    for key in env_keys:
        raw_value = str(os.getenv(key, "")).strip()
        if not raw_value:
            continue
        for token in raw_value.split(","):
            audience = str(token or "").strip()
            if not audience or audience in seen:
                continue
            seen.add(audience)
            values.append(audience)
    return values


def _normalize_external_auth_provider(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"google", "google_oauth"}:
        return "google"
    if token in {"apple", "apple_oauth"}:
        return "apple"
    raise HTTPException(status_code=400, detail="Unsupported authentication provider.")


def _parse_external_identity_jwt_segment(token: str, index: int, *, detail: str) -> dict[str, Any]:
    try:
        segment = str(token or "").split(".", 2)[index]
    except Exception as exc:
        raise HTTPException(status_code=401, detail=detail) from exc
    try:
        payload = json.loads(_b64url_decode(segment).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail=detail) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail=detail)
    return payload


def _parse_external_identity_jwt_header(token: str) -> dict[str, Any]:
    return _parse_external_identity_jwt_segment(token, 0, detail="Identity token header is invalid.")


def _parse_external_identity_jwt_payload(token: str) -> dict[str, Any]:
    return _parse_external_identity_jwt_segment(token, 1, detail="Identity token payload is invalid.")


def _fetch_provider_jwks(cache_key: str, url: str) -> dict[str, Any]:
    now = time.time()
    stale_payload: dict[str, Any] | None = None
    with PROVIDER_JWKS_CACHE_LOCK:
        cached = PROVIDER_JWKS_CACHE.get(cache_key)
        if isinstance(cached, dict):
            payload = cached.get("payload")
            expires_at = float(cached.get("expires_at") or 0)
            stale_until = float(cached.get("stale_until") or 0)
            if isinstance(payload, dict) and expires_at > now:
                return payload
            if isinstance(payload, dict) and stale_until > now:
                stale_payload = payload

    def _request_jwks() -> dict[str, Any]:
        response = requests.get(url, timeout=PROVIDER_HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()

    try:
        payload = call_with_retries(
            name=f"provider_jwks:{cache_key}",
            operation=_request_jwks,
            retry_policy=RetryPolicy(attempts=PROVIDER_JWKS_RETRY_ATTEMPTS, initial_delay_seconds=0.05),
            circuit_policy=CircuitBreakerPolicy(failure_threshold=3, reset_after_seconds=30.0),
        )
    except DownstreamCircuitOpenError as exc:
        if stale_payload is not None:
            LOGGER.warning("Using stale provider JWKS for %s because circuit is open.", cache_key)
            return stale_payload
        raise HTTPException(status_code=503, detail="Authentication provider keys are unavailable.") from exc
    except Exception as exc:
        if stale_payload is not None:
            LOGGER.warning("Using stale provider JWKS for %s after fetch failure: %s", cache_key, exc)
            return stale_payload
        raise HTTPException(status_code=503, detail="Authentication provider keys are unavailable.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=503, detail="Authentication provider keys are unavailable.")
    with PROVIDER_JWKS_CACHE_LOCK:
        PROVIDER_JWKS_CACHE[cache_key] = {
            "payload": payload,
            "expires_at": now + PROVIDER_JWKS_CACHE_TTL_SECONDS,
            "stale_until": now + PROVIDER_JWKS_STALE_TTL_SECONDS,
        }
    return payload


def _verify_rs256_identity_token(token: str, *, jwks_cache_key: str, jwks_url: str) -> dict[str, Any]:
    header = _parse_external_identity_jwt_header(token)
    payload = _parse_external_identity_jwt_payload(token)
    parts = str(token or "").split(".", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Identity token is invalid.")
    header_segment, payload_segment, signature_segment = parts
    kid = str(header.get("kid") or "").strip()
    alg = str(header.get("alg") or "").strip().upper()
    if not kid or alg != "RS256":
        raise HTTPException(status_code=401, detail="Identity token is invalid.")
    jwks_payload = _fetch_provider_jwks(jwks_cache_key, jwks_url)
    jwk_keys = jwks_payload.get("keys")
    matching_key = next(
        (
            item
            for item in list(jwk_keys or [])
            if isinstance(item, dict) and str(item.get("kid") or "").strip() == kid
        ),
        None,
    )
    if not isinstance(matching_key, dict):
        raise HTTPException(status_code=401, detail="Identity token could not be verified.")
    try:
        modulus = int.from_bytes(_b64url_decode(str(matching_key.get("n") or "").strip()), "big")
        exponent = int.from_bytes(_b64url_decode(str(matching_key.get("e") or "").strip()), "big")
        public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
        public_key.verify(
            _b64url_decode(signature_segment),
            f"{header_segment}.{payload_segment}".encode("utf-8"),
            asymmetric_padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Identity token could not be verified.") from exc
    return payload


def _validate_external_identity_claims(
    provider: str,
    claims: dict[str, Any],
) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    normalized_provider = _normalize_external_auth_provider(provider)
    now = int(time.time())
    issuer = str(claims.get("iss") or "").strip()
    audience = str(claims.get("aud") or "").strip()
    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower() or None
    name = str(claims.get("name") or "").strip() or None
    avatar_url = str(claims.get("picture") or claims.get("avatar_url") or "").strip() or None
    exp_value = claims.get("exp")
    try:
        exp = int(float(exp_value)) if exp_value is not None else 0
    except Exception:
        exp = 0
    if not subject:
        raise HTTPException(status_code=401, detail="Identity token is missing a subject.")
    if normalized_provider in {"apple", "google"} and not exp:
        raise HTTPException(status_code=401, detail="Identity token expiration is missing.")
    if exp and exp <= now:
        raise HTTPException(status_code=401, detail="Identity token has expired.")
    if normalized_provider == "apple":
        if issuer != "https://appleid.apple.com":
            raise HTTPException(status_code=401, detail="Apple identity token is invalid.")
    elif normalized_provider == "google":
        if issuer not in {"https://accounts.google.com", "accounts.google.com"}:
            raise HTTPException(status_code=401, detail="Google identity token is invalid.")
        email_verified = claims.get("email_verified")
        if email and str(email_verified).strip().lower() not in {"true", "1"}:
            raise HTTPException(status_code=401, detail="Google account email is not verified.")
    allowed_audiences = _configured_provider_audiences(normalized_provider)
    if normalized_provider in {"apple", "google"} and not allowed_audiences:
        raise HTTPException(status_code=503, detail=f"{normalized_provider.title()} authentication is not configured.")
    if normalized_provider in {"apple", "google"} and not audience:
        raise HTTPException(status_code=401, detail="Identity token audience is missing.")
    if audience not in set(allowed_audiences):
        raise HTTPException(status_code=401, detail="Identity token audience is invalid.")
    return subject, email, name, avatar_url


def verify_external_identity_token(
    provider: str,
    *,
    id_token: str,
    fallback_email: Optional[str] = None,
    fallback_name: Optional[str] = None,
    fallback_avatar_url: Optional[str] = None,
) -> dict[str, Optional[str]]:
    normalized_provider = _normalize_external_auth_provider(provider)
    clean_token = str(id_token or "").strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="identity_token is required.")
    if normalized_provider == "apple":
        claims = _verify_rs256_identity_token(
            clean_token,
            jwks_cache_key="apple",
            jwks_url="https://appleid.apple.com/auth/keys",
        )
    else:
        claims = _verify_rs256_identity_token(
            clean_token,
            jwks_cache_key="google",
            jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        )
    subject, email, name, avatar_url = _validate_external_identity_claims(normalized_provider, claims)
    resolved_email = email or (str(fallback_email or "").strip().lower() or None)
    resolved_name = name or (str(fallback_name or "").strip() or None)
    resolved_avatar_url = avatar_url or (str(fallback_avatar_url or "").strip() or None)
    return {
        "provider": normalized_provider,
        "subject": subject,
        "email": resolved_email,
        "name": resolved_name,
        "avatar_url": resolved_avatar_url,
    }


def _resolve_auth_session_ttl_seconds(channel: Any, ttl_seconds: Optional[int] = None) -> int:
    if ttl_seconds is not None:
        return max(int(ttl_seconds), 60)
    normalized_channel = _normalize_auth_session_channel(channel, default="web")
    if normalized_channel == "mobile":
        return max(int(MOBILE_JWT_EXP_SECONDS), 60)
    return max(int(JWT_EXP_SECONDS), 60)


def _resolve_auth_refresh_ttl_seconds(channel: Any) -> int:
    normalized_channel = _normalize_auth_session_channel(channel, default="web")
    if normalized_channel == "mobile":
        return max(int(MOBILE_REFRESH_EXP_SECONDS), 60)
    if browser_auth_session_channel(normalized_channel):
        return max(int(AUTH_REFRESH_COOKIE_MAX_AGE_SECONDS), 60)
    return max(int(JWT_EXP_SECONDS), 60)


def _resolve_auth_session_record_ttl_seconds(channel: Any, access_ttl_seconds: int) -> int:
    resolved_access_ttl = max(int(access_ttl_seconds or JWT_EXP_SECONDS), 60)
    normalized_channel = _normalize_auth_session_channel(channel, default="web")
    if normalized_channel == "mobile" or browser_auth_session_channel(normalized_channel):
        return max(resolved_access_ttl, _resolve_auth_refresh_ttl_seconds(normalized_channel))
    return resolved_access_ttl


def _resolve_authenticated_workspace_id(
    requested_workspace_id: Optional[str],
    workspace_access: dict[str, dict[str, Any]],
) -> str:
    requested = _normalize_workspace_token(requested_workspace_id, default="")
    entries = _workspace_access_entries(workspace_access)
    if requested:
        for entry in entries:
            if _normalize_workspace_token(entry.get("workspace_id"), default="") == requested:
                return requested
    default_entry = _default_workspace_entry(workspace_access)
    if isinstance(default_entry, dict) and str(default_entry.get("workspace_id") or "").strip():
        return _normalize_workspace_token(default_entry.get("workspace_id"), default="default")
    return requested or "default"


def _workspace_access_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        raw_items = list(value.values())
    elif isinstance(value, list):
        raw_items = list(value)
    else:
        raw_items = []
    entries: list[dict[str, Any]] = []
    seen_workspace_ids: set[str] = set()
    for raw_entry in raw_items:
        if not isinstance(raw_entry, dict):
            continue
        workspace_id = _normalize_workspace_token(raw_entry.get("workspace_id"), default="")
        if not workspace_id or workspace_id in seen_workspace_ids:
            continue
        entry = dict(raw_entry)
        entry["workspace_id"] = workspace_id
        tenant_id = _normalize_tenant_token(entry.get("tenant_id"), default="")
        if tenant_id:
            entry["tenant_id"] = tenant_id
        entries.append(entry)
        seen_workspace_ids.add(workspace_id)
    return entries


def _default_workspace_entry(workspace_access: Any) -> Optional[dict[str, Any]]:
    entries = _workspace_access_entries(workspace_access)
    if not entries:
        return None
    return entries[0]


def _workspace_entry_for_id(workspace_access: Any, workspace_id: Optional[str]) -> Optional[dict[str, Any]]:
    token = _normalize_workspace_token(workspace_id, default="")
    if not token:
        return None
    for entry in _workspace_access_entries(workspace_access):
        if _normalize_workspace_token(entry.get("workspace_id"), default="") == token:
            return entry
    return None


def _workspace_identity_projection(
    workspace_access: Any,
    *,
    requested_workspace_id: Optional[str] = None,
) -> dict[str, Any]:
    default_entry = _default_workspace_entry(workspace_access)
    default_workspace_id = _normalize_workspace_token(
        (default_entry or {}).get("workspace_id"),
        default="",
    ) or None
    current_workspace_id = _resolve_authenticated_workspace_id(
        requested_workspace_id,
        {
            _normalize_workspace_token(entry.get("workspace_id"), default=""): entry
            for entry in _workspace_access_entries(workspace_access)
        },
    )
    current_entry = _workspace_entry_for_id(workspace_access, current_workspace_id) or default_entry
    return {
        "default_workspace_id": default_workspace_id,
        "default_tenant_id": _normalize_tenant_token((default_entry or {}).get("tenant_id"), default="") or None,
        "current_workspace_id": _normalize_workspace_token(current_workspace_id, default="") or default_workspace_id,
        "current_tenant_id": _normalize_tenant_token((current_entry or {}).get("tenant_id"), default="") or None,
    }


def _resolve_workspace_token_for_current_user(
    current_user: Optional[Dict[str, Any]],
    workspace_id: Optional[str],
) -> str:
    token = _normalize_workspace_token(workspace_id, default="")
    if token and token != "default":
        return token
    workspace_access = workspace_access_map(current_user)
    if workspace_access:
        return _resolve_authenticated_workspace_id(token or "default", workspace_access)
    return token or "default"


def _workspace_entitlement_projection(
    workspace_access: Any,
    *,
    current_workspace_id: Optional[str] = None,
    default_workspace_id: Optional[str] = None,
) -> dict[str, Any]:
    entries = _workspace_access_entries(workspace_access)
    workspace_entitlements: dict[str, dict[str, Any]] = {}
    for entry in entries:
        workspace_id = _normalize_workspace_token(entry.get("workspace_id"), default="")
        if not workspace_id:
            continue
        try:
            workspace_entitlements[workspace_id] = entitlements_service.workspace_entitlement_payload_for_workspace_id(
                workspace_id=workspace_id,
            )
        except Exception:
            workspace_entitlements[workspace_id] = entitlements_service.workspace_entitlement_payload()
    resolved_current_workspace_id = _normalize_workspace_token(current_workspace_id, default="")
    resolved_default_workspace_id = _normalize_workspace_token(default_workspace_id, default="")
    current_workspace_entitlements = (
        workspace_entitlements.get(resolved_current_workspace_id)
        or workspace_entitlements.get(resolved_default_workspace_id)
        or (next(iter(workspace_entitlements.values())) if workspace_entitlements else None)
    )
    default_workspace_entitlements = (
        workspace_entitlements.get(resolved_default_workspace_id)
        or current_workspace_entitlements
    )
    return {
        "workspace_entitlements": workspace_entitlements,
        "current_workspace_entitlements": current_workspace_entitlements,
        "default_workspace_entitlements": default_workspace_entitlements,
    }


def _issue_authenticated_user_payload(
    user: dict[str, Any],
    *,
    role: str,
    workspace_access: dict[str, dict[str, Any]],
    channel: Any = "web",
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    device_platform: Optional[str] = None,
    workspace_id: Optional[str] = None,
    auth_flow: str,
    session_ttl_seconds: Optional[int] = None,
    enterprise_security: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user record.")

    normalized_channel = _normalize_auth_session_channel(channel, default="web")
    resolved_workspace_access = list(workspace_access.values())
    resolved_tenant_access = list(tenant_access_map({"workspace_access": workspace_access}).values())
    resolved_workspace_id = _resolve_authenticated_workspace_id(workspace_id, workspace_access)
    workspace_identity = _workspace_identity_projection(
        workspace_access,
        requested_workspace_id=resolved_workspace_id,
    )
    if normalized_channel == "mobile":
        workspace_record = _control_plane_call(control_plane_repository.get_workspace_by_id(resolved_workspace_id))
        try:
            entitlements_service.enforce_mobile_app_access(workspace=workspace_record if isinstance(workspace_record, dict) else None)
        except entitlements_service.EntitlementError as exc:
            raise HTTPException(status_code=403, detail=exc.message) from exc
    effective_device_id = str(device_id or "").strip() or None
    device_link = None
    if effective_device_id:
        device_link = upsert_user_device_link(
            user_id,
            device_id=effective_device_id,
            workspace_id=resolved_workspace_id,
            channel=normalized_channel,
            display_name=str(device_name or "").strip() or None,
            platform=str(device_platform or "").strip() or None,
            trust_state="verified",
            status="active",
            session_binding_required=True,
            metadata={
                "auth_flow": auth_flow,
                "workspace_id": resolved_workspace_id,
            },
        )

    resolved_ttl_seconds = _resolve_auth_session_ttl_seconds(normalized_channel, session_ttl_seconds)
    session_record_ttl_seconds = _resolve_auth_session_record_ttl_seconds(normalized_channel, resolved_ttl_seconds)
    token = issue_token(
        user_id,
        email=str(user.get("email") or ""),
        role=role,
        workspace_access=resolved_workspace_access,
        channel=normalized_channel,
        device_id=effective_device_id,
        trust_state=(device_link or {}).get("trust_state"),
        session_metadata={
            "auth_flow": auth_flow,
            "workspace_id": resolved_workspace_id,
            "device_platform": str(device_platform or "").strip() or None,
            "device_name": str(device_name or "").strip() or None,
        },
        ttl_seconds=resolved_ttl_seconds,
        session_record_ttl_seconds=session_record_ttl_seconds,
    )
    token_payload = _decode_token_payload(token)
    auth_session = get_auth_session(str(token_payload.get("sid") or "").strip())
    session_recovery = None
    if auth_session and (
        normalized_channel == "mobile" or browser_auth_session_channel(normalized_channel)
    ):
        session_recovery = issue_auth_session_refresh_token(
            str(auth_session.get("session_id") or "").strip(),
            user_id=user_id,
            ttl_seconds=_resolve_auth_refresh_ttl_seconds(normalized_channel),
        )
    payload = _auth_payload_for_user(
        user,
        role=role,
        token=token,
        workspace_access=resolved_workspace_access,
        current_workspace_id=workspace_identity.get("current_workspace_id"),
        default_workspace_id=workspace_identity.get("default_workspace_id"),
        current_tenant_id=workspace_identity.get("current_tenant_id"),
        default_tenant_id=workspace_identity.get("default_tenant_id"),
        tenant_access=resolved_tenant_access,
        enterprise_security=enterprise_security,
        auth_session=auth_session,
        device_link=device_link,
        identity_versions=load_user_identity_versions(user_id),
        session_recovery=session_recovery,
    )
    if auth_flow in {"login", "register", "refresh"}:
        session_id = str((auth_session or {}).get("session_id") or "").strip()
        security_audit_service.emit_security_audit_event(
            action=f"auth.{auth_flow}",
            tenant_id=payload.get("current_tenant_id"),
            workspace_id=payload.get("current_workspace_id"),
            actor_user_id=user_id,
            actor_email=str(user.get("email") or "").strip().lower() or None,
            actor_auth_type="bearer",
            channel=normalized_channel,
            trace_id=session_id,
            idempotency_key=f"auth.{auth_flow}:{session_id or user_id}",
            metadata={
                "device_id": effective_device_id,
                "device_name": str(device_name or "").strip() or None,
                "device_platform": str(device_platform or "").strip() or None,
                "session_id": session_id or None,
                "session_family_id": str((auth_session or {}).get("session_family_id") or "").strip() or None,
            },
        )
    return payload


def normalize_rbac_role(value: Any, *, default: str = "member") -> str:
    token = str(value or "").strip().lower()
    if token in RBAC_ROLE_ORDER:
        return token
    fallback = str(default or "member").strip().lower()
    return fallback if fallback in RBAC_ROLE_ORDER else "member"


def _normalize_workspace_token(value: Any, *, default: str = "") -> str:
    token = str(value or "").strip()
    return token or str(default or "").strip()


def _normalize_tenant_token(value: Any, *, default: str = "") -> str:
    token = str(value or "").strip()
    return token or str(default or "").strip()


def _require_workspace_token(
    value: Any,
    *,
    detail: str = "workspace_id is required.",
    status_code: int = 403,
) -> str:
    token = _normalize_workspace_token(value, default="")
    if not token:
        raise HTTPException(status_code=status_code, detail=detail)
    return token


def _require_tenant_token(
    value: Any,
    *,
    detail: str = "tenant_id is required.",
    status_code: int = 403,
) -> str:
    token = _normalize_tenant_token(value, default="")
    if not token:
        raise HTTPException(status_code=status_code, detail=detail)
    return token


def _normalize_distinct_tokens(value: Any) -> list[str]:
    items: list[str] = []
    if isinstance(value, str):
        raw = str(value).strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                value = parsed
        if isinstance(value, str):
            items = [part.strip() for part in str(value).split(",") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part or "").strip() for part in value if str(part or "").strip()]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_workspace_capability_policy(value: Any) -> dict[str, list[str]]:
    payload = value if isinstance(value, dict) else {}
    allow = _normalize_distinct_tokens(payload.get("allow"))
    deny = _normalize_distinct_tokens(payload.get("deny"))
    return {"allow": allow, "deny": deny}


def _normalize_workspace_dangerous_policy(value: Any) -> dict[str, list[str]]:
    payload = value if isinstance(value, dict) else {}
    allow = _normalize_distinct_tokens(payload.get("allow"))
    deny = _normalize_distinct_tokens(payload.get("deny"))
    return {"allow": allow, "deny": deny}


def _normalize_connector_permission_policy(value: Any) -> dict[str, list[str]]:
    payload = value if isinstance(value, dict) else {}
    allow = _normalize_distinct_tokens(payload.get("allow"))
    deny = _normalize_distinct_tokens(payload.get("deny"))
    return {"allow": allow, "deny": deny}


def _normalize_machine_enrollment_scope(value: Any, *, default: str = "workspace") -> str:
    token = str(value or "").strip().lower()
    if token in {"workspace", "tenant", "global"}:
        return token
    return str(default or "workspace").strip().lower() or "workspace"


def _normalize_sso_provider(value: Any, *, default: str = "oidc") -> str:
    token = str(value or "").strip().lower()
    if token in {"oidc", "saml", "google_workspace", "azure_ad", "okta", "generic"}:
        return token
    return str(default or "oidc").strip().lower() or "oidc"


def _normalize_mfa_methods(value: Any) -> list[str]:
    allowed = {"totp", "webauthn", "backup_codes", "sms"}
    methods = _normalize_distinct_tokens(value)
    return [method for method in methods if method in allowed]


def _normalize_scim_provisioning_mode(value: Any, *, default: str = "admin_api") -> str:
    token = str(value or "").strip().lower()
    if token in {"admin_api", "scim_bridge", "disabled"}:
        return token
    return str(default or "admin_api").strip().lower() or "admin_api"


def _resolve_inherited_allow_deny(
    *policies: Optional[dict[str, Any]],
) -> dict[str, list[str]]:
    allow: list[str] = []
    deny: list[str] = []
    for policy in policies:
        if not isinstance(policy, dict):
            continue
        next_allow = _normalize_distinct_tokens(policy.get("allow"))
        next_deny = _normalize_distinct_tokens(policy.get("deny"))
        if next_allow:
            allow = next_allow
        if next_deny:
            deny = next_deny
    return {"allow": allow, "deny": deny}


def _normalize_workspace_policy_claim_map(
    value: Any,
    *,
    normalizer,
) -> dict[str, dict[str, list[str]]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    for raw_workspace_id, raw_policy in value.items():
        workspace_id = _normalize_workspace_token(raw_workspace_id, default="")
        if not workspace_id:
            continue
        out[workspace_id] = normalizer(raw_policy)
    return out


def _normalize_workspace_role_claim_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for raw_workspace_id, raw_role in value.items():
        workspace_id = _normalize_workspace_token(raw_workspace_id, default="")
        if not workspace_id:
            continue
        out[workspace_id] = normalize_rbac_role(raw_role, default="viewer")
    return out


def _normalize_workspace_trusted_machine_claim_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for raw_workspace_id, raw_items in value.items():
        workspace_id = _normalize_workspace_token(raw_workspace_id, default="")
        if not workspace_id:
            continue
        out[workspace_id] = _normalize_distinct_tokens(raw_items)
    return out


def current_user_role(current_user: Optional[Dict[str, Any]], *, default: str = "member") -> str:
    if not isinstance(current_user, dict):
        return normalize_rbac_role(default)
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key":
        return normalize_rbac_role(current_user.get("role"), default=default)
    return normalize_rbac_role(current_user.get("role"), default=default)


def current_user_is_owner(current_user: Optional[Dict[str, Any]]) -> bool:
    return current_user_role(current_user, default="viewer") == "owner"


def _has_auth_admin_identity(user_id: Optional[str], email: Optional[str]) -> bool:
    clean_user_id = str(user_id or "").strip()
    clean_email = str(email or "").strip().lower()
    return (
        bool(clean_user_id and clean_user_id in ORION_ADMIN_USER_IDS)
        or bool(clean_email and clean_email in ORION_ADMIN_EMAILS)
    )


def current_user_has_auth_admin_access(current_user: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(current_user, dict):
        return False
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key":
        return bool(current_user.get("auth_admin")) or (
            bool(current_user.get("is_admin"))
            and normalize_rbac_role(current_user.get("role"), default="member") == "owner"
        )
    if auth_type == "local_dev":
        return current_user_role(current_user, default="viewer") == "owner"
    if auth_type == "bearer":
        if bool(current_user.get("auth_admin")):
            return True
        return _has_auth_admin_identity(
            str(current_user.get("user_id") or "").strip() or None,
            str(current_user.get("email") or "").strip().lower() or None,
        )
    return False


def enforce_minimum_role(current_user: Optional[Dict[str, Any]], minimum_role: str) -> Dict[str, Any]:
    if not isinstance(current_user, dict):
        raise HTTPException(status_code=401, detail="Authentication required.")
    actual_role = current_user_role(current_user)
    required_role = normalize_rbac_role(minimum_role)
    if RBAC_ROLE_ORDER[actual_role] < RBAC_ROLE_ORDER[required_role]:
        raise HTTPException(
            status_code=403,
            detail=f"{required_role.capitalize()} role required.",
        )
    enriched = dict(current_user)
    enriched["role"] = actual_role
    enriched["is_admin"] = bool(current_user.get("is_admin"))
    enriched["auth_admin"] = bool(current_user.get("auth_admin"))
    return enriched


def _resolved_bearer_role(user_id: str, email: Optional[str], claimed_role: Any) -> str:
    return normalize_rbac_role(claimed_role, default="member")


def _orion_api_key() -> str:
    return str(os.getenv("ORION_API_KEY") or "").strip()


def _orion_auth_required() -> bool:
    raw = os.getenv("ORION_AUTH_REQUIRED")
    return (str(raw).strip() != "0") if raw is not None else True


def _resolved_environment() -> str:
    return str(os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or os.getenv("ENV") or os.getenv("NODE_ENV") or "").strip().lower()


def _environment_is_production() -> bool:
    return _resolved_environment() in {"prod", "production"}


def _local_dev_auth_role() -> str:
    return normalize_rbac_role(os.getenv("ORION_LOCAL_DEV_AUTH_ROLE"), default="member")


def _local_dev_workspace_ids() -> list[str]:
    raw = os.getenv("ORION_LOCAL_DEV_WORKSPACE_IDS")
    if raw is None:
        return ["default"]
    return _normalize_workspace_ids_claim(raw)


def _local_dev_user_id() -> str:
    return str(os.getenv("ORION_LOCAL_DEV_USER_ID") or "local-dev").strip() or "local-dev"


def _local_dev_email() -> str:
    return str(os.getenv("ORION_LOCAL_DEV_EMAIL") or "local-dev@empyralis.local").strip().lower() or "local-dev@empyralis.local"


def _jwt_secret() -> str:
    secret = str(resolve_jwt_secret() or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="JWT secret is not configured.")
    return secret


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("utf-8")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("utf-8"))


def _connect_auth_db() -> sqlite3.Connection:
    connection = connect_sqlite_rw(AUTH_DB_FILE, logger=LOGGER, label="auth")
    schema_key = str(AUTH_DB_FILE)
    if schema_key in AUTH_DB_SCHEMA_READY_PATHS:
        return connection
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            avatar_url TEXT,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_memberships (
            user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, workspace_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_registry (
            workspace_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_policies (
            workspace_id TEXT PRIMARY KEY,
            capability_allow_json TEXT,
            capability_deny_json TEXT,
            dangerous_allow_json TEXT,
            dangerous_deny_json TEXT,
            connector_allow_json TEXT,
            connector_deny_json TEXT,
            machine_enrollment_scope TEXT,
            trusted_owner_machine_ids_json TEXT,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_policies (
            tenant_id TEXT PRIMARY KEY,
            capability_allow_json TEXT,
            capability_deny_json TEXT,
            dangerous_allow_json TEXT,
            dangerous_deny_json TEXT,
            connector_allow_json TEXT,
            connector_deny_json TEXT,
            machine_enrollment_scope TEXT,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_enterprise_settings (
            tenant_id TEXT PRIMARY KEY,
            sso_enabled INTEGER NOT NULL DEFAULT 0,
            sso_provider TEXT,
            sso_issuer_url TEXT,
            sso_metadata_url TEXT,
            sso_client_id TEXT,
            sso_audience TEXT,
            sso_domains_json TEXT,
            sso_scopes_json TEXT,
            mfa_required INTEGER NOT NULL DEFAULT 0,
            mfa_methods_json TEXT,
            mfa_grace_period_hours INTEGER,
            scim_enabled INTEGER NOT NULL DEFAULT 0,
            scim_base_url TEXT,
            scim_provisioning_mode TEXT,
            scim_last_token_rotation_at INTEGER,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_enterprise_security (
            user_id TEXT PRIMARY KEY,
            mfa_enrolled INTEGER NOT NULL DEFAULT 0,
            mfa_method TEXT,
            mfa_enrolled_at INTEGER,
            mfa_last_verified_at INTEGER,
            auth_provider TEXT,
            sso_subject TEXT,
            provisioning_source TEXT,
            external_id TEXT,
            last_provisioned_at INTEGER,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_auth_methods (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            method_type TEXT NOT NULL,
            provider TEXT NOT NULL,
            subject TEXT,
            label TEXT,
            status TEXT NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            can_recover INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_provider_connections (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            workspace_id TEXT,
            status TEXT NOT NULL,
            label TEXT,
            external_account_id TEXT,
            metadata_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_identity_versions (
            user_id TEXT PRIMARY KEY,
            membership_version INTEGER NOT NULL DEFAULT 1,
            auth_version INTEGER NOT NULL DEFAULT 1,
            provider_scope_version INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            device_id TEXT,
            runtime_id TEXT,
            trust_state TEXT NOT NULL,
            status TEXT NOT NULL,
            session_family_id TEXT,
            metadata_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            last_seen_at INTEGER,
            expires_at INTEGER NOT NULL,
            revoked_at INTEGER,
            revoked_reason TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_devices (
            device_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            workspace_id TEXT,
            channel TEXT NOT NULL,
            display_name TEXT,
            platform TEXT,
            trust_state TEXT NOT NULL,
            status TEXT NOT NULL,
            session_binding_required INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL,
            linked_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            last_seen_at INTEGER,
            revoked_at INTEGER,
            revoked_reason TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_session_refresh_tokens (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            rotated_at INTEGER,
            revoked_at INTEGER,
            revoked_reason TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_user_acquisition_touches (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            deployed_agent_id TEXT NOT NULL,
            channel_key TEXT NOT NULL,
            endpoint_key TEXT NOT NULL DEFAULT '',
            external_user_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            campaign_token TEXT NOT NULL DEFAULT '',
            start_count INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            converted_user_id TEXT,
            converted_email TEXT,
            converted_auth_flow TEXT,
            converted_at INTEGER,
            first_started_at INTEGER NOT NULL,
            last_started_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
        )
        """
    )
    existing_columns = {
        str(row[1]).strip().lower()
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    if "avatar_url" not in existing_columns:
        connection.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
    workspace_policy_columns = {
        str(row[1]).strip().lower()
        for row in connection.execute("PRAGMA table_info(workspace_policies)").fetchall()
    }
    if "connector_allow_json" not in workspace_policy_columns:
        connection.execute("ALTER TABLE workspace_policies ADD COLUMN connector_allow_json TEXT")
    if "connector_deny_json" not in workspace_policy_columns:
        connection.execute("ALTER TABLE workspace_policies ADD COLUMN connector_deny_json TEXT")
    if "machine_enrollment_scope" not in workspace_policy_columns:
        connection.execute("ALTER TABLE workspace_policies ADD COLUMN machine_enrollment_scope TEXT")
    tenant_policy_columns = {
        str(row[1]).strip().lower()
        for row in connection.execute("PRAGMA table_info(tenant_policies)").fetchall()
    }
    if "connector_allow_json" not in tenant_policy_columns:
        connection.execute("ALTER TABLE tenant_policies ADD COLUMN connector_allow_json TEXT")
    if "connector_deny_json" not in tenant_policy_columns:
        connection.execute("ALTER TABLE tenant_policies ADD COLUMN connector_deny_json TEXT")
    if "machine_enrollment_scope" not in tenant_policy_columns:
        connection.execute("ALTER TABLE tenant_policies ADD COLUMN machine_enrollment_scope TEXT")
    tenant_enterprise_columns = {
        str(row[1]).strip().lower()
        for row in connection.execute("PRAGMA table_info(tenant_enterprise_settings)").fetchall()
    }
    if "sso_provider" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN sso_provider TEXT")
    if "sso_issuer_url" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN sso_issuer_url TEXT")
    if "sso_metadata_url" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN sso_metadata_url TEXT")
    if "sso_client_id" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN sso_client_id TEXT")
    if "sso_audience" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN sso_audience TEXT")
    if "sso_domains_json" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN sso_domains_json TEXT")
    if "sso_scopes_json" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN sso_scopes_json TEXT")
    if "mfa_required" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN mfa_required INTEGER NOT NULL DEFAULT 0")
    if "mfa_methods_json" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN mfa_methods_json TEXT")
    if "mfa_grace_period_hours" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN mfa_grace_period_hours INTEGER")
    if "scim_enabled" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN scim_enabled INTEGER NOT NULL DEFAULT 0")
    if "scim_base_url" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN scim_base_url TEXT")
    if "scim_provisioning_mode" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN scim_provisioning_mode TEXT")
    if "scim_last_token_rotation_at" not in tenant_enterprise_columns:
        connection.execute("ALTER TABLE tenant_enterprise_settings ADD COLUMN scim_last_token_rotation_at INTEGER")
    user_enterprise_columns = {
        str(row[1]).strip().lower()
        for row in connection.execute("PRAGMA table_info(user_enterprise_security)").fetchall()
    }
    if "mfa_method" not in user_enterprise_columns:
        connection.execute("ALTER TABLE user_enterprise_security ADD COLUMN mfa_method TEXT")
    if "mfa_enrolled_at" not in user_enterprise_columns:
        connection.execute("ALTER TABLE user_enterprise_security ADD COLUMN mfa_enrolled_at INTEGER")
    if "mfa_last_verified_at" not in user_enterprise_columns:
        connection.execute("ALTER TABLE user_enterprise_security ADD COLUMN mfa_last_verified_at INTEGER")
    if "auth_provider" not in user_enterprise_columns:
        connection.execute("ALTER TABLE user_enterprise_security ADD COLUMN auth_provider TEXT")
    if "sso_subject" not in user_enterprise_columns:
        connection.execute("ALTER TABLE user_enterprise_security ADD COLUMN sso_subject TEXT")
    if "provisioning_source" not in user_enterprise_columns:
        connection.execute("ALTER TABLE user_enterprise_security ADD COLUMN provisioning_source TEXT")
    if "external_id" not in user_enterprise_columns:
        connection.execute("ALTER TABLE user_enterprise_security ADD COLUMN external_id TEXT")
    if "last_provisioned_at" not in user_enterprise_columns:
        connection.execute("ALTER TABLE user_enterprise_security ADD COLUMN last_provisioned_at INTEGER")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_auth_methods_user ON user_auth_methods(user_id, is_primary DESC, created_at ASC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_provider_connections_user ON user_provider_connections(user_id, provider, workspace_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, status, channel, expires_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_devices_user ON user_devices(user_id, status, channel, workspace_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_user ON auth_session_refresh_tokens(user_id, expires_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_user_acquisition_touches_deployment ON channel_user_acquisition_touches(tenant_id, workspace_id, deployed_agent_id, last_started_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_user_acquisition_touches_conversion ON channel_user_acquisition_touches(tenant_id, workspace_id, converted_user_id, converted_at DESC)"
    )
    connection.commit()
    AUTH_DB_SCHEMA_READY_PATHS.add(schema_key)
    return connection


def _normalize_auth_session_channel(value: Any, *, default: str = "web") -> str:
    token = str(value or "").strip().lower()
    if token in AUTH_SESSION_CHANNELS:
        return token
    fallback = str(default or "web").strip().lower()
    return fallback if fallback in AUTH_SESSION_CHANNELS else "web"


def _normalize_auth_session_status(value: Any, *, default: str = "active") -> str:
    token = str(value or "").strip().lower()
    if token in AUTH_SESSION_STATUSES:
        return token
    fallback = str(default or "active").strip().lower()
    return fallback if fallback in AUTH_SESSION_STATUSES else "active"


def _normalize_device_link_status(value: Any, *, default: str = "active") -> str:
    token = str(value or "").strip().lower()
    if token in DEVICE_LINK_STATUSES:
        return token
    fallback = str(default or "active").strip().lower()
    return fallback if fallback in DEVICE_LINK_STATUSES else "active"


def _normalize_device_trust_state(value: Any, *, default: str = "verified") -> str:
    token = str(value or "").strip().lower()
    if token in DEVICE_TRUST_STATES:
        return token
    fallback = str(default or "verified").strip().lower()
    return fallback if fallback in DEVICE_TRUST_STATES else "verified"


def _decode_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _identity_versions_from_row(row: Any, user_id: str) -> dict[str, Any]:
    if row is None:
        return {
            "user_id": user_id,
            "membership_version": 1,
            "auth_version": 1,
            "provider_scope_version": 1,
            "updated_at": None,
        }
    return {
        "user_id": user_id,
        "membership_version": max(int(row["membership_version"] or 1), 1),
        "auth_version": max(int(row["auth_version"] or 1), 1),
        "provider_scope_version": max(int(row["provider_scope_version"] or 1), 1),
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def _ensure_user_identity_versions_locked(
    connection: sqlite3.Connection,
    user_id: str,
    *,
    now_ts: Optional[int] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return _identity_versions_from_row(None, "")
    existing = connection.execute(
        """
        SELECT user_id, membership_version, auth_version, provider_scope_version, updated_at
        FROM user_identity_versions
        WHERE user_id = ?
        LIMIT 1
        """,
        (clean_user_id,),
    ).fetchone()
    if existing is not None:
        return _identity_versions_from_row(existing, clean_user_id)
    ts = int(now_ts or time.time())
    connection.execute(
        """
        INSERT OR REPLACE INTO user_identity_versions (
            user_id, membership_version, auth_version, provider_scope_version, updated_at
        ) VALUES (?, 1, 1, 1, ?)
        """,
        (clean_user_id, ts),
    )
    row = connection.execute(
        """
        SELECT user_id, membership_version, auth_version, provider_scope_version, updated_at
        FROM user_identity_versions
        WHERE user_id = ?
        LIMIT 1
        """,
        (clean_user_id,),
    ).fetchone()
    return _identity_versions_from_row(row, clean_user_id)


def load_user_identity_versions(user_id: str) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return _identity_versions_from_row(None, "")
    pg = _auth_store_pg(auth_store_repository.ensure_user_identity_versions(clean_user_id))
    if pg is not _PG_NA:
        return _identity_versions_from_row(pg, clean_user_id)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            record = _ensure_user_identity_versions_locked(connection, clean_user_id)
            connection.commit()
    return record


def _bump_user_identity_versions(
    user_id: str,
    *,
    membership: bool = False,
    auth: bool = False,
    provider_scope: bool = False,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return _identity_versions_from_row(None, "")
    pg = _auth_store_pg(
        auth_store_repository.bump_user_identity_versions(
            clean_user_id, membership=membership, auth=auth, provider_scope=provider_scope
        )
    )
    if pg is not _PG_NA:
        return _identity_versions_from_row(pg, clean_user_id)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            existing = _ensure_user_identity_versions_locked(connection, clean_user_id)
            ts = int(time.time())
            next_membership = int(existing.get("membership_version") or 1) + (1 if membership else 0)
            next_auth = int(existing.get("auth_version") or 1) + (1 if auth else 0)
            next_provider_scope = int(existing.get("provider_scope_version") or 1) + (1 if provider_scope else 0)
            connection.execute(
                """
                INSERT OR REPLACE INTO user_identity_versions (
                    user_id, membership_version, auth_version, provider_scope_version, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (clean_user_id, next_membership, next_auth, next_provider_scope, ts),
            )
            row = connection.execute(
                """
                SELECT user_id, membership_version, auth_version, provider_scope_version, updated_at
                FROM user_identity_versions
                WHERE user_id = ?
                LIMIT 1
                """,
                (clean_user_id,),
            ).fetchone()
            connection.commit()
    return _identity_versions_from_row(row, clean_user_id)


def _auth_session_from_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    expires_at = int(row["expires_at"]) if row["expires_at"] is not None else None
    revoked_at = int(row["revoked_at"]) if row["revoked_at"] is not None else None
    status = _normalize_auth_session_status(row["status"])
    now_ts = int(time.time())
    if status == "active" and expires_at is not None and expires_at <= now_ts:
        status = "expired"
    return {
        "session_id": str(row["session_id"] or "").strip(),
        "user_id": str(row["user_id"] or "").strip(),
        "channel": _normalize_auth_session_channel(row["channel"]),
        "device_id": str(row["device_id"] or "").strip() or None,
        "runtime_id": str(row["runtime_id"] or "").strip() or None,
        "trust_state": _normalize_device_trust_state(
            row["trust_state"],
            default="verified" if str(row["device_id"] or "").strip() else "unbound",
        ),
        "status": status,
        "session_family_id": str(row["session_family_id"] or "").strip() or None,
        "metadata": _decode_json_object(row["metadata_json"] if "metadata_json" in row.keys() else row.get("metadata_json")),
        "created_at": int(row["created_at"]) if row["created_at"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
        "last_seen_at": int(row["last_seen_at"]) if row["last_seen_at"] is not None else None,
        "expires_at": expires_at,
        "revoked_at": revoked_at,
        "revoked_reason": str(row["revoked_reason"] or "").strip() or None,
    }


def _device_link_from_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "device_id": str(row["device_id"] or "").strip(),
        "user_id": str(row["user_id"] or "").strip(),
        "workspace_id": _normalize_workspace_token(row["workspace_id"], default="") if str(row["workspace_id"] or "").strip() else None,
        "channel": _normalize_auth_session_channel(row["channel"], default="mobile"),
        "display_name": str(row["display_name"] or "").strip() or None,
        "platform": str(row["platform"] or "").strip().lower() or None,
        "trust_state": _normalize_device_trust_state(row["trust_state"]),
        "status": _normalize_device_link_status(row["status"]),
        "session_binding_required": bool(row["session_binding_required"]),
        "metadata": _decode_json_object(row["metadata_json"] if "metadata_json" in row.keys() else row.get("metadata_json")),
        "linked_at": int(row["linked_at"]) if row["linked_at"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
        "last_seen_at": int(row["last_seen_at"]) if row["last_seen_at"] is not None else None,
        "revoked_at": int(row["revoked_at"]) if row["revoked_at"] is not None else None,
        "revoked_reason": str(row["revoked_reason"] or "").strip() or None,
    }


def _auth_session_recovery_from_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    expires_at = int(row["expires_at"]) if row["expires_at"] is not None else None
    revoked_at = int(row["revoked_at"]) if row["revoked_at"] is not None else None
    return {
        "session_id": str(row["session_id"] or "").strip(),
        "user_id": str(row["user_id"] or "").strip(),
        "issued_at": int(row["created_at"]) if row["created_at"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
        "refresh_expires_at": expires_at,
        "rotated_at": int(row["rotated_at"]) if row["rotated_at"] is not None else None,
        "revoked_at": revoked_at,
        "revoked_reason": str(row["revoked_reason"] or "").strip() or None,
        "active": revoked_at is None and (expires_at is None or expires_at > int(time.time())),
        # Not part of the original public shape, but refresh_authenticated_session
        # needs the raw hash to verify a presented refresh token -- added here
        # rather than a second query, since get_auth_session_recovery() (the
        # Postgres-first wrapper around this row) is the one place both the
        # SQLite and Postgres paths already converge.
        "token_hash": str(row["token_hash"] or "").strip(),
    }


def _hash_auth_session_refresh_secret(session_id: str, secret: str) -> str:
    signing_input = f"{str(session_id or '').strip()}.{str(secret or '').strip()}".encode("utf-8")
    digest = hmac.new(_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return _b64url_encode(digest)


def _encode_auth_session_refresh_token(session_id: str, secret: str) -> str:
    return f"esr_{str(session_id or '').strip()}.{str(secret or '').strip()}"


def _decode_auth_session_refresh_token(refresh_token: str) -> tuple[str, str]:
    token = str(refresh_token or "").strip()
    if not token.startswith("esr_") or "." not in token:
        raise HTTPException(status_code=401, detail="Refresh token is invalid.")
    raw = token[4:]
    session_id, secret = raw.split(".", 1)
    clean_session_id = str(session_id or "").strip()
    clean_secret = str(secret or "").strip()
    if not clean_session_id or not clean_secret:
        raise HTTPException(status_code=401, detail="Refresh token is invalid.")
    return clean_session_id, clean_secret


def _upsert_auth_session_refresh_token_locked(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    user_id: str,
    token_hash: str,
    expires_at: int,
    now_ts: Optional[int] = None,
    rotated_at: Optional[int] = None,
    revoked_at: Optional[int] = None,
    revoked_reason: Optional[str] = None,
) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    clean_user_id = str(user_id or "").strip()
    if not clean_session_id or not clean_user_id:
        return {}
    ts = int(now_ts or time.time())
    existing = connection.execute(
        "SELECT created_at FROM auth_session_refresh_tokens WHERE session_id = ? LIMIT 1",
        (clean_session_id,),
    ).fetchone()
    created_at = int(existing["created_at"]) if existing is not None and existing["created_at"] is not None else ts
    connection.execute(
        """
        INSERT OR REPLACE INTO auth_session_refresh_tokens (
            session_id,
            user_id,
            token_hash,
            created_at,
            updated_at,
            expires_at,
            rotated_at,
            revoked_at,
            revoked_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_session_id,
            clean_user_id,
            str(token_hash or "").strip(),
            created_at,
            ts,
            int(expires_at),
            int(rotated_at) if rotated_at is not None else None,
            int(revoked_at) if revoked_at is not None else None,
            str(revoked_reason or "").strip() or None,
        ),
    )
    row = connection.execute(
        "SELECT * FROM auth_session_refresh_tokens WHERE session_id = ? LIMIT 1",
        (clean_session_id,),
    ).fetchone()
    return _auth_session_recovery_from_row(row)


def _get_auth_session_refresh_token_locked(
    connection: sqlite3.Connection,
    session_id: str,
) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return {}
    row = connection.execute(
        "SELECT * FROM auth_session_refresh_tokens WHERE session_id = ? LIMIT 1",
        (clean_session_id,),
    ).fetchone()
    return _auth_session_recovery_from_row(row)


def _revoke_auth_session_refresh_tokens_locked(
    connection: sqlite3.Connection,
    session_ids: list[str],
    *,
    reason: Optional[str] = None,
    now_ts: Optional[int] = None,
) -> None:
    clean_session_ids = [str(item or "").strip() for item in session_ids if str(item or "").strip()]
    if not clean_session_ids:
        return
    ts = int(now_ts or time.time())
    placeholders = ",".join("?" for _ in clean_session_ids)
    connection.execute(
        f"""
        UPDATE auth_session_refresh_tokens
        SET updated_at = ?,
            revoked_at = ?,
            revoked_reason = ?
        WHERE session_id IN ({placeholders}) AND revoked_at IS NULL
        """,
        (ts, ts, str(reason or "").strip() or "Session recovery revoked.", *clean_session_ids),
    )


def issue_auth_session_refresh_token(
    session_id: str,
    *,
    user_id: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        raise HTTPException(status_code=400, detail="session_id is required.")
    resolved_ttl = max(int(ttl_seconds or MOBILE_REFRESH_EXP_SECONDS), 60)
    secret = secrets.token_urlsafe(48)
    token_hash = _hash_auth_session_refresh_secret(clean_session_id, secret)
    refresh_token = _encode_auth_session_refresh_token(clean_session_id, secret)
    pg = _auth_store_pg(
        auth_store_repository.issue_refresh_token(
            session_id=clean_session_id, user_id=user_id, token_hash=token_hash, ttl_seconds=resolved_ttl
        )
    )
    if pg is not _PG_NA:
        if pg.get("session_missing"):
            raise HTTPException(status_code=404, detail="Auth session not found.")
        if pg.get("session_inactive"):
            raise HTTPException(status_code=401, detail="Auth session is no longer active.")
        return {**_auth_session_recovery_from_row(pg.get("row")), "refresh_token": refresh_token}
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            session_row = connection.execute(
                "SELECT session_id, user_id, status FROM auth_sessions WHERE session_id = ? LIMIT 1",
                (clean_session_id,),
            ).fetchone()
            if session_row is None:
                raise HTTPException(status_code=404, detail="Auth session not found.")
            if _normalize_auth_session_status(session_row["status"]) != "active":
                raise HTTPException(status_code=401, detail="Auth session is no longer active.")
            resolved_user_id = str(user_id or session_row["user_id"] or "").strip()
            ts = int(time.time())
            record = _upsert_auth_session_refresh_token_locked(
                connection,
                session_id=clean_session_id,
                user_id=resolved_user_id,
                token_hash=token_hash,
                expires_at=ts + resolved_ttl,
                now_ts=ts,
                rotated_at=ts,
            )
            connection.commit()
    return {
        **record,
        "refresh_token": refresh_token,
    }


def get_auth_session_recovery(session_id: str) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return {}
    pg = _auth_store_pg(auth_store_repository.fetch_refresh_token(clean_session_id))
    if pg is not _PG_NA:
        return _auth_session_recovery_from_row(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            return _get_auth_session_refresh_token_locked(connection, clean_session_id)


def _upsert_auth_session_locked(
    connection: sqlite3.Connection,
    *,
    session_id: Optional[str],
    user_id: str,
    channel: str,
    device_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    trust_state: Optional[str] = None,
    status: str = "active",
    session_family_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    ttl_seconds: Optional[int] = None,
    now_ts: Optional[int] = None,
    revoked_at: Optional[int] = None,
    revoked_reason: Optional[str] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    clean_session_id = str(session_id or "").strip() or f"as_{uuid.uuid4().hex}"
    clean_channel = _normalize_auth_session_channel(channel)
    clean_device_id = str(device_id or "").strip() or None
    clean_runtime_id = str(runtime_id or "").strip() or None
    clean_trust_state = _normalize_device_trust_state(
        trust_state,
        default="verified" if clean_device_id else "unbound",
    )
    clean_status = _normalize_auth_session_status(status)
    ts = int(now_ts or time.time())
    expires_at = ts + max(int(ttl_seconds or JWT_EXP_SECONDS), 60)
    existing = connection.execute(
        "SELECT created_at FROM auth_sessions WHERE session_id = ? LIMIT 1",
        (clean_session_id,),
    ).fetchone()
    created_at = int(existing["created_at"]) if existing is not None and existing["created_at"] is not None else ts
    connection.execute(
        """
        INSERT OR REPLACE INTO auth_sessions (
            session_id,
            user_id,
            channel,
            device_id,
            runtime_id,
            trust_state,
            status,
            session_family_id,
            metadata_json,
            created_at,
            updated_at,
            last_seen_at,
            expires_at,
            revoked_at,
            revoked_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_session_id,
            clean_user_id,
            clean_channel,
            clean_device_id,
            clean_runtime_id,
            clean_trust_state,
            clean_status,
            str(session_family_id or "").strip() or None,
            json.dumps(metadata or {}),
            created_at,
            ts,
            ts,
            expires_at,
            revoked_at,
            str(revoked_reason or "").strip() or None,
        ),
    )
    row = connection.execute(
        "SELECT * FROM auth_sessions WHERE session_id = ? LIMIT 1",
        (clean_session_id,),
    ).fetchone()
    return _auth_session_from_row(row)


_LAST_SESSION_GC_TS = 0.0
_SESSION_GC_INTERVAL_SECONDS = 600


def _maybe_gc_expired_sessions() -> None:
    """Throttled sweep that deletes sessions past expires_at (refresh tokens
    cascade). Runs at most once per _SESSION_GC_INTERVAL_SECONDS, triggered on
    session creation (login / register), so dead sessions never accumulate the
    way they did under the old SQLite-only store. Postgres-first; SQLite when
    the pool is absent. Best-effort: failures never block auth."""
    global _LAST_SESSION_GC_TS
    now = time.time()
    if now - _LAST_SESSION_GC_TS < _SESSION_GC_INTERVAL_SECONDS:
        return
    _LAST_SESSION_GC_TS = now
    try:
        pg = _auth_store_pg(auth_store_repository.gc_expired_sessions(now_ts=int(now)))
        if pg is not _PG_NA:
            if pg:
                LOGGER.info("Session GC (Postgres): deleted %d expired session(s).", int(pg))
            return
        cutoff = int(now)
        with AUTH_LOCK:
            with _connect_auth_db() as connection:
                cursor = connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (cutoff,))
                connection.execute("DELETE FROM auth_session_refresh_tokens WHERE expires_at <= ?", (cutoff,))
                connection.commit()
        if cursor.rowcount:
            LOGGER.info("Session GC (SQLite): deleted %d expired session(s).", int(cursor.rowcount or 0))
    except Exception:
        LOGGER.debug("Session GC sweep failed (non-fatal).", exc_info=True)


def create_auth_session(
    user_id: str,
    *,
    channel: str = "web",
    device_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    trust_state: Optional[str] = None,
    session_id: Optional[str] = None,
    session_family_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    ttl_seconds: Optional[int] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")
    _maybe_gc_expired_sessions()
    clean_session_id = str(session_id or "").strip() or f"as_{uuid.uuid4().hex}"
    clean_device_id = str(device_id or "").strip() or None
    ts = int(time.time())
    pg_row = {
        "session_id": clean_session_id,
        "user_id": clean_user_id,
        "channel": _normalize_auth_session_channel(channel),
        "device_id": clean_device_id,
        "runtime_id": str(runtime_id or "").strip() or None,
        "trust_state": _normalize_device_trust_state(trust_state, default="verified" if clean_device_id else "unbound"),
        "status": _normalize_auth_session_status("active"),
        "session_family_id": str(session_family_id or "").strip() or None,
        "metadata_json": json.dumps(metadata or {}),
        "created_at": ts,
        "updated_at": ts,
        "last_seen_at": ts,
        "expires_at": ts + max(int(ttl_seconds or JWT_EXP_SECONDS), 60),
        "revoked_at": None,
        "revoked_reason": None,
    }
    pg = _auth_store_pg(auth_store_repository.create_auth_session(row=pg_row))
    if pg is not _PG_NA:
        return _auth_session_from_row(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            _ensure_user_identity_versions_locked(connection, clean_user_id)
            record = _upsert_auth_session_locked(
                connection,
                session_id=session_id,
                user_id=clean_user_id,
                channel=channel,
                device_id=device_id,
                runtime_id=runtime_id,
                trust_state=trust_state,
                session_family_id=session_family_id,
                metadata=metadata,
                ttl_seconds=ttl_seconds,
            )
            connection.commit()
    return record


def get_auth_session(session_id: str) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return {}
    pg = _auth_store_pg(auth_store_repository.fetch_one("auth_sessions", session_id=clean_session_id))
    if pg is not _PG_NA:
        return _auth_session_from_row(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            row = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_id = ? LIMIT 1",
                (clean_session_id,),
            ).fetchone()
    return _auth_session_from_row(row)


def touch_auth_session(session_id: str, *, ttl_seconds: Optional[int] = None) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return {}
    pg = _auth_store_pg(auth_store_repository.touch_auth_session(clean_session_id, ttl_seconds=ttl_seconds))
    if pg is not _PG_NA:
        return _auth_session_from_row(pg) if pg else {}
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            existing = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_id = ? LIMIT 1",
                (clean_session_id,),
            ).fetchone()
            if existing is None:
                return {}
            ts = int(time.time())
            new_expires_at = ts + max(int(ttl_seconds), 60) if ttl_seconds is not None else None
            connection.execute(
                "UPDATE auth_sessions SET updated_at = ?, last_seen_at = ?, expires_at = COALESCE(?, expires_at) WHERE session_id = ?",
                (ts, ts, new_expires_at, clean_session_id),
            )
            row = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_id = ? LIMIT 1",
                (clean_session_id,),
            ).fetchone()
            connection.commit()
    return _auth_session_from_row(row)


def revoke_auth_session(session_id: str, *, reason: Optional[str] = None) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return {}
    pg = _auth_store_pg(
        auth_store_repository.revoke_auth_session(clean_session_id, reason=str(reason or "").strip() or "Session revoked.")
    )
    if pg is not _PG_NA:
        return _auth_session_from_row(pg) if pg else {}
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            existing = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_id = ? LIMIT 1",
                (clean_session_id,),
            ).fetchone()
            if existing is None:
                return {}
            ts = int(time.time())
            connection.execute(
                """
                UPDATE auth_sessions
                SET status = 'revoked',
                    trust_state = CASE WHEN device_id IS NOT NULL THEN 'revoked' ELSE trust_state END,
                    updated_at = ?,
                    revoked_at = ?,
                    revoked_reason = ?
                WHERE session_id = ?
                """,
                (ts, ts, str(reason or "").strip() or "Session revoked.", clean_session_id),
            )
            _revoke_auth_session_refresh_tokens_locked(
                connection,
                [clean_session_id],
                reason=reason or "Session revoked.",
                now_ts=ts,
            )
            row = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_id = ? LIMIT 1",
                (clean_session_id,),
            ).fetchone()
            connection.commit()
    return _auth_session_from_row(row)


def revoke_user_auth_sessions(
    user_id: str,
    *,
    device_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> int:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return 0
    clean_device_id = str(device_id or "").strip() or None
    pg = _auth_store_pg(
        auth_store_repository.revoke_user_auth_sessions(
            clean_user_id, device_id=clean_device_id, reason=str(reason or "").strip() or "User sessions revoked."
        )
    )
    if pg is not _PG_NA:
        return int(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            ts = int(time.time())
            params: list[Any] = [ts, ts, str(reason or "").strip() or "User sessions revoked.", clean_user_id]
            where = "user_id = ?"
            session_query = "SELECT session_id FROM auth_sessions WHERE user_id = ? AND status = 'active'"
            session_params: list[Any] = [clean_user_id]
            if clean_device_id:
                where += " AND device_id = ?"
                params.append(clean_device_id)
                session_query += " AND device_id = ?"
                session_params.append(clean_device_id)
            session_rows = connection.execute(session_query, tuple(session_params)).fetchall()
            session_ids = [str(row["session_id"] or "").strip() for row in session_rows if row is not None and str(row["session_id"] or "").strip()]
            cursor = connection.execute(
                f"""
                UPDATE auth_sessions
                SET status = 'revoked',
                    trust_state = CASE WHEN device_id IS NOT NULL THEN 'revoked' ELSE trust_state END,
                    updated_at = ?,
                    revoked_at = ?,
                    revoked_reason = ?
                WHERE {where} AND status = 'active'
                """,
                tuple(params),
            )
            _revoke_auth_session_refresh_tokens_locked(
                connection,
                session_ids,
                reason=reason or "User sessions revoked.",
                now_ts=ts,
            )
            connection.commit()
            return int(cursor.rowcount or 0)


def list_user_auth_sessions(user_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return []
    pg = _auth_store_pg(auth_store_repository.list_auth_sessions(clean_user_id, include_inactive=include_inactive))
    if pg is not _PG_NA:
        return [_auth_session_from_row(row) for row in pg]
    query = "SELECT * FROM auth_sessions WHERE user_id = ?"
    params: list[Any] = [clean_user_id]
    if not include_inactive:
        query += " AND status = 'active'"
    query += " ORDER BY updated_at DESC, created_at DESC"
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
    return [_auth_session_from_row(row) for row in rows]


def _upsert_user_device_link_locked(
    connection: sqlite3.Connection,
    *,
    device_id: str,
    user_id: str,
    workspace_id: Optional[str] = None,
    channel: str = "mobile",
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    trust_state: str = "verified",
    status: str = "active",
    session_binding_required: bool = True,
    metadata: Optional[dict[str, Any]] = None,
    now_ts: Optional[int] = None,
    revoked_at: Optional[int] = None,
    revoked_reason: Optional[str] = None,
) -> dict[str, Any]:
    clean_device_id = str(device_id or "").strip()
    clean_user_id = str(user_id or "").strip()
    ts = int(now_ts or time.time())
    existing = connection.execute(
        "SELECT linked_at FROM user_devices WHERE device_id = ? LIMIT 1",
        (clean_device_id,),
    ).fetchone()
    linked_at = int(existing["linked_at"]) if existing is not None and existing["linked_at"] is not None else ts
    connection.execute(
        """
        INSERT OR REPLACE INTO user_devices (
            device_id,
            user_id,
            workspace_id,
            channel,
            display_name,
            platform,
            trust_state,
            status,
            session_binding_required,
            metadata_json,
            linked_at,
            updated_at,
            last_seen_at,
            revoked_at,
            revoked_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_device_id,
            clean_user_id,
            _normalize_workspace_token(workspace_id, default="") if str(workspace_id or "").strip() else None,
            _normalize_auth_session_channel(channel, default="mobile"),
            str(display_name or "").strip() or None,
            str(platform or "").strip().lower() or None,
            _normalize_device_trust_state(trust_state),
            _normalize_device_link_status(status),
            1 if session_binding_required else 0,
            json.dumps(metadata or {}),
            linked_at,
            ts,
            ts,
            revoked_at,
            str(revoked_reason or "").strip() or None,
        ),
    )
    row = connection.execute(
        "SELECT * FROM user_devices WHERE device_id = ? LIMIT 1",
        (clean_device_id,),
    ).fetchone()
    return _device_link_from_row(row)


def upsert_user_device_link(
    user_id: str,
    *,
    device_id: str,
    workspace_id: Optional[str] = None,
    channel: str = "mobile",
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    trust_state: str = "verified",
    status: str = "active",
    session_binding_required: bool = True,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    clean_device_id = str(device_id or "").strip()
    if not clean_user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")
    if not clean_device_id:
        raise HTTPException(status_code=400, detail="device_id is required.")
    ts = int(time.time())
    pg_row = {
        "device_id": clean_device_id,
        "user_id": clean_user_id,
        "workspace_id": _normalize_workspace_token(workspace_id, default="") if str(workspace_id or "").strip() else None,
        "channel": _normalize_auth_session_channel(channel, default="mobile"),
        "display_name": str(display_name or "").strip() or None,
        "platform": str(platform or "").strip().lower() or None,
        "trust_state": _normalize_device_trust_state(trust_state),
        "status": _normalize_device_link_status(status),
        "session_binding_required": 1 if session_binding_required else 0,
        "metadata_json": json.dumps(metadata or {}),
        "linked_at": ts,
        "updated_at": ts,
        "last_seen_at": ts,
        "revoked_at": None,
        "revoked_reason": None,
    }
    pg = _auth_store_pg(auth_store_repository.upsert_device(row=pg_row))
    if pg is not _PG_NA:
        return _device_link_from_row(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            _ensure_user_identity_versions_locked(connection, clean_user_id)
            record = _upsert_user_device_link_locked(
                connection,
                device_id=clean_device_id,
                user_id=clean_user_id,
                workspace_id=workspace_id,
                channel=channel,
                display_name=display_name,
                platform=platform,
                trust_state=trust_state,
                status=status,
                session_binding_required=session_binding_required,
                metadata=metadata,
            )
            connection.commit()
    return record


def get_user_device_link(device_id: str) -> dict[str, Any]:
    clean_device_id = str(device_id or "").strip()
    if not clean_device_id:
        return {}
    pg = _auth_store_pg(auth_store_repository.fetch_one("user_devices", device_id=clean_device_id))
    if pg is not _PG_NA:
        return _device_link_from_row(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            row = connection.execute(
                "SELECT * FROM user_devices WHERE device_id = ? LIMIT 1",
                (clean_device_id,),
            ).fetchone()
    return _device_link_from_row(row)


def ensure_local_gateway_device_link(
    *,
    user_id: str,
    tenant_id: str,
    workspace_id: str,
    device_id: str,
    gateway_id: str,
    display_name: Optional[str] = None,
    platform: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    clean_gateway_id = str(gateway_id or "").strip()
    clean_workspace_id = _normalize_workspace_token(workspace_id, default="")
    clean_tenant_id = _normalize_tenant_token(tenant_id, default="")
    if not clean_gateway_id:
        raise HTTPException(status_code=400, detail="gateway_id is required.")
    if not clean_workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")
    if not clean_tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required.")
    payload = dict(metadata or {})
    payload.update(
        {
            "tenant_id": clean_tenant_id,
            "workspace_id": clean_workspace_id,
            "gateway_id": clean_gateway_id,
            "device_id": str(device_id or "").strip(),
        }
    )
    return upsert_user_device_link(
        user_id,
        device_id=device_id,
        workspace_id=clean_workspace_id,
        channel="local_runtime_companion",
        display_name=display_name,
        platform=platform,
        trust_state="verified",
        status="active",
        session_binding_required=True,
        metadata=payload,
    )


def validate_local_gateway_device_link(
    *,
    user_id: str,
    workspace_id: str,
    device_id: str,
    gateway_id: Optional[str] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    clean_workspace_id = _normalize_workspace_token(workspace_id, default="")
    clean_device_id = str(device_id or "").strip()
    clean_gateway_id = str(gateway_id or "").strip() or None
    if not clean_user_id or not clean_workspace_id or not clean_device_id:
        raise HTTPException(status_code=401, detail="Gateway device identity is incomplete.")
    device_record = get_user_device_link(clean_device_id)
    if not device_record:
        raise HTTPException(status_code=401, detail="Gateway device is not linked.")
    if str(device_record.get("user_id") or "").strip() != clean_user_id:
        raise HTTPException(status_code=401, detail="Gateway device owner mismatch.")
    if _normalize_workspace_token(device_record.get("workspace_id"), default="") != clean_workspace_id:
        raise HTTPException(status_code=401, detail="Gateway device workspace mismatch.")
    if _normalize_auth_session_channel(device_record.get("channel"), default="local_runtime_companion") != "local_runtime_companion":
        raise HTTPException(status_code=401, detail="Gateway device channel is invalid.")
    if _normalize_device_link_status(device_record.get("status"), default="active") != "active":
        raise HTTPException(status_code=401, detail="Gateway device is not active.")
    if _normalize_device_trust_state(device_record.get("trust_state"), default="verified") == "revoked":
        raise HTTPException(status_code=401, detail="Gateway device trust was revoked.")
    metadata = dict(device_record.get("metadata") or {})
    linked_gateway_id = str(metadata.get("gateway_id") or "").strip() or None
    if clean_gateway_id and linked_gateway_id and linked_gateway_id != clean_gateway_id:
        raise HTTPException(status_code=401, detail="Gateway device binding mismatch.")
    return device_record


def revoke_local_gateway_device_link(
    *,
    user_id: str,
    device_id: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    return revoke_user_device_link(
        user_id,
        device_id,
        reason=reason or "Gateway device trust revoked.",
    )


def list_user_device_links(user_id: str, *, include_inactive: bool = True) -> list[dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return []
    pg = _auth_store_pg(auth_store_repository.list_devices(clean_user_id, include_inactive=include_inactive))
    if pg is not _PG_NA:
        return [_device_link_from_row(row) for row in pg]
    query = "SELECT * FROM user_devices WHERE user_id = ?"
    params: list[Any] = [clean_user_id]
    if not include_inactive:
        query += " AND status = 'active'"
    query += " ORDER BY updated_at DESC, linked_at DESC"
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
    return [_device_link_from_row(row) for row in rows]


def revoke_user_device_link(user_id: str, device_id: str, *, reason: Optional[str] = None) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    clean_device_id = str(device_id or "").strip()
    if not clean_user_id or not clean_device_id:
        return {}
    pg = _auth_store_pg(
        auth_store_repository.revoke_device(
            clean_user_id, clean_device_id, reason=str(reason or "").strip() or "Device link revoked."
        )
    )
    if pg is not _PG_NA:
        if not pg:
            return {}
        revoke_user_auth_sessions(clean_user_id, device_id=clean_device_id, reason=reason or "Device link revoked.")
        return _device_link_from_row(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            existing = connection.execute(
                "SELECT * FROM user_devices WHERE device_id = ? AND user_id = ? LIMIT 1",
                (clean_device_id, clean_user_id),
            ).fetchone()
            if existing is None:
                return {}
            ts = int(time.time())
            connection.execute(
                """
                UPDATE user_devices
                SET status = 'revoked',
                    trust_state = 'revoked',
                    updated_at = ?,
                    last_seen_at = ?,
                    revoked_at = ?,
                    revoked_reason = ?
                WHERE device_id = ? AND user_id = ?
                """,
                (ts, ts, ts, str(reason or "").strip() or "Device link revoked.", clean_device_id, clean_user_id),
            )
            row = connection.execute(
                "SELECT * FROM user_devices WHERE device_id = ? AND user_id = ? LIMIT 1",
                (clean_device_id, clean_user_id),
            ).fetchone()
            connection.commit()
    revoke_user_auth_sessions(clean_user_id, device_id=clean_device_id, reason=reason or "Device link revoked.")
    return _device_link_from_row(row)


def _touch_user_device_link(device_id: str) -> dict[str, Any]:
    clean_device_id = str(device_id or "").strip()
    if not clean_device_id:
        return {}
    pg = _auth_store_pg(auth_store_repository.touch_device(clean_device_id))
    if pg is not _PG_NA:
        return _device_link_from_row(pg) if pg else {}
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            existing = connection.execute(
                "SELECT * FROM user_devices WHERE device_id = ? LIMIT 1",
                (clean_device_id,),
            ).fetchone()
            if existing is None:
                return {}
            ts = int(time.time())
            connection.execute(
                "UPDATE user_devices SET updated_at = ?, last_seen_at = ? WHERE device_id = ?",
                (ts, ts, clean_device_id),
            )
            row = connection.execute(
                "SELECT * FROM user_devices WHERE device_id = ? LIMIT 1",
                (clean_device_id,),
            ).fetchone()
            connection.commit()
    return _device_link_from_row(row)


def _write_workspace_membership(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    workspace_id: str,
    role: str,
    now_ts: Optional[int] = None,
) -> None:
    clean_user_id = str(user_id or "").strip()
    clean_workspace_id = _normalize_workspace_token(workspace_id)
    clean_role = normalize_rbac_role(role, default="member")
    if not clean_user_id or not clean_workspace_id:
        return
    ts = int(now_ts or time.time())
    existing = connection.execute(
        "SELECT created_at FROM workspace_memberships WHERE user_id = ? AND workspace_id = ?",
        (clean_user_id, clean_workspace_id),
    ).fetchone()
    created_at = int(existing["created_at"]) if existing is not None else ts
    connection.execute(
        """
        INSERT OR REPLACE INTO workspace_memberships (
            user_id, workspace_id, role, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (clean_user_id, clean_workspace_id, clean_role, created_at, ts),
    )


def _list_workspace_memberships(user_id: str) -> list[dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return []
    pg_rows = _control_plane_call(control_plane_repository.list_workspace_memberships_for_user(clean_user_id))
    if isinstance(pg_rows, list):
        return [dict(row) for row in pg_rows if isinstance(row, dict)]
    return []


def _write_workspace_registry(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    tenant_id: str,
    now_ts: Optional[int] = None,
) -> None:
    clean_workspace_id = _normalize_workspace_token(workspace_id)
    clean_tenant_id = _normalize_tenant_token(tenant_id)
    ts = int(now_ts or time.time())
    existing = connection.execute(
        "SELECT created_at FROM workspace_registry WHERE workspace_id = ?",
        (clean_workspace_id,),
    ).fetchone()
    created_at = int(existing["created_at"]) if existing is not None else ts
    connection.execute(
        """
        INSERT OR REPLACE INTO workspace_registry (
            workspace_id, tenant_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?)
        """,
        (clean_workspace_id, clean_tenant_id, created_at, ts),
    )


def ensure_workspace_tenant_binding(workspace_id: str, tenant_id: Optional[str] = None) -> dict[str, Any]:
    clean_workspace_id = _require_workspace_token(workspace_id, detail="workspace_id is required for tenant binding.", status_code=400)
    clean_tenant_id = _require_tenant_token(tenant_id, detail="tenant_id is required for tenant binding.", status_code=400)
    binding = _control_plane_call(
        control_plane_repository.ensure_workspace_tenant_binding(
            workspace_id=clean_workspace_id,
            tenant_id=clean_tenant_id,
        )
    )
    if not isinstance(binding, dict):
        raise HTTPException(status_code=500, detail="Workspace tenant binding could not be persisted.")
    resolved = {
        "workspace_id": _normalize_workspace_token(binding.get("workspace_id"), default=clean_workspace_id),
        "tenant_id": _normalize_tenant_token(binding.get("tenant_id"), default=clean_tenant_id),
    }
    _invalidate_workspace_auth_caches(workspace_id=resolved["workspace_id"])
    _auth_hot_cache_put(
        WORKSPACE_TENANT_CACHE,
        resolved["workspace_id"],
        resolved["tenant_id"],
    )
    return resolved


def tenant_id_for_workspace(workspace_id: str) -> str:
    clean_workspace_id = _require_workspace_token(
        workspace_id,
        detail="workspace_id is required to resolve tenant scope.",
    )
    cached_tenant_id = _auth_hot_cache_get(WORKSPACE_TENANT_CACHE, clean_workspace_id)
    if isinstance(cached_tenant_id, str) and cached_tenant_id.strip():
        return _require_tenant_token(
            cached_tenant_id,
            detail="Workspace is not bound to a valid tenant.",
        )
    pg_tenant_id = _control_plane_call(control_plane_repository.tenant_id_for_workspace(clean_workspace_id))
    if isinstance(pg_tenant_id, str) and pg_tenant_id.strip():
        resolved_tenant_id = _require_tenant_token(
            pg_tenant_id,
            detail="Workspace is not bound to a valid tenant.",
        )
        _auth_hot_cache_put(WORKSPACE_TENANT_CACHE, clean_workspace_id, resolved_tenant_id)
        return resolved_tenant_id
    raise HTTPException(status_code=403, detail=f"Workspace '{clean_workspace_id}' is not bound to a tenant.")


def upsert_workspace_membership(user_id: str, workspace_id: str, role: str) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")
    clean_workspace_id = _require_workspace_token(workspace_id, detail="workspace_id is required.", status_code=400)
    clean_role = normalize_rbac_role(role, default="member")
    user = _find_user_by_id(clean_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user_email = str(user.get("email") or "").strip().lower()
    user_name = str(user.get("name") or "").strip() or None
    if not user_email:
        raise HTTPException(status_code=400, detail="User is missing a valid email address.")
    resolved_tenant_id = tenant_id_for_workspace(clean_workspace_id)
    stored = _control_plane_call(
        control_plane_repository.ensure_workspace_membership(
            user_id=clean_user_id,
            email=user_email,
            display_name=user_name,
            tenant_id=resolved_tenant_id,
            workspace_id=clean_workspace_id,
            role=clean_role,
        )
    )
    if not isinstance(stored, dict):
        raise HTTPException(status_code=500, detail="Workspace membership could not be persisted.")
    _bump_user_identity_versions(clean_user_id, membership=True)
    return {"user_id": clean_user_id, "workspace_id": clean_workspace_id, "role": clean_role}


def accept_workspace_invites_for_user(user_id: str, email: str) -> list[dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    email_token = str(email or "").strip().lower()
    if not clean_user_id or not email_token:
        return []
    invites = _control_plane_call(
        control_plane_repository.list_pending_workspace_invites_for_email(email_token)
    )
    if not isinstance(invites, list):
        return []
    accepted: list[dict[str, Any]] = []
    for invite in invites:
        if not isinstance(invite, dict):
            continue
        workspace_id = _normalize_workspace_token(invite.get("workspace_id"), default="")
        if not workspace_id:
            continue
        role = normalize_rbac_role(invite.get("role"), default="member")
        upsert_workspace_membership(clean_user_id, workspace_id, role)
        invite_id = str(invite.get("id") or "").strip()
        if invite_id:
            # Stamp auto_accepted_at_login=True so accept_workspace_invite_route
            # (server_modules/routes_workspaces.py) can tell "this invite was
            # already fulfilled as a side effect of my own login, report
            # success" apart from "this invite is genuinely already used by
            # someone/something else, reject" -- see accept_workspace_invite's
            # docstring in control_plane_repository.py for the full picture.
            _control_plane_call(
                control_plane_repository.accept_workspace_invite(
                    invite_id=invite_id,
                    accepted_by_user_id=clean_user_id,
                    metadata_patch={"auto_accepted_at_login": True},
                )
            )
        accepted.append(
            {
                "invite_id": invite_id or None,
                "workspace_id": workspace_id,
                "role": role,
            }
        )
    return accepted


def _write_workspace_policy(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    capability_allow: Optional[list[str]] = None,
    capability_deny: Optional[list[str]] = None,
    dangerous_allow: Optional[list[str]] = None,
    dangerous_deny: Optional[list[str]] = None,
    connector_allow: Optional[list[str]] = None,
    connector_deny: Optional[list[str]] = None,
    machine_enrollment_scope: Optional[str] = None,
    trusted_owner_machine_ids: Optional[list[str]] = None,
    updated_at: Optional[int] = None,
) -> None:
    clean_workspace_id = _require_workspace_token(workspace_id, detail="workspace_id is required.", status_code=400)
    ts = int(updated_at or time.time())
    connection.execute(
        """
        INSERT OR REPLACE INTO workspace_policies (
            workspace_id,
            capability_allow_json,
            capability_deny_json,
            dangerous_allow_json,
            dangerous_deny_json,
            connector_allow_json,
            connector_deny_json,
            machine_enrollment_scope,
            trusted_owner_machine_ids_json,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_workspace_id,
            json.dumps(_normalize_distinct_tokens(capability_allow)),
            json.dumps(_normalize_distinct_tokens(capability_deny)),
            json.dumps(_normalize_distinct_tokens(dangerous_allow)),
            json.dumps(_normalize_distinct_tokens(dangerous_deny)),
            json.dumps(_normalize_distinct_tokens(connector_allow)),
            json.dumps(_normalize_distinct_tokens(connector_deny)),
            _normalize_machine_enrollment_scope(machine_enrollment_scope, default="workspace"),
            json.dumps(_normalize_distinct_tokens(trusted_owner_machine_ids)),
            ts,
        ),
    )


def _decode_json_token_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return _normalize_distinct_tokens(parsed)
    return _normalize_distinct_tokens(raw)


def _workspace_policy_from_row(row: Any, workspace_id: str, *, tenant_id: Optional[str] = None) -> dict[str, Any]:
    resolved_tenant_id = _require_tenant_token(
        tenant_id,
        detail=f"Workspace '{workspace_id}' is not bound to a valid tenant.",
    )
    if row is None:
        return {
            "workspace_id": workspace_id,
            "tenant_id": resolved_tenant_id,
            "capabilities": {"allow": [], "deny": []},
            "dangerous_action_classes": {"allow": [], "deny": []},
            "connectors": {"allow": [], "deny": []},
            "machine_enrollment_scope": "workspace",
            "trusted_owner_machine_ids": [],
            "updated_at": None,
        }
    return {
        "workspace_id": workspace_id,
        "tenant_id": resolved_tenant_id,
        "capabilities": {
            "allow": _decode_json_token_list(row["capability_allow_json"]),
            "deny": _decode_json_token_list(row["capability_deny_json"]),
        },
        "dangerous_action_classes": {
            "allow": _decode_json_token_list(row["dangerous_allow_json"]),
            "deny": _decode_json_token_list(row["dangerous_deny_json"]),
        },
        "connectors": {
            "allow": _decode_json_token_list(row["connector_allow_json"]),
            "deny": _decode_json_token_list(row["connector_deny_json"]),
        },
        "machine_enrollment_scope": _normalize_machine_enrollment_scope(row["machine_enrollment_scope"]),
        "trusted_owner_machine_ids": _decode_json_token_list(row["trusted_owner_machine_ids_json"]),
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def load_workspace_policy(workspace_id: str) -> dict[str, Any]:
    clean_workspace_id = _require_workspace_token(workspace_id)
    cached_policy = _auth_hot_cache_get(WORKSPACE_POLICY_CACHE, clean_workspace_id)
    if isinstance(cached_policy, dict):
        return cached_policy
    resolved_tenant_id = tenant_id_for_workspace(clean_workspace_id)
    pg = _auth_store_pg(auth_store_repository.fetch_one("workspace_policies", workspace_id=clean_workspace_id))
    if pg is not _PG_NA:
        policy = _workspace_policy_from_row(pg, clean_workspace_id, tenant_id=resolved_tenant_id)
        _auth_hot_cache_put(WORKSPACE_POLICY_CACHE, clean_workspace_id, policy)
        return policy
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            row = connection.execute(
                """
                SELECT
                    workspace_id,
                    capability_allow_json,
                    capability_deny_json,
                    dangerous_allow_json,
                    dangerous_deny_json,
                    connector_allow_json,
                    connector_deny_json,
                    machine_enrollment_scope,
                    trusted_owner_machine_ids_json,
                    updated_at
                FROM workspace_policies
                WHERE workspace_id = ?
                """,
                (clean_workspace_id,),
            ).fetchone()
            connection.commit()
    policy = _workspace_policy_from_row(row, clean_workspace_id, tenant_id=resolved_tenant_id)
    _auth_hot_cache_put(WORKSPACE_POLICY_CACHE, clean_workspace_id, policy)
    return policy


def _tenant_policy_from_row(row: Any, tenant_id: str) -> dict[str, Any]:
    if row is None:
        return {
            "tenant_id": tenant_id,
            "capabilities": {"allow": [], "deny": []},
            "dangerous_action_classes": {"allow": [], "deny": []},
            "connectors": {"allow": [], "deny": []},
            "machine_enrollment_scope": "workspace",
            "updated_at": None,
        }
    return {
        "tenant_id": tenant_id,
        "capabilities": {
            "allow": _decode_json_token_list(row["capability_allow_json"]),
            "deny": _decode_json_token_list(row["capability_deny_json"]),
        },
        "dangerous_action_classes": {
            "allow": _decode_json_token_list(row["dangerous_allow_json"]),
            "deny": _decode_json_token_list(row["dangerous_deny_json"]),
        },
        "connectors": {
            "allow": _decode_json_token_list(row["connector_allow_json"]),
            "deny": _decode_json_token_list(row["connector_deny_json"]),
        },
        "machine_enrollment_scope": _normalize_machine_enrollment_scope(row["machine_enrollment_scope"]),
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def load_tenant_policy(tenant_id: str) -> dict[str, Any]:
    clean_tenant_id = _normalize_tenant_token(tenant_id)
    cached_policy = _auth_hot_cache_get(TENANT_POLICY_CACHE, clean_tenant_id)
    if isinstance(cached_policy, dict):
        return cached_policy
    pg = _auth_store_pg(auth_store_repository.fetch_one("tenant_policies", tenant_id=clean_tenant_id))
    if pg is not _PG_NA:
        policy = _tenant_policy_from_row(pg, clean_tenant_id)
        _auth_hot_cache_put(TENANT_POLICY_CACHE, clean_tenant_id, policy)
        return policy
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            row = connection.execute(
                """
                SELECT
                    tenant_id,
                    capability_allow_json,
                    capability_deny_json,
                    dangerous_allow_json,
                    dangerous_deny_json,
                    connector_allow_json,
                    connector_deny_json,
                    machine_enrollment_scope,
                    updated_at
                FROM tenant_policies
                WHERE tenant_id = ?
                """,
                (clean_tenant_id,),
            ).fetchone()
    policy = _tenant_policy_from_row(row, clean_tenant_id)
    _auth_hot_cache_put(TENANT_POLICY_CACHE, clean_tenant_id, policy)
    return policy


def upsert_tenant_policy(
    tenant_id: str,
    *,
    capability_allow: Optional[list[str]] = None,
    capability_deny: Optional[list[str]] = None,
    dangerous_allow: Optional[list[str]] = None,
    dangerous_deny: Optional[list[str]] = None,
    connector_allow: Optional[list[str]] = None,
    connector_deny: Optional[list[str]] = None,
    machine_enrollment_scope: Optional[str] = None,
) -> dict[str, Any]:
    clean_tenant_id = _normalize_tenant_token(tenant_id)
    ts = int(time.time())
    _pg_existing = load_tenant_policy(clean_tenant_id)
    _pg_row = {
        "tenant_id": clean_tenant_id,
        "capability_allow_json": json.dumps(_normalize_distinct_tokens(capability_allow if capability_allow is not None else _pg_existing["capabilities"]["allow"])),
        "capability_deny_json": json.dumps(_normalize_distinct_tokens(capability_deny if capability_deny is not None else _pg_existing["capabilities"]["deny"])),
        "dangerous_allow_json": json.dumps(_normalize_distinct_tokens(dangerous_allow if dangerous_allow is not None else _pg_existing["dangerous_action_classes"]["allow"])),
        "dangerous_deny_json": json.dumps(_normalize_distinct_tokens(dangerous_deny if dangerous_deny is not None else _pg_existing["dangerous_action_classes"]["deny"])),
        "connector_allow_json": json.dumps(_normalize_distinct_tokens(connector_allow if connector_allow is not None else _pg_existing["connectors"]["allow"])),
        "connector_deny_json": json.dumps(_normalize_distinct_tokens(connector_deny if connector_deny is not None else _pg_existing["connectors"]["deny"])),
        "machine_enrollment_scope": _normalize_machine_enrollment_scope(
            machine_enrollment_scope if machine_enrollment_scope is not None else _pg_existing.get("machine_enrollment_scope"),
            default="workspace",
        ),
        "updated_at": ts,
    }
    pg = _auth_store_pg(auth_store_repository.upsert_row("tenant_policies", _pg_row))
    if pg is not _PG_NA:
        _invalidate_workspace_auth_caches(tenant_id=clean_tenant_id)
        return load_tenant_policy(clean_tenant_id)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            existing_row = connection.execute(
                """
                SELECT
                    tenant_id,
                    capability_allow_json,
                    capability_deny_json,
                    dangerous_allow_json,
                    dangerous_deny_json,
                    connector_allow_json,
                    connector_deny_json,
                    machine_enrollment_scope,
                    updated_at
                FROM tenant_policies
                WHERE tenant_id = ?
                """,
                (clean_tenant_id,),
            ).fetchone()
            existing = _tenant_policy_from_row(existing_row, clean_tenant_id)
            connection.execute(
                """
                INSERT OR REPLACE INTO tenant_policies (
                    tenant_id,
                    capability_allow_json,
                    capability_deny_json,
                    dangerous_allow_json,
                    dangerous_deny_json,
                    connector_allow_json,
                    connector_deny_json,
                    machine_enrollment_scope,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_tenant_id,
                    json.dumps(_normalize_distinct_tokens(capability_allow if capability_allow is not None else existing["capabilities"]["allow"])),
                    json.dumps(_normalize_distinct_tokens(capability_deny if capability_deny is not None else existing["capabilities"]["deny"])),
                    json.dumps(_normalize_distinct_tokens(dangerous_allow if dangerous_allow is not None else existing["dangerous_action_classes"]["allow"])),
                    json.dumps(_normalize_distinct_tokens(dangerous_deny if dangerous_deny is not None else existing["dangerous_action_classes"]["deny"])),
                    json.dumps(_normalize_distinct_tokens(connector_allow if connector_allow is not None else existing["connectors"]["allow"])),
                    json.dumps(_normalize_distinct_tokens(connector_deny if connector_deny is not None else existing["connectors"]["deny"])),
                    _normalize_machine_enrollment_scope(
                        machine_enrollment_scope if machine_enrollment_scope is not None else existing.get("machine_enrollment_scope"),
                        default="workspace",
                    ),
                    ts,
                ),
            )
            connection.commit()
    _invalidate_workspace_auth_caches(tenant_id=clean_tenant_id)
    return load_tenant_policy(clean_tenant_id)


def upsert_workspace_policy(
    workspace_id: str,
    *,
    capability_allow: Optional[list[str]] = None,
    capability_deny: Optional[list[str]] = None,
    dangerous_allow: Optional[list[str]] = None,
    dangerous_deny: Optional[list[str]] = None,
    connector_allow: Optional[list[str]] = None,
    connector_deny: Optional[list[str]] = None,
    machine_enrollment_scope: Optional[str] = None,
    trusted_owner_machine_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    clean_workspace_id = _normalize_workspace_token(workspace_id)
    resolved_tenant_id = tenant_id_for_workspace(clean_workspace_id)
    _pg_existing = load_workspace_policy(clean_workspace_id)
    _pg_row = {
        "workspace_id": clean_workspace_id,
        "capability_allow_json": json.dumps(_normalize_distinct_tokens(capability_allow if capability_allow is not None else list(_pg_existing["capabilities"]["allow"]))),
        "capability_deny_json": json.dumps(_normalize_distinct_tokens(capability_deny if capability_deny is not None else list(_pg_existing["capabilities"]["deny"]))),
        "dangerous_allow_json": json.dumps(_normalize_distinct_tokens(dangerous_allow if dangerous_allow is not None else list(_pg_existing["dangerous_action_classes"]["allow"]))),
        "dangerous_deny_json": json.dumps(_normalize_distinct_tokens(dangerous_deny if dangerous_deny is not None else list(_pg_existing["dangerous_action_classes"]["deny"]))),
        "connector_allow_json": json.dumps(_normalize_distinct_tokens(connector_allow if connector_allow is not None else list(_pg_existing["connectors"]["allow"]))),
        "connector_deny_json": json.dumps(_normalize_distinct_tokens(connector_deny if connector_deny is not None else list(_pg_existing["connectors"]["deny"]))),
        "machine_enrollment_scope": _normalize_machine_enrollment_scope(
            machine_enrollment_scope if machine_enrollment_scope is not None else str(_pg_existing.get("machine_enrollment_scope") or "workspace"),
            default="workspace",
        ),
        "trusted_owner_machine_ids_json": json.dumps(_normalize_distinct_tokens(trusted_owner_machine_ids if trusted_owner_machine_ids is not None else list(_pg_existing["trusted_owner_machine_ids"]))),
        "updated_at": int(time.time()),
    }
    pg = _auth_store_pg(auth_store_repository.upsert_row("workspace_policies", _pg_row))
    if pg is not _PG_NA:
        _invalidate_workspace_auth_caches(workspace_id=clean_workspace_id, tenant_id=resolved_tenant_id)
        return load_workspace_policy(clean_workspace_id)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            existing_row = connection.execute(
                """
                SELECT
                    workspace_id,
                    capability_allow_json,
                    capability_deny_json,
                    dangerous_allow_json,
                    dangerous_deny_json,
                    connector_allow_json,
                    connector_deny_json,
                    machine_enrollment_scope,
                    trusted_owner_machine_ids_json,
                    updated_at
                FROM workspace_policies
                WHERE workspace_id = ?
                """,
                (clean_workspace_id,),
            ).fetchone()
            existing = _workspace_policy_from_row(existing_row, clean_workspace_id, tenant_id=resolved_tenant_id)
            _write_workspace_policy(
                connection,
                workspace_id=clean_workspace_id,
                capability_allow=capability_allow if capability_allow is not None else list(existing["capabilities"]["allow"]),
                capability_deny=capability_deny if capability_deny is not None else list(existing["capabilities"]["deny"]),
                dangerous_allow=dangerous_allow if dangerous_allow is not None else list(existing["dangerous_action_classes"]["allow"]),
                dangerous_deny=dangerous_deny if dangerous_deny is not None else list(existing["dangerous_action_classes"]["deny"]),
                connector_allow=connector_allow if connector_allow is not None else list(existing["connectors"]["allow"]),
                connector_deny=connector_deny if connector_deny is not None else list(existing["connectors"]["deny"]),
                machine_enrollment_scope=(
                    machine_enrollment_scope
                    if machine_enrollment_scope is not None
                    else str(existing.get("machine_enrollment_scope") or "workspace")
                ),
                trusted_owner_machine_ids=trusted_owner_machine_ids if trusted_owner_machine_ids is not None else list(existing["trusted_owner_machine_ids"]),
            )
            connection.commit()
    _invalidate_workspace_auth_caches(workspace_id=clean_workspace_id, tenant_id=resolved_tenant_id)
    return load_workspace_policy(clean_workspace_id)


def grant_workspace_owner_machine_trust(workspace_id: str, machine_id: str) -> dict[str, Any]:
    clean_machine_id = str(machine_id or "").strip().lower()
    if not clean_machine_id:
        raise HTTPException(status_code=400, detail="machine_id is required.")
    existing = load_workspace_policy(workspace_id)
    trusted = list(existing.get("trusted_owner_machine_ids") or [])
    if clean_machine_id not in trusted:
        trusted.append(clean_machine_id)
    return upsert_workspace_policy(
        workspace_id,
        capability_allow=list(existing["capabilities"]["allow"]),
        capability_deny=list(existing["capabilities"]["deny"]),
        dangerous_allow=list(existing["dangerous_action_classes"]["allow"]),
        dangerous_deny=list(existing["dangerous_action_classes"]["deny"]),
        connector_allow=list(existing["connectors"]["allow"]),
        connector_deny=list(existing["connectors"]["deny"]),
        machine_enrollment_scope=str(existing.get("machine_enrollment_scope") or "workspace"),
        trusted_owner_machine_ids=trusted,
    )


def revoke_workspace_owner_machine_trust(workspace_id: str, machine_id: str) -> dict[str, Any]:
    clean_machine_id = str(machine_id or "").strip().lower()
    existing = load_workspace_policy(workspace_id)
    trusted = [item for item in list(existing.get("trusted_owner_machine_ids") or []) if item != clean_machine_id]
    return upsert_workspace_policy(
        workspace_id,
        capability_allow=list(existing["capabilities"]["allow"]),
        capability_deny=list(existing["capabilities"]["deny"]),
        dangerous_allow=list(existing["dangerous_action_classes"]["allow"]),
        dangerous_deny=list(existing["dangerous_action_classes"]["deny"]),
        connector_allow=list(existing["connectors"]["allow"]),
        connector_deny=list(existing["connectors"]["deny"]),
        machine_enrollment_scope=str(existing.get("machine_enrollment_scope") or "workspace"),
        trusted_owner_machine_ids=trusted,
    )


def _tenant_enterprise_settings_from_row(row: Any, tenant_id: str) -> dict[str, Any]:
    if row is None:
        return {
            "tenant_id": tenant_id,
            "sso": {
                "enabled": False,
                "provider": "oidc",
                "issuer_url": None,
                "metadata_url": None,
                "client_id": None,
                "audience": None,
                "domains": [],
                "scopes": [],
            },
            "mfa": {
                "required": False,
                "methods": [],
                "grace_period_hours": None,
            },
            "scim": {
                "enabled": False,
                "base_url": None,
                "provisioning_mode": "admin_api",
                "last_token_rotation_at": None,
            },
            "updated_at": None,
        }
    return {
        "tenant_id": tenant_id,
        "sso": {
            "enabled": bool(row["sso_enabled"]),
            "provider": _normalize_sso_provider(row["sso_provider"]),
            "issuer_url": str(row["sso_issuer_url"] or "").strip() or None,
            "metadata_url": str(row["sso_metadata_url"] or "").strip() or None,
            "client_id": str(row["sso_client_id"] or "").strip() or None,
            "audience": str(row["sso_audience"] or "").strip() or None,
            "domains": _decode_json_token_list(row["sso_domains_json"]),
            "scopes": _decode_json_token_list(row["sso_scopes_json"]),
        },
        "mfa": {
            "required": bool(row["mfa_required"]),
            "methods": _normalize_mfa_methods(row["mfa_methods_json"]),
            "grace_period_hours": int(row["mfa_grace_period_hours"]) if row["mfa_grace_period_hours"] is not None else None,
        },
        "scim": {
            "enabled": bool(row["scim_enabled"]),
            "base_url": str(row["scim_base_url"] or "").strip() or None,
            "provisioning_mode": _normalize_scim_provisioning_mode(row["scim_provisioning_mode"]),
            "last_token_rotation_at": int(row["scim_last_token_rotation_at"]) if row["scim_last_token_rotation_at"] is not None else None,
        },
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def load_tenant_enterprise_settings(tenant_id: str) -> dict[str, Any]:
    clean_tenant_id = _normalize_tenant_token(tenant_id)
    pg = _auth_store_pg(auth_store_repository.fetch_one("tenant_enterprise_settings", tenant_id=clean_tenant_id))
    if pg is not _PG_NA:
        return _tenant_enterprise_settings_from_row(pg, clean_tenant_id)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            row = connection.execute(
                """
                SELECT
                    tenant_id,
                    sso_enabled,
                    sso_provider,
                    sso_issuer_url,
                    sso_metadata_url,
                    sso_client_id,
                    sso_audience,
                    sso_domains_json,
                    sso_scopes_json,
                    mfa_required,
                    mfa_methods_json,
                    mfa_grace_period_hours,
                    scim_enabled,
                    scim_base_url,
                    scim_provisioning_mode,
                    scim_last_token_rotation_at,
                    updated_at
                FROM tenant_enterprise_settings
                WHERE tenant_id = ?
                """,
                (clean_tenant_id,),
            ).fetchone()
    return _tenant_enterprise_settings_from_row(row, clean_tenant_id)


def upsert_tenant_enterprise_settings(
    tenant_id: str,
    *,
    sso: Optional[dict[str, Any]] = None,
    mfa: Optional[dict[str, Any]] = None,
    scim: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    clean_tenant_id = _normalize_tenant_token(tenant_id)
    existing = load_tenant_enterprise_settings(clean_tenant_id)
    next_sso = dict(existing.get("sso") or {})
    next_sso.update(dict(sso or {}))
    next_mfa = dict(existing.get("mfa") or {})
    next_mfa.update(dict(mfa or {}))
    next_scim = dict(existing.get("scim") or {})
    next_scim.update(dict(scim or {}))
    ts = int(time.time())
    _pg_row = {
        "tenant_id": clean_tenant_id,
        "sso_enabled": 1 if bool(next_sso.get("enabled")) else 0,
        "sso_provider": _normalize_sso_provider(next_sso.get("provider"), default=str(existing.get("sso", {}).get("provider") or "oidc")),
        "sso_issuer_url": str(next_sso.get("issuer_url") or "").strip() or None,
        "sso_metadata_url": str(next_sso.get("metadata_url") or "").strip() or None,
        "sso_client_id": str(next_sso.get("client_id") or "").strip() or None,
        "sso_audience": str(next_sso.get("audience") or "").strip() or None,
        "sso_domains_json": json.dumps(_normalize_distinct_tokens(next_sso.get("domains"))),
        "sso_scopes_json": json.dumps(_normalize_distinct_tokens(next_sso.get("scopes"))),
        "mfa_required": 1 if bool(next_mfa.get("required")) else 0,
        "mfa_methods_json": json.dumps(_normalize_mfa_methods(next_mfa.get("methods"))),
        "mfa_grace_period_hours": int(next_mfa.get("grace_period_hours")) if next_mfa.get("grace_period_hours") not in {None, ""} else None,
        "scim_enabled": 1 if bool(next_scim.get("enabled")) else 0,
        "scim_base_url": str(next_scim.get("base_url") or "").strip() or None,
        "scim_provisioning_mode": _normalize_scim_provisioning_mode(next_scim.get("provisioning_mode"), default=str(existing.get("scim", {}).get("provisioning_mode") or "admin_api")),
        "scim_last_token_rotation_at": int(next_scim.get("last_token_rotation_at")) if next_scim.get("last_token_rotation_at") not in {None, ""} else None,
        "updated_at": ts,
    }
    pg = _auth_store_pg(auth_store_repository.upsert_row("tenant_enterprise_settings", _pg_row))
    if pg is not _PG_NA:
        return load_tenant_enterprise_settings(clean_tenant_id)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO tenant_enterprise_settings (
                    tenant_id,
                    sso_enabled,
                    sso_provider,
                    sso_issuer_url,
                    sso_metadata_url,
                    sso_client_id,
                    sso_audience,
                    sso_domains_json,
                    sso_scopes_json,
                    mfa_required,
                    mfa_methods_json,
                    mfa_grace_period_hours,
                    scim_enabled,
                    scim_base_url,
                    scim_provisioning_mode,
                    scim_last_token_rotation_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_tenant_id,
                    1 if bool(next_sso.get("enabled")) else 0,
                    _normalize_sso_provider(next_sso.get("provider"), default=str(existing.get("sso", {}).get("provider") or "oidc")),
                    str(next_sso.get("issuer_url") or "").strip() or None,
                    str(next_sso.get("metadata_url") or "").strip() or None,
                    str(next_sso.get("client_id") or "").strip() or None,
                    str(next_sso.get("audience") or "").strip() or None,
                    json.dumps(_normalize_distinct_tokens(next_sso.get("domains"))),
                    json.dumps(_normalize_distinct_tokens(next_sso.get("scopes"))),
                    1 if bool(next_mfa.get("required")) else 0,
                    json.dumps(_normalize_mfa_methods(next_mfa.get("methods"))),
                    int(next_mfa.get("grace_period_hours")) if next_mfa.get("grace_period_hours") not in {None, ""} else None,
                    1 if bool(next_scim.get("enabled")) else 0,
                    str(next_scim.get("base_url") or "").strip() or None,
                    _normalize_scim_provisioning_mode(next_scim.get("provisioning_mode"), default=str(existing.get("scim", {}).get("provisioning_mode") or "admin_api")),
                    int(next_scim.get("last_token_rotation_at")) if next_scim.get("last_token_rotation_at") not in {None, ""} else None,
                    ts,
                ),
            )
            connection.commit()
    return load_tenant_enterprise_settings(clean_tenant_id)


def _user_enterprise_security_from_row(row: Any, user_id: str) -> dict[str, Any]:
    if row is None:
        return {
            "user_id": user_id,
            "mfa_enrolled": False,
            "mfa_method": None,
            "mfa_enrolled_at": None,
            "mfa_last_verified_at": None,
            "auth_provider": None,
            "sso_subject": None,
            "provisioning_source": None,
            "external_id": None,
            "last_provisioned_at": None,
            "updated_at": None,
        }
    return {
        "user_id": user_id,
        "mfa_enrolled": bool(row["mfa_enrolled"]),
        "mfa_method": str(row["mfa_method"] or "").strip() or None,
        "mfa_enrolled_at": int(row["mfa_enrolled_at"]) if row["mfa_enrolled_at"] is not None else None,
        "mfa_last_verified_at": int(row["mfa_last_verified_at"]) if row["mfa_last_verified_at"] is not None else None,
        "auth_provider": str(row["auth_provider"] or "").strip() or None,
        "sso_subject": str(row["sso_subject"] or "").strip() or None,
        "provisioning_source": str(row["provisioning_source"] or "").strip() or None,
        "external_id": str(row["external_id"] or "").strip() or None,
        "last_provisioned_at": int(row["last_provisioned_at"]) if row["last_provisioned_at"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def load_user_enterprise_security(user_id: str) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return _user_enterprise_security_from_row(None, "")
    pg = _auth_store_pg(auth_store_repository.fetch_one("user_enterprise_security", user_id=clean_user_id))
    if pg is not _PG_NA:
        return _user_enterprise_security_from_row(pg, clean_user_id)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            row = connection.execute(
                """
                SELECT
                    user_id,
                    mfa_enrolled,
                    mfa_method,
                    mfa_enrolled_at,
                    mfa_last_verified_at,
                    auth_provider,
                    sso_subject,
                    provisioning_source,
                    external_id,
                    last_provisioned_at,
                    updated_at
                FROM user_enterprise_security
                WHERE user_id = ?
                """,
                (clean_user_id,),
            ).fetchone()
    return _user_enterprise_security_from_row(row, clean_user_id)


def upsert_user_enterprise_security(
    user_id: str,
    *,
    mfa_enrolled: Optional[bool] = None,
    mfa_method: Optional[str] = None,
    mfa_enrolled_at: Optional[int] = None,
    mfa_last_verified_at: Optional[int] = None,
    auth_provider: Optional[str] = None,
    sso_subject: Optional[str] = None,
    provisioning_source: Optional[str] = None,
    external_id: Optional[str] = None,
    last_provisioned_at: Optional[int] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")
    existing = load_user_enterprise_security(clean_user_id)
    resolved_mfa_enrolled = existing["mfa_enrolled"] if mfa_enrolled is None else bool(mfa_enrolled)
    resolved_mfa_method = existing["mfa_method"] if mfa_method is None else (str(mfa_method or "").strip() or None)
    resolved_enrolled_at = existing["mfa_enrolled_at"] if mfa_enrolled_at is None else int(mfa_enrolled_at)
    resolved_verified_at = existing["mfa_last_verified_at"] if mfa_last_verified_at is None else int(mfa_last_verified_at)
    ts = int(time.time())
    pg_row = {
        "user_id": clean_user_id,
        "mfa_enrolled": 1 if resolved_mfa_enrolled else 0,
        "mfa_method": resolved_mfa_method,
        "mfa_enrolled_at": resolved_enrolled_at,
        "mfa_last_verified_at": resolved_verified_at,
        "auth_provider": existing["auth_provider"] if auth_provider is None else (str(auth_provider or "").strip() or None),
        "sso_subject": existing["sso_subject"] if sso_subject is None else (str(sso_subject or "").strip() or None),
        "provisioning_source": existing["provisioning_source"] if provisioning_source is None else (str(provisioning_source or "").strip() or None),
        "external_id": existing["external_id"] if external_id is None else (str(external_id or "").strip() or None),
        "last_provisioned_at": existing["last_provisioned_at"] if last_provisioned_at is None else int(last_provisioned_at),
        "updated_at": ts,
    }
    pg = _auth_store_pg(auth_store_repository.upsert_row("user_enterprise_security", pg_row))
    if pg is not _PG_NA:
        _bump_user_identity_versions(clean_user_id, auth=True)
        return load_user_enterprise_security(clean_user_id)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            _ensure_user_identity_versions_locked(connection, clean_user_id)
            connection.execute(
                """
                INSERT OR REPLACE INTO user_enterprise_security (
                    user_id,
                    mfa_enrolled,
                    mfa_method,
                    mfa_enrolled_at,
                    mfa_last_verified_at,
                    auth_provider,
                    sso_subject,
                    provisioning_source,
                    external_id,
                    last_provisioned_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_user_id,
                    1 if resolved_mfa_enrolled else 0,
                    resolved_mfa_method,
                    resolved_enrolled_at,
                    resolved_verified_at,
                    existing["auth_provider"] if auth_provider is None else (str(auth_provider or "").strip() or None),
                    existing["sso_subject"] if sso_subject is None else (str(sso_subject or "").strip() or None),
                    existing["provisioning_source"] if provisioning_source is None else (str(provisioning_source or "").strip() or None),
                    existing["external_id"] if external_id is None else (str(external_id or "").strip() or None),
                    existing["last_provisioned_at"] if last_provisioned_at is None else int(last_provisioned_at),
                    ts,
                ),
            )
            connection.commit()
    _bump_user_identity_versions(clean_user_id, auth=True)
    return load_user_enterprise_security(clean_user_id)


def _normalize_auth_method_type(value: Any, *, default: str = "password") -> str:
    token = str(value or "").strip().lower()
    if token in {"password", "oauth", "sso", "magic_link", "api_key"}:
        return token
    fallback = str(default or "password").strip().lower()
    return fallback if fallback in {"password", "oauth", "sso", "magic_link", "api_key"} else "password"


def _normalize_auth_method_status(value: Any, *, default: str = "active") -> str:
    token = str(value or "").strip().lower()
    if token in {"active", "disabled", "pending", "revoked"}:
        return token
    fallback = str(default or "active").strip().lower()
    return fallback if fallback in {"active", "disabled", "pending", "revoked"} else "active"


def _normalize_provider_connection_status(value: Any, *, default: str = "active") -> str:
    token = str(value or "").strip().lower()
    if token in {"active", "pending", "disconnected", "revoked", "error"}:
        return token
    fallback = str(default or "active").strip().lower()
    return fallback if fallback in {"active", "pending", "disconnected", "revoked", "error"} else "active"


def _stable_identity_row_id(*parts: Any) -> str:
    normalized = "::".join(str(part or "").strip().lower() for part in parts if str(part or "").strip())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest


def _auth_method_from_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    metadata: dict[str, Any] = {}
    raw_metadata = row["metadata_json"] if "metadata_json" in row.keys() else "{}"
    if isinstance(raw_metadata, str) and raw_metadata.strip():
        try:
            parsed = json.loads(raw_metadata)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            metadata = parsed
    return {
        "id": str(row["id"] or "").strip(),
        "user_id": str(row["user_id"] or "").strip(),
        "method_type": _normalize_auth_method_type(row["method_type"]),
        "provider": str(row["provider"] or "").strip().lower(),
        "subject": str(row["subject"] or "").strip() or None,
        "label": str(row["label"] or "").strip() or None,
        "status": _normalize_auth_method_status(row["status"]),
        "is_primary": bool(row["is_primary"]),
        "can_recover": bool(row["can_recover"]),
        "metadata": metadata,
        "created_at": int(row["created_at"]) if row["created_at"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def _provider_connection_from_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    metadata: dict[str, Any] = {}
    raw_metadata = row["metadata_json"] if "metadata_json" in row.keys() else "{}"
    if isinstance(raw_metadata, str) and raw_metadata.strip():
        try:
            parsed = json.loads(raw_metadata)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            metadata = parsed
    return {
        "id": str(row["id"] or "").strip(),
        "user_id": str(row["user_id"] or "").strip(),
        "provider": str(row["provider"] or "").strip().lower(),
        "workspace_id": _normalize_workspace_token(row["workspace_id"], default="") if str(row["workspace_id"] or "").strip() else None,
        "status": _normalize_provider_connection_status(row["status"]),
        "label": str(row["label"] or "").strip() or None,
        "external_account_id": str(row["external_account_id"] or "").strip() or None,
        "metadata": metadata,
        "created_at": int(row["created_at"]) if row["created_at"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def _upsert_user_auth_method_locked(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    method_type: str,
    provider: str,
    subject: Optional[str] = None,
    label: Optional[str] = None,
    status: str = "active",
    is_primary: bool = False,
    can_recover: bool = True,
    metadata: Optional[dict[str, Any]] = None,
    now_ts: Optional[int] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    clean_provider = str(provider or "").strip().lower() or "unknown"
    clean_subject = str(subject or "").strip() or None
    clean_label = str(label or "").strip() or None
    method_id = _stable_identity_row_id("auth_method", clean_user_id, method_type, clean_provider, clean_subject or clean_label or "")
    ts = int(now_ts or time.time())
    existing = connection.execute(
        """
        SELECT created_at
        FROM user_auth_methods
        WHERE id = ?
        LIMIT 1
        """,
        (method_id,),
    ).fetchone()
    created_at = int(existing["created_at"]) if existing is not None and existing["created_at"] is not None else ts
    if is_primary:
        connection.execute(
            "UPDATE user_auth_methods SET is_primary = 0, updated_at = ? WHERE user_id = ?",
            (ts, clean_user_id),
        )
    connection.execute(
        """
        INSERT OR REPLACE INTO user_auth_methods (
            id,
            user_id,
            method_type,
            provider,
            subject,
            label,
            status,
            is_primary,
            can_recover,
            metadata_json,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            method_id,
            clean_user_id,
            _normalize_auth_method_type(method_type),
            clean_provider,
            clean_subject,
            clean_label,
            _normalize_auth_method_status(status),
            1 if is_primary else 0,
            1 if can_recover else 0,
            json.dumps(metadata or {}),
            created_at,
            ts,
        ),
    )
    row = connection.execute(
        "SELECT * FROM user_auth_methods WHERE id = ? LIMIT 1",
        (method_id,),
    ).fetchone()
    return _auth_method_from_row(row)


def _upsert_user_provider_connection_locked(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    provider: str,
    workspace_id: Optional[str] = None,
    status: str = "active",
    label: Optional[str] = None,
    external_account_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    now_ts: Optional[int] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    clean_provider = str(provider or "").strip().lower() or "unknown"
    clean_workspace_id = _normalize_workspace_token(workspace_id, default="") if str(workspace_id or "").strip() else None
    clean_external_account_id = str(external_account_id or "").strip() or None
    connection_id = _stable_identity_row_id(
        "provider_connection",
        clean_user_id,
        clean_provider,
        clean_workspace_id or "global",
        clean_external_account_id or "",
    )
    ts = int(now_ts or time.time())
    existing = connection.execute(
        """
        SELECT created_at
        FROM user_provider_connections
        WHERE id = ?
        LIMIT 1
        """,
        (connection_id,),
    ).fetchone()
    created_at = int(existing["created_at"]) if existing is not None and existing["created_at"] is not None else ts
    connection.execute(
        """
        INSERT OR REPLACE INTO user_provider_connections (
            id,
            user_id,
            provider,
            workspace_id,
            status,
            label,
            external_account_id,
            metadata_json,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            connection_id,
            clean_user_id,
            clean_provider,
            clean_workspace_id,
            _normalize_provider_connection_status(status),
            str(label or "").strip() or None,
            clean_external_account_id,
            json.dumps(metadata or {}),
            created_at,
            ts,
        ),
    )
    row = connection.execute(
        "SELECT * FROM user_provider_connections WHERE id = ? LIMIT 1",
        (connection_id,),
    ).fetchone()
    return _provider_connection_from_row(row)


def _ensure_identity_boundary_records_pg(
    user_id: str, email: Optional[str], password_hash: str, ts: int
) -> None:
    """Postgres-first re-implementation of the boundary reconciliation below,
    using the PG-first primitives (fetch_one/fetch_all + upsert_user_auth_method).
    Mirrors the SQLite branch's decision logic exactly."""
    load_user_identity_versions(user_id)  # ensures the identity-versions row (PG-first)
    methods = _auth_store_pg(
        auth_store_repository.fetch_all("user_auth_methods", order_by="created_at ASC", user_id=user_id)
    )
    if methods is _PG_NA or not isinstance(methods, list):
        methods = []
    has_auth_methods = len(methods) > 0
    security_row = _auth_store_pg(auth_store_repository.fetch_one("user_enterprise_security", user_id=user_id))
    if security_row is _PG_NA:
        return
    if security_row is not None:
        auth_provider = str(security_row.get("auth_provider") or "").strip().lower()
        sso_subject = str(security_row.get("sso_subject") or "").strip() or None
        external_id = str(security_row.get("external_id") or "").strip() or None
        provisioning_source = str(security_row.get("provisioning_source") or "").strip() or None
        has_external_identity = bool(auth_provider or sso_subject or external_id)
        password_sources = {"", "local_password", "password", "email_password"}
        has_password_method = any(
            str(m.get("method_type") or "") == "password" and str(m.get("provider") or "") == "empyralis_password"
            for m in methods
        )
        if password_hash and (not has_external_identity or str(provisioning_source or "").strip().lower() in password_sources) and not has_password_method:
            upsert_user_auth_method(
                user_id, method_type="password", provider="empyralis_password", subject=email,
                label="Email and password", is_primary=not has_auth_methods, can_recover=True,
                metadata={"email": email, "identity_role": "account_access"},
            )
            has_auth_methods = True
        if auth_provider or sso_subject or external_id:
            sso_provider = auth_provider or "external_identity"
            sso_subj = sso_subject or external_id or email
            has_sso_method = any(
                str(m.get("method_type") or "") == "sso" and str(m.get("provider") or "") == sso_provider and str(m.get("subject") or "") == str(sso_subj or "")
                for m in methods
            )
            if not has_sso_method:
                upsert_user_auth_method(
                    user_id, method_type="sso", provider=sso_provider, subject=sso_subj,
                    label=f"{(auth_provider or 'External').replace('_', ' ').title()} sign-in",
                    is_primary=not has_auth_methods, can_recover=False,
                    metadata={"email": email, "external_id": external_id, "provisioning_source": provisioning_source, "identity_role": "account_access"},
                )
    elif password_hash:
        upsert_user_auth_method(
            user_id, method_type="password", provider="empyralis_password", subject=email,
            label="Email and password", is_primary=not has_auth_methods, can_recover=True,
            metadata={"email": email, "identity_role": "account_access"},
        )


def _ensure_user_identity_boundary_records(user_id: str) -> None:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return
    ts = int(time.time())
    user = _find_user_by_id(clean_user_id)
    if user is None:
        return
    email = str(user.get("email") or "").strip().lower() or None
    identity = (
        _control_plane_call(control_plane_repository.get_local_auth_identity_by_email(email))
        if email
        else None
    )
    password_hash = str((identity or {}).get("password_hash") or "").strip()
    if _auth_store_pg(auth_store_repository.ensure_user_identity_versions(clean_user_id)) is not _PG_NA:
        _ensure_identity_boundary_records_pg(clean_user_id, email, password_hash, ts)
        return
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            _ensure_user_identity_versions_locked(connection, clean_user_id)
            any_method = connection.execute(
                "SELECT COUNT(1) AS count FROM user_auth_methods WHERE user_id = ?",
                (clean_user_id,),
            ).fetchone()
            has_auth_methods = int(any_method["count"]) > 0 if any_method is not None else False
            security_row = connection.execute(
                """
                SELECT auth_provider, sso_subject, external_id, provisioning_source
                FROM user_enterprise_security
                WHERE user_id = ?
                LIMIT 1
                """,
                (clean_user_id,),
            ).fetchone()
            if security_row is not None:
                auth_provider = str(security_row["auth_provider"] or "").strip().lower()
                sso_subject = str(security_row["sso_subject"] or "").strip() or None
                external_id = str(security_row["external_id"] or "").strip() or None
                provisioning_source = str(security_row["provisioning_source"] or "").strip() or None
                has_external_identity = bool(auth_provider or sso_subject or external_id)
                password_sources = {"", "local_password", "password", "email_password"}
                existing_password_method = connection.execute(
                    """
                    SELECT id
                    FROM user_auth_methods
                    WHERE user_id = ? AND method_type = 'password' AND provider = 'empyralis_password'
                    LIMIT 1
                    """,
                    (clean_user_id,),
                ).fetchone()
                if password_hash and (
                    not has_external_identity or str(provisioning_source or "").strip().lower() in password_sources
                ) and existing_password_method is None:
                    _upsert_user_auth_method_locked(
                        connection,
                        user_id=clean_user_id,
                        method_type="password",
                        provider="empyralis_password",
                        subject=email,
                        label="Email and password",
                        is_primary=not has_auth_methods,
                        can_recover=True,
                        metadata={"email": email, "identity_role": "account_access"},
                        now_ts=ts,
                    )
                    has_auth_methods = True
                if auth_provider or sso_subject or external_id:
                    existing_sso_method = connection.execute(
                        """
                        SELECT id
                        FROM user_auth_methods
                        WHERE user_id = ? AND method_type = 'sso' AND provider = ? AND subject = ?
                        LIMIT 1
                        """,
                        (clean_user_id, auth_provider or "external_identity", sso_subject or external_id or email),
                    ).fetchone()
                    if existing_sso_method is None:
                        _upsert_user_auth_method_locked(
                            connection,
                            user_id=clean_user_id,
                            method_type="sso",
                            provider=auth_provider or "external_identity",
                            subject=sso_subject or external_id or email,
                            label=f"{(auth_provider or 'External').replace('_', ' ').title()} sign-in",
                            is_primary=not has_auth_methods,
                            can_recover=False,
                            metadata={
                                "email": email,
                                "external_id": external_id,
                                "provisioning_source": provisioning_source,
                                "identity_role": "account_access",
                            },
                            now_ts=ts,
                        )
            elif password_hash:
                _upsert_user_auth_method_locked(
                    connection,
                    user_id=clean_user_id,
                    method_type="password",
                    provider="empyralis_password",
                    subject=email,
                    label="Email and password",
                    is_primary=not has_auth_methods,
                    can_recover=True,
                    metadata={"email": email, "identity_role": "account_access"},
                    now_ts=ts,
                )
            connection.commit()


def _auth_payload_for_user(
    user: dict[str, Any],
    *,
    role: str,
    token: Optional[str] = None,
    workspace_access: Optional[list[dict[str, Any]]] = None,
    current_workspace_id: Optional[str] = None,
    default_workspace_id: Optional[str] = None,
    current_tenant_id: Optional[str] = None,
    default_tenant_id: Optional[str] = None,
    tenant_access: Optional[list[dict[str, Any]]] = None,
    enterprise_security: Optional[dict[str, Any]] = None,
    auth_session: Optional[dict[str, Any]] = None,
    device_link: Optional[dict[str, Any]] = None,
    identity_versions: Optional[dict[str, Any]] = None,
    session_recovery: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    user_id = str(user.get("id") or "").strip()
    entitlement_projection = _workspace_entitlement_projection(
        workspace_access,
        current_workspace_id=current_workspace_id,
        default_workspace_id=default_workspace_id,
    )
    payload: dict[str, Any] = {
        "ok": True,
        "user": _public_user_payload(user, role=role),
        "identity_boundary": user_identity_boundary(user_id),
    }
    if token is not None:
        payload["token"] = token
    if workspace_access is not None:
        payload["workspace_access"] = workspace_access
    if current_workspace_id is not None:
        payload["current_workspace_id"] = current_workspace_id
    if default_workspace_id is not None:
        payload["default_workspace_id"] = default_workspace_id
    if current_tenant_id is not None:
        payload["current_tenant_id"] = current_tenant_id
    if default_tenant_id is not None:
        payload["default_tenant_id"] = default_tenant_id
    if tenant_access is not None:
        payload["tenant_access"] = tenant_access
    if enterprise_security is not None:
        payload["enterprise_security"] = enterprise_security
    if auth_session is not None:
        payload["auth_session"] = auth_session
    if device_link is not None:
        payload["device_link"] = device_link
    if identity_versions is not None:
        payload["identity_versions"] = identity_versions
    if session_recovery is not None:
        payload["session_recovery"] = session_recovery
    if entitlement_projection["workspace_entitlements"]:
        payload["workspace_entitlements"] = entitlement_projection["workspace_entitlements"]
    if entitlement_projection["current_workspace_entitlements"] is not None:
        payload["current_workspace_entitlements"] = entitlement_projection["current_workspace_entitlements"]
    if entitlement_projection["default_workspace_entitlements"] is not None:
        payload["default_workspace_entitlements"] = entitlement_projection["default_workspace_entitlements"]
    return payload


def list_user_auth_methods(user_id: str) -> list[dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return []
    _ensure_user_identity_boundary_records(clean_user_id)
    pg = _auth_store_pg(
        auth_store_repository.fetch_all(
            "user_auth_methods", order_by="is_primary DESC, created_at ASC, provider ASC", user_id=clean_user_id
        )
    )
    if pg is not _PG_NA:
        return [_auth_method_from_row(row) for row in pg]
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM user_auth_methods
                WHERE user_id = ?
                ORDER BY is_primary DESC, created_at ASC, provider ASC
                """,
                (clean_user_id,),
            ).fetchall()
    return [_auth_method_from_row(row) for row in rows]


def upsert_user_auth_method(
    user_id: str,
    *,
    method_type: str,
    provider: str,
    subject: Optional[str] = None,
    label: Optional[str] = None,
    status: str = "active",
    is_primary: bool = False,
    can_recover: bool = True,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")
    clean_provider = str(provider or "").strip().lower() or "unknown"
    clean_subject = str(subject or "").strip() or None
    clean_label = str(label or "").strip() or None
    method_id = _stable_identity_row_id("auth_method", clean_user_id, method_type, clean_provider, clean_subject or clean_label or "")
    ts = int(time.time())
    pg_row = {
        "id": method_id,
        "user_id": clean_user_id,
        "method_type": _normalize_auth_method_type(method_type),
        "provider": clean_provider,
        "subject": clean_subject,
        "label": clean_label,
        "status": _normalize_auth_method_status(status),
        "is_primary": 1 if is_primary else 0,
        "can_recover": 1 if can_recover else 0,
        "metadata_json": json.dumps(metadata or {}),
        "created_at": ts,
        "updated_at": ts,
    }
    pg = _auth_store_pg(auth_store_repository.upsert_auth_method(row=pg_row, unset_other_primary=bool(is_primary)))
    if pg is not _PG_NA:
        _bump_user_identity_versions(clean_user_id, auth=True)
        return _auth_method_from_row(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            _ensure_user_identity_versions_locked(connection, clean_user_id)
            record = _upsert_user_auth_method_locked(
                connection,
                user_id=clean_user_id,
                method_type=method_type,
                provider=provider,
                subject=subject,
                label=label,
                status=status,
                is_primary=is_primary,
                can_recover=can_recover,
                metadata=metadata,
            )
            connection.commit()
    _bump_user_identity_versions(clean_user_id, auth=True)
    return record


def list_user_provider_connections(user_id: str) -> list[dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return []
    pg = _auth_store_pg(
        auth_store_repository.fetch_all(
            "user_provider_connections", order_by="provider ASC, workspace_id ASC, created_at ASC", user_id=clean_user_id
        )
    )
    if pg is not _PG_NA:
        return [_provider_connection_from_row(row) for row in pg]
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM user_provider_connections
                WHERE user_id = ?
                ORDER BY provider ASC, workspace_id ASC, created_at ASC
                """,
                (clean_user_id,),
            ).fetchall()
    return [_provider_connection_from_row(row) for row in rows]


def upsert_user_provider_connection(
    user_id: str,
    *,
    provider: str,
    workspace_id: Optional[str] = None,
    status: str = "active",
    label: Optional[str] = None,
    external_account_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")
    clean_provider = str(provider or "").strip().lower() or "unknown"
    clean_workspace_id = _normalize_workspace_token(workspace_id, default="") if str(workspace_id or "").strip() else None
    clean_external_account_id = str(external_account_id or "").strip() or None
    connection_id = _stable_identity_row_id(
        "provider_connection", clean_user_id, clean_provider, clean_workspace_id or "global", clean_external_account_id or ""
    )
    ts = int(time.time())
    pg_row = {
        "id": connection_id,
        "user_id": clean_user_id,
        "provider": clean_provider,
        "workspace_id": clean_workspace_id,
        "status": _normalize_provider_connection_status(status),
        "label": str(label or "").strip() or None,
        "external_account_id": clean_external_account_id,
        "metadata_json": json.dumps(metadata or {}),
        "created_at": ts,
        "updated_at": ts,
    }
    pg = _auth_store_pg(auth_store_repository.upsert_row("user_provider_connections", pg_row))
    if pg is not _PG_NA:
        _bump_user_identity_versions(clean_user_id, provider_scope=True)
        return _provider_connection_from_row(pg)
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            _ensure_user_identity_versions_locked(connection, clean_user_id)
            record = _upsert_user_provider_connection_locked(
                connection,
                user_id=clean_user_id,
                provider=provider,
                workspace_id=workspace_id,
                status=status,
                label=label,
                external_account_id=external_account_id,
                metadata=metadata,
            )
            connection.commit()
    _bump_user_identity_versions(clean_user_id, provider_scope=True)
    return record


def user_identity_boundary(user_id: str) -> dict[str, Any]:
    clean_user_id = str(user_id or "").strip()
    auth_methods = list_user_auth_methods(clean_user_id)
    provider_connections = list_user_provider_connections(clean_user_id)
    device_links = list_user_device_links(clean_user_id)
    auth_sessions = list_user_auth_sessions(clean_user_id)
    identity_versions = load_user_identity_versions(clean_user_id)
    primary_auth_method = next((item for item in auth_methods if item.get("is_primary")), auth_methods[0] if auth_methods else None)
    active_provider_connections = [
        item for item in provider_connections if _normalize_provider_connection_status(item.get("status")) == "active"
    ]
    active_device_links = [
        item for item in device_links if _normalize_device_link_status(item.get("status")) == "active"
    ]
    return {
        "account_owner": "empyralis",
        "account_id": clean_user_id,
        "auth_methods": auth_methods,
        "provider_connections": provider_connections,
        "devices": device_links,
        "auth_sessions": auth_sessions,
        "identity_versions": identity_versions,
        "machine_enrollment": {
            "separate_from_account_identity": True,
            "managed_via": "machines_and_runtime_enrollment",
        },
        "summary": {
            "primary_auth_method": primary_auth_method,
            "auth_method_count": len(auth_methods),
            "linked_provider_count": len(active_provider_connections),
            "linked_device_count": len(active_device_links),
            "active_session_count": len(auth_sessions),
            "has_recovery_method": any(
                bool(item.get("can_recover"))
                and _normalize_auth_method_status(item.get("status")) == "active"
                for item in auth_methods
            ),
        },
        "boundaries": {
            "identity": "Empyralis owns the user account, workspaces, runs, artifacts, notifications, and billing.",
            "auth_methods": "Account access methods are separate from AI provider capabilities.",
            "provider_connections": "Linked providers add capability only and can be revoked without deleting the Empyralis account.",
            "devices": "Linked devices are scoped to the Empyralis account and can be revoked without rotating account identity.",
            "auth_sessions": "Bearer sessions are refreshable, device-bound, channel-scoped, and can be revoked independently of the account.",
            "machine_enrollment": "Machine enrollment is a separate execution boundary and does not define account ownership.",
        },
    }


def enterprise_status_for_user(current_user: Optional[Dict[str, Any]]) -> dict[str, Any]:
    user_id = _current_bearer_user_id(current_user)
    user_security = load_user_enterprise_security(user_id)
    tenant_ids = sorted(allowed_tenant_ids(current_user) or [])
    tenant_settings = [load_tenant_enterprise_settings(tenant_id) for tenant_id in tenant_ids]
    mfa_required = any(bool(item.get("mfa", {}).get("required")) for item in tenant_settings)
    return {
        "ok": True,
        "user": user_security,
        "tenants": tenant_settings,
        "summary": {
            "sso_enabled": any(bool(item.get("sso", {}).get("enabled")) for item in tenant_settings),
            "mfa_required": mfa_required,
            "mfa_enrolled": bool(user_security.get("mfa_enrolled")),
            "scim_enabled": any(bool(item.get("scim", {}).get("enabled")) for item in tenant_settings),
        },
    }


def provision_user_account(
    *,
    email: str,
    name: Optional[str] = None,
    tenant_id: Optional[str] = None,
    workspace_roles: Optional[dict[str, Any]] = None,
    provisioning_source: str = "admin_api",
    external_id: Optional[str] = None,
    auth_provider: Optional[str] = None,
    sso_subject: Optional[str] = None,
) -> dict[str, Any]:
    email_token = str(email or "").strip().lower()
    if not email_token or "@" not in email_token:
        raise HTTPException(status_code=400, detail="Valid email is required.")
    resolved_tenant_id = _require_tenant_token(tenant_id, detail="tenant_id is required for admin provisioning.", status_code=400)
    normalized_workspace_roles: dict[str, str] = {}
    for raw_workspace_id, raw_role in dict(workspace_roles or {}).items():
        workspace_id = _normalize_workspace_token(raw_workspace_id, default="")
        if not workspace_id:
            continue
        normalized_workspace_roles[workspace_id] = normalize_rbac_role(raw_role, default="member")
    if not normalized_workspace_roles:
        raise HTTPException(status_code=400, detail="At least one workspace role is required for admin provisioning.")
    created_at = int(time.time())
    user_id = str((_find_user_by_email(email_token) or {}).get("id") or "").strip() or str(uuid.uuid4())
    _control_plane_workspace_roles = dict(normalized_workspace_roles)
    for workspace_id, role in _control_plane_workspace_roles.items():
        # Bind the workspace to its tenant FIRST, via the shared idempotent
        # binding, so the tenant/workspace rows and the workspace_registry
        # binding always exist. ensure_workspace_membership only creates them on
        # the Postgres path when the workspace is new (and never on the SQLite
        # fallback), which left externally-provisioned/SSO workspaces unbound and
        # 403'ing /auth/me. Binding here guarantees resolution in both stores.
        ensure_workspace_tenant_binding(workspace_id, resolved_tenant_id)
        stored = _control_plane_call(
            control_plane_repository.ensure_workspace_membership(
                user_id=user_id,
                email=email_token,
                display_name=str(name or "").strip() or None,
                tenant_id=resolved_tenant_id,
                workspace_id=workspace_id,
                role=role,
                provider=str(auth_provider or "external_identity").strip().lower() or "external_identity",
                subject=str(sso_subject or external_id or email_token).strip() or email_token,
            )
        )
        if not isinstance(stored, dict):
            raise HTTPException(status_code=500, detail="Provisioned user could not be persisted.")
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            _ensure_user_identity_versions_locked(connection, user_id, now_ts=created_at)
            current_security_row = connection.execute(
                """
                SELECT
                    user_id,
                    mfa_enrolled,
                    mfa_method,
                    mfa_enrolled_at,
                    mfa_last_verified_at,
                    auth_provider,
                    sso_subject,
                    provisioning_source,
                    external_id,
                    last_provisioned_at,
                    updated_at
                FROM user_enterprise_security
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            current_security = _user_enterprise_security_from_row(current_security_row, user_id)
            connection.execute(
                """
                INSERT OR REPLACE INTO user_enterprise_security (
                    user_id,
                    mfa_enrolled,
                    mfa_method,
                    mfa_enrolled_at,
                    mfa_last_verified_at,
                    auth_provider,
                    sso_subject,
                    provisioning_source,
                    external_id,
                    last_provisioned_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    1 if bool(current_security.get("mfa_enrolled")) else 0,
                    current_security.get("mfa_method"),
                    current_security.get("mfa_enrolled_at"),
                    current_security.get("mfa_last_verified_at"),
                    str(auth_provider or current_security.get("auth_provider") or "").strip() or None,
                    str(sso_subject or current_security.get("sso_subject") or "").strip() or None,
                    str(provisioning_source or current_security.get("provisioning_source") or "admin_api").strip() or "admin_api",
                    str(external_id or current_security.get("external_id") or "").strip() or None,
                    created_at,
                    created_at,
                ),
            )
            if auth_provider or sso_subject or external_id:
                existing_auth_methods_row = connection.execute(
                    "SELECT COUNT(1) AS count FROM user_auth_methods WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                has_auth_methods = (
                    int(existing_auth_methods_row["count"]) > 0
                    if existing_auth_methods_row is not None
                    else False
                )
                _upsert_user_auth_method_locked(
                    connection,
                    user_id=user_id,
                    method_type="sso",
                    provider=str(auth_provider or "external_identity").strip().lower(),
                    subject=str(sso_subject or external_id or email_token).strip() or email_token,
                    label=f"{str(auth_provider or 'External').replace('_', ' ').title()} sign-in",
                    is_primary=not has_auth_methods,
                    can_recover=False,
                    metadata={
                        "email": email_token,
                        "external_id": str(external_id or "").strip() or None,
                        "provisioning_source": str(provisioning_source or "admin_api").strip() or "admin_api",
                        "identity_role": "account_access",
                    },
                    now_ts=created_at,
                    )
            connection.commit()
    _bump_user_identity_versions(user_id, membership=True, auth=True)
    user = _find_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=500, detail="Provisioned user was not persisted.")
    workspace_access = _effective_workspace_access(
        user_id=user_id,
        email=email_token,
        role="member",
        auth_type="bearer",
        is_admin=False,
        workspace_ids=list(normalized_workspace_roles.keys()),
    )
    workspace_identity = _workspace_identity_projection(workspace_access)
    return _auth_payload_for_user(
        user,
        role="member",
        workspace_access=list(workspace_access.values()),
        current_workspace_id=workspace_identity.get("current_workspace_id"),
        default_workspace_id=workspace_identity.get("default_workspace_id"),
        current_tenant_id=workspace_identity.get("current_tenant_id"),
        default_tenant_id=workspace_identity.get("default_tenant_id"),
        tenant_access=list(tenant_access_map({"workspace_access": workspace_access}).values()),
        enterprise_security=load_user_enterprise_security(user_id),
        identity_versions=load_user_identity_versions(user_id),
    )


def _effective_workspace_access(
    *,
    user_id: str,
    email: Optional[str],
    role: str,
    auth_type: str,
    is_admin: bool,
    workspace_ids: list[str],
    workspace_roles_claim: Any = None,
    workspace_capabilities_claim: Any = None,
    workspace_dangerous_claim: Any = None,
    workspace_trusted_machines_claim: Any = None,
) -> dict[str, dict[str, Any]]:
    if auth_type == "api_key":
        return {}
    if is_admin and auth_type not in {"bearer", "local_dev"}:
        return {}
    membership_roles = {
        _normalize_workspace_token(item.get("workspace_id")): normalize_rbac_role(item.get("role"), default="viewer")
        for item in _list_workspace_memberships(user_id)
        if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
    }
    claim_roles = _normalize_workspace_role_claim_map(workspace_roles_claim)
    claim_capabilities = _normalize_workspace_policy_claim_map(
        workspace_capabilities_claim,
        normalizer=_normalize_workspace_capability_policy,
    )
    claim_dangerous = _normalize_workspace_policy_claim_map(
        workspace_dangerous_claim,
        normalizer=_normalize_workspace_dangerous_policy,
    )
    claim_trusted = _normalize_workspace_trusted_machine_claim_map(workspace_trusted_machines_claim)

    effective_workspace_ids = {
        _normalize_workspace_token(item)
        for item in list(workspace_ids or [])
        if str(item or "").strip()
    }
    effective_workspace_ids.update(claim_roles.keys())
    effective_workspace_ids.update(membership_roles.keys())

    access: dict[str, dict[str, Any]] = {}
    for workspace_id in sorted(effective_workspace_ids):
        try:
            tenant_id = tenant_id_for_workspace(workspace_id)
        except HTTPException:
            # A membership/claim can outlive its workspace (the workspace was
            # deleted, or -- in local dev -- the workspaces table was wiped
            # out from under still-referenced membership rows). Don't let one
            # orphaned workspace_id take down access resolution for the
            # user's other, still-valid workspaces; just drop it.
            LOGGER.warning(
                "workspace_access_map: skipping orphaned workspace_id '%s' for user_id '%s' "
                "(no matching row in workspaces).",
                workspace_id,
                user_id,
            )
            continue
        tenant_policy = load_tenant_policy(tenant_id)
        policy_row = load_workspace_policy(workspace_id)
        role_value = membership_roles.get(workspace_id) or claim_roles.get(workspace_id)
        if not role_value and workspace_id in effective_workspace_ids:
            role_value = normalize_rbac_role(role, default="viewer")
        workspace_capability_policy = {
            "allow": list(claim_capabilities.get(workspace_id, {}).get("allow") or policy_row["capabilities"]["allow"]),
            "deny": list(claim_capabilities.get(workspace_id, {}).get("deny") or policy_row["capabilities"]["deny"]),
        }
        capability_policy = _resolve_inherited_allow_deny(
            tenant_policy.get("capabilities"),
            workspace_capability_policy,
        )
        workspace_dangerous_policy = {
            "allow": list(claim_dangerous.get(workspace_id, {}).get("allow") or policy_row["dangerous_action_classes"]["allow"]),
            "deny": list(claim_dangerous.get(workspace_id, {}).get("deny") or policy_row["dangerous_action_classes"]["deny"]),
        }
        dangerous_policy = _resolve_inherited_allow_deny(
            tenant_policy.get("dangerous_action_classes"),
            workspace_dangerous_policy,
        )
        connector_policy = _resolve_inherited_allow_deny(
            tenant_policy.get("connectors"),
            policy_row.get("connectors"),
        )
        trusted_owner_machine_ids = list(
            claim_trusted.get(workspace_id) or policy_row.get("trusted_owner_machine_ids") or []
        )
        access[workspace_id] = {
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "tenant_role": normalize_rbac_role(role_value, default="viewer"),
            "role": normalize_rbac_role(role_value, default="viewer"),
            "capabilities": _normalize_workspace_capability_policy(capability_policy),
            "tenant_capabilities": _normalize_workspace_capability_policy(tenant_policy.get("capabilities")),
            "workspace_capabilities": _normalize_workspace_capability_policy(workspace_capability_policy),
            "dangerous_action_classes": _normalize_workspace_dangerous_policy(dangerous_policy),
            "tenant_dangerous_action_classes": _normalize_workspace_dangerous_policy(tenant_policy.get("dangerous_action_classes")),
            "workspace_dangerous_action_classes": _normalize_workspace_dangerous_policy(workspace_dangerous_policy),
            "connectors": _normalize_connector_permission_policy(connector_policy),
            "tenant_connectors": _normalize_connector_permission_policy(tenant_policy.get("connectors")),
            "workspace_connectors": _normalize_connector_permission_policy(policy_row.get("connectors")),
            "machine_enrollment_scope": _normalize_machine_enrollment_scope(
                policy_row.get("machine_enrollment_scope") or tenant_policy.get("machine_enrollment_scope"),
                default="workspace",
            ),
            "trusted_owner_machine_ids": _normalize_distinct_tokens(trusted_owner_machine_ids),
            "owner_user_id": str(user_id or "").strip() or None,
            "owner_email": str(email or "").strip().lower() or None,
        }
    return access


def _build_local_dev_user() -> Dict[str, Any]:
    role = _local_dev_auth_role()
    user_id = _local_dev_user_id()
    email = _local_dev_email()
    workspace_ids = _local_dev_workspace_ids()
    workspace_roles = {workspace_id: role for workspace_id in workspace_ids}
    workspace_access: dict[str, dict[str, Any]] = {}
    for workspace_id in sorted({item for item in workspace_ids if str(item).strip()}):
        try:
            tenant_id = tenant_id_for_workspace(workspace_id)
        except HTTPException:
            tenant_id = _require_tenant_token(
                _normalize_tenant_token(os.getenv("ORION_LOCAL_DEV_TENANT_ID") or f"tenant_{workspace_id}", default=""),
                detail=f"Local-dev tenant scope for workspace '{workspace_id}' is invalid.",
            )
        tenant_policy = load_tenant_policy(tenant_id)
        try:
            workspace_policy = load_workspace_policy(workspace_id)
        except HTTPException:
            workspace_policy = _workspace_policy_from_row(None, workspace_id, tenant_id=tenant_id)
        capability_policy = _resolve_inherited_allow_deny(
            tenant_policy.get("capabilities"),
            workspace_policy.get("capabilities"),
        )
        dangerous_policy = _resolve_inherited_allow_deny(
            tenant_policy.get("dangerous_action_classes"),
            workspace_policy.get("dangerous_action_classes"),
        )
        connector_policy = _resolve_inherited_allow_deny(
            tenant_policy.get("connectors"),
            workspace_policy.get("connectors"),
        )
        workspace_access[workspace_id] = {
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "tenant_role": role,
            "role": role,
            "capabilities": _normalize_workspace_capability_policy(capability_policy),
            "tenant_capabilities": _normalize_workspace_capability_policy(tenant_policy.get("capabilities")),
            "workspace_capabilities": _normalize_workspace_capability_policy(workspace_policy.get("capabilities")),
            "dangerous_action_classes": _normalize_workspace_dangerous_policy(dangerous_policy),
            "tenant_dangerous_action_classes": _normalize_workspace_dangerous_policy(tenant_policy.get("dangerous_action_classes")),
            "workspace_dangerous_action_classes": _normalize_workspace_dangerous_policy(workspace_policy.get("dangerous_action_classes")),
            "connectors": _normalize_connector_permission_policy(connector_policy),
            "tenant_connectors": _normalize_connector_permission_policy(tenant_policy.get("connectors")),
            "workspace_connectors": _normalize_connector_permission_policy(workspace_policy.get("connectors")),
            "machine_enrollment_scope": _normalize_machine_enrollment_scope(
                workspace_policy.get("machine_enrollment_scope") or tenant_policy.get("machine_enrollment_scope"),
                default="workspace",
            ),
            "trusted_owner_machine_ids": _normalize_distinct_tokens(workspace_policy.get("trusted_owner_machine_ids")),
            "owner_user_id": user_id,
            "owner_email": email,
        }
    return {
        "user_id": user_id,
        "auth_type": "local_dev",
        "email": email,
        "tenant_ids": [entry.get("tenant_id") for entry in workspace_access.values() if isinstance(entry, dict)],
        "workspace_ids": workspace_ids,
        "workspace_roles": workspace_roles,
        "workspace_access": workspace_access,
        "tenant_access": tenant_access_map({"workspace_access": workspace_access}),
        "role": role,
        "is_admin": role == "owner",
        "auth_admin": role == "owner",
        "local_dev": True,
    }


def _hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 100_000)
    return f"{_b64url_encode(salt_bytes)}.{_b64url_encode(digest)}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        salt_raw, digest_raw = str(password_hash or "").split(".", 1)
        candidate = _hash_password(password, salt=_b64url_decode(salt_raw))
    except Exception:
        return False
    return secrets.compare_digest(candidate, password_hash)


def _find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    email_token = str(email or "").strip().lower()
    pg_user = _control_plane_call(control_plane_repository.get_user_by_email(email_token))
    if isinstance(pg_user, dict):
        return pg_user
    return None


def _find_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    user_token = str(user_id or "").strip()
    if not user_token:
        return None
    pg_user = _control_plane_call(control_plane_repository.get_user_by_id(user_token))
    if isinstance(pg_user, dict):
        return pg_user
    return None


def _public_user_payload(user: Dict[str, Any], *, role: Optional[str] = None) -> Dict[str, Any]:
    user_id = str(user.get("id") or "").strip()
    email = str(user.get("email") or "").strip().lower()
    payload = {
        "id": user_id,
        "email": email,
        "name": str(user.get("name") or "").strip() or None,
        "avatar_url": str(user.get("avatar_url") or "").strip() or None,
    }
    if role is not None:
        normalized_role = normalize_rbac_role(role)
        payload["role"] = normalized_role
        payload["is_admin"] = _has_auth_admin_identity(user_id or None, email or None)
    return payload


def _validated_bearer_context(payload: Dict[str, Any], *, touch_session: bool = False) -> Dict[str, Any]:
    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Bearer token subject is missing.")
    exp = int(payload.get("exp") or 0)
    if exp and exp < int(time.time()):
        raise HTTPException(status_code=401, detail="Bearer token has expired.")

    identity_versions = load_user_identity_versions(user_id)
    for claim_key in ("membership_version", "auth_version", "provider_scope_version"):
        claim_value = int(payload.get(claim_key) or 0)
        current_value = int(identity_versions.get(claim_key) or 1)
        if claim_value and claim_value != current_value:
            raise HTTPException(status_code=401, detail="Bearer token is stale and must be refreshed.")

    session_id = str(payload.get("sid") or "").strip()
    session_record: Optional[dict[str, Any]] = None
    device_record: Optional[dict[str, Any]] = None
    if session_id:
        session_record = get_auth_session(session_id)
        if not session_record:
            raise HTTPException(status_code=401, detail="Bearer session is invalid.")
        if str(session_record.get("user_id") or "").strip() != user_id:
            raise HTTPException(status_code=401, detail="Bearer session identity mismatch.")
        if str(session_record.get("status") or "").strip().lower() != "active":
            raise HTTPException(status_code=401, detail="Bearer session is no longer active.")

        claimed_channel = _normalize_auth_session_channel(payload.get("channel"), default=session_record.get("channel") or "web")
        if claimed_channel != _normalize_auth_session_channel(session_record.get("channel"), default="web"):
            raise HTTPException(status_code=401, detail="Bearer session channel mismatch.")

        claimed_device_id = str(payload.get("device_id") or "").strip() or None
        actual_device_id = str(session_record.get("device_id") or "").strip() or None
        if claimed_device_id and actual_device_id and claimed_device_id != actual_device_id:
            raise HTTPException(status_code=401, detail="Bearer session device mismatch.")

        claimed_runtime_id = str(payload.get("runtime_id") or "").strip() or None
        actual_runtime_id = str(session_record.get("runtime_id") or "").strip() or None
        if claimed_runtime_id and actual_runtime_id and claimed_runtime_id != actual_runtime_id:
            raise HTTPException(status_code=401, detail="Bearer session runtime mismatch.")

        effective_device_id = claimed_device_id or actual_device_id
        if effective_device_id:
            device_record = get_user_device_link(effective_device_id)
            if not device_record:
                raise HTTPException(status_code=401, detail="Bearer session device is not linked.")
            if str(device_record.get("user_id") or "").strip() != user_id:
                raise HTTPException(status_code=401, detail="Bearer session device owner mismatch.")
            if str(device_record.get("status") or "").strip().lower() != "active":
                raise HTTPException(status_code=401, detail="Bearer session device is not active.")
            if str(device_record.get("trust_state") or "").strip().lower() == "revoked":
                raise HTTPException(status_code=401, detail="Bearer session device trust was revoked.")
            device_channel = _normalize_auth_session_channel(device_record.get("channel"), default=claimed_channel)
            if device_channel != claimed_channel:
                raise HTTPException(status_code=401, detail="Bearer session device channel mismatch.")
            if bool(device_record.get("session_binding_required")) and actual_device_id != effective_device_id:
                raise HTTPException(status_code=401, detail="Bearer session is not bound to the active device link.")
            if touch_session:
                _touch_user_device_link(effective_device_id)
        if touch_session:
            session_record = touch_auth_session(session_id) or session_record

    return {
        "user_id": user_id,
        "email": str(payload.get("email") or "").strip().lower() or None,
        "workspace_ids": _normalize_workspace_ids_claim(payload.get("workspace_ids")),
        "identity_versions": identity_versions,
        "auth_session": session_record,
        "device_link": device_record,
        "payload": payload,
    }


def issue_token(
    user_id: str,
    *,
    email: Optional[str] = None,
    role: str = "member",
    workspace_access: Optional[list[dict[str, Any]]] = None,
    channel: str = "web",
    device_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    trust_state: Optional[str] = None,
    session_id: Optional[str] = None,
    session_family_id: Optional[str] = None,
    session_metadata: Optional[dict[str, Any]] = None,
    ttl_seconds: Optional[int] = None,
    session_record_ttl_seconds: Optional[int] = None,
) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    resolved_ttl_seconds = max(int(ttl_seconds or JWT_EXP_SECONDS), 60)
    normalized_workspace_access = [dict(item) for item in list(workspace_access or []) if isinstance(item, dict)]
    workspace_ids = [
        _normalize_workspace_token(item.get("workspace_id"))
        for item in normalized_workspace_access
        if str(item.get("workspace_id") or "").strip()
    ]
    tenant_ids = [
        _normalize_tenant_token(item.get("tenant_id"))
        for item in normalized_workspace_access
        if str(item.get("tenant_id") or "").strip()
    ]
    workspace_roles = {
        _normalize_workspace_token(item.get("workspace_id")): normalize_rbac_role(item.get("role"), default=role)
        for item in normalized_workspace_access
        if str(item.get("workspace_id") or "").strip()
    }
    identity_versions = load_user_identity_versions(user_id)
    auth_session = create_auth_session(
        str(user_id or "").strip(),
        channel=channel,
        device_id=device_id,
        runtime_id=runtime_id,
        trust_state=trust_state,
        session_id=session_id,
        session_family_id=session_family_id,
        metadata=session_metadata,
        ttl_seconds=session_record_ttl_seconds or resolved_ttl_seconds,
    )
    payload = {
        "sub": str(user_id),
        "email": str(email or "").strip().lower() or None,
        "role": normalize_rbac_role(role),
        "tenant_ids": sorted({token for token in tenant_ids if token}),
        "workspace_ids": sorted({token for token in workspace_ids if token}),
        "workspace_roles": workspace_roles,
        "sid": str(auth_session.get("session_id") or "").strip() or None,
        "channel": _normalize_auth_session_channel(channel),
        "device_id": str(device_id or auth_session.get("device_id") or "").strip() or None,
        "runtime_id": str(runtime_id or auth_session.get("runtime_id") or "").strip() or None,
        "trust_state": _normalize_device_trust_state(
            auth_session.get("trust_state"),
            default="verified" if str(device_id or "").strip() else "unbound",
        ),
        "membership_version": int(identity_versions.get("membership_version") or 1),
        "auth_version": int(identity_versions.get("auth_version") or 1),
        "provider_scope_version": int(identity_versions.get("provider_scope_version") or 1),
        "iat": now,
        "exp": now + resolved_ttl_seconds,
    }
    header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    signature = hmac.new(_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"


def _decode_token_payload(token: str) -> Dict[str, Any]:
    try:
        header_segment, payload_segment, signature_segment = str(token or "").split(".", 2)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token.") from exc
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    expected_signature = hmac.new(_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided_signature = _b64url_decode(signature_segment)
    if not secrets.compare_digest(expected_signature, provided_signature):
        raise HTTPException(status_code=401, detail="Invalid bearer token.")
    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token payload.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Invalid bearer token payload.")
    return payload


def verify_token(token: str) -> str:
    payload = _decode_token_payload(token)
    context = _validated_bearer_context(payload, touch_session=False)
    return str(context.get("user_id") or "").strip()


def _normalize_workspace_ids_claim(value: Any) -> list[str]:
    items: list[str] = []
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part or "").strip() for part in value if str(part or "").strip()]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = item.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def allowed_workspace_ids(user: Optional[Dict[str, Any]]) -> Optional[set[str]]:
    if not isinstance(user, dict):
        return None
    auth_type = str(user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key" and current_user_has_auth_admin_access(user):
        return None
    if bool(user.get("is_admin")) and auth_type not in {"bearer", "local_dev"}:
        return None
    access = workspace_access_map(user)
    if access:
        return set(access.keys())
    values = user.get("workspace_ids")
    normalized = _normalize_workspace_ids_claim(values)
    return set(normalized)


def allowed_tenant_ids(user: Optional[Dict[str, Any]]) -> Optional[set[str]]:
    if not isinstance(user, dict):
        return None
    auth_type = str(user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key" and current_user_has_auth_admin_access(user):
        return None
    if bool(user.get("is_admin")) and auth_type not in {"bearer", "local_dev"}:
        return None
    workspace_access = workspace_access_map(user)
    if workspace_access:
        resolved: set[str] = set()
        for workspace_id, entry in workspace_access.items():
            try:
                resolved.add(tenant_id_for_workspace(workspace_id))
            except HTTPException:
                tenant_id = _normalize_tenant_token(
                    entry.get("tenant_id") if isinstance(entry, dict) else None,
                    default="",
                )
                if tenant_id:
                    resolved.add(tenant_id)
        if resolved:
            return resolved
    access = tenant_access_map(user)
    if access:
        return set(access.keys())
    resolved_fallback: set[str] = set()
    for workspace_id in _normalize_workspace_ids_claim(user.get("workspace_ids")):
        try:
            resolved_fallback.add(tenant_id_for_workspace(workspace_id))
        except HTTPException:
            # Orphaned workspace_id claim (workspace no longer exists) --
            # drop it rather than blowing up tenant resolution for the user.
            LOGGER.warning(
                "allowed_tenant_ids: dropping orphaned workspace_id claim '%s' for user_id '%s'.",
                workspace_id,
                str(user.get("user_id") or "").strip(),
            )
            continue
    return resolved_fallback


def workspace_access_map(current_user: Optional[Dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(current_user, dict):
        return {}
    existing = current_user.get("workspace_access")
    if isinstance(existing, dict) and existing:
        out: dict[str, dict[str, Any]] = {}
        for raw_workspace_id, raw_entry in existing.items():
            workspace_id = _normalize_workspace_token(raw_workspace_id, default="")
            if not workspace_id or not isinstance(raw_entry, dict):
                continue
            out[workspace_id] = {
                "workspace_id": workspace_id,
                "tenant_id": _normalize_tenant_token(raw_entry.get("tenant_id")),
                "tenant_role": normalize_rbac_role(raw_entry.get("tenant_role"), default="viewer"),
                "role": normalize_rbac_role(raw_entry.get("role"), default="viewer"),
                "capabilities": _normalize_workspace_capability_policy(raw_entry.get("capabilities")),
                "tenant_capabilities": _normalize_workspace_capability_policy(raw_entry.get("tenant_capabilities")),
                "workspace_capabilities": _normalize_workspace_capability_policy(raw_entry.get("workspace_capabilities")),
                "dangerous_action_classes": _normalize_workspace_dangerous_policy(
                    raw_entry.get("dangerous_action_classes")
                ),
                "tenant_dangerous_action_classes": _normalize_workspace_dangerous_policy(
                    raw_entry.get("tenant_dangerous_action_classes")
                ),
                "workspace_dangerous_action_classes": _normalize_workspace_dangerous_policy(
                    raw_entry.get("workspace_dangerous_action_classes")
                ),
                "connectors": _normalize_connector_permission_policy(raw_entry.get("connectors")),
                "tenant_connectors": _normalize_connector_permission_policy(raw_entry.get("tenant_connectors")),
                "workspace_connectors": _normalize_connector_permission_policy(raw_entry.get("workspace_connectors")),
                "machine_enrollment_scope": _normalize_machine_enrollment_scope(
                    raw_entry.get("machine_enrollment_scope"),
                    default="workspace",
                ),
                "trusted_owner_machine_ids": _normalize_distinct_tokens(
                    raw_entry.get("trusted_owner_machine_ids")
                ),
                "owner_user_id": str(raw_entry.get("owner_user_id") or current_user.get("user_id") or "").strip() or None,
                "owner_email": str(raw_entry.get("owner_email") or current_user.get("email") or "").strip().lower() or None,
            }
        if out:
            return out
    workspace_ids = _normalize_workspace_ids_claim(current_user.get("workspace_ids"))
    return _effective_workspace_access(
        user_id=str(current_user.get("user_id") or "").strip(),
        email=str(current_user.get("email") or "").strip().lower() or None,
        role=current_user_role(current_user),
        auth_type=str(current_user.get("auth_type") or "").strip().lower(),
        is_admin=bool(current_user.get("is_admin")),
        workspace_ids=workspace_ids,
        workspace_roles_claim=current_user.get("workspace_roles"),
        workspace_capabilities_claim=current_user.get("workspace_capabilities"),
        workspace_dangerous_claim=current_user.get("workspace_dangerous_classes"),
        workspace_trusted_machines_claim=current_user.get("workspace_trusted_machines"),
    )


def tenant_access_map(current_user: Optional[Dict[str, Any]]) -> dict[str, dict[str, Any]]:
    workspace_access = workspace_access_map(current_user)
    out: dict[str, dict[str, Any]] = {}
    for workspace_id, entry in workspace_access.items():
        if not isinstance(entry, dict):
            continue
        tenant_id = _normalize_tenant_token(entry.get("tenant_id"), default="")
        if not tenant_id:
            continue
        existing = out.get(tenant_id)
        role = normalize_rbac_role(entry.get("role"), default="viewer")
        if existing is None:
            tenant_policy = load_tenant_policy(tenant_id)
            out[tenant_id] = {
                "tenant_id": tenant_id,
                "role": role,
                "workspace_ids": [workspace_id],
                "capabilities": _normalize_workspace_capability_policy(tenant_policy.get("capabilities")),
                "dangerous_action_classes": _normalize_workspace_dangerous_policy(
                    tenant_policy.get("dangerous_action_classes")
                ),
                "connectors": _normalize_connector_permission_policy(tenant_policy.get("connectors")),
                "machine_enrollment_scope": _normalize_machine_enrollment_scope(
                    tenant_policy.get("machine_enrollment_scope"),
                    default="workspace",
                ),
            }
            continue
        existing["workspace_ids"] = sorted(
            {str(item) for item in list(existing.get("workspace_ids") or []) + [workspace_id] if str(item).strip()}
        )
        if RBAC_ROLE_ORDER[role] > RBAC_ROLE_ORDER[normalize_rbac_role(existing.get("role"), default="viewer")]:
            existing["role"] = role
    return out


def workspace_role(current_user: Optional[Dict[str, Any]], workspace_id: Optional[str]) -> Optional[str]:
    if not isinstance(current_user, dict):
        return None
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key" and current_user_has_auth_admin_access(current_user):
        return "owner"
    if auth_type != "bearer" and bool(current_user.get("is_admin")):
        return "owner"
    token = _resolve_workspace_token_for_current_user(current_user, workspace_id)
    entry = workspace_access_map(current_user).get(token)
    if isinstance(entry, dict):
        return normalize_rbac_role(entry.get("role"), default="viewer")
    return None


def workspace_tenant_id(current_user: Optional[Dict[str, Any]], workspace_id: Optional[str]) -> str:
    token = _require_workspace_token(_resolve_workspace_token_for_current_user(current_user, workspace_id))
    entry = workspace_access_map(current_user).get(token)
    try:
        authoritative_tenant_id = tenant_id_for_workspace(token)
    except HTTPException:
        authoritative_tenant_id = ""
    if authoritative_tenant_id:
        return _require_tenant_token(
            authoritative_tenant_id,
            detail=f"Workspace '{token}' is not bound to a valid tenant.",
        )
    if isinstance(entry, dict):
        return _require_tenant_token(entry.get("tenant_id"), detail=f"Workspace '{token}' is not bound to a valid tenant.")
    return tenant_id_for_workspace(token)


def tenant_role(current_user: Optional[Dict[str, Any]], tenant_id: Optional[str]) -> Optional[str]:
    if not isinstance(current_user, dict):
        return None
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key" and current_user_has_auth_admin_access(current_user):
        return "owner"
    if auth_type != "bearer" and bool(current_user.get("is_admin")):
        return "owner"
    token = _normalize_tenant_token(tenant_id)
    entry = tenant_access_map(current_user).get(token)
    if isinstance(entry, dict):
        return normalize_rbac_role(entry.get("role"), default="viewer")
    return None


def workspace_capability_policy(current_user: Optional[Dict[str, Any]], workspace_id: Optional[str]) -> dict[str, list[str]]:
    token = _resolve_workspace_token_for_current_user(current_user, workspace_id)
    entry = workspace_access_map(current_user).get(token)
    if not isinstance(entry, dict):
        return {"allow": [], "deny": []}
    return _normalize_workspace_capability_policy(entry.get("capabilities"))


def workspace_dangerous_action_policy(current_user: Optional[Dict[str, Any]], workspace_id: Optional[str]) -> dict[str, list[str]]:
    token = _resolve_workspace_token_for_current_user(current_user, workspace_id)
    entry = workspace_access_map(current_user).get(token)
    if not isinstance(entry, dict):
        return {"allow": [], "deny": []}
    return _normalize_workspace_dangerous_policy(entry.get("dangerous_action_classes"))


def workspace_trusted_owner_machine_ids(current_user: Optional[Dict[str, Any]], workspace_id: Optional[str]) -> list[str]:
    token = _resolve_workspace_token_for_current_user(current_user, workspace_id)
    entry = workspace_access_map(current_user).get(token)
    if not isinstance(entry, dict):
        return []
    return _normalize_distinct_tokens(entry.get("trusted_owner_machine_ids"))


def workspace_connector_policy(current_user: Optional[Dict[str, Any]], workspace_id: Optional[str]) -> dict[str, list[str]]:
    token = _resolve_workspace_token_for_current_user(current_user, workspace_id)
    entry = workspace_access_map(current_user).get(token)
    if not isinstance(entry, dict):
        return {"allow": [], "deny": []}
    return _normalize_connector_permission_policy(entry.get("connectors"))


def workspace_machine_enrollment_scope(current_user: Optional[Dict[str, Any]], workspace_id: Optional[str]) -> str:
    token = _resolve_workspace_token_for_current_user(current_user, workspace_id)
    entry = workspace_access_map(current_user).get(token)
    if not isinstance(entry, dict):
        return "workspace"
    return _normalize_machine_enrollment_scope(entry.get("machine_enrollment_scope"), default="workspace")


def workspace_capability_decision(
    current_user: Optional[Dict[str, Any]],
    workspace_id: Optional[str],
    capability_id: Optional[str],
) -> dict[str, Any]:
    clean_capability_id = str(capability_id or "").strip().lower()
    if not clean_capability_id:
        return {"decision": "allow", "reason": "workspace_capability_not_applicable"}
    policy = workspace_capability_policy(current_user, workspace_id)
    denied = set(policy.get("deny") or [])
    allowed = set(policy.get("allow") or [])
    role = workspace_role(current_user, workspace_id)
    if clean_capability_id in denied or WORKSPACE_CAPABILITY_ALL in denied:
        return {"decision": "deny", "reason": "workspace_capability_denied"}
    if allowed and WORKSPACE_CAPABILITY_ALL not in allowed and clean_capability_id not in allowed and role != "owner":
        return {"decision": "deny", "reason": "workspace_capability_not_granted"}
    return {"decision": "allow", "reason": "workspace_capability_allowed"}


def workspace_connector_decision(
    current_user: Optional[Dict[str, Any]],
    workspace_id: Optional[str],
    connector_id: Optional[str],
) -> dict[str, Any]:
    clean_connector_id = str(connector_id or "").strip().lower()
    if not clean_connector_id:
        return {"decision": "allow", "reason": "workspace_connector_not_applicable"}
    policy = workspace_connector_policy(current_user, workspace_id)
    denied = set(policy.get("deny") or [])
    allowed = set(policy.get("allow") or [])
    role = workspace_role(current_user, workspace_id)
    if clean_connector_id in denied or WORKSPACE_CAPABILITY_ALL in denied:
        return {"decision": "deny", "reason": "workspace_connector_denied"}
    if allowed and WORKSPACE_CAPABILITY_ALL not in allowed and clean_connector_id not in allowed and role != "owner":
        return {"decision": "deny", "reason": "workspace_connector_not_granted"}
    return {"decision": "allow", "reason": "workspace_connector_allowed"}


def build_workspace_authorization_metadata(
    current_user: Optional[Dict[str, Any]],
    workspace_id: Optional[str],
    *,
    capability_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    connector_id: Optional[str] = None,
) -> dict[str, Any]:
    token = _resolve_workspace_token_for_current_user(current_user, workspace_id)
    resolved_tenant_id = workspace_tenant_id(current_user, token)
    trusted_owner_machine_ids = workspace_trusted_owner_machine_ids(current_user, token)
    clean_machine_id = str(machine_id or "").strip().lower() or None
    connector_policy = workspace_connector_policy(current_user, token)
    return {
        "tenant_id": resolved_tenant_id,
        "workspace_id": token,
        "policy_scope_precedence": ["global", "tenant", "workspace", "machine", "capability"],
        "tenant_role": tenant_role(current_user, resolved_tenant_id) or workspace_role(current_user, token) or current_user_role(current_user, default="viewer"),
        "workspace_role": workspace_role(current_user, token) or current_user_role(current_user, default="viewer"),
        "tenant_access": tenant_access_map(current_user).get(resolved_tenant_id) or None,
        "workspace_capability_policy": workspace_capability_policy(current_user, token),
        "workspace_dangerous_action_policy": workspace_dangerous_action_policy(current_user, token),
        "workspace_connector_policy": connector_policy,
        "machine_enrollment_scope": workspace_machine_enrollment_scope(current_user, token),
        "computer_action_policy": {
            "allow_dangerous_classes": list(
                workspace_dangerous_action_policy(current_user, token).get("allow") or []
            ),
            "deny_dangerous_classes": list(
                workspace_dangerous_action_policy(current_user, token).get("deny") or []
            ),
            "trusted_owner_machine_ids": trusted_owner_machine_ids,
            "owner_machine_trusted": bool(clean_machine_id and clean_machine_id in set(trusted_owner_machine_ids)),
        },
        "workspace_capability_decision": workspace_capability_decision(
            current_user,
            token,
            capability_id,
        ),
        "workspace_connector_decision": workspace_connector_decision(
            current_user,
            token,
            connector_id,
        ),
    }


def enforce_workspace_access(
    current_user: Optional[Dict[str, Any]],
    workspace_id: Optional[str],
    *,
    tenant_id: Optional[str] = None,
    minimum_role: str = "viewer",
    capability_id: Optional[str] = None,
    connector_id: Optional[str] = None,
) -> str:
    token = _resolve_workspace_token_for_current_user(current_user, workspace_id)
    resolved_tenant_id = workspace_tenant_id(current_user, token)
    requested_tenant_id = _normalize_tenant_token(tenant_id) if tenant_id is not None else resolved_tenant_id
    if requested_tenant_id != resolved_tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Workspace '{token}' is not available inside tenant '{requested_tenant_id}'.",
        )
    allowed_tenants = allowed_tenant_ids(current_user)
    if allowed_tenants is not None and resolved_tenant_id not in allowed_tenants:
        raise HTTPException(status_code=403, detail="Tenant is not accessible for this user.")
    allowed = allowed_workspace_ids(current_user)
    if allowed is None:
        capability_decision = workspace_capability_decision(current_user, token, capability_id)
        if capability_decision["decision"] != "allow":
            raise HTTPException(
                status_code=403,
                detail=f"Capability '{str(capability_id or '').strip()}' is not allowed in workspace '{token}'.",
            )
        connector_decision = workspace_connector_decision(current_user, token, connector_id)
        if connector_decision["decision"] != "allow":
            raise HTTPException(
                status_code=403,
                detail=f"Connector '{str(connector_id or '').strip()}' is not allowed in workspace '{token}'.",
            )
        return token
    if token not in allowed:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    actual_role = workspace_role(current_user, token) or current_user_role(current_user, default="viewer")
    required_role = normalize_rbac_role(minimum_role, default="viewer")
    if RBAC_ROLE_ORDER[actual_role] < RBAC_ROLE_ORDER[required_role]:
        raise HTTPException(
            status_code=403,
            detail=f"{required_role.capitalize()} role required for workspace '{token}'.",
        )
    capability_decision = workspace_capability_decision(current_user, token, capability_id)
    if capability_decision["decision"] != "allow":
        raise HTTPException(
            status_code=403,
            detail=f"Capability '{str(capability_id or '').strip()}' is not allowed in workspace '{token}'.",
        )
    connector_decision = workspace_connector_decision(current_user, token, connector_id)
    if connector_decision["decision"] != "allow":
        raise HTTPException(
            status_code=403,
            detail=f"Connector '{str(connector_id or '').strip()}' is not allowed in workspace '{token}'.",
        )
    return token


def _enforce_window_limit(
    *,
    request: Request,
    buckets: Dict[str, list[float]],
    lock: threading.Lock,
    key: str,
    limit: int,
    profile_name: str,
    actor_id: Optional[str] = None,
) -> None:
    decision = quota_policy_service.evaluate_request_window_quota(
        profile_name=profile_name,
        subject=quota_policy_service.QuotaSubject(
            surface_kind=quota_policy_service.get_quota_policy_profile(profile_name).surface_kind,
            request_path=_request_path(request),
            client_ip=_client_ip(request),
            actor_id=str(actor_id or "").strip() or None,
        ),
        buckets=buckets,
        lock=lock,
        key=key,
        limit=limit,
    )
    if not decision.allowed:
        raise quota_response_service.http_exception_from_quota_decision(decision)


def _client_ip(request: Request) -> str:
    return client_identity_service.resolve_client_ip(request)


def _request_path(request: Request) -> str:
    try:
        path = str(request.url.path or "").strip()
        if path:
            return path
    except Exception:
        pass
    try:
        scope = getattr(request, "scope", None) or {}
        path = str(scope.get("path") or "").strip()
        if path:
            return path
    except Exception:
        pass
    return "/"


def _should_rate_limit_authenticated_api_path(request_path: Any) -> bool:
    normalized = str(request_path or "").strip()
    if not normalized:
        return True
    path = normalized.rstrip("/") or "/"
    if path in {
        "/auth/account-shell",
        "/api/auth/account-shell",
        "/api/v1/auth/account-shell",
        "/sessions",
        "/api/sessions",
    }:
        return False
    return True


def _is_model_invocation_path(request_path: Any) -> bool:
    path = str(request_path or "").strip().rstrip("/") or "/"
    if path in {"/turn", "/api/turn", "/api/sage/chat"}:
        return True
    if path.startswith("/threads/") and path.endswith("/turns"):
        return True
    if path.startswith("/api/threads/") and path.endswith("/turns"):
        return True
    return False


def _authenticated_api_limit_for_channel(channel: Any) -> int:
    normalized = _normalize_auth_session_channel(channel, default="web")
    if normalized == "mobile":
        return max(int(ORION_MOBILE_AUTHENTICATED_API_RATE_LIMIT_PER_MINUTE or 0), 1)
    return max(int(ORION_AUTHENTICATED_API_RATE_LIMIT_PER_MINUTE or 0), 1)


def _model_invocation_limit_for_channel(channel: Any) -> int:
    normalized = _normalize_auth_session_channel(channel, default="web")
    if normalized == "mobile":
        return max(int(ORION_MOBILE_MODEL_INVOCATION_RATE_LIMIT_PER_MINUTE or 0), 1)
    return max(int(ORION_MODEL_INVOCATION_RATE_LIMIT_PER_MINUTE or 0), 1)


def _enforce_model_invocation_rate_limit(
    *,
    request: Request,
    actor_id: str,
    channel: Any,
) -> None:
    _enforce_window_limit(
        request=request,
        buckets=USER_RATE_LIMIT_BUCKETS,
        lock=USER_RATE_LIMIT_LOCK,
        key=f"model:{str(actor_id or '').strip() or 'unknown'}",
        limit=_model_invocation_limit_for_channel(channel),
        profile_name=quota_policy_service.MODEL_INVOCATION_PROFILE.name,
        actor_id=str(actor_id or "").strip() or None,
    )


def limit_login_requests(request: Request) -> None:
    _enforce_window_limit(
        request=request,
        buckets=LOGIN_RATE_LIMIT_BUCKETS,
        lock=LOGIN_RATE_LIMIT_LOCK,
        key=_client_ip(request),
        limit=max(1, int(ORION_AUTH_LOGIN_RATE_LIMIT_PER_MINUTE or 5)),
        profile_name=quota_policy_service.AUTH_LOGIN_PROFILE.name,
    )


def limit_public_requests(request: Request) -> None:
    _enforce_window_limit(
        request=request,
        buckets=USER_RATE_LIMIT_BUCKETS,
        lock=USER_RATE_LIMIT_LOCK,
        key=f"public:{_client_ip(request)}",
        limit=60,
        profile_name=quota_policy_service.AUTH_PUBLIC_REGISTRATION_PROFILE.name,
    )


def limit_refresh_requests(request: Request) -> None:
    _enforce_window_limit(
        request=request,
        buckets=REFRESH_RATE_LIMIT_BUCKETS,
        lock=REFRESH_RATE_LIMIT_LOCK,
        key=f"refresh:{_client_ip(request)}",
        limit=max(1, int(ORION_AUTH_REFRESH_RATE_LIMIT_PER_MINUTE or 10)),
        profile_name=quota_policy_service.AUTH_REFRESH_PROFILE.name,
    )


def limit_email_verification_requests(request: Request) -> None:
    _enforce_window_limit(
        request=request,
        buckets=EMAIL_VERIFICATION_RATE_LIMIT_BUCKETS,
        lock=EMAIL_VERIFICATION_RATE_LIMIT_LOCK,
        key=f"email_verification:{_client_ip(request)}",
        limit=max(1, int(ORION_AUTH_EMAIL_VERIFICATION_RATE_LIMIT_PER_MINUTE or 10)),
        profile_name=quota_policy_service.AUTH_EMAIL_VERIFICATION_PROFILE.name,
    )


def mobile_beta_auto_signin_enabled() -> bool:
    raw = os.getenv("ORION_MOBILE_BETA_AUTO_SIGNIN_ENABLED")
    if raw is None:
        return bool(ORION_MOBILE_BETA_AUTO_SIGNIN_ENABLED)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _mobile_beta_bootstrap_identity(device_id: str) -> tuple[str, str]:
    clean_device_id = str(device_id or "").strip()
    if not clean_device_id:
        raise HTTPException(status_code=400, detail="device_id is required.")
    normalized_device = hashlib.sha256(clean_device_id.encode("utf-8")).hexdigest()[:24]
    email = f"mobile-beta+{normalized_device}@empyralis.local"
    derived_secret = hmac.new(
        _jwt_secret().encode("utf-8"),
        f"mobile-beta:{clean_device_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    password = f"mobile-beta-{derived_secret[:32]}"
    return email, password


def login_mobile_beta_user(
    *,
    device_id: str,
    device_name: Optional[str] = None,
    device_platform: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    if not mobile_beta_auto_signin_enabled():
        raise HTTPException(status_code=403, detail="Mobile beta auto sign-in is disabled.")
    clean_device_id = str(device_id or "").strip()
    if not clean_device_id:
        raise HTTPException(status_code=400, detail="device_id is required.")
    email, password = _mobile_beta_bootstrap_identity(clean_device_id)
    existing_user = _find_user_by_email(email)
    if existing_user is None:
        device_label = str(device_name or "").strip()
        default_name = f"{device_label} owner" if device_label else "Empyralis mobile beta"
        return register_user(
            email,
            password,
            name=default_name,
            channel="mobile",
            device_id=clean_device_id,
            device_name=device_name,
            device_platform=device_platform,
            workspace_id=workspace_id,
            session_ttl_seconds=session_ttl_seconds,
        )
    return login_user(
        email,
        password,
        channel="mobile",
        device_id=clean_device_id,
        device_name=device_name,
        device_platform=device_platform,
        workspace_id=workspace_id,
        session_ttl_seconds=session_ttl_seconds,
    )


def register_user(
    email: str,
    password: str,
    *,
    name: Optional[str] = None,
    acquisition_token: Optional[str] = None,
    channel: Any = "web",
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    device_platform: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    email_token = str(email or "").strip().lower()
    if not email_token or "@" not in email_token:
        raise HTTPException(status_code=400, detail="Valid email is required.")
    if not isinstance(password, str) or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if _find_user_by_email(email_token) is not None:
        raise HTTPException(status_code=409, detail="User already exists.")
    user_id = str(uuid.uuid4())
    created_at = int(time.time())
    user_name = str(name or "").strip() or None
    password_hash = _hash_password(password)
    control_plane_user = _control_plane_call(
        control_plane_repository.create_local_password_account(
            user_id=user_id,
            email=email_token,
            display_name=user_name,
            password_hash=password_hash,
            role="owner",
        )
    )
    control_plane_user_payload = control_plane_user.get("user") if isinstance(control_plane_user, dict) else {}
    control_plane_memberships = control_plane_user.get("memberships") if isinstance(control_plane_user, dict) else []
    if isinstance(control_plane_user_payload, dict) and str(control_plane_user_payload.get("id") or "").strip():
        user_id = str(control_plane_user_payload.get("id") or "").strip()
    workspace_roles = {
        _normalize_workspace_token(item.get("workspace_id")): normalize_rbac_role(item.get("role"), default="owner")
        for item in list(control_plane_memberships or [])
        if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
    }
    tenant_ids = {
        _normalize_tenant_token(item.get("tenant_id"))
        for item in list(control_plane_memberships or [])
        if isinstance(item, dict) and str(item.get("tenant_id") or "").strip()
    }
    if not workspace_roles:
        bootstrap_workspace_id = f"ws_{user_id[:12]}"
        bootstrap_tenant_id = f"tenant_{user_id[:12]}"
        workspace_roles = {bootstrap_workspace_id: "owner"}
        tenant_ids = {bootstrap_tenant_id}
    workspace_ids = list(workspace_roles.keys())
    auth_method_id = _stable_identity_row_id("auth_method", user_id, "password", "empyralis_password", email_token)
    pg = _auth_store_pg(
        auth_store_repository.upsert_many(
            [
                (
                    "user_enterprise_security",
                    {
                        "user_id": user_id, "mfa_enrolled": 0, "mfa_method": None, "mfa_enrolled_at": None,
                        "mfa_last_verified_at": None, "auth_provider": None, "sso_subject": None,
                        "provisioning_source": "local_password", "external_id": None, "last_provisioned_at": None,
                        "updated_at": created_at,
                    },
                ),
                (
                    "user_auth_methods",
                    {
                        "id": auth_method_id, "user_id": user_id, "method_type": "password",
                        "provider": "empyralis_password", "subject": email_token, "label": "Email and password",
                        "status": "active", "is_primary": 1, "can_recover": 1,
                        "metadata_json": json.dumps({"email": email_token, "identity_role": "account_access"}),
                        "created_at": created_at, "updated_at": created_at,
                    },
                ),
                (
                    "user_identity_versions",
                    {
                        "user_id": user_id, "membership_version": 1, "auth_version": 1,
                        "provider_scope_version": 1, "updated_at": created_at,
                    },
                ),
            ]
        )
    )
    if pg is _PG_NA:
        with AUTH_LOCK:
            with _connect_auth_db() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO user_enterprise_security (
                        user_id,
                        mfa_enrolled,
                        mfa_method,
                        mfa_enrolled_at,
                        mfa_last_verified_at,
                        auth_provider,
                        sso_subject,
                        provisioning_source,
                        external_id,
                        last_provisioned_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, 0, None, None, None, None, None, "local_password", None, None, created_at),
                )
                _upsert_user_auth_method_locked(
                    connection,
                    user_id=user_id,
                    method_type="password",
                    provider="empyralis_password",
                    subject=email_token,
                    label="Email and password",
                    is_primary=True,
                    can_recover=True,
                    metadata={
                        "email": email_token,
                        "identity_role": "account_access",
                    },
                    now_ts=created_at,
                )
                _ensure_user_identity_versions_locked(connection, user_id, now_ts=created_at)
                connection.commit()
    user = _find_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=500, detail="Registered user was not persisted.")
    accept_workspace_invites_for_user(
        user_id,
        str(user.get("email") or "").strip().lower() or email_token,
    )
    membership_rows = _list_workspace_memberships(user_id)
    workspace_ids = [
        _normalize_workspace_token(item.get("workspace_id"))
        for item in membership_rows
        if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
    ]
    workspace_roles = {
        _normalize_workspace_token(item.get("workspace_id")): normalize_rbac_role(item.get("role"), default="owner")
        for item in membership_rows
        if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
    }
    workspace_access = _effective_workspace_access(
        user_id=user_id,
        email=str(user.get("email") or "").strip().lower() or email_token,
        role="owner",
        auth_type="bearer",
        is_admin=_has_auth_admin_identity(
            user_id,
            str(user.get("email") or "").strip().lower() or email_token,
        ),
        workspace_ids=workspace_ids,
    )
    payload = _issue_authenticated_user_payload(
        user,
        role="owner",
        workspace_access=workspace_access,
        channel=channel,
        device_id=device_id,
        device_name=device_name,
        device_platform=device_platform,
        workspace_id=workspace_id,
        auth_flow="register",
        session_ttl_seconds=session_ttl_seconds,
    )
    if str(acquisition_token or "").strip():
        try:
            from server_modules import channel_user_acquisition_service

            payload["channel_attribution"] = channel_user_acquisition_service.get_channel_user_acquisition_service().bind_attribution_token_to_user(
                attribution_token=str(acquisition_token or "").strip(),
                user_id=str(user.get("id") or "").strip() or user_id,
                email=str(user.get("email") or "").strip().lower() or email_token,
                auth_flow="register",
            )
        except Exception:
            pass
    # Kick off signup email verification: create + email a 6-digit code.
    # Best-effort by design -- account creation above already succeeded and
    # must not be undone or blocked by email provider latency/misconfig (see
    # docs/design/email-verification-plan.md). A failure here (most likely:
    # EMAIL_PROVIDER_API_KEY unset) is logged loudly, never swallowed
    # silently -- the user can still hit /auth/verify-email/resend once the
    # provider is configured, which surfaces the same failure as a real HTTP
    # error instead of a no-op.
    try:
        run_async_tool_call(
            email_verification_service.start_verification(
                user_id=user_id,
                email=str(user.get("email") or "").strip().lower() or email_token,
            )
        )
    except Exception:
        LOGGER.error(
            "signup_email_verification_start_failed: could not send verification email "
            "for user_id=%s (email provider may be unconfigured -- see "
            "EMAIL_PROVIDER_API_KEY).",
            user_id,
            exc_info=True,
        )
    return payload


def _find_user_id_by_auth_method(provider: str, subject: str) -> Optional[str]:
    clean_provider = _normalize_external_auth_provider(provider)
    clean_subject = str(subject or "").strip()
    if not clean_subject:
        return None
    with AUTH_LOCK:
        with _connect_auth_db() as connection:
            row = connection.execute(
                """
                SELECT user_id
                FROM user_auth_methods
                WHERE provider = ? AND subject = ? AND status = 'active'
                ORDER BY is_primary DESC, created_at ASC
                LIMIT 1
                """,
                (clean_provider, clean_subject),
            ).fetchone()
            if row is not None and str(row["user_id"] or "").strip():
                return str(row["user_id"] or "").strip()
            row = connection.execute(
                """
                SELECT user_id
                FROM user_enterprise_security
                WHERE auth_provider = ? AND sso_subject = ?
                LIMIT 1
                """,
                (clean_provider, clean_subject),
            ).fetchone()
    if row is not None and str(row["user_id"] or "").strip():
        return str(row["user_id"] or "").strip()
    return None


def _login_payload_for_user(
    user: Dict[str, Any],
    *,
    acquisition_token: Optional[str] = None,
    channel: Any = "web",
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    device_platform: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user record.")
    accept_workspace_invites_for_user(
        user_id,
        str(user.get("email") or "").strip().lower(),
    )
    membership_rows = _list_workspace_memberships(user_id)
    workspace_ids = [
        _normalize_workspace_token(item.get("workspace_id"))
        for item in membership_rows
        if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
    ]
    if not workspace_ids:
        raise HTTPException(status_code=403, detail="Authenticated user does not have workspace access.")
    effective_role = "viewer"
    for item in membership_rows:
        candidate_role = normalize_rbac_role((item or {}).get("role"), default="viewer")
        if RBAC_ROLE_ORDER[candidate_role] > RBAC_ROLE_ORDER[effective_role]:
            effective_role = candidate_role
    workspace_access = _effective_workspace_access(
        user_id=user_id,
        email=str(user.get("email") or "").strip().lower() or None,
        role=effective_role,
        auth_type="bearer",
        is_admin=_has_auth_admin_identity(
            user_id,
            str(user.get("email") or "").strip().lower() or None,
        ),
        workspace_ids=workspace_ids,
    )
    payload = _issue_authenticated_user_payload(
        user,
        role=effective_role,
        workspace_access=workspace_access,
        channel=channel,
        device_id=device_id,
        device_name=device_name,
        device_platform=device_platform,
        workspace_id=workspace_id,
        auth_flow="login",
        session_ttl_seconds=session_ttl_seconds,
        enterprise_security=load_user_enterprise_security(user_id),
    )
    if str(acquisition_token or "").strip():
        try:
            from server_modules import channel_user_acquisition_service

            payload["channel_attribution"] = channel_user_acquisition_service.get_channel_user_acquisition_service().bind_attribution_token_to_user(
                attribution_token=str(acquisition_token or "").strip(),
                user_id=user_id,
                email=str(user.get("email") or "").strip().lower() or None,
                auth_flow="login",
            )
        except Exception:
            pass
    return payload


def login_external_user(
    *,
    provider: str,
    subject: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    acquisition_token: Optional[str] = None,
    channel: Any = "mobile",
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    device_platform: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    clean_provider = _normalize_external_auth_provider(provider)
    clean_subject = str(subject or "").strip()
    if not clean_subject:
        raise HTTPException(status_code=400, detail="subject is required.")
    linked_user_id = _find_user_id_by_auth_method(clean_provider, clean_subject)
    user = _find_user_by_id(linked_user_id) if linked_user_id else None
    clean_email = str(email or "").strip().lower() or None
    if user is None and clean_email:
        user = _find_user_by_email(clean_email)
    if user is None and not clean_email:
        raise HTTPException(status_code=400, detail="An email address is required the first time you sign in with this provider.")

    display_name = str(name or (user or {}).get("name") or "").strip() or (
        (clean_email or "").split("@", 1)[0] if clean_email else clean_subject
    )
    membership_rows = _list_workspace_memberships(str(user.get("id") or "").strip()) if isinstance(user, dict) else []
    primary_membership = next(
        (
            item
            for item in membership_rows
            if isinstance(item, dict)
            and str(item.get("workspace_id") or "").strip()
            and str(item.get("tenant_id") or "").strip()
        ),
        None,
    )
    resolved_workspace_id = (
        _normalize_workspace_token((primary_membership or {}).get("workspace_id"), default="")
        or f"ws_{uuid.uuid4().hex[:12]}"
    )
    resolved_tenant_id = (
        _normalize_tenant_token((primary_membership or {}).get("tenant_id"), default="")
        or f"tenant_{uuid.uuid4().hex[:12]}"
    )
    resolved_role = normalize_rbac_role((primary_membership or {}).get("role"), default="owner")
    provision_user_account(
        email=clean_email or str((user or {}).get("email") or "").strip().lower(),
        name=display_name,
        tenant_id=resolved_tenant_id,
        workspace_roles={resolved_workspace_id: resolved_role},
        provisioning_source=f"{clean_provider}_mobile_oauth",
        external_id=clean_subject,
        auth_provider=clean_provider,
        sso_subject=clean_subject,
    )
    user = _find_user_by_id(linked_user_id or str((user or {}).get("id") or "").strip()) or _find_user_by_email(
        clean_email or str((user or {}).get("email") or "").strip().lower()
    )
    if user is None:
        raise HTTPException(status_code=500, detail="Authenticated user could not be loaded.")
    if avatar_url and not str(user.get("avatar_url") or "").strip():
        user["avatar_url"] = str(avatar_url or "").strip() or None
    return _login_payload_for_user(
        user,
        acquisition_token=acquisition_token,
        channel=channel,
        device_id=device_id,
        device_name=device_name,
        device_platform=device_platform,
        workspace_id=workspace_id,
        session_ttl_seconds=session_ttl_seconds,
    )


def login_user(
    email: str,
    password: str,
    *,
    acquisition_token: Optional[str] = None,
    channel: Any = "web",
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    device_platform: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    user = _find_user_by_email(email)
    identity = _control_plane_call(control_plane_repository.get_local_auth_identity_by_email(email))
    password_hash = str((user or {}).get("password_hash") or (identity or {}).get("password_hash") or "")
    if not user and isinstance(identity, dict):
        user = {
            "id": str(identity.get("user_id") or "").strip(),
            "email": str(identity.get("email") or "").strip().lower(),
            "name": str(identity.get("display_name") or "").strip() or None,
            "avatar_url": str(identity.get("avatar_url") or "").strip() or None,
            "tenant_id": str(identity.get("tenant_id") or "").strip() or None,
            "workspace_id": str(identity.get("workspace_id") or "").strip() or None,
            "password_hash": password_hash,
        }
    if not user or not _verify_password(password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return _login_payload_for_user(
        user,
        acquisition_token=acquisition_token,
        channel=channel,
        device_id=device_id,
        device_name=device_name,
        device_platform=device_platform,
        workspace_id=workspace_id,
        session_ttl_seconds=session_ttl_seconds,
    )


def refresh_authenticated_session(
    refresh_token: str,
    *,
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    device_platform: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session_ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    clean_session_id, clean_secret = _decode_auth_session_refresh_token(refresh_token)
    expected_hash = _hash_auth_session_refresh_secret(clean_session_id, clean_secret)

    # Postgres-first, matching issue_auth_session_refresh_token() (which wrote
    # this same row at login) -- this function previously read exclusively via
    # _connect_auth_db()'s local SQLite file, a completely different store
    # than the one login writes to whenever Postgres is configured. Every
    # refresh silently failed to find its own just-issued token as a result:
    # the loop that made this reachable was fixed (SessionRefreshTimer), but
    # the endpoint it calls never worked. get_auth_session_recovery() and
    # get_auth_session() already implement the correct Postgres-first/SQLite-
    # fallback pattern; this just needed to call them instead of querying
    # SQLite directly.
    refresh_record = get_auth_session_recovery(clean_session_id)
    if not refresh_record:
        raise HTTPException(status_code=401, detail="Refresh token is invalid.")
    if str(refresh_record.get("token_hash") or "").strip() != expected_hash:
        raise HTTPException(status_code=401, detail="Refresh token is invalid.")
    if refresh_record.get("revoked_at") is not None:
        raise HTTPException(status_code=401, detail="Refresh token is no longer active.")
    refresh_expires_at = int(refresh_record.get("refresh_expires_at") or 0)
    if refresh_expires_at and refresh_expires_at <= int(time.time()):
        raise HTTPException(status_code=401, detail="Refresh token has expired.")

    session_record = get_auth_session(clean_session_id)
    if not session_record:
        raise HTTPException(status_code=401, detail="Auth session is invalid.")
    if str(session_record.get("status") or "").strip().lower() != "active":
        raise HTTPException(status_code=401, detail="Auth session is no longer active.")

    effective_device_id = str(device_id or session_record.get("device_id") or "").strip() or None
    device_record = None
    if effective_device_id:
        device_record = get_user_device_link(effective_device_id)
        if not device_record:
            raise HTTPException(status_code=401, detail="Refresh token device is not linked.")
        if str(device_record.get("user_id") or "").strip() != str(session_record.get("user_id") or "").strip():
            raise HTTPException(status_code=401, detail="Refresh token device owner mismatch.")
        if str(device_record.get("status") or "").strip().lower() != "active":
            raise HTTPException(status_code=401, detail="Refresh token device is not active.")
        if str(device_record.get("trust_state") or "").strip().lower() == "revoked":
            raise HTTPException(status_code=401, detail="Refresh token device trust was revoked.")
        if bool(device_record.get("session_binding_required")) and str(session_record.get("device_id") or "").strip() != effective_device_id:
            raise HTTPException(status_code=401, detail="Refresh token is not bound to the active device.")

    user_id = str(session_record.get("user_id") or "").strip()
    user = _find_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    membership_rows = _list_workspace_memberships(user_id)
    effective_role = "viewer"
    for item in membership_rows:
        candidate_role = normalize_rbac_role((item or {}).get("role"), default="viewer")
        if RBAC_ROLE_ORDER[candidate_role] > RBAC_ROLE_ORDER[effective_role]:
            effective_role = candidate_role
    workspace_access = _effective_workspace_access(
        user_id=user_id,
        email=str(user.get("email") or "").strip().lower() or None,
        role=effective_role,
        auth_type="bearer",
        is_admin=_has_auth_admin_identity(
            user_id,
            str(user.get("email") or "").strip().lower() or None,
        ),
        workspace_ids=[
            _normalize_workspace_token(item.get("workspace_id"))
            for item in membership_rows
            if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
        ],
    )
    if not workspace_access:
        raise HTTPException(status_code=403, detail="Authenticated user does not have workspace access.")

    resolved_workspace_id = _resolve_authenticated_workspace_id(workspace_id, workspace_access)
    effective_device_id = str(device_id or session_record.get("device_id") or "").strip() or None
    if effective_device_id:
        device_record = upsert_user_device_link(
            user_id,
            device_id=effective_device_id,
            workspace_id=resolved_workspace_id,
            channel=session_record.get("channel") or "mobile",
            display_name=str(device_name or (device_record or {}).get("display_name") or "").strip() or None,
            platform=str(device_platform or (device_record or {}).get("platform") or "").strip() or None,
            trust_state="verified",
            status="active",
            session_binding_required=True,
            metadata={
                **dict((device_record or {}).get("metadata") or {}),
                "auth_flow": "refresh",
                "workspace_id": resolved_workspace_id,
            },
        )

    refresh_channel = session_record.get("channel") or "mobile"
    refreshed_access_ttl_seconds = _resolve_auth_session_ttl_seconds(refresh_channel, session_ttl_seconds)
    refreshed_session_record_ttl_seconds = _resolve_auth_session_record_ttl_seconds(
        refresh_channel,
        refreshed_access_ttl_seconds,
    )
    token = issue_token(
        user_id,
        email=str(user.get("email") or "").strip().lower() or None,
        role=effective_role,
        workspace_access=workspace_access,
        channel=session_record.get("channel") or "mobile",
        device_id=effective_device_id,
        runtime_id=session_record.get("runtime_id"),
        trust_state=(device_record or {}).get("trust_state"),
        session_id=clean_session_id,
        session_family_id=session_record.get("session_family_id") or clean_session_id,
        session_metadata={
            **dict(session_record.get("metadata") or {}),
            "auth_flow": "refresh",
            "workspace_id": resolved_workspace_id,
            "device_name": str(device_name or "").strip() or None,
            "device_platform": str(device_platform or "").strip() or None,
            "workspace_ids": [item.get("workspace_id") for item in workspace_access if isinstance(item, dict)],
            "role": effective_role,
        },
        ttl_seconds=refreshed_access_ttl_seconds,
        session_record_ttl_seconds=refreshed_session_record_ttl_seconds,
    )
    token_payload = _decode_token_payload(token)
    auth_session = get_auth_session(str(token_payload.get("sid") or "").strip())
    session_recovery = issue_auth_session_refresh_token(
        str(auth_session.get("session_id") or "").strip(),
        user_id=user_id,
        ttl_seconds=_resolve_auth_refresh_ttl_seconds(refresh_channel),
    )
    workspace_identity = _workspace_identity_projection(
        workspace_access,
        requested_workspace_id=resolved_workspace_id,
    )
    return _auth_payload_for_user(
        user,
        role=effective_role,
        token=token,
        workspace_access=workspace_access,
        current_workspace_id=workspace_identity.get("current_workspace_id"),
        default_workspace_id=workspace_identity.get("default_workspace_id"),
        current_tenant_id=workspace_identity.get("current_tenant_id"),
        default_tenant_id=workspace_identity.get("default_tenant_id"),
        tenant_access=list(tenant_access_map({"workspace_access": workspace_access}).values()),
        enterprise_security=load_user_enterprise_security(user_id),
        auth_session=auth_session,
        device_link=device_record,
        identity_versions=load_user_identity_versions(user_id),
        session_recovery=session_recovery,
    )


def list_authenticated_user_devices(current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    user_id = _current_bearer_user_id(current_user)
    current_device_id = str((current_user or {}).get("device_id") or ((current_user or {}).get("device_link") or {}).get("device_id") or "").strip() or None
    devices = list_user_device_links(user_id)
    return {
        "ok": True,
        "current_device_id": current_device_id,
        "devices": devices,
    }


def revoke_authenticated_user_device(current_user: Optional[Dict[str, Any]], device_id: str) -> Dict[str, Any]:
    user_id = _current_bearer_user_id(current_user)
    clean_device_id = str(device_id or "").strip()
    if not clean_device_id:
        raise HTTPException(status_code=400, detail="device_id is required.")
    revoked = revoke_user_device_link(user_id, clean_device_id, reason="User revoked the linked device.")
    return {
        "ok": True,
        "current_device_id": str((current_user or {}).get("device_id") or "").strip() or None,
        "revoked_current_device": clean_device_id == str((current_user or {}).get("device_id") or "").strip(),
        "device": revoked,
    }


def logout_authenticated_session(current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    user_id = _current_bearer_user_id(current_user)
    session_id = str(
        ((current_user or {}).get("auth_session") or {}).get("session_id")
        or (current_user or {}).get("session_id")
        or ""
    ).strip()
    revoked_session = revoke_auth_session(session_id, reason="User logged out.") if session_id else {}
    return {
        "ok": True,
        "user_id": user_id,
        "session_id": session_id or None,
        "revoked": bool(revoked_session),
    }


def _current_bearer_user_id(current_user: Optional[Dict[str, Any]]) -> str:
    if not isinstance(current_user, dict):
        raise HTTPException(status_code=401, detail="Authentication required.")
    if str(current_user.get("auth_type") or "").strip() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required.")
    user_id = str(current_user.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authenticated user is missing.")
    return user_id


def get_authenticated_user_record(current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    user = _find_user_by_id(_current_bearer_user_id(current_user))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


def list_authenticated_workspace_memberships(current_user: Optional[Dict[str, Any]]) -> list[dict[str, Any]]:
    user_id = _current_bearer_user_id(current_user)
    return _list_workspace_memberships(user_id)


def get_authenticated_identity_versions(current_user: Optional[Dict[str, Any]]) -> dict[str, Any]:
    if isinstance(current_user, dict) and isinstance(current_user.get("identity_versions"), dict):
        return dict(current_user.get("identity_versions") or {})
    user_id = _current_bearer_user_id(current_user)
    return load_user_identity_versions(user_id)


def get_authenticated_user_profile(current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    user = get_authenticated_user_record(current_user)
    role = current_user_role(current_user)
    resolved_workspace_access = workspace_access_map(current_user)
    workspace_identity = _workspace_identity_projection(
        resolved_workspace_access,
        requested_workspace_id=_normalize_workspace_token((current_user or {}).get("workspace_id"), default=""),
    )
    auth_session = dict(current_user.get("auth_session") or {}) or None
    session_recovery = None
    if auth_session:
        session_recovery = get_auth_session_recovery(str(auth_session.get("session_id") or "").strip()) or None
    return _auth_payload_for_user(
        user,
        role=role,
        workspace_access=list(resolved_workspace_access.values()),
        current_workspace_id=workspace_identity.get("current_workspace_id"),
        default_workspace_id=workspace_identity.get("default_workspace_id"),
        current_tenant_id=workspace_identity.get("current_tenant_id"),
        default_tenant_id=workspace_identity.get("default_tenant_id"),
        tenant_access=list(tenant_access_map(current_user).values()),
        enterprise_security=enterprise_status_for_user(current_user),
        auth_session=auth_session,
        device_link=dict(current_user.get("device_link") or {}) or None,
        identity_versions=get_authenticated_identity_versions(current_user) or None,
        session_recovery=session_recovery,
    )


def update_authenticated_user_profile(
    current_user: Optional[Dict[str, Any]],
    *,
    name: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> Dict[str, Any]:
    user_id = _current_bearer_user_id(current_user)
    next_name = str(name or "").strip() or None
    next_avatar_url = str(avatar_url or "").strip() or None
    updated_user = _control_plane_call(
        control_plane_repository.update_user_profile(
            user_id=user_id,
            display_name=next_name,
            avatar_url=next_avatar_url,
        )
    )
    if not isinstance(updated_user, dict):
        raise HTTPException(status_code=404, detail="User not found.")
    role = current_user_role(current_user)
    return {"ok": True, "user": _public_user_payload(updated_user, role=role)}


def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    request_path = _request_path(request)
    if not _orion_auth_required():
        if _environment_is_production():
            raise HTTPException(status_code=503, detail="Auth cannot be disabled in production.")
        user = _build_local_dev_user()
        if _should_rate_limit_authenticated_api_path(request_path):
            _enforce_window_limit(
                request=request,
                buckets=USER_RATE_LIMIT_BUCKETS,
                lock=USER_RATE_LIMIT_LOCK,
                key=f"user:{str(user.get('user_id') or 'local-dev').strip() or 'local-dev'}",
                limit=_authenticated_api_limit_for_channel(user.get("channel")),
                profile_name=quota_policy_service.AUTHENTICATED_API_PROFILE.name,
                actor_id=str(user.get("user_id") or "").strip() or None,
            )
        if _is_model_invocation_path(request_path):
            _enforce_model_invocation_rate_limit(
                request=request,
                actor_id=str(user.get("user_id") or "").strip() or "local-dev",
                channel=user.get("channel"),
            )
        return user

    auth_header = str(authorization or "").strip()
    bearer_token = ""
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
    else:
        bearer_token = str(auth_cookie_access_token(request) or "").strip()
        if bearer_token:
            validate_csrf(request)

    if bearer_token:
        payload = _decode_token_payload(bearer_token)
        context = _validated_bearer_context(payload, touch_session=True)
        user_id = str(context.get("user_id") or "").strip()
        email = str(context.get("email") or "").strip().lower() or None
        workspace_ids = list(context.get("workspace_ids") or [])
        role = _resolved_bearer_role(user_id, email, payload.get("role"))
        auth_admin = _has_auth_admin_identity(user_id, email)
        workspace_access = _effective_workspace_access(
            user_id=user_id,
            email=email,
            role=role,
            auth_type="bearer",
            is_admin=auth_admin,
            workspace_ids=workspace_ids,
            workspace_roles_claim=payload.get("workspace_roles"),
            workspace_capabilities_claim=payload.get("workspace_capabilities"),
            workspace_dangerous_claim=payload.get("workspace_dangerous_classes"),
            workspace_trusted_machines_claim=payload.get("workspace_trusted_machines"),
        )
        if _should_rate_limit_authenticated_api_path(request_path):
            _enforce_window_limit(
                request=request,
                buckets=USER_RATE_LIMIT_BUCKETS,
                lock=USER_RATE_LIMIT_LOCK,
                key=f"user:{user_id}",
                limit=_authenticated_api_limit_for_channel(
                    payload.get("channel") or (context.get("auth_session") or {}).get("channel")
                ),
                profile_name=quota_policy_service.AUTHENTICATED_API_PROFILE.name,
                actor_id=user_id,
            )
        if _is_model_invocation_path(request_path):
            _enforce_model_invocation_rate_limit(
                request=request,
                actor_id=user_id,
                channel=payload.get("channel") or (context.get("auth_session") or {}).get("channel"),
            )
        return {
            "user_id": user_id,
            "auth_type": "bearer",
            "email": email,
            "tenant_ids": [entry.get("tenant_id") for entry in workspace_access.values() if isinstance(entry, dict)],
            "workspace_ids": workspace_ids,
            "workspace_roles": {
                workspace_id: entry.get("role")
                for workspace_id, entry in workspace_access.items()
                if isinstance(entry, dict)
            },
            "workspace_access": workspace_access,
            "tenant_access": tenant_access_map({"workspace_access": workspace_access}),
            "role": role,
            "is_admin": auth_admin,
            "auth_admin": auth_admin,
            "identity_versions": context.get("identity_versions") or {},
            "auth_session": context.get("auth_session"),
            "device_link": context.get("device_link"),
            "session_id": str((context.get("auth_session") or {}).get("session_id") or payload.get("sid") or "").strip() or None,
            "channel": str(payload.get("channel") or (context.get("auth_session") or {}).get("channel") or "").strip() or None,
            "device_id": str(payload.get("device_id") or (context.get("auth_session") or {}).get("device_id") or "").strip() or None,
            "runtime_id": str(payload.get("runtime_id") or (context.get("auth_session") or {}).get("runtime_id") or "").strip() or None,
        }

    expected_api_key = _orion_api_key()
    provided_api_key = str(x_api_key or "").strip()
    if expected_api_key and provided_api_key and secrets.compare_digest(provided_api_key, expected_api_key):
        if ORION_SERVICE_RATE_LIMIT_PER_MINUTE > 0:
            _enforce_window_limit(
                request=request,
                buckets=USER_RATE_LIMIT_BUCKETS,
                lock=USER_RATE_LIMIT_LOCK,
                key="user:service",
                limit=ORION_SERVICE_RATE_LIMIT_PER_MINUTE,
                profile_name=quota_policy_service.SERVICE_API_PROFILE.name,
                actor_id="service",
            )
        return {
            "user_id": "service",
            "auth_type": "api_key",
            "email": None,
            "role": "service",
            "is_admin": False,
            "auth_admin": False,
            "workspace_ids": [],
            "tenant_ids": [],
        }

    raise HTTPException(status_code=401, detail="Authentication required.")


def resolve_oauth_user_from_state(user_id: str, workspace_id: str) -> Dict[str, Any]:
    """Resolve user for OAuth callback when session cookie is unavailable.

    Safari and other browsers block cross-site cookies on OAuth redirects
    (discord.com → empyralis.ai).  The session cookie is missing so get_current_user
    throws 401.  Instead we look up the user by the user_id that was stored in the
    HMAC-signed OAuth state parameter during start_oauth.
    """
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        raise HTTPException(status_code=401, detail="User identity missing from OAuth state.")
    user = _find_user_by_id(clean_user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    email = str(user.get("email") or "").strip().lower()
    membership_rows = _list_workspace_memberships(clean_user_id)
    workspace_ids = [
        _normalize_workspace_token(item.get("workspace_id"))
        for item in membership_rows
        if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
    ]
    clean_workspace_id = _normalize_workspace_token(workspace_id)
    if clean_workspace_id not in workspace_ids and clean_workspace_id not in ("ws-1", "default"):
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    # Single-user platform — the user IS the owner.
    workspace_access = _effective_workspace_access(
        user_id=clean_user_id,
        email=email,
        role="owner",
        auth_type="bearer",
        is_admin=_has_auth_admin_identity(clean_user_id, email),
        workspace_ids=workspace_ids,
    )
    return {
        "user_id": clean_user_id,
        "auth_type": "bearer",
        "email": email or None,
        "tenant_ids": [entry.get("tenant_id") for entry in workspace_access.values() if isinstance(entry, dict)],
        "workspace_ids": workspace_ids,
        "workspace_roles": {
            wid: entry.get("role")
            for wid, entry in workspace_access.items()
            if isinstance(entry, dict)
        },
        "workspace_access": workspace_access,
        "role": "owner",
        "is_admin": _has_auth_admin_identity(clean_user_id, email),
        "auth_admin": _has_auth_admin_identity(clean_user_id, email),
        "identity_versions": {},
    }


def ensure_public_registration_enabled() -> bool:
    if not public_registration_enabled():
        raise HTTPException(status_code=404, detail="Public registration is disabled.")
    return True


def require_admin_access(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    user = get_current_user(
        request=request,
        authorization=authorization,
        x_api_key=x_api_key,
    )
    if not current_user_has_auth_admin_access(user):
        raise HTTPException(status_code=403, detail="Admin role required.")
    enriched = dict(user)
    enriched["is_admin"] = True
    enriched["auth_admin"] = True
    enriched.setdefault("role", "owner")
    enriched.setdefault("tenant_role", enriched.get("role") or "owner")
    return enriched


def require_member_access(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    user = get_current_user(
        request=request,
        authorization=authorization,
        x_api_key=x_api_key,
    )
    return enforce_minimum_role(user, "member")

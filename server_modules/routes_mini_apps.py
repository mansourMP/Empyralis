from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from server_modules import auth as auth_module
from server_modules import mini_app_host_service
from server_modules import activity_ledger_service
from server_modules import control_plane_repository
from server_modules import credit_ledger_contract
from server_modules import billing_credit_config
from server_modules import calorie_tracking_service
from server_modules import flashcards_tracking_service
from server_modules import mini_app_invoke_service
from server_modules import mini_apps_service
from server_modules import mini_app_token_exchange_service
from server_modules import client_identity_service, quota_policy_service, quota_response_service
from server_modules import usage_accounting_service


router = APIRouter()
get_current_user = auth_module.get_current_user
MINI_APP_BRIDGE_RATE_LIMIT_LOCK = threading.Lock()
MINI_APP_BRIDGE_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
MINI_APP_INVOKE_RATE_LIMIT_LOCK = threading.Lock()
MINI_APP_INVOKE_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
ORION_MINI_APP_BRIDGE_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_MINI_APP_BRIDGE_RATE_LIMIT_PER_MINUTE", "60")
)
ORION_MINI_APP_INVOKE_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("ORION_MINI_APP_INVOKE_RATE_LIMIT_PER_MINUTE", "90")
)
MINI_APP_ACTIVE_SESSION_TTL_SECONDS = int(
    os.getenv("EMPYRALIS_MINI_APP_ACTIVE_SESSION_TTL_SECONDS", str(2 * 60 * 60))
)


def _client_ip(request: Request) -> str:
    return client_identity_service.resolve_client_ip(request)


def _enforce_mini_app_request_limit(
    *,
    request: Request,
    current_user: Optional[Dict[str, Any]],
    profile_name: str,
    buckets: Dict[str, list[float]],
    lock: threading.Lock,
    limit: int,
) -> None:
    workspace_hint = str(
        (request.path_params or {}).get("workspace_id")
        or (request.query_params.get("workspace_id") if request.query_params else "")
        or ""
    ).strip() or "default"
    actor_id = str((current_user or {}).get("user_id") or "").strip() or "anonymous"
    decision = quota_policy_service.evaluate_request_window_quota(
        profile_name=profile_name,
        subject=quota_policy_service.QuotaSubject(
            surface_kind=quota_policy_service.get_quota_policy_profile(profile_name).surface_kind,
            request_path=str(request.url.path or "/"),
            client_ip=_client_ip(request),
            actor_id=actor_id,
            workspace_id=workspace_hint,
        ),
        buckets=buckets,
        lock=lock,
        key=f"{workspace_hint}:{actor_id}",
        limit=max(1, int(limit or 1)),
    )
    if not decision.allowed:
        raise quota_response_service.http_exception_from_quota_decision(decision)


class MiniAppContractUpsertRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2_000)
    icon_url: Optional[str] = Field(default=None, max_length=2_048)
    category: Optional[str] = Field(default=None, max_length=120)
    publisher_id: Optional[str] = Field(default=None, max_length=160)
    publisher_name: Optional[str] = Field(default=None, max_length=240)
    publisher_domain: Optional[str] = Field(default=None, max_length=255)
    support_url: Optional[str] = Field(default=None, max_length=2_048)
    privacy_url: Optional[str] = Field(default=None, max_length=2_048)
    terms_url: Optional[str] = Field(default=None, max_length=2_048)
    runtime_type: Optional[str] = Field(default=None, max_length=80)
    runtime_mode: Optional[str] = Field(default=None, max_length=80)
    destination_url: Optional[str] = Field(default=None, max_length=2_048)
    platform_route: Optional[str] = Field(default=None, max_length=2_048)
    verification_status: Optional[str] = Field(default=None, max_length=80)
    official_claim: Optional[Dict[str, Any]] = None
    domain_proof: Optional[Dict[str, Any]] = None
    delivery_mode: Optional[str] = Field(default=None, max_length=80)
    hosted_url: Optional[str] = Field(default=None, max_length=2_048)
    embed_kind: Optional[str] = Field(default=None, max_length=80)
    allowed_origins: Optional[List[str]] = Field(default=None, max_length=50)
    bridge_contracts: Optional[Dict[str, List[str]]] = None
    permissions: Optional[List[str]] = Field(default=None, max_length=100)
    trust_tier: Optional[str] = Field(default=None, max_length=80)
    background_ai_allowed: Optional[bool] = None
    ai_invoke_policy: Optional[Dict[str, Any]] = None
    context_envelope: Optional[Dict[str, List[str]]] = None
    visibility: Optional[str] = Field(default=None, max_length=80)
    install_status: Optional[str] = Field(default=None, max_length=80)
    current_state: Optional[Dict[str, Any]] = None
    recent_events: Optional[List[Dict[str, Any]]] = None
    daily_summary: Optional[Dict[str, Any]] = None
    weekly_summary: Optional[Dict[str, Any]] = None
    long_term_facts: Optional[List[Any]] = None
    records: Optional[List[Dict[str, Any]]] = None


class MiniAppPublishRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    destination_url: Optional[str] = Field(default=None, max_length=2_048)
    description: Optional[str] = Field(default=None, max_length=2_000)
    icon_url: Optional[str] = Field(default=None, max_length=2_048)


class AppPublishRequest(BaseModel):
    name: str = Field(max_length=120)
    description: Optional[str] = Field(default=None, max_length=2_000)
    source_url: Optional[str] = Field(default=None, max_length=2_048)
    visibility: str = Field(default="private", max_length=40)


class AppSettingsRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2_000)
    source_url: Optional[str] = Field(default=None, max_length=2_048)
    ai_enabled: Optional[bool] = None
    records_enabled: Optional[bool] = None
    sage_enabled: Optional[bool] = None
    visibility: Optional[str] = Field(default=None, max_length=40)
    monthly_credit_limit: Optional[int] = None
    per_invocation_credit_limit: Optional[int] = None


class AppCloneRequest(BaseModel):
    public_app_id: str = Field(min_length=1, max_length=160)


class AppBlueprintProposalRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2_000)
    source_url: Optional[str] = Field(default=None, max_length=2_048)
    proposed_by: str = Field(default="agent", max_length=40)


class AppPublicationDecisionRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1_000)


class MiniAppRetrieveRecordsRequest(BaseModel):
    ids: Optional[List[str]] = None
    kind: Optional[str] = None
    tag: Optional[str] = None
    tags: Optional[List[str]] = None
    since: Optional[str] = None
    until: Optional[str] = None
    text_query: Optional[str] = None
    limit: int = Field(default=mini_apps_service.DEFAULT_RETRIEVE_LIMIT, ge=1, le=mini_apps_service.MAX_RETRIEVE_LIMIT)


class MiniAppActiveSessionRequest(BaseModel):
    active_session_id: Optional[str] = None


class CalorieEventLogRequest(BaseModel):
    id: Optional[str] = None
    meal_label: Optional[str] = None
    calories: Optional[float] = Field(default=None, ge=0)
    protein_g: Optional[float] = Field(default=None, ge=0)
    carbs_g: Optional[float] = Field(default=None, ge=0)
    fat_g: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None
    timestamp: Optional[str] = None
    tags: Optional[List[str]] = None
    explicit_user_intent: bool = False


class CalorieGoalsRequest(BaseModel):
    calorie_goal: Optional[float] = Field(default=None, ge=0)
    protein_goal_g: Optional[float] = Field(default=None, ge=0)
    carbs_goal_g: Optional[float] = Field(default=None, ge=0)
    fat_goal_g: Optional[float] = Field(default=None, ge=0)
    explicit_user_intent: bool = False


class FlashcardCreateRequest(BaseModel):
    id: Optional[str] = None
    deck: Optional[str] = None
    topic: Optional[str] = None
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    difficulty: Optional[str] = None
    tags: Optional[List[str]] = None
    timestamp: Optional[str] = None
    explicit_user_intent: bool = False


class FlashcardDeckMetadataRequest(BaseModel):
    deck: str = Field(min_length=1)
    topic_focus: Optional[str] = None
    language: Optional[str] = None
    target_new_per_day: Optional[int] = Field(default=None, ge=0)
    target_reviews_per_day: Optional[int] = Field(default=None, ge=0)
    tags: Optional[List[str]] = None
    timestamp: Optional[str] = None
    explicit_user_intent: bool = False


class FlashcardReviewRequest(BaseModel):
    id: Optional[str] = None
    card_id: Optional[str] = None
    deck: str = Field(min_length=1)
    topic: Optional[str] = None
    correct: Optional[bool] = None
    quality: Optional[int] = Field(default=None, ge=0, le=5)
    response_ms: Optional[int] = Field(default=None, ge=0)
    tags: Optional[List[str]] = None
    timestamp: Optional[str] = None
    explicit_user_intent: bool = False


class FlashcardRetrieveRequest(BaseModel):
    deck: Optional[str] = None
    topic: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    kind: Optional[str] = None
    limit: int = Field(default=mini_apps_service.DEFAULT_RETRIEVE_LIMIT, ge=1, le=mini_apps_service.MAX_RETRIEVE_LIMIT)


class FlashcardGenerateRequest(BaseModel):
    deck: str = Field(min_length=1, max_length=120)
    source_text: str = Field(min_length=1, max_length=100_000)
    topic: Optional[str] = Field(default=None, max_length=120)
    language: Optional[str] = Field(default=None, max_length=80)
    count: int = Field(default=flashcards_tracking_service.DEFAULT_GENERATED_CARD_COUNT, ge=1, le=flashcards_tracking_service.MAX_GENERATED_CARD_COUNT)
    provider: Optional[str] = None
    model: Optional[str] = None
    active_session_id: Optional[str] = None
    active_session_token: Optional[str] = None
    session_state: Optional[str] = None
    background_invoke: bool = False
    explicit_user_intent: bool = False


class MiniAppInvokeRequest(BaseModel):
    input: Optional[str] = None
    prompt: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    active_session_id: Optional[str] = None
    active_session_token: Optional[str] = None
    session_state: Optional[str] = None
    background_invoke: bool = False


class HostedMiniAppBridgeRequest(BaseModel):
    origin: str = Field(min_length=1)
    bridge_kind: str = Field(min_length=1)
    bridge_type: str = Field(min_length=1)
    request_text: Optional[str] = None
    target: Optional[Dict[str, Any]] = None
    context_envelope: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    launch_token: Optional[str] = None


class MiniAppInstallSharedRequest(BaseModel):
    token: str = Field(min_length=1)


def _current_user_id(current_user: Dict[str, Any]) -> str:
    return str((current_user or {}).get("user_id") or "").strip()


def _current_user_creator_label(current_user: Dict[str, Any]) -> str:
    user = current_user or {}
    for key in ("display_name", "name", "username"):
        value = str(user.get(key) or "").strip()
        if value:
            return value
    email = str(user.get("email") or "").strip()
    if email and "@" in email:
        return f"@{email.split('@', 1)[0]}"
    user_id = str(user.get("user_id") or user.get("id") or "").strip()
    return f"@{user_id}" if user_id else "this workspace"


def _workspace_tenant_id(current_user: Dict[str, Any], workspace_id: str) -> str:
    try:
        return auth_module.workspace_tenant_id(current_user, workspace_id)
    except Exception:
        return str((current_user or {}).get("tenant_id") or "default").strip() or "default"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    padding = "=" * ((4 - len(token) % 4) % 4)
    return base64.urlsafe_b64decode((token + padding).encode("ascii"))


def _active_session_secret() -> bytes:
    secret = os.getenv("EMPYRALIS_MINI_APP_ACTIVE_SESSION_SECRET")
    if not secret:
        environment = str(os.getenv("ORION_ENV") or os.getenv("ENV") or "").strip().lower()
        if environment not in {"", "dev", "development", "local", "test", "testing"}:
            raise RuntimeError("Mini-app active session secret is not configured.")
        secret = "empyralis-mini-app-active-session-local-dev-secret"
    return secret.encode("utf-8")


def _issue_mini_app_active_session_token(
    *,
    workspace_id: str,
    app_id: str,
    user_id: str,
    active_session_id: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    now = int(time.time())
    session_id = str(active_session_id or "").strip() or f"miniapp_{uuid.uuid4().hex[:24]}"
    payload = {
        "workspace_id": str(workspace_id or "").strip(),
        "app_id": str(app_id or "").strip(),
        "user_id": str(user_id or "").strip(),
        "active_session_id": session_id,
        "iat": now,
        "exp": now + max(30, int(ttl_seconds or MINI_APP_ACTIVE_SESSION_TTL_SECONDS)),
    }
    if not payload["workspace_id"] or not payload["app_id"] or not payload["user_id"]:
        raise ValueError("Mini app active session requires workspace, app, and user scope.")
    body = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_active_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return {
        "active_session_id": session_id,
        "active_session_token": f"{body}.{_b64url_encode(signature)}",
        "expires_at": payload["exp"],
    }


def _verify_mini_app_active_session_token(
    token: str,
    *,
    workspace_id: str,
    app_id: str,
    user_id: str,
    active_session_id: str,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    raw_token = str(token or "").strip()
    if not raw_token or "." not in raw_token:
        raise PermissionError("Mini app AI invoke requires a server-issued active session token.")
    body, signature = raw_token.split(".", 1)
    expected = _b64url_encode(hmac.new(_active_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise PermissionError("Mini app active session token signature is invalid.")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise PermissionError("Mini app active session token is malformed.") from exc
    if not isinstance(payload, dict):
        raise PermissionError("Mini app active session token is malformed.")
    if int(payload.get("exp") or 0) <= int(now if now is not None else time.time()):
        raise PermissionError("Mini app active session has expired.")
    expected_values = {
        "workspace_id": workspace_id,
        "app_id": app_id,
        "user_id": user_id,
        "active_session_id": active_session_id,
    }
    for key, expected_value in expected_values.items():
        if str(payload.get(key) or "").strip() != str(expected_value or "").strip():
            raise PermissionError("Mini app active session token scope does not match this invoke request.")
    return payload


def _requires_launch_token(contract: Dict[str, Any]) -> bool:
    return (
        str(contract.get("delivery_mode") or "").strip().lower() == "hosted"
        and bool(str(contract.get("hosted_url") or "").strip())
    )


def _assert_hosted_launch_allowed(
    *,
    contract: Dict[str, Any],
    workspace_id: str,
    app_id: str,
    current_user: Dict[str, Any],
    origin: str,
    launch_token: Optional[str],
) -> Dict[str, Any]:
    if not _requires_launch_token(contract):
        return {}
    return mini_app_host_service.verify_hosted_launch_token(
        str(launch_token or ""),
        workspace_id=workspace_id,
        app_id=str(contract.get("app_id") or app_id),
        user_id=_current_user_id(current_user),
        origin=origin,
    )


def _assert_hosted_bridge_nonce(
    *,
    launch_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    clean_metadata = _coerce_dict(metadata)
    expected_nonce = str(launch_payload.get("bridge_nonce") or "").strip()
    provided_nonce = str(clean_metadata.get("bridge_nonce") or "").strip()
    if not expected_nonce or provided_nonce != expected_nonce:
        raise PermissionError("Hosted mini app bridge nonce is invalid.")
    clean_metadata.pop("bridge_nonce", None)
    clean_metadata["bridge_nonce_verified"] = True
    return clean_metadata


def _assert_mini_app_ai_invoke_allowed(workspace_id: str, app_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        contract = mini_apps_service.get_mini_app_contract(workspace_id, app_id)
    except KeyError as exc:
        raise PermissionError("Mini app AI invoke requires an installed mini-app contract.") from exc
    trust_tier = str(contract.get("trust_tier") or "user_private").strip().lower()
    if trust_tier == "public_untrusted_url":
        raise PermissionError("Public untrusted mini apps cannot use platform AI.")
    permissions = {
        str(item or "").strip().lower()
        for item in list(contract.get("permissions") or [])
        if str(item or "").strip()
    }
    if mini_app_host_service.APP_PERMISSION_AI_INVOKE not in permissions:
        raise PermissionError("Mini app AI invoke requires explicit permission 'app.ai.invoke'.")
    policy = dict(contract.get("ai_invoke_policy") or {}) if isinstance(contract.get("ai_invoke_policy"), dict) else {}
    if bool(policy.get("consent_required", True)) and str(policy.get("consent_status") or "").strip().lower() != "granted":
        raise PermissionError("Mini app AI invoke requires first-run consent before it can spend credits or provider quota.")
    if int(policy.get("monthly_credit_cap") or 0) <= 0:
        raise PermissionError("Mini app AI invoke requires a positive monthly credit cap.")
    if int(policy.get("per_invocation_credit_cap") or 0) <= 0:
        raise PermissionError("Mini app AI invoke requires a positive per-invocation credit cap.")
    payer = str(policy.get("payer") or "platform_credits").strip()
    if not usage_accounting_service.is_platform_paid_usage_value(payer):
        raise PermissionError("Mini app BYOK/local AI routing is not enabled for invoke yet.")
    return contract, policy


def _assert_mini_app_ai_session_active(
    *,
    contract: Dict[str, Any],
    workspace_id: str,
    app_id: str,
    user_id: str,
    active_session_id: Optional[str],
    active_session_token: Optional[str],
    session_state: Optional[str],
    background_invoke: bool = False,
) -> Dict[str, Any]:
    trust_tier = str(contract.get("trust_tier") or "user_private").strip().lower()
    session_token = str(active_session_id or "").strip()
    state_token = str(session_state or "").strip().lower()
    background_allowed = trust_tier == "first_party" and bool(contract.get("background_ai_allowed"))
    if background_invoke:
        if not background_allowed:
            raise PermissionError("Mini app background AI requires an explicit first-party background grant.")
        return {
            "active_session_id": session_token or None,
            "session_state": state_token or "background",
            "background_invoke": True,
            "background_ai_allowed": True,
            "trust_tier": trust_tier,
        }
    if session_token and state_token in {"active", "foreground", "open"}:
        verified_session = _verify_mini_app_active_session_token(
            str(active_session_token or ""),
            workspace_id=workspace_id,
            app_id=str(contract.get("app_id") or app_id),
            user_id=user_id,
            active_session_id=session_token,
        )
        return {
            "active_session_id": session_token,
            "session_state": state_token,
            "background_invoke": False,
            "background_ai_allowed": background_allowed,
            "trust_tier": trust_tier,
            "active_session_expires_at": int(verified_session.get("exp") or 0),
        }
    raise PermissionError("Mini app AI invoke requires an active open app session.")


def _ensure_first_party_mini_app_ai_contract(workspace_id: str, app_id: str) -> Dict[str, Any]:
    try:
        existing = mini_apps_service.get_mini_app_contract(workspace_id, app_id)
    except KeyError:
        return mini_apps_service.upsert_mini_app_contract(
            workspace_id,
            app_id,
            label="Flashcards" if app_id == flashcards_tracking_service.FLASHCARDS_APP_ID else app_id,
            delivery_mode="structured",
            permissions=[mini_app_host_service.APP_PERMISSION_AI_INVOKE],
            trust_tier="first_party",
            background_ai_allowed=False,
            ai_invoke_policy={
                "consent_required": False,
                "consent_status": "granted",
                "payer": "platform_credits",
                "monthly_credit_cap": mini_apps_service.DEFAULT_AI_MONTHLY_CREDIT_CAP,
                "per_invocation_credit_cap": mini_apps_service.DEFAULT_AI_INVOCATION_CREDIT_CAP,
            },
        )
    normalized_app_id = str(existing.get("app_id") or app_id or "").strip().lower()
    if normalized_app_id not in mini_apps_service.FIRST_PARTY_MINI_APP_IDS:
        return existing
    permissions = [
        str(item or "").strip()
        for item in list(existing.get("permissions") or [])
        if str(item or "").strip()
    ]
    permission_tokens = {item.lower() for item in permissions}
    policy = _coerce_dict(existing.get("ai_invoke_policy"))
    consent_status = str(policy.get("consent_status") or "not_granted").strip().lower()
    monthly_cap = _safe_float(policy.get("monthly_credit_cap"))
    per_invocation_cap = _safe_float(policy.get("per_invocation_credit_cap"))
    can_repair_default_policy = (
        mini_app_host_service.APP_PERMISSION_AI_INVOKE not in permission_tokens
        and consent_status != "revoked"
        and monthly_cap > 0
        and per_invocation_cap > 0
    )
    if not can_repair_default_policy:
        return existing
    return mini_apps_service.upsert_mini_app_contract(
        workspace_id,
        normalized_app_id,
        permissions=[*permissions, mini_app_host_service.APP_PERMISSION_AI_INVOKE],
        trust_tier="first_party",
        background_ai_allowed=False,
        ai_invoke_policy={
            **policy,
            "consent_required": False,
            "consent_status": "granted",
            "payer": policy.get("payer") or "platform_credits",
            "monthly_credit_cap": int(monthly_cap),
            "per_invocation_credit_cap": int(per_invocation_cap),
        },
    )


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _current_month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_at_or_after(value: Any, floor_iso: str) -> bool:
    token = str(value or "").strip()
    if not token:
        return True
    try:
        raw = token[:-1] + "+00:00" if token.endswith("Z") else token
        parsed = datetime.fromisoformat(raw).astimezone(timezone.utc)
        floor_raw = floor_iso[:-1] + "+00:00" if floor_iso.endswith("Z") else floor_iso
        floor = datetime.fromisoformat(floor_raw).astimezone(timezone.utc)
        return parsed >= floor
    except Exception:
        return token >= floor_iso


def _mini_app_usage_credits(usage: Any) -> float:
    payload = _coerce_dict(usage)
    accounting = _coerce_dict(payload.get("usage_accounting"))
    source = accounting or payload
    for key in ("retail_credits_charged", "credits_charged", "credit_quantity"):
        if key in source:
            return max(0.0, _safe_float(source.get(key)))
    for key in ("estimated_cost_usd", "provider_cost_usd", "platform_cost_usd"):
        if key in source:
            return billing_credit_config.display_credit_float_for_usd(source.get(key))
    return 0.0


async def _mini_app_monthly_credits_used(
    *,
    tenant_id: str,
    workspace_id: str,
    app_id: str,
) -> float:
    tenant_token = str(tenant_id or "").strip()
    if not tenant_token:
        raise RuntimeError("Mini app AI monthly cap cannot be enforced without tenant scope.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise RuntimeError("Mini app AI monthly cap cannot be enforced without the control-plane ledger.")
    month_start = _current_month_start_iso()
    durable_rows = await control_plane_repository.list_credit_ledger_events(
        tenant_id=tenant_token,
        workspace_id=workspace_id,
        surface="mini_app",
        credit_type="ai_tokens",
        app_id=app_id,
        since_created_at=month_start,
        limit=1000,
    )
    durable_list = list(durable_rows or [])
    if len(durable_list) >= 1000:
        raise RuntimeError("Mini app AI monthly cap cannot be enforced because credit ledger history is truncated.")
    if durable_list:
        return round(sum(max(0.0, _safe_float(row.get("credits_debited"))) for row in durable_list), 6)

    rows = await control_plane_repository.list_workspace_hosted_ai_monthly_cost_ledger_entries(
        tenant_id=tenant_token,
        workspace_id=workspace_id,
        limit=200,
    )
    row_list = list(rows or [])
    if len(row_list) >= 200:
        raise RuntimeError("Mini app AI monthly cap cannot be enforced because ledger history is truncated.")
    total = 0.0
    for row in row_list:
        payload = _coerce_dict(row)
        metadata = _coerce_dict(payload.get("metadata"))
        if str(payload.get("source_surface") or metadata.get("source_surface") or "").strip() != "mini_app_invoke":
            continue
        if str(metadata.get("app_id") or "").strip() != str(app_id or "").strip():
            continue
        completed_at = str(payload.get("completed_at") or payload.get("created_at") or "").strip()
        if completed_at and not _timestamp_at_or_after(completed_at, month_start):
            continue
        total += _mini_app_usage_credits(metadata.get("usage_accounting") or metadata)
    return round(total, 6)


async def _assert_mini_app_ai_budget_available(
    *,
    ai_policy: Dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    app_id: str,
    invocation_credits: float = 0.0,
    include_current_invocation: bool = False,
) -> None:
    monthly_cap = _safe_float(ai_policy.get("monthly_credit_cap"))
    per_invocation_cap = _safe_float(ai_policy.get("per_invocation_credit_cap"))
    if monthly_cap <= 0:
        raise PermissionError("Mini app AI invoke requires a positive monthly credit cap.")
    if per_invocation_cap <= 0:
        raise PermissionError("Mini app AI invoke requires a positive per-invocation credit cap.")
    credits = max(0.0, _safe_float(invocation_credits))
    if include_current_invocation and credits > per_invocation_cap:
        raise PermissionError("Mini app AI invoke exceeded its per-invocation credit cap.")
    used_this_month = await _mini_app_monthly_credits_used(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        app_id=app_id,
    )
    if used_this_month + (credits if include_current_invocation else 0.0) > monthly_cap:
        raise PermissionError("Mini app AI invoke exceeded its monthly credit cap.")


async def _assert_mini_app_ai_budget_preflight(
    *,
    ai_policy: Dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    app_id: str,
) -> None:
    await _assert_mini_app_ai_budget_available(
        ai_policy=ai_policy,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        app_id=app_id,
    )


async def _assert_mini_app_ai_budget_settlement(
    *,
    ai_policy: Dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    app_id: str,
    invocation_credits: float,
) -> None:
    await _assert_mini_app_ai_budget_available(
        ai_policy=ai_policy,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        app_id=app_id,
        invocation_credits=invocation_credits,
        include_current_invocation=True,
    )


def _mini_app_accounting_snapshot(
    *,
    result: Dict[str, Any],
    usage: Dict[str, Any],
    request_id: str,
    tenant_id: str,
    workspace_id: str,
    app_id: str,
    ai_policy: Dict[str, Any],
    timestamp: str,
    session_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session_payload = _coerce_dict(session_metadata)
    base: Dict[str, Any] = {
        "run_id": request_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "app_id": app_id,
        "completed_at": timestamp,
        "context": {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "metadata": {
                "app_id": app_id,
                "request_id": request_id,
                "surface": "mini_app",
                "source_surface": "mini_app_invoke",
                **session_payload,
            },
        },
    }
    policy_provider = str(ai_policy.get("provider") or "").strip()
    policy_model = str(ai_policy.get("model") or "").strip()
    if isinstance(usage.get("usage_accounting"), dict):
        accounting = _coerce_dict(usage.get("usage_accounting"))
        base["usage_accounting"] = {
            **accounting,
            "run_id": request_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "app_id": app_id,
            "surface": "mini_app",
            "source_surface": "mini_app_invoke",
            "effective_provider": result.get("provider") or usage.get("provider") or policy_provider or accounting.get("effective_provider"),
            "effective_model": result.get("model") or usage.get("model") or policy_model or accounting.get("effective_model"),
            "requested_provider": policy_provider or accounting.get("requested_provider"),
            "requested_model": policy_model or accounting.get("requested_model"),
            "payer": ai_policy.get("payer") or accounting.get("payer") or "platform_credits",
            "completed_at": timestamp,
            "metadata": {
                **_coerce_dict(accounting.get("metadata")),
                "app_id": app_id,
                "request_id": request_id,
                "source_surface": "mini_app_invoke",
                **session_payload,
            },
        }
    else:
        base["usage_masked"] = {
            **usage,
            "provider": result.get("provider") or usage.get("provider") or policy_provider,
            "model": result.get("model") or usage.get("model") or policy_model,
            "requested_provider": policy_provider or usage.get("requested_provider"),
            "requested_model": policy_model or usage.get("requested_model"),
            "surface": "mini_app",
            "source_surface": "mini_app_invoke",
            "payer": ai_policy.get("payer") or usage.get("payer") or "platform_credits",
            "timestamp": timestamp,
            "metadata": {
                **_coerce_dict(usage.get("metadata")),
                "app_id": app_id,
                "request_id": request_id,
                "source_surface": "mini_app_invoke",
                **session_payload,
            },
        }
    return base


def _prepare_mini_app_hosted_ai_usage(
    *,
    tenant_id: str,
    workspace_id: str,
    app_id: str,
    ai_policy: Dict[str, Any],
    result: Dict[str, Any],
    session_metadata: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any], float, str, bool]:
    usage = _coerce_dict(result.get("usage"))
    if not usage:
        raise RuntimeError("Mini app hosted AI usage is missing for platform credit accounting.")
    request_id = f"mini_app_{uuid.uuid4().hex[:24]}"
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    snapshot = _mini_app_accounting_snapshot(
        result=result,
        usage=usage,
        request_id=request_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        app_id=app_id,
        ai_policy=ai_policy,
        session_metadata=session_metadata,
        timestamp=timestamp,
    )
    row = usage_accounting_service.usage_row_from_snapshot(snapshot)
    if not isinstance(row, dict):
        raise RuntimeError("Mini app hosted AI usage could not be normalized for platform credit accounting.")
    payer = str(ai_policy.get("payer") or row.get("payer") or "platform_credits").strip()
    platform_paid = usage_accounting_service.is_platform_paid_usage_value(payer)
    if platform_paid:
        validation_error = usage_accounting_service.platform_paid_usage_validation_error(row)
        if validation_error:
            raise RuntimeError(
                "Mini app hosted AI usage is not exact enough for platform credit accounting "
                f"({validation_error})."
            )
        if _safe_float(row.get("retail_credits_charged")) <= 0 and _safe_float(row.get("estimated_cost_usd")) > 0:
            row["retail_credits_charged"] = billing_credit_config.display_credit_float_for_usd(
                row.get("estimated_cost_usd")
            )
    line_item_metadata = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={
            **_coerce_dict(row.get("metadata")),
            "app_id": app_id,
            "surface": "mini_app",
            "source_surface": "mini_app_invoke",
            "billing_source": "empyralis_credits" if platform_paid else payer,
            "public_tier": ai_policy.get("public_tier") or ai_policy.get("model_tier"),
            **_coerce_dict(session_metadata),
        },
        total_tokens=row.get("total_tokens"),
    )
    invocation_credits = max(
        _mini_app_usage_credits(usage),
        _mini_app_usage_credits(row),
    )
    return row, line_item_metadata, invocation_credits, request_id, platform_paid


async def _persist_mini_app_hosted_ai_usage(
    *,
    tenant_id: str,
    workspace_id: str,
    app_id: str,
    ai_policy: Dict[str, Any],
    row: Dict[str, Any],
    line_item_metadata: Dict[str, Any],
    request_id: str,
    platform_paid: bool,
) -> None:
    if not platform_paid:
        return
    payer = str(ai_policy.get("payer") or row.get("payer") or "platform_credits").strip()
    unified_ledger_event = credit_ledger_contract.build_unified_credit_ledger_event(
        surface="mini_app",
        source_surface="mini_app_invoke",
        payer="platform_credits",
        credit_type=line_item_metadata.get("credit_type") or "ai_tokens",
        provider=row.get("provider"),
        model=row.get("model"),
        runtime_target="cloud_default",
        workspace_id=workspace_id,
        thread_id=f"mini-app:{app_id}",
        run_id=request_id,
        app_id=app_id,
        provider_usage=row.get("usage_accounting") if isinstance(row.get("usage_accounting"), dict) else row,
        platform_cost_usd=row.get("estimated_cost_usd"),
        provider_reported_cost=row.get("provider_cost_usd") or row.get("estimated_cost_usd"),
        provider_reported_currency="USD",
        credits_debited=row.get("retail_credits_charged"),
        estimation_mode=row.get("estimation_mode"),
        created_at=row.get("completed_at") or row.get("timestamp"),
    )
    try:
        ledger_entry = await control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            request_id=request_id,
            thread_id=f"mini-app:{app_id}",
            source_surface="mini_app_invoke",
            provider=row.get("provider"),
            model=row.get("model"),
            prompt_tokens=int(row.get("prompt_tokens") or 0),
            completion_tokens=int(row.get("completion_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            estimated_cost_usd=float(row.get("estimated_cost_usd") or 0.0),
            completed_at=row.get("completed_at") or row.get("timestamp"),
            metadata={
                **_coerce_dict(row.get("metadata")),
                **line_item_metadata,
                "app_id": app_id,
                "request_id": request_id,
                "surface": "mini_app",
                "source_surface": "mini_app_invoke",
                "ai_payer": payer,
                "usage_accounting": row,
                "monthly_credit_cap": ai_policy.get("monthly_credit_cap"),
                "per_invocation_credit_cap": ai_policy.get("per_invocation_credit_cap"),
                "unified_credit_ledger_event": unified_ledger_event,
            },
        )
    except Exception as exc:
        raise RuntimeError("Mini app hosted AI usage cost ledger persistence failed.") from exc
    if not isinstance(ledger_entry, dict):
        raise RuntimeError("Mini app hosted AI usage cost ledger persistence failed.")
    try:
        durable_event = await control_plane_repository.record_credit_ledger_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            event=unified_ledger_event,
            source_table="workspace_hosted_ai_monthly_cost_ledger",
            source_event_id=str(ledger_entry.get("id") or request_id),
        )
    except Exception as exc:
        raise RuntimeError("Mini app unified credit ledger persistence failed.") from exc
    if not isinstance(durable_event, dict):
        raise RuntimeError("Mini app unified credit ledger persistence failed.")
    try:
        from server_modules import billing_service

        billing_service.debit_workspace_credit_balance_for_hosted_usage(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            request_id=request_id,
        )
    except Exception as exc:
        raise RuntimeError("Mini app hosted AI credit debit failed.") from exc


def _write_authorization(current_user: Dict[str, Any], *, explicit_user_intent: bool) -> Dict[str, Any]:
    return {
        "explicit_user_intent": bool(explicit_user_intent),
        "actor_user_id": str((current_user or {}).get("user_id") or "").strip() or None,
        "approval_source": "mini_app_route",
    }


@router.get("/mini-apps/share/{share_token}")
async def preview_shared_mini_app(
    share_token: str,
):
    try:
        return mini_apps_service.preview_unlisted_shared_app(share_token)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/apps")
async def list_apps(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    return mini_apps_service.list_apps(resolved_workspace_id)


@router.post("/workspaces/{workspace_id}/apps")
async def publish_app(
    workspace_id: str,
    body: AppPublishRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.publish_app(
            resolved_workspace_id,
            name=body.name,
            description=body.description,
            source_url=body.source_url,
            visibility=body.visibility,
            creator_label=_current_user_creator_label(current_user),
            creator_id=_current_user_id(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/apps/clone")
async def clone_public_app(
    workspace_id: str,
    body: AppCloneRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.clone_public_app(
            resolved_workspace_id,
            public_app_id=body.public_app_id,
            creator_label=_current_user_creator_label(current_user),
            creator_id=_current_user_id(current_user),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/apps/proposals")
async def propose_app_blueprint(
    workspace_id: str,
    body: AppBlueprintProposalRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.propose_app_blueprint(
            resolved_workspace_id,
            name=body.name,
            description=body.description,
            source_url=body.source_url,
            proposed_by=body.proposed_by,
            creator_label=_current_user_creator_label(current_user),
            creator_id=_current_user_id(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/apps/{app_id}/publish-request")
async def request_app_publication(
    workspace_id: str,
    app_id: str,
    body: AppPublicationDecisionRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.request_app_publication(
            resolved_workspace_id,
            app_id,
            actor_label=_current_user_creator_label(current_user),
            actor_id=_current_user_id(current_user),
            note=body.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/apps/{app_id}")
async def get_app(
    workspace_id: str,
    app_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    try:
        return mini_apps_service.get_app(resolved_workspace_id, app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/workspaces/{workspace_id}/apps/{app_id}/settings")
async def update_app_settings(
    workspace_id: str,
    app_id: str,
    body: AppSettingsRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.update_app_settings(
            resolved_workspace_id,
            app_id,
            name=body.name,
            description=body.description,
            source_url=body.source_url,
            ai_enabled=body.ai_enabled,
            records_enabled=body.records_enabled,
            sage_enabled=body.sage_enabled,
            visibility=body.visibility,
            monthly_credit_limit=body.monthly_credit_limit,
            per_invocation_credit_limit=body.per_invocation_credit_limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/mini-apps")
async def list_mini_apps(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    return mini_apps_service.list_mini_app_contracts(resolved_workspace_id)


@router.get("/workspaces/{workspace_id}/mini-apps/{app_id}")
async def get_mini_app_contract(
    workspace_id: str,
    app_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    try:
        return mini_apps_service.get_mini_app_contract(resolved_workspace_id, app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/workspaces/{workspace_id}/mini-apps/{app_id}")
async def upsert_mini_app_contract(
    workspace_id: str,
    app_id: str,
    body: MiniAppContractUpsertRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.upsert_mini_app_contract(
            resolved_workspace_id,
            app_id,
            label=body.label,
            description=body.description,
            icon_url=body.icon_url,
            category=body.category,
            publisher_id=body.publisher_id,
            publisher_name=body.publisher_name,
            publisher_domain=body.publisher_domain,
            support_url=body.support_url,
            privacy_url=body.privacy_url,
            terms_url=body.terms_url,
            runtime_type=body.runtime_type,
            runtime_mode=body.runtime_mode,
            destination_url=body.destination_url,
            platform_route=body.platform_route,
            verification_status=body.verification_status,
            official_claim=body.official_claim,
            domain_proof=body.domain_proof,
            delivery_mode=body.delivery_mode,
            hosted_url=body.hosted_url,
            embed_kind=body.embed_kind,
            allowed_origins=body.allowed_origins,
            bridge_contracts=body.bridge_contracts,
            permissions=body.permissions,
            trust_tier=body.trust_tier,
            background_ai_allowed=body.background_ai_allowed,
            ai_invoke_policy=body.ai_invoke_policy,
            context_envelope=body.context_envelope,
            visibility=body.visibility,
            install_status=body.install_status,
            current_state=body.current_state,
            recent_events=body.recent_events,
            daily_summary=body.daily_summary,
            weekly_summary=body.weekly_summary,
            long_term_facts=body.long_term_facts,
            records=body.records,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/publish")
async def publish_mini_app(
    workspace_id: str,
    body: MiniAppPublishRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.publish_mini_app(
            resolved_workspace_id,
            label=body.label,
            destination_url=body.destination_url,
            description=body.description,
            icon_url=body.icon_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class MiniAppTokenExchangeRequest(BaseModel):
    launch_token: str = Field(min_length=1)
    scopes: Optional[List[str]] = Field(default=None, max_length=20)


@router.post("/workspaces/{workspace_id}/mini-apps/{app_id}/exchange-token")
async def exchange_mini_app_token(
    workspace_id: str,
    app_id: str,
    body: MiniAppTokenExchangeRequest,
    current_user=Depends(get_current_user),
):
    auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    try:
        return mini_app_token_exchange_service.exchange_launch_token(
            workspace_id=workspace_id,
            app_id=app_id,
            launch_token=body.launch_token,
            requested_scopes=body.scopes,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/{app_id}/share-link")
async def generate_mini_app_share_link(
    workspace_id: str,
    app_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.generate_unlisted_share_link(resolved_workspace_id, app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/install-shared")
async def install_shared_mini_app(
    workspace_id: str,
    body: MiniAppInstallSharedRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return mini_apps_service.install_unlisted_shared_app(resolved_workspace_id, body.token)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/{app_id}/records/retrieve")
async def retrieve_mini_app_records(
    workspace_id: str,
    app_id: str,
    body: MiniAppRetrieveRecordsRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    try:
        return mini_apps_service.retrieve_mini_app_records(
            resolved_workspace_id,
            app_id,
            filters=body.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/mini-apps/{app_id}/hosted-manifest")
async def get_hosted_mini_app_manifest(
    workspace_id: str,
    app_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    try:
        return mini_apps_service.get_hosted_mini_app_manifest(
            resolved_workspace_id,
            app_id,
            user_id=_current_user_id(current_user),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/{app_id}/active-session")
async def create_mini_app_active_session(
    workspace_id: str,
    app_id: str,
    body: MiniAppActiveSessionRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    try:
        contract = mini_apps_service.get_mini_app_contract(resolved_workspace_id, app_id)
        return {
            "workspace_id": resolved_workspace_id,
            "app_id": str(contract.get("app_id") or app_id),
            **_issue_mini_app_active_session_token(
                workspace_id=resolved_workspace_id,
                app_id=str(contract.get("app_id") or app_id),
                user_id=_current_user_id(current_user),
                active_session_id=body.active_session_id,
            ),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/{app_id}/bridge/messages")
async def bridge_hosted_mini_app_message(
    workspace_id: str,
    app_id: str,
    body: HostedMiniAppBridgeRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    _enforce_mini_app_request_limit(
        request=request,
        current_user=current_user,
        profile_name=quota_policy_service.MINI_APP_BRIDGE_PROFILE.name,
        buckets=MINI_APP_BRIDGE_RATE_LIMIT_BUCKETS,
        lock=MINI_APP_BRIDGE_RATE_LIMIT_LOCK,
        limit=ORION_MINI_APP_BRIDGE_RATE_LIMIT_PER_MINUTE,
    )
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = _workspace_tenant_id(current_user, resolved_workspace_id)
    try:
        contract = mini_apps_service.get_mini_app_contract(resolved_workspace_id, app_id)
        launch_payload = _assert_hosted_launch_allowed(
            contract=contract,
            workspace_id=resolved_workspace_id,
            app_id=app_id,
            current_user=current_user,
            origin=body.origin,
            launch_token=body.launch_token,
        )
        bridge_metadata = _assert_hosted_bridge_nonce(
            launch_payload=launch_payload,
            metadata=body.metadata,
        ) if _requires_launch_token(contract) else _coerce_dict(body.metadata)
        return await mini_app_host_service.process_hosted_bridge_request(
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            current_user=current_user,
            app_contract=contract,
            origin=body.origin,
            bridge_kind=body.bridge_kind,
            bridge_type=body.bridge_type,
            request_text=str(body.request_text or "").strip(),
            target=body.target,
            context_envelope=body.context_envelope,
            metadata=bridge_metadata,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/{app_id}/invoke")
async def invoke_mini_app(
    workspace_id: str,
    app_id: str,
    body: MiniAppInvokeRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    _enforce_mini_app_request_limit(
        request=request,
        current_user=current_user,
        profile_name=quota_policy_service.MINI_APP_INVOKE_PROFILE.name,
        buckets=MINI_APP_INVOKE_RATE_LIMIT_BUCKETS,
        lock=MINI_APP_INVOKE_RATE_LIMIT_LOCK,
        limit=ORION_MINI_APP_INVOKE_RATE_LIMIT_PER_MINUTE,
    )
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    body_input = str(body.input or body.prompt or "").strip()
    try:
        contract, ai_policy = _assert_mini_app_ai_invoke_allowed(resolved_workspace_id, app_id)
        session_metadata = _assert_mini_app_ai_session_active(
            contract=contract,
            workspace_id=resolved_workspace_id,
            app_id=app_id,
            user_id=_current_user_id(current_user),
            active_session_id=body.active_session_id,
            active_session_token=body.active_session_token,
            session_state=body.session_state,
            background_invoke=body.background_invoke,
        )
        tenant_id = _workspace_tenant_id(current_user, resolved_workspace_id)
        await _assert_mini_app_ai_budget_preflight(
            ai_policy=ai_policy,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            app_id=app_id,
        )
        result = mini_app_invoke_service.invoke_mini_app(
            resolved_workspace_id,
            app_id,
            body_input,
            requested_provider=str(ai_policy.get("provider") or "").strip(),
            requested_model=str(ai_policy.get("model") or "").strip(),
        )
        usage_row, line_item_metadata, invocation_credits, request_id, platform_paid = _prepare_mini_app_hosted_ai_usage(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            app_id=app_id,
            ai_policy=ai_policy,
            result=result,
            session_metadata=session_metadata,
        )
        await _assert_mini_app_ai_budget_settlement(
            ai_policy=ai_policy,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            app_id=app_id,
            invocation_credits=invocation_credits,
        )
        await _persist_mini_app_hosted_ai_usage(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            app_id=app_id,
            ai_policy=ai_policy,
            row=usage_row,
            line_item_metadata=line_item_metadata,
            request_id=request_id,
            platform_paid=platform_paid,
        )
        activity_event = await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            actor_type="mini_app",
            actor_id=str(result.get("app_id") or app_id),
            event_class="application_activity",
            detail_level="feed_summary",
            app_id=str(result.get("app_id") or app_id),
            channel="mini_app",
            direction="outbound",
            action="ai_invoke",
            title=f"{str(result.get('app_id') or app_id)} used AI",
            summary=str(result.get("provider") or "configured provider"),
            status="complete",
            metadata={
                "provider": result.get("provider"),
                "model": result.get("model"),
                "resolved_provider": result.get("provider"),
                "resolved_model": result.get("model"),
                "usage_accounting": usage_row,
                "retail_credits_charged": invocation_credits,
                "credit_type": line_item_metadata.get("credit_type"),
                "credit_item_type": line_item_metadata.get("credit_item_type"),
                "credit_quantity": line_item_metadata.get("quantity"),
                "credit_quantity_unit": line_item_metadata.get("quantity_unit"),
                "credit_multiplier": line_item_metadata.get("credit_multiplier"),
                "attempted_providers": result.get("attempted_providers"),
                **session_metadata,
                "ai_payer": ai_policy.get("payer"),
                "monthly_credit_cap": ai_policy.get("monthly_credit_cap"),
                "per_invocation_credit_cap": ai_policy.get("per_invocation_credit_cap"),
            },
        )
        if not isinstance(activity_event, dict):
            raise RuntimeError("Mini app AI activity history persistence failed.")
        return {
            **result,
            "activity_event_id": str((activity_event or {}).get("id") or "").strip() or None,
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/calorie_tracking/events")
async def log_calorie_event(
    workspace_id: str,
    body: CalorieEventLogRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return calorie_tracking_service.log_calorie_event(
            resolved_workspace_id,
            body.model_dump(exclude_none=True, exclude={"explicit_user_intent"}),
            write_authorization=_write_authorization(
                current_user,
                explicit_user_intent=body.explicit_user_intent,
            ),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.put("/workspaces/{workspace_id}/mini-apps/calorie_tracking/goals")
async def update_calorie_goals(
    workspace_id: str,
    body: CalorieGoalsRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return calorie_tracking_service.update_calorie_goals(
            resolved_workspace_id,
            body.model_dump(exclude_unset=True, exclude_none=False, exclude={"explicit_user_intent"}),
            write_authorization=_write_authorization(
                current_user,
                explicit_user_intent=body.explicit_user_intent,
            ),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/mini-apps/calorie_tracking/overview")
async def calorie_overview(
    workspace_id: str,
    date: Optional[str] = Query(default=None),
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    return calorie_tracking_service.calorie_overview(
        resolved_workspace_id,
        date_filter=date,
    )


@router.post("/workspaces/{workspace_id}/mini-apps/flashcards/cards")
async def create_flashcard(
    workspace_id: str,
    body: FlashcardCreateRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return flashcards_tracking_service.create_flashcard(
            resolved_workspace_id,
            body.model_dump(exclude_none=True, exclude={"explicit_user_intent"}),
            write_authorization=_write_authorization(
                current_user,
                explicit_user_intent=body.explicit_user_intent,
            ),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/flashcards/generate")
async def generate_flashcards(
    workspace_id: str,
    body: FlashcardGenerateRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        _ensure_first_party_mini_app_ai_contract(
            resolved_workspace_id,
            flashcards_tracking_service.FLASHCARDS_APP_ID,
        )
        contract, ai_policy = _assert_mini_app_ai_invoke_allowed(
            resolved_workspace_id,
            flashcards_tracking_service.FLASHCARDS_APP_ID,
        )
        session_metadata = _assert_mini_app_ai_session_active(
            contract=contract,
            workspace_id=resolved_workspace_id,
            app_id=flashcards_tracking_service.FLASHCARDS_APP_ID,
            user_id=_current_user_id(current_user),
            active_session_id=body.active_session_id,
            active_session_token=body.active_session_token,
            session_state=body.session_state,
            background_invoke=body.background_invoke,
        )
        tenant_id = _workspace_tenant_id(current_user, resolved_workspace_id)
        await _assert_mini_app_ai_budget_available(
            ai_policy=ai_policy,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            app_id=flashcards_tracking_service.FLASHCARDS_APP_ID,
        )
        result = flashcards_tracking_service.generate_flashcards(
            resolved_workspace_id,
            deck=body.deck,
            source_text=body.source_text,
            topic=body.topic,
            language=body.language,
            count=body.count,
            requested_provider=str(ai_policy.get("provider") or "").strip(),
            requested_model=str(ai_policy.get("model") or "").strip(),
            write_authorization=_write_authorization(
                current_user,
                explicit_user_intent=body.explicit_user_intent,
            ),
        )
        usage_row, line_item_metadata, invocation_credits, request_id, platform_paid = _prepare_mini_app_hosted_ai_usage(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            app_id=flashcards_tracking_service.FLASHCARDS_APP_ID,
            ai_policy=ai_policy,
            result=result,
            session_metadata=session_metadata,
        )
        await _assert_mini_app_ai_budget_available(
            ai_policy=ai_policy,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            app_id=flashcards_tracking_service.FLASHCARDS_APP_ID,
            invocation_credits=invocation_credits,
            include_current_invocation=True,
        )
        await _persist_mini_app_hosted_ai_usage(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            app_id=flashcards_tracking_service.FLASHCARDS_APP_ID,
            ai_policy=ai_policy,
            row=usage_row,
            line_item_metadata=line_item_metadata,
            request_id=request_id,
            platform_paid=platform_paid,
        )
        activity_event = await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            actor_type="mini_app",
            actor_id=flashcards_tracking_service.FLASHCARDS_APP_ID,
            event_class="application_activity",
            detail_level="feed_summary",
            app_id=flashcards_tracking_service.FLASHCARDS_APP_ID,
            channel="mini_app",
            direction="outbound",
            action="ai_invoke",
            title="flashcards used AI",
            summary=str(result.get("provider") or "configured provider"),
            status="complete",
            metadata={
                "provider": result.get("provider"),
                "model": result.get("model"),
                "resolved_provider": result.get("provider"),
                "resolved_model": result.get("model"),
                "usage_accounting": usage_row,
                "retail_credits_charged": invocation_credits,
                "credit_type": line_item_metadata.get("credit_type"),
                "credit_item_type": line_item_metadata.get("credit_item_type"),
                "credit_quantity": line_item_metadata.get("quantity"),
                "credit_quantity_unit": line_item_metadata.get("quantity_unit"),
                "credit_multiplier": line_item_metadata.get("credit_multiplier"),
                "attempted_providers": result.get("attempted_providers"),
                **session_metadata,
                "ai_payer": ai_policy.get("payer"),
                "monthly_credit_cap": ai_policy.get("monthly_credit_cap"),
                "per_invocation_credit_cap": ai_policy.get("per_invocation_credit_cap"),
            },
        )
        if not isinstance(activity_event, dict):
            raise RuntimeError("Mini app AI activity history persistence failed.")
        return {
            **result,
            "activity_event_id": str((activity_event or {}).get("id") or "").strip() or None,
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/workspaces/{workspace_id}/mini-apps/flashcards/decks")
async def update_flashcard_deck_metadata(
    workspace_id: str,
    body: FlashcardDeckMetadataRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return flashcards_tracking_service.update_deck_metadata(
            resolved_workspace_id,
            body.model_dump(exclude_none=True, exclude={"explicit_user_intent"}),
            write_authorization=_write_authorization(
                current_user,
                explicit_user_intent=body.explicit_user_intent,
            ),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/mini-apps/flashcards/reviews")
async def log_flashcard_review_result(
    workspace_id: str,
    body: FlashcardReviewRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return flashcards_tracking_service.log_review_result(
            resolved_workspace_id,
            body.model_dump(exclude_none=True, exclude={"explicit_user_intent"}),
            write_authorization=_write_authorization(
                current_user,
                explicit_user_intent=body.explicit_user_intent,
            ),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/mini-apps/flashcards/overview")
async def flashcards_overview(
    workspace_id: str,
    date: Optional[str] = Query(default=None),
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    return flashcards_tracking_service.flashcards_overview(
        resolved_workspace_id,
        date_filter=date,
    )


@router.post("/workspaces/{workspace_id}/mini-apps/flashcards/records")
async def retrieve_flashcard_records(
    workspace_id: str,
    body: FlashcardRetrieveRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    try:
        return flashcards_tracking_service.retrieve_flashcard_records(
            resolved_workspace_id,
            deck=body.deck,
            topic=body.topic,
            since=body.since,
            until=body.until,
            kind=body.kind,
            limit=body.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

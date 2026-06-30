from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from server_modules import config_defaults_service
from server_modules import control_plane_repository
from server_modules import rust_runtime_kernel_client
from server_modules import workspace_config_schema
from server_modules.direct_tool_config_service import run_async_tool_call


CHANNEL_ATTRIBUTION_QUERY_PARAM = "channel_attribution"
CHANNEL_ATTRIBUTION_TOKEN_VERSION = "acu1"
DEFAULT_ACQUISITION_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
_START_COMMAND_RE = re.compile(r"^\s*/start(?:@[A-Za-z0-9_]+)?(?:\s+(.+))?\s*$", re.IGNORECASE)


def _utc_now_epoch() -> int:
    return int(time.time())


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("utf-8")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("utf-8"))


def _default_frontend_signup_url() -> str:
    candidates = (
        os.getenv("EMPYRALIS_PUBLIC_FRONTEND_ORIGIN"),
        os.getenv("BACKEND_PUBLIC_ORIGIN"),
        os.getenv("BACKEND_PUBLIC_HOST"),
        os.getenv("FRONTEND_ORIGINS"),
    )
    for raw_candidate in candidates:
        token = str(raw_candidate or "").strip()
        if not token:
            continue
        if "," in token:
            token = token.split(",", 1)[0].strip()
        if token:
            return f"{token.rstrip('/')}/signup"
    return "http://127.0.0.1:3000/signup"


def _append_query_param(url: str, key: str, value: str) -> str:
    split = urlsplit(str(url or "").strip())
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _control_plane_call(coro: Any) -> Any:
    try:
        return run_async_tool_call(coro)
    except Exception:
        return None


def _enforce_acquisition_control_plane_decision(
    *,
    operation: str,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    actor_id: str,
    record_type: str,
    idempotency_key: str,
    source: str = "channel_acquisition",
) -> Dict[str, Any]:
    payload = {
        "operation": operation,
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "actor_id": str(actor_id or "").strip() or "system",
        "actor_role": "system",
        "record_type": record_type,
        "agent_id": str(deployed_agent_id or "").strip(),
        "idempotency_key": str(idempotency_key or "").strip(),
        "source": str(source or "channel_acquisition").strip().lower() or "channel_acquisition",
        "workspace_access": True,
        "owner_access": True,
        "admin_access": True,
        "billing_entitled": True,
        "quota_ok": True,
        "status_transition_valid": True,
        "approval_provided": True,
        "owner_approval_provided": True,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "control-plane-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(exc.reason or "channel_acquisition_denied").strip()
        raise RuntimeError(f"Rust channel acquisition gate blocked {operation}: {reason}") from exc
    next_action = str(
        (decision.get("mutation_plan") if isinstance(decision.get("mutation_plan"), dict) else {}).get("next_action")
        or decision.get("next_action")
        or ""
    ).strip()
    if next_action != "apply_control_plane_write":
        raise RuntimeError(
            "Rust channel acquisition gate returned unexpected next_action for "
            f"{operation}: {next_action or 'missing'}"
        )
    return decision


class ChannelUserAcquisitionService:
    def __init__(
        self,
        *,
        now_epoch: Callable[[], int] = _utc_now_epoch,
        jwt_secret_resolver: Callable[[], str] = lambda: str(__import__("server_modules.jwt_secret", fromlist=["resolve_jwt_secret"]).resolve_jwt_secret() or "").strip(),
        control_plane_runner: Callable[[Any], Any] = _control_plane_call,
        frontend_signup_url_resolver: Callable[[], str] = _default_frontend_signup_url,
        fallback_connection_factory: Optional[Callable[[], sqlite3.Connection]] = None,
    ) -> None:
        self.now_epoch = now_epoch
        self.jwt_secret_resolver = jwt_secret_resolver
        self.control_plane_runner = control_plane_runner
        self.frontend_signup_url_resolver = frontend_signup_url_resolver
        self.fallback_connection_factory = fallback_connection_factory or self._default_fallback_connection

    def _default_fallback_connection(self) -> sqlite3.Connection:
        from server_modules import auth as auth_module

        connection = auth_module._connect_auth_db()
        self._ensure_fallback_tables(connection)
        return connection

    def _ensure_fallback_tables(self, connection: sqlite3.Connection) -> None:
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
                limit_hit_click_at INTEGER,
                limit_hit_click_source TEXT,
                first_started_at INTEGER NOT NULL,
                last_started_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channel_user_acquisition_touches_deployment
            ON channel_user_acquisition_touches(tenant_id, workspace_id, deployed_agent_id, last_started_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channel_user_acquisition_touches_conversion
            ON channel_user_acquisition_touches(tenant_id, workspace_id, converted_user_id, converted_at DESC)
            """
        )
        connection.commit()

    def public_start_enabled(self, profile: Dict[str, Any]) -> bool:
        config = self._public_start_config(profile)
        return bool(config.get("enabled"))

    def start_payload(self, message_text: Any) -> Optional[str]:
        match = _START_COMMAND_RE.match(str(message_text or "").strip())
        if not match:
            return None
        payload = _normalize_text(match.group(1))
        return payload or None

    def prepare_public_start_response(
        self,
        *,
        profile: Dict[str, Any],
        workspace_id: str,
        external_user_id: str,
        sender_username: Optional[str] = None,
        sender_display_name: Optional[str] = None,
        campaign_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._public_start_config(profile)
        if not bool(config.get("enabled")):
            raise ValueError("public start is not enabled for this Telegram profile.")
        touch = self._upsert_touch(
            tenant_id=str(config.get("tenant_id") or "").strip(),
            workspace_id=str(config.get("workspace_id") or workspace_id).strip() or workspace_id,
            deployed_agent_id=str(config.get("deployed_agent_id") or "").strip(),
            channel_key="telegram",
            endpoint_key=str(config.get("endpoint_key") or "").strip(),
            external_user_id=str(external_user_id or "").strip(),
            source=str(config.get("source") or "telegram_start").strip().lower(),
            campaign_token=str(campaign_token or "").strip() or None,
            metadata={
                "sender_username": _normalize_text(sender_username) or None,
                "sender_display_name": _normalize_text(sender_display_name) or None,
                "deployed_agent_name": str(config.get("deployed_agent_name") or "").strip() or None,
            },
        )
        if not isinstance(touch, dict) or not str(touch.get("id") or "").strip():
            raise RuntimeError("Failed to persist channel acquisition touch.")
        attribution_token = self._issue_attribution_token(
            touch_id=str(touch.get("id") or "").strip(),
            tenant_id=str(touch.get("tenant_id") or config.get("tenant_id") or "").strip(),
            workspace_id=str(touch.get("workspace_id") or workspace_id).strip() or workspace_id,
            deployed_agent_id=str(touch.get("deployed_agent_id") or config.get("deployed_agent_id") or "").strip(),
            channel_key="telegram",
            external_user_id=str(touch.get("external_user_id") or external_user_id).strip(),
            source=str(touch.get("source") or config.get("source") or "telegram_start").strip(),
            campaign_token=str(campaign_token or touch.get("campaign_token") or "").strip() or None,
        )
        cta_base_url = str(config.get("cta_base_url") or self.frontend_signup_url_resolver()).strip()
        cta_label = str(config.get("cta_label") or "Continue on Empyralist").strip() or "Continue on Empyralist"
        cta_url = _append_query_param(cta_base_url, CHANNEL_ATTRIBUTION_QUERY_PARAM, attribution_token)
        deployed_agent_name = str(config.get("deployed_agent_name") or "Empyralis Agent").strip() or "Empyralis Agent"
        intro = str(
            config.get("intro")
            or f"{deployed_agent_name} is ready to help on Telegram."
        ).strip()
        core_value = str(
            config.get("core_value")
            or "Continue on Empyralist to save your progress, unlock the full experience, and keep going on web or mobile."
        ).strip()
        text = f"{deployed_agent_name}\n\n{intro}\n\n{core_value}"
        return {
            "touch": touch,
            "attribution_token": attribution_token,
            "cta_label": cta_label,
            "cta_url": cta_url,
            "text": text,
            "reply_markup": {
                "inline_keyboard": [[{"text": cta_label, "url": cta_url}]],
            },
        }

    def bind_attribution_token_to_user(
        self,
        *,
        attribution_token: str,
        user_id: str,
        email: Optional[str],
        auth_flow: str,
    ) -> Optional[Dict[str, Any]]:
        payload = self._decode_attribution_token(attribution_token)
        touch_id = str(payload.get("touch_id") or "").strip()
        tenant_id = str(payload.get("tenant_id") or "").strip()
        workspace_id = str(payload.get("workspace_id") or "").strip()
        if not touch_id or not tenant_id or not workspace_id:
            raise ValueError("Channel attribution token is incomplete.")
        touch = self._mark_touch_converted(
            touch_id=touch_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            converted_user_id=str(user_id or "").strip(),
            converted_email=str(email or "").strip().lower() or None,
            auth_flow=str(auth_flow or "").strip().lower() or None,
        )
        return touch

    def touch_from_attribution_token(self, attribution_token: str) -> Optional[Dict[str, Any]]:
        payload = self._decode_attribution_token(attribution_token)
        touch_id = str(payload.get("touch_id") or "").strip()
        tenant_id = str(payload.get("tenant_id") or "").strip()
        workspace_id = str(payload.get("workspace_id") or "").strip()
        if not touch_id:
            return None
        touch = self.control_plane_runner(
            control_plane_repository.get_channel_user_acquisition_touch_by_id(
                touch_id,
                tenant_id=tenant_id or None,
                workspace_id=workspace_id or None,
            )
        )
        if isinstance(touch, dict):
            return touch
        return self._get_touch_fallback(touch_id=touch_id)

    def decode_attribution_token_payload(self, attribution_token: str) -> Dict[str, Any]:
        return self._decode_attribution_token(attribution_token)

    def resolve_or_create_touch_from_attribution_token(self, attribution_token: str) -> Optional[Dict[str, Any]]:
        payload = self._decode_attribution_token(attribution_token)
        existing = self.touch_from_attribution_token(attribution_token)
        if isinstance(existing, dict):
            return existing
        return self._upsert_touch(
            tenant_id=str(payload.get("tenant_id") or "").strip(),
            workspace_id=str(payload.get("workspace_id") or "").strip(),
            deployed_agent_id=str(payload.get("deployed_agent_id") or "").strip(),
            channel_key=str(payload.get("channel_key") or "telegram").strip().lower() or "telegram",
            endpoint_key="",
            external_user_id=str(payload.get("external_user_id") or "").strip(),
            source=str(payload.get("source") or "").strip().lower() or None,
            campaign_token=str(payload.get("campaign_token") or "").strip() or None,
            metadata={
                "restored_from_attribution": True,
            },
        )

    def ensure_touch_attribution_token(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        deployed_agent_id: str,
        channel_key: str,
        external_user_id: str,
        endpoint_key: Optional[str] = None,
        source: Optional[str] = None,
        campaign_token: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_tenant_id = str(tenant_id or "").strip()
        resolved_workspace_id = str(workspace_id or "").strip()
        resolved_deployed_agent_id = str(deployed_agent_id or "").strip()
        resolved_channel_key = str(channel_key or "").strip().lower() or "telegram"
        resolved_external_user_id = str(external_user_id or "").strip()
        if not resolved_tenant_id or not resolved_workspace_id or not resolved_deployed_agent_id or not resolved_external_user_id:
            raise ValueError("tenant_id, workspace_id, deployed_agent_id, channel_key, and external_user_id are required.")
        touch = self.control_plane_runner(
            control_plane_repository.get_channel_user_acquisition_touch(
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                deployed_agent_id=resolved_deployed_agent_id,
                channel_key=resolved_channel_key,
                external_user_id=resolved_external_user_id,
            )
        )
        if not isinstance(touch, dict):
            touch = self._get_touch_fallback_by_scope(
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                deployed_agent_id=resolved_deployed_agent_id,
                channel_key=resolved_channel_key,
                external_user_id=resolved_external_user_id,
            )
        if not isinstance(touch, dict):
            touch = self._upsert_touch(
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                deployed_agent_id=resolved_deployed_agent_id,
                channel_key=resolved_channel_key,
                endpoint_key=str(endpoint_key or "").strip().lower(),
                external_user_id=resolved_external_user_id,
                source=str(source or "").strip().lower() or None,
                campaign_token=str(campaign_token or "").strip() or None,
                metadata=metadata,
            )
        if not isinstance(touch, dict):
            raise RuntimeError("Failed to persist channel acquisition touch.")
        attribution_token = self._issue_attribution_token(
            touch_id=str(touch.get("id") or "").strip(),
            tenant_id=str(touch.get("tenant_id") or resolved_tenant_id).strip(),
            workspace_id=str(touch.get("workspace_id") or resolved_workspace_id).strip(),
            deployed_agent_id=str(touch.get("deployed_agent_id") or resolved_deployed_agent_id).strip(),
            channel_key=str(touch.get("channel_key") or resolved_channel_key).strip().lower() or resolved_channel_key,
            external_user_id=str(touch.get("external_user_id") or resolved_external_user_id).strip(),
            source=str(touch.get("source") or source or "").strip().lower(),
            campaign_token=str(touch.get("campaign_token") or campaign_token or "").strip() or None,
        )
        return {
            "touch": touch,
            "attribution_token": attribution_token,
        }

    def _public_start_config(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        payload = _coerce_dict(profile)
        nested = payload.get("public_start")
        config = _coerce_dict(nested)
        if "deployed_agent_id" not in config:
            config["deployed_agent_id"] = payload.get("deployed_agent_id")
        if "tenant_id" not in config:
            config["tenant_id"] = payload.get("tenant_id")
        if "workspace_id" not in config:
            config["workspace_id"] = payload.get("workspace_id")
        if "endpoint_key" not in config:
            config["endpoint_key"] = payload.get("endpoint_key") or payload.get("id")
        if "deployed_agent_name" not in config:
            config["deployed_agent_name"] = payload.get("deployed_agent_name") or payload.get("label")
        if "intro" not in config:
            config["intro"] = payload.get("public_intro")
        if "core_value" not in config:
            config["core_value"] = payload.get("public_core_value") or payload.get("description")
        if "cta_label" not in config:
            config["cta_label"] = payload.get("platform_cta_label")
        if "cta_base_url" not in config:
            config["cta_base_url"] = payload.get("platform_cta_url")
        if "source" not in config:
            config["source"] = payload.get("public_source")
        config["deployed_agent_id"] = _normalize_text(config.get("deployed_agent_id"))
        config["tenant_id"] = _normalize_text(config.get("tenant_id"))
        config["workspace_id"] = _normalize_text(config.get("workspace_id"))
        config["endpoint_key"] = _normalize_token(config.get("endpoint_key"))
        config["deployed_agent_name"] = _normalize_text(config.get("deployed_agent_name")) or "Empyralis Agent"
        config["intro"] = _normalize_text(config.get("intro"))
        config["core_value"] = _normalize_text(config.get("core_value"))
        workspace_defaults = self._workspace_admin_defaults(config.get("workspace_id"))
        config["cta_label"] = (
            _normalize_text(config.get("cta_label"))
            or _normalize_text(workspace_defaults.public_start_cta_label)
            or config_defaults_service.default_public_start_cta_label()
        )
        config["cta_base_url"] = (
            _normalize_text(config.get("cta_base_url"))
            or _normalize_text(workspace_defaults.public_start_cta_url)
            or self.frontend_signup_url_resolver()
        )
        config["source"] = _normalize_token(config.get("source")) or "telegram_start"
        config["enabled"] = bool(config.get("enabled")) or bool(config.get("deployed_agent_id"))
        return config

    def _workspace_admin_defaults(self, workspace_id: Any) -> workspace_config_schema.WorkspaceAdminDefaultsConfig:
        clean_workspace_id = _normalize_text(workspace_id)
        if not clean_workspace_id:
            return workspace_config_schema.workspace_admin_defaults_from_metadata({})
        workspace = self.control_plane_runner(control_plane_repository.get_workspace_by_id(clean_workspace_id))
        metadata = _coerce_dict((workspace or {}).get("metadata")) if isinstance(workspace, dict) else {}
        return workspace_config_schema.workspace_admin_defaults_from_metadata(metadata)

    def _issue_attribution_token(
        self,
        *,
        touch_id: str,
        tenant_id: str,
        workspace_id: str,
        deployed_agent_id: str,
        channel_key: str,
        external_user_id: str,
        source: str,
        campaign_token: Optional[str],
    ) -> str:
        secret = str(self.jwt_secret_resolver() or "").strip()
        if not secret:
            raise RuntimeError("JWT secret is not configured.")
        now_ts = self.now_epoch()
        payload = {
            "v": 1,
            "touch_id": touch_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "deployed_agent_id": deployed_agent_id,
            "channel_key": str(channel_key or "").strip().lower(),
            "external_user_id": external_user_id,
            "source": str(source or "").strip().lower(),
            "campaign_token": str(campaign_token or "").strip() or None,
            "iat": now_ts,
            "exp": now_ts + DEFAULT_ACQUISITION_TOKEN_TTL_SECONDS,
        }
        body = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = _b64url_encode(hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest())
        return f"{CHANNEL_ATTRIBUTION_TOKEN_VERSION}.{body}.{signature}"

    def _decode_attribution_token(self, attribution_token: str) -> Dict[str, Any]:
        token = str(attribution_token or "").strip()
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != CHANNEL_ATTRIBUTION_TOKEN_VERSION:
            raise ValueError("Channel attribution token is invalid.")
        secret = str(self.jwt_secret_resolver() or "").strip()
        if not secret:
            raise RuntimeError("JWT secret is not configured.")
        encoded_payload = parts[1]
        expected_signature = _b64url_encode(
            hmac.new(secret.encode("utf-8"), encoded_payload.encode("utf-8"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(parts[2], expected_signature):
            raise ValueError("Channel attribution token signature is invalid.")
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Channel attribution token payload is invalid.")
        expires_at = int(payload.get("exp") or 0)
        if expires_at and expires_at < self.now_epoch():
            raise ValueError("Channel attribution token has expired.")
        return payload

    def _upsert_touch(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        deployed_agent_id: str,
        channel_key: str,
        endpoint_key: str,
        external_user_id: str,
        source: Optional[str],
        campaign_token: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        resolved_tenant_id = str(tenant_id or "").strip()
        resolved_workspace_id = str(workspace_id or "").strip()
        resolved_deployed_agent_id = str(deployed_agent_id or "").strip()
        if not resolved_tenant_id or not resolved_workspace_id or not resolved_deployed_agent_id:
            raise ValueError("tenant_id, workspace_id, and deployed_agent_id are required.")
        resolved_channel_key = str(channel_key or "").strip().lower() or "telegram"
        resolved_external_user_id = str(external_user_id or "").strip()
        _enforce_acquisition_control_plane_decision(
            operation="channel_user_acquisition_touch_write",
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent_id=resolved_deployed_agent_id,
            actor_id=resolved_external_user_id,
            record_type="channel_user_acquisition_touch",
            idempotency_key=f"{resolved_deployed_agent_id}:{resolved_channel_key}:{resolved_external_user_id}:touch",
            source=str(source or "channel_acquisition").strip().lower() or "channel_acquisition",
        )
        touch = self.control_plane_runner(
            control_plane_repository.upsert_channel_user_acquisition_touch(
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                deployed_agent_id=resolved_deployed_agent_id,
                channel_key=resolved_channel_key,
                endpoint_key=str(endpoint_key or "").strip().lower() or None,
                external_user_id=resolved_external_user_id,
                source=str(source or "").strip().lower() or None,
                campaign_token=str(campaign_token or "").strip() or None,
                metadata=metadata,
            )
        )
        if isinstance(touch, dict):
            return touch
        return self._upsert_touch_fallback(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent_id=resolved_deployed_agent_id,
            channel_key=resolved_channel_key,
            endpoint_key=str(endpoint_key or "").strip().lower(),
            external_user_id=resolved_external_user_id,
            source=str(source or "").strip().lower(),
            campaign_token=str(campaign_token or "").strip(),
            metadata=metadata,
        )

    def _mark_touch_converted(
        self,
        *,
        touch_id: str,
        tenant_id: str,
        workspace_id: str,
        converted_user_id: str,
        converted_email: Optional[str],
        auth_flow: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        _enforce_acquisition_control_plane_decision(
            operation="channel_user_acquisition_conversion_write",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id="unknown_agent",
            actor_id=converted_user_id,
            record_type="channel_user_acquisition_touch",
            idempotency_key=f"{touch_id}:conversion:{converted_user_id}",
            source="channel_acquisition",
        )
        touch = self.control_plane_runner(
            control_plane_repository.mark_channel_user_acquisition_touch_converted(
                touch_id=touch_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                converted_user_id=converted_user_id,
                converted_email=converted_email,
                auth_flow=auth_flow,
            )
        )
        if isinstance(touch, dict):
            return touch
        return self._mark_touch_converted_fallback(
            touch_id=touch_id,
            converted_user_id=converted_user_id,
            converted_email=converted_email,
            auth_flow=auth_flow,
        )

    def _upsert_touch_fallback(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        deployed_agent_id: str,
        channel_key: str,
        endpoint_key: str,
        external_user_id: str,
        source: str,
        campaign_token: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        with self.fallback_connection_factory() as connection:
            self._ensure_fallback_tables(connection)
            now_ts = self.now_epoch()
            touch_id = f"acq_{uuid.uuid4().hex[:16]}"
            connection.execute(
                """
                INSERT INTO channel_user_acquisition_touches (
                    id, tenant_id, workspace_id, deployed_agent_id, channel_key, endpoint_key,
                    external_user_id, source, campaign_token, start_count, metadata_json,
                    first_started_at, last_started_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
                DO UPDATE SET
                    endpoint_key = CASE WHEN excluded.endpoint_key <> '' THEN excluded.endpoint_key ELSE channel_user_acquisition_touches.endpoint_key END,
                    source = CASE WHEN excluded.source <> '' THEN excluded.source ELSE channel_user_acquisition_touches.source END,
                    campaign_token = CASE WHEN excluded.campaign_token <> '' THEN excluded.campaign_token ELSE channel_user_acquisition_touches.campaign_token END,
                    start_count = channel_user_acquisition_touches.start_count + 1,
                    metadata_json = CASE
                        WHEN excluded.metadata_json <> '{}' THEN excluded.metadata_json
                        ELSE channel_user_acquisition_touches.metadata_json
                    END,
                    last_started_at = excluded.last_started_at,
                    updated_at = excluded.updated_at
                """,
                (
                    touch_id,
                    tenant_id,
                    workspace_id,
                    deployed_agent_id,
                    channel_key,
                    endpoint_key,
                    external_user_id,
                    source,
                    campaign_token,
                    json.dumps(_coerce_dict(metadata), sort_keys=True),
                    now_ts,
                    now_ts,
                    now_ts,
                    now_ts,
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT *
                FROM channel_user_acquisition_touches
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND deployed_agent_id = ?
                  AND channel_key = ?
                  AND external_user_id = ?
                LIMIT 1
                """,
                (tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id),
            ).fetchone()
            return self._fallback_row_to_touch(row)

    def _get_touch_fallback(self, *, touch_id: str) -> Optional[Dict[str, Any]]:
        with self.fallback_connection_factory() as connection:
            self._ensure_fallback_tables(connection)
            row = connection.execute(
                """
                SELECT *
                FROM channel_user_acquisition_touches
                WHERE id = ?
                LIMIT 1
                """,
                (touch_id,),
            ).fetchone()
            return self._fallback_row_to_touch(row)

    def _get_touch_fallback_by_scope(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        deployed_agent_id: str,
        channel_key: str,
        external_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self.fallback_connection_factory() as connection:
            self._ensure_fallback_tables(connection)
            row = connection.execute(
                """
                SELECT *
                FROM channel_user_acquisition_touches
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND deployed_agent_id = ?
                  AND channel_key = ?
                  AND external_user_id = ?
                LIMIT 1
                """,
                (tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id),
            ).fetchone()
            return self._fallback_row_to_touch(row)

    def _mark_touch_converted_fallback(
        self,
        *,
        touch_id: str,
        converted_user_id: str,
        converted_email: Optional[str],
        auth_flow: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        with self.fallback_connection_factory() as connection:
            self._ensure_fallback_tables(connection)
            now_ts = self.now_epoch()
            connection.execute(
                """
                UPDATE channel_user_acquisition_touches
                SET
                    converted_user_id = CASE
                        WHEN converted_user_id IS NULL OR converted_user_id = ? THEN ?
                        ELSE converted_user_id
                    END,
                    converted_email = CASE
                        WHEN converted_user_id IS NULL OR converted_user_id = ? THEN COALESCE(?, converted_email)
                        ELSE converted_email
                    END,
                    converted_auth_flow = CASE
                        WHEN converted_user_id IS NULL OR converted_user_id = ? THEN COALESCE(?, converted_auth_flow)
                        ELSE converted_auth_flow
                    END,
                    converted_at = CASE
                        WHEN converted_user_id IS NULL OR converted_user_id = ? THEN COALESCE(converted_at, ?)
                        ELSE converted_at
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    converted_user_id,
                    converted_user_id,
                    converted_user_id,
                    str(converted_email or "").strip().lower() or None,
                    converted_user_id,
                    str(auth_flow or "").strip().lower() or None,
                    converted_user_id,
                    now_ts,
                    now_ts,
                    touch_id,
                ),
            )
            connection.commit()
        return self._get_touch_fallback(touch_id=touch_id)

    def _fallback_row_to_touch(self, row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        payload = dict(row)
        try:
            metadata = json.loads(str(payload.get("metadata_json") or "{}") or "{}")
        except Exception:
            metadata = {}
        return {
            "id": str(payload.get("id") or "").strip(),
            "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
            "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
            "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
            "channel_key": str(payload.get("channel_key") or "").strip().lower() or None,
            "endpoint_key": str(payload.get("endpoint_key") or "").strip().lower() or None,
            "external_user_id": str(payload.get("external_user_id") or "").strip() or None,
            "source": str(payload.get("source") or "").strip() or None,
            "campaign_token": str(payload.get("campaign_token") or "").strip() or None,
            "start_count": int(payload.get("start_count") or 0),
            "metadata": metadata if isinstance(metadata, dict) else {},
            "converted_user_id": str(payload.get("converted_user_id") or "").strip() or None,
            "converted_email": str(payload.get("converted_email") or "").strip().lower() or None,
            "converted_auth_flow": str(payload.get("converted_auth_flow") or "").strip().lower() or None,
            "converted_at": int(payload.get("converted_at") or 0) or None,
            "limit_hit_click_at": int(payload.get("limit_hit_click_at") or 0) or None,
            "limit_hit_click_source": str(payload.get("limit_hit_click_source") or "").strip().lower() or None,
            "first_started_at": int(payload.get("first_started_at") or 0) or None,
            "last_started_at": int(payload.get("last_started_at") or 0) or None,
            "created_at": int(payload.get("created_at") or 0) or None,
            "updated_at": int(payload.get("updated_at") or 0) or None,
        }


_CHANNEL_USER_ACQUISITION_SERVICE = ChannelUserAcquisitionService()


def get_channel_user_acquisition_service() -> ChannelUserAcquisitionService:
    return _CHANNEL_USER_ACQUISITION_SERVICE

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from fastapi import HTTPException

from server_modules import auth as auth_module
from server_modules import entitlements_service
from server_modules import security_audit_service


PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULT_PAIRING_SCOPES = ("chat",)
DEFAULT_PAIRING_TTL_SECONDS = 15 * 60
MAX_PAIRING_TTL_SECONDS = 60 * 60
MIN_PAIRING_TTL_SECONDS = 60
MAX_PENDING_PAIRING_INTENTS_PER_PROVIDER = 3
PAIRING_COMMAND_RE = re.compile(
    r"^\s*/?(?:pair|link|connect)\s+([A-Za-z0-9-]{6,48})\s*$",
    re.IGNORECASE,
)
PAIRING_BARE_CODE_RE = re.compile(r"^\s*(EMP-[A-Za-z0-9-]{6,48})\s*$", re.IGNORECASE)
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
PUBLIC_FRONTEND_ORIGIN_ENV_KEYS = (
    "EMPYRALIS_PUBLIC_FRONTEND_ORIGIN",
    "FRONTEND_PUBLIC_ORIGIN",
    "NEXT_PUBLIC_APP_ORIGIN",
    "BACKEND_PUBLIC_ORIGIN",
)


def _normalize_provider(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token or not PROVIDER_RE.match(token):
        raise HTTPException(status_code=400, detail="provider is invalid.")
    return token


def _normalize_external_subject(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        raise HTTPException(status_code=400, detail="external_subject is required.")
    return token


def _default_scopes_for_provider(provider: Any) -> list[str]:
    resolved_provider = str(provider or "").strip().lower()
    scopes: list[str] = list(DEFAULT_PAIRING_SCOPES)
    if resolved_provider:
        provider_chat_scope = f"{resolved_provider}:chat"
        if provider_chat_scope not in scopes:
            scopes.append(provider_chat_scope)
    if resolved_provider == "whatsapp":
        scopes.append("whatsapp:opt_in")
    deduped: list[str] = []
    for token in scopes:
        if token not in deduped:
            deduped.append(token)
    return deduped


def _normalize_scopes(value: Any, *, provider: Any = "") -> list[str]:
    scopes = _default_scopes_for_provider(provider)
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    elif value is None:
        raw_items = []
    else:
        raw_items = [value]
    for item in raw_items:
        token = str(item or "").strip().lower()
        if not token:
            continue
        if not re.match(r"^[a-z][a-z0-9:_-]{0,63}$", token):
            continue
        if token not in scopes:
            scopes.append(token)
    return scopes


def _normalize_pairing_code(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9-]+", "", str(value or "").strip().upper())
    if not token:
        raise HTTPException(status_code=400, detail="Pairing code is required.")
    if not token.startswith("EMP-"):
        token = f"EMP-{token}"
    if len(token) < 10:
        raise HTTPException(status_code=400, detail="Pairing code is invalid.")
    return token


def _encode_metadata(value: Any) -> str:
    if not isinstance(value, dict):
        value = {}
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _decode_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _decode_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
    except Exception:
        return []
    return decoded if isinstance(decoded, list) else []


def _display_provider(provider: str) -> str:
    mapping = {
        "telegram": "Telegram",
        "whatsapp": "WhatsApp",
        "discord": "Discord",
        "slack": "Slack",
        "wechat": "WeChat",
        "imessage": "iMessage",
    }
    return mapping.get(provider, provider.replace("_", " ").title())


def _pair_command_hint(provider: str) -> str:
    return "/pair CODE" if provider == "telegram" else "pair CODE"


def _public_frontend_origin() -> str:
    for key in PUBLIC_FRONTEND_ORIGIN_ENV_KEYS:
        value = str(os.getenv(key) or "").strip()
        if value:
            return value.rstrip("/")
    origins = str(os.getenv("FRONTEND_ORIGINS") or "").strip()
    if origins:
        first_origin = origins.split(",", 1)[0].strip()
        if first_origin:
            return first_origin.rstrip("/")
    return "http://127.0.0.1:3000"


def _platform_channel_connect_url(
    provider: str,
    *,
    workspace_id: Optional[str] = None,
    intent_id: Optional[str] = None,
) -> str:
    params = {
        "source": "channel_connect",
        "channel": _normalize_provider(provider),
    }
    clean_workspace_id = str(workspace_id or "").strip()
    if clean_workspace_id:
        params["workspace_id"] = clean_workspace_id
    clean_intent_id = str(intent_id or "").strip()
    if clean_intent_id:
        params["intent_id"] = clean_intent_id
    return f"{_public_frontend_origin()}/continue?{urlencode(params)}"


def _subject_hint(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return "unknown"
    if len(token) <= 4:
        return token
    return f"...{token[-4:]}"


def _issue_pairing_code() -> str:
    chunks = [
        "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(4)),
        "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(4)),
    ]
    return "EMP-" + "-".join(chunks)


def _hash_pairing_code(provider: str, pairing_code: str) -> str:
    clean_provider = _normalize_provider(provider)
    clean_code = _normalize_pairing_code(pairing_code)
    digest = hmac.new(
        auth_module._jwt_secret().encode("utf-8"),
        f"{clean_provider}:{clean_code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


class ChannelPairingService:
    def _connect(self) -> sqlite3.Connection:
        return auth_module._connect_auth_db()

    def _now(self) -> int:
        return int(time.time())

    def platform_connect_url(
        self,
        *,
        provider: Any,
        workspace_id: Optional[str] = None,
        intent_id: Optional[str] = None,
    ) -> str:
        return _platform_channel_connect_url(
            _normalize_provider(provider),
            workspace_id=workspace_id,
            intent_id=intent_id,
        )

    def _ensure_tables_locked(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_pairing_intents (
                intent_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                account_user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                allow_relink INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed_at INTEGER,
                consumed_external_subject TEXT,
                revoked_at INTEGER,
                revoked_reason TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_links (
                link_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                external_subject TEXT NOT NULL,
                account_user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                linked_at INTEGER NOT NULL,
                last_verified_at INTEGER,
                revoked_at INTEGER,
                revoked_reason TEXT,
                revoked_by_user_id TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channel_pairing_intents_account
            ON channel_pairing_intents(account_user_id, workspace_id, provider, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channel_pairing_intents_code
            ON channel_pairing_intents(provider, code_hash, expires_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channel_links_account
            ON channel_links(account_user_id, workspace_id, provider, linked_at DESC)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_links_active_subject
            ON channel_links(provider, external_subject)
            WHERE revoked_at IS NULL
            """
        )

    def _intent_from_row(self, row: Any) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            "intent_id": str(row["intent_id"] or "").strip(),
            "provider": _normalize_provider(row["provider"]),
            "account_id": str(row["account_user_id"] or "").strip(),
            "tenant_id": str(row["tenant_id"] or "").strip(),
            "workspace_id": str(row["workspace_id"] or "").strip(),
            "scopes": _normalize_scopes(_decode_json_list(row["scopes_json"]), provider=row["provider"]),
            "metadata": _decode_metadata(row["metadata_json"]),
            "allow_relink": bool(row["allow_relink"]),
            "created_at": int(row["created_at"]) if row["created_at"] is not None else None,
            "expires_at": int(row["expires_at"]) if row["expires_at"] is not None else None,
            "consumed_at": int(row["consumed_at"]) if row["consumed_at"] is not None else None,
            "consumed_external_subject": str(row["consumed_external_subject"] or "").strip() or None,
            "revoked_at": int(row["revoked_at"]) if row["revoked_at"] is not None else None,
            "revoked_reason": str(row["revoked_reason"] or "").strip() or None,
            "active": (
                row["revoked_at"] is None
                and row["consumed_at"] is None
                and int(row["expires_at"] or 0) > self._now()
            ),
        }

    def _link_from_row(self, row: Any) -> dict[str, Any]:
        if row is None:
            return {}
        revoked_at = int(row["revoked_at"]) if row["revoked_at"] is not None else None
        external_subject = str(row["external_subject"] or "").strip()
        return {
            "link_id": str(row["link_id"] or "").strip(),
            "provider": _normalize_provider(row["provider"]),
            "external_subject": external_subject,
            "external_subject_hint": _subject_hint(external_subject),
            "account_id": str(row["account_user_id"] or "").strip(),
            "tenant_id": str(row["tenant_id"] or "").strip(),
            "workspace_id": str(row["workspace_id"] or "").strip(),
            "scopes": _normalize_scopes(_decode_json_list(row["scopes_json"]), provider=row["provider"]),
            "metadata": _decode_metadata(row["metadata_json"]),
            "linked_at": int(row["linked_at"]) if row["linked_at"] is not None else None,
            "last_verified_at": int(row["last_verified_at"]) if row["last_verified_at"] is not None else None,
            "revoked_at": revoked_at,
            "revoked_reason": str(row["revoked_reason"] or "").strip() or None,
            "revoked_by_user_id": str(row["revoked_by_user_id"] or "").strip() or None,
            "status": "revoked" if revoked_at is not None else "active",
        }

    def _find_active_link_locked(
        self,
        connection: sqlite3.Connection,
        *,
        provider: str,
        external_subject: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT *
            FROM channel_links
            WHERE provider = ? AND external_subject = ? AND revoked_at IS NULL
            LIMIT 1
            """,
            (provider, external_subject),
        ).fetchone()
        link = self._link_from_row(row)
        if not link:
            return {}
        membership = connection.execute(
            """
            SELECT role
            FROM workspace_memberships
            WHERE user_id = ? AND workspace_id = ?
            LIMIT 1
            """,
            (link["account_id"], link["workspace_id"]),
        ).fetchone()
        if membership is None:
            return {}
        return link

    def create_pairing_intent(
        self,
        current_user: Optional[Dict[str, Any]],
        *,
        provider: Any,
        workspace_id: Optional[str],
        scopes: Any = None,
        ttl_seconds: Optional[int] = None,
        allow_relink: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        user_id = auth_module._current_bearer_user_id(current_user)
        resolved_provider = _normalize_provider(provider)
        resolved_workspace_id = auth_module.enforce_workspace_access(
            current_user,
            workspace_id or "default",
            minimum_role="member",
        )
        if resolved_provider in {"telegram", "whatsapp"}:
            try:
                entitlements_service.enforce_channel_surface_access_for_workspace_id(
                    resolved_provider,
                    workspace_id=resolved_workspace_id,
                )
            except entitlements_service.EntitlementDeniedError as exc:
                raise HTTPException(status_code=403, detail=exc.message) from exc
        resolved_tenant_id = auth_module.workspace_tenant_id(current_user, resolved_workspace_id)
        resolved_scopes = _normalize_scopes(scopes, provider=resolved_provider)
        ttl = max(MIN_PAIRING_TTL_SECONDS, min(int(ttl_seconds or DEFAULT_PAIRING_TTL_SECONDS), MAX_PAIRING_TTL_SECONDS))
        created_at = self._now()
        intent_id = f"cpi_{uuid.uuid4().hex}"
        pairing_code = _issue_pairing_code()
        expires_at = created_at + ttl
        metadata_payload = dict(metadata or {})
        code_hash = _hash_pairing_code(resolved_provider, pairing_code)

        with auth_module.AUTH_LOCK:
            with self._connect() as connection:
                self._ensure_tables_locked(connection)
                pending_row = connection.execute(
                    """
                    SELECT COUNT(*) AS pending_count
                    FROM channel_pairing_intents
                    WHERE account_user_id = ? AND workspace_id = ? AND provider = ?
                      AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at > ?
                    """,
                    (
                        user_id,
                        resolved_workspace_id,
                        resolved_provider,
                        created_at,
                    ),
                ).fetchone()
                pending_count = int((pending_row["pending_count"] if pending_row else 0) or 0)
                if pending_count >= MAX_PENDING_PAIRING_INTENTS_PER_PROVIDER:
                    raise HTTPException(
                        status_code=429,
                        detail=(
                            f"Too many pending {_display_provider(resolved_provider)} pairing requests. "
                            "Use an existing code or wait for one to expire."
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO channel_pairing_intents (
                        intent_id,
                        provider,
                        code_hash,
                        account_user_id,
                        tenant_id,
                        workspace_id,
                        scopes_json,
                        metadata_json,
                        allow_relink,
                        created_at,
                        expires_at,
                        consumed_at,
                        consumed_external_subject,
                        revoked_at,
                        revoked_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
                    """,
                    (
                        intent_id,
                        resolved_provider,
                        code_hash,
                        user_id,
                        resolved_tenant_id,
                        resolved_workspace_id,
                        json.dumps(resolved_scopes, separators=(",", ":")),
                        _encode_metadata(metadata_payload),
                        1 if allow_relink else 0,
                        created_at,
                        expires_at,
                    ),
                )
                connection.commit()

        security_audit_service.emit_security_audit_event(
            action="channel.pairing_intent.create",
            current_user=current_user,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            actor_user_id=user_id,
            actor_email=str((current_user or {}).get("email") or "").strip().lower() or None,
            actor_auth_type="bearer",
            channel=resolved_provider,
            trace_id=intent_id,
            idempotency_key=f"channel.pairing_intent.create:{intent_id}",
            metadata={
                "provider": resolved_provider,
                "workspace_id": resolved_workspace_id,
                "scopes": resolved_scopes,
                "allow_relink": bool(allow_relink),
                "expires_at": expires_at,
            },
        )
        return {
            "ok": True,
            "intent": {
                "intent_id": intent_id,
                "provider": resolved_provider,
                "account_id": user_id,
                "tenant_id": resolved_tenant_id,
                "workspace_id": resolved_workspace_id,
                "scopes": resolved_scopes,
                "allow_relink": bool(allow_relink),
                "created_at": created_at,
                "expires_at": expires_at,
                "pairing_code": pairing_code,
                "connect_url": _platform_channel_connect_url(
                    resolved_provider,
                    workspace_id=resolved_workspace_id,
                    intent_id=intent_id,
                ),
                "instructions": (
                    f"Open {_display_provider(resolved_provider)} from your account. "
                    "If it is not linked yet, the channel will send an Empyralis connect link. "
                    "Finish setup and relinking inside Empyralis."
                ),
                "legacy_pairing_command": _pair_command_hint(resolved_provider),
            },
        }

    def consume_pairing_code(
        self,
        *,
        provider: Any,
        pairing_code: Any,
        external_subject: Any,
        observed_workspace_id: Optional[str] = None,
        observed_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_provider = _normalize_provider(provider)
        resolved_code = _normalize_pairing_code(pairing_code)
        resolved_external_subject = _normalize_external_subject(external_subject)
        now_ts = self._now()
        code_hash = _hash_pairing_code(resolved_provider, resolved_code)
        with auth_module.AUTH_LOCK:
            with self._connect() as connection:
                self._ensure_tables_locked(connection)
                row = connection.execute(
                    """
                    SELECT *
                    FROM channel_pairing_intents
                    WHERE provider = ? AND code_hash = ? AND revoked_at IS NULL AND consumed_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (resolved_provider, code_hash),
                ).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="Pairing code is invalid or has already been used.")
                intent = self._intent_from_row(row)
                if not intent:
                    raise HTTPException(status_code=404, detail="Pairing code is invalid or has already been used.")
                if int(intent.get("expires_at") or 0) <= now_ts:
                    raise HTTPException(status_code=410, detail="Pairing code has expired.")
                if observed_workspace_id and str(intent.get("workspace_id") or "").strip() != str(observed_workspace_id or "").strip():
                    raise HTTPException(status_code=409, detail="Pairing code was created for a different workspace.")
                merged_metadata = {**dict(intent.get("metadata") or {}), **dict(observed_metadata or {})}

                existing_link = self._find_active_link_locked(
                    connection,
                    provider=resolved_provider,
                    external_subject=resolved_external_subject,
                )
                link_action = "created"
                if existing_link:
                    same_account = existing_link["account_id"] == intent["account_id"]
                    same_workspace = existing_link["workspace_id"] == intent["workspace_id"]
                    if same_account and same_workspace:
                        connection.execute(
                            """
                            UPDATE channel_links
                            SET last_verified_at = ?, metadata_json = ?
                            WHERE link_id = ?
                            """,
                            (
                                now_ts,
                                _encode_metadata({**existing_link.get("metadata", {}), **merged_metadata}),
                                existing_link["link_id"],
                            ),
                        )
                        link_id = existing_link["link_id"]
                        link_action = "verified"
                    else:
                        if not bool(intent.get("allow_relink")):
                            raise HTTPException(
                                status_code=409,
                                detail=f"This {_display_provider(resolved_provider)} identity is already linked to another workspace.",
                            )
                        connection.execute(
                            """
                            UPDATE channel_links
                            SET revoked_at = ?, revoked_reason = ?, revoked_by_user_id = ?
                            WHERE link_id = ?
                            """,
                            (
                                now_ts,
                                "Relinked through a newer pairing intent.",
                                str(intent.get("account_id") or "").strip(),
                                existing_link["link_id"],
                            ),
                        )
                        link_id = f"chl_{uuid.uuid4().hex}"
                        link_action = "relinked"
                        connection.execute(
                            """
                            INSERT INTO channel_links (
                                link_id,
                                provider,
                                external_subject,
                                account_user_id,
                                tenant_id,
                                workspace_id,
                                scopes_json,
                                metadata_json,
                                linked_at,
                                last_verified_at,
                                revoked_at,
                                revoked_reason,
                                revoked_by_user_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                            """,
                            (
                                link_id,
                                resolved_provider,
                                resolved_external_subject,
                                intent["account_id"],
                                intent["tenant_id"],
                                intent["workspace_id"],
                                json.dumps(intent["scopes"], separators=(",", ":")),
                                _encode_metadata(merged_metadata),
                                now_ts,
                                now_ts,
                            ),
                        )
                else:
                    link_id = f"chl_{uuid.uuid4().hex}"
                    connection.execute(
                        """
                        INSERT INTO channel_links (
                            link_id,
                            provider,
                            external_subject,
                            account_user_id,
                            tenant_id,
                            workspace_id,
                            scopes_json,
                            metadata_json,
                            linked_at,
                            last_verified_at,
                            revoked_at,
                            revoked_reason,
                            revoked_by_user_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                        """,
                        (
                            link_id,
                            resolved_provider,
                            resolved_external_subject,
                            intent["account_id"],
                            intent["tenant_id"],
                            intent["workspace_id"],
                            json.dumps(intent["scopes"], separators=(",", ":")),
                            _encode_metadata(merged_metadata),
                            now_ts,
                            now_ts,
                        ),
                    )

                connection.execute(
                    """
                    UPDATE channel_pairing_intents
                    SET consumed_at = ?, consumed_external_subject = ?
                    WHERE intent_id = ?
                    """,
                    (now_ts, resolved_external_subject, intent["intent_id"]),
                )
                row = connection.execute(
                    "SELECT * FROM channel_links WHERE link_id = ? LIMIT 1",
                    (link_id,),
                ).fetchone()
                connection.commit()

        link = self._link_from_row(row)
        security_audit_service.emit_security_audit_event(
            action=f"channel.link.{link_action}",
            tenant_id=link.get("tenant_id"),
            workspace_id=link.get("workspace_id"),
            actor_user_id=str(link.get("account_id") or "").strip() or None,
            actor_auth_type="channel_pairing",
            channel=resolved_provider,
            trace_id=link.get("link_id") or intent["intent_id"],
            idempotency_key=f"channel.link.{link_action}:{link.get('link_id') or intent['intent_id']}",
            metadata={
                "provider": resolved_provider,
                "workspace_id": link.get("workspace_id"),
                "external_subject_hint": _subject_hint(resolved_external_subject),
                "scopes": link.get("scopes") or [],
            },
        )
        return {
            "ok": True,
            "status": link_action,
            "intent": intent,
            "link": link,
        }

    def resolve_channel_link(
        self,
        *,
        provider: Any,
        external_subject: Any,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_provider = _normalize_provider(provider)
        resolved_external_subject = _normalize_external_subject(external_subject)
        requested_workspace = str(workspace_id or "").strip()
        with auth_module.AUTH_LOCK:
            with self._connect() as connection:
                self._ensure_tables_locked(connection)
                link = self._find_active_link_locked(
                    connection,
                    provider=resolved_provider,
                    external_subject=resolved_external_subject,
                )
                if not link:
                    return {}
                if requested_workspace and str(link.get("workspace_id") or "").strip() != requested_workspace:
                    return {}
                return link

    def list_channel_links(
        self,
        current_user: Optional[Dict[str, Any]],
        *,
        provider: Optional[str] = None,
        workspace_id: Optional[str] = None,
        include_revoked: bool = False,
    ) -> Dict[str, Any]:
        user_id = auth_module._current_bearer_user_id(current_user)
        provider_filter = _normalize_provider(provider) if provider else None
        workspace_filter = None
        if workspace_id:
            workspace_filter = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
        query = """
            SELECT *
            FROM channel_links
            WHERE account_user_id = ?
        """
        params: list[Any] = [user_id]
        if provider_filter:
            query += " AND provider = ?"
            params.append(provider_filter)
        if workspace_filter:
            query += " AND workspace_id = ?"
            params.append(workspace_filter)
        if not include_revoked:
            query += " AND revoked_at IS NULL"
        query += " ORDER BY linked_at DESC"
        with auth_module.AUTH_LOCK:
            with self._connect() as connection:
                self._ensure_tables_locked(connection)
                rows = connection.execute(query, tuple(params)).fetchall()
        return {
            "ok": True,
            "links": [self._link_from_row(row) for row in rows],
        }

    def revoke_channel_link(
        self,
        current_user: Optional[Dict[str, Any]],
        *,
        link_id: Any,
        confirm: bool,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id = auth_module._current_bearer_user_id(current_user)
        clean_link_id = str(link_id or "").strip()
        if not clean_link_id:
            raise HTTPException(status_code=400, detail="link_id is required.")
        if not bool(confirm):
            raise HTTPException(status_code=400, detail="confirm=true is required to revoke a channel link.")
        now_ts = self._now()
        revoke_reason = str(reason or "").strip() or "User revoked the linked channel."
        with auth_module.AUTH_LOCK:
            with self._connect() as connection:
                self._ensure_tables_locked(connection)
                row = connection.execute(
                    "SELECT * FROM channel_links WHERE link_id = ? LIMIT 1",
                    (clean_link_id,),
                ).fetchone()
                link = self._link_from_row(row)
                if not link:
                    raise HTTPException(status_code=404, detail="Channel link not found.")
                if str(link.get("account_id") or "").strip() != user_id:
                    raise HTTPException(status_code=403, detail="Channel link is not accessible for this account.")
                if link.get("revoked_at") is None:
                    connection.execute(
                        """
                        UPDATE channel_links
                        SET revoked_at = ?, revoked_reason = ?, revoked_by_user_id = ?
                        WHERE link_id = ?
                        """,
                        (now_ts, revoke_reason, user_id, clean_link_id),
                    )
                    row = connection.execute(
                        "SELECT * FROM channel_links WHERE link_id = ? LIMIT 1",
                        (clean_link_id,),
                    ).fetchone()
                    connection.commit()
                link = self._link_from_row(row)

        security_audit_service.emit_security_audit_event(
            action="channel.link.revoke",
            current_user=current_user,
            tenant_id=link.get("tenant_id"),
            workspace_id=link.get("workspace_id"),
            actor_user_id=user_id,
            actor_email=str((current_user or {}).get("email") or "").strip().lower() or None,
            actor_auth_type="bearer",
            channel=str(link.get("provider") or "").strip() or None,
            trace_id=clean_link_id,
            idempotency_key=f"channel.link.revoke:{clean_link_id}",
            metadata={
                "provider": link.get("provider"),
                "external_subject_hint": link.get("external_subject_hint"),
                "reason": revoke_reason,
            },
        )
        return {"ok": True, "link": link}

    def extract_pairing_code(self, message_text: Any) -> Optional[str]:
        text = str(message_text or "").strip()
        if not text:
            return None
        match = PAIRING_COMMAND_RE.match(text) or PAIRING_BARE_CODE_RE.match(text)
        if not match:
            return None
        return _normalize_pairing_code(match.group(1))

    def authorize_channel_message(
        self,
        *,
        provider: Any,
        external_subject: Any,
        workspace_id: Optional[str],
        message_text: Any,
        observed_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_provider = _normalize_provider(provider)
        resolved_external_subject = _normalize_external_subject(external_subject)
        requested_workspace = str(workspace_id or "").strip()
        active_link = self.resolve_channel_link(
            provider=resolved_provider,
            external_subject=resolved_external_subject,
        )
        if active_link:
            if not requested_workspace or active_link.get("workspace_id") == requested_workspace:
                return {
                    "authorized": True,
                    "status": "linked",
                    "workspace_id": active_link.get("workspace_id"),
                    "link": active_link,
                }
        pairing_code = self.extract_pairing_code(message_text)
        if pairing_code:
            try:
                consumed = self.consume_pairing_code(
                    provider=resolved_provider,
                    pairing_code=pairing_code,
                    external_subject=resolved_external_subject,
                    observed_workspace_id=requested_workspace or None,
                    observed_metadata=observed_metadata,
                )
            except HTTPException as exc:
                connect_url = _platform_channel_connect_url(
                    resolved_provider,
                    workspace_id=requested_workspace or None,
                )
                return {
                    "authorized": False,
                    "status": "pairing_failed",
                    "connect_url": connect_url,
                    "reply_text": (
                        f"{str(exc.detail or 'Pairing failed.')} "
                        f"Open Empyralis to connect this channel: {connect_url}"
                    ),
                }
            return {
                "authorized": False,
                "status": "paired",
                "workspace_id": consumed.get("link", {}).get("workspace_id"),
                "link": consumed.get("link"),
                "reply_text": (
                    f"{_display_provider(resolved_provider)} linked to your Empyralis workspace. "
                    "Send your next message and I’ll continue there."
                ),
            }
        if active_link:
            connect_url = _platform_channel_connect_url(resolved_provider)
            return {
                "authorized": False,
                "status": "linked_elsewhere",
                "connect_url": connect_url,
                "reply_text": (
                    f"This {_display_provider(resolved_provider)} identity is already linked to another workspace. "
                    f"Open Empyralis to manage or relink it: {connect_url}"
                ),
            }
        connect_url = _platform_channel_connect_url(
            resolved_provider,
            workspace_id=requested_workspace or None,
        )
        return {
            "authorized": False,
            "status": "pairing_required",
            "connect_url": connect_url,
            "reply_text": (
                f"This {_display_provider(resolved_provider)} identity is not linked to Empyralis yet. "
                f"Open this link to connect it: {connect_url}"
            ),
        }


_CHANNEL_PAIRING_SERVICE = ChannelPairingService()


def get_channel_pairing_service() -> ChannelPairingService:
    return _CHANNEL_PAIRING_SERVICE


def create_authenticated_channel_pairing_intent(
    current_user: Optional[Dict[str, Any]],
    *,
    provider: Any,
    workspace_id: Optional[str],
    scopes: Any = None,
    ttl_seconds: Optional[int] = None,
    allow_relink: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return get_channel_pairing_service().create_pairing_intent(
        current_user,
        provider=provider,
        workspace_id=workspace_id,
        scopes=scopes,
        ttl_seconds=ttl_seconds,
        allow_relink=allow_relink,
        metadata=metadata,
    )


def list_authenticated_channel_links(
    current_user: Optional[Dict[str, Any]],
    *,
    provider: Optional[str] = None,
    workspace_id: Optional[str] = None,
    include_revoked: bool = False,
) -> Dict[str, Any]:
    return get_channel_pairing_service().list_channel_links(
        current_user,
        provider=provider,
        workspace_id=workspace_id,
        include_revoked=include_revoked,
    )


def revoke_authenticated_channel_link(
    current_user: Optional[Dict[str, Any]],
    *,
    link_id: Any,
    confirm: bool,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    return get_channel_pairing_service().revoke_channel_link(
        current_user,
        link_id=link_id,
        confirm=confirm,
        reason=reason,
    )

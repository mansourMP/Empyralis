from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from server_modules import control_plane_repository, rust_runtime_kernel_client, safe_mode_service
from server_modules.vault_helpers import (
    DecryptFn,
    LoadVaultFn,
    normalize_workspace_id,
    resolve_agent_credential as _raw_resolve_agent_credential,
    resolve_default_vault_credential as _raw_resolve_default_vault_credential,
    resolve_vault_credential as _raw_resolve_vault_credential,
)


_TOKEN_VERSION = "empyralis.secret-grant.v1"
DEFAULT_SECRET_GRANT_TTL_SECONDS = 30
_SUPPORTED_SECRET_KINDS = {"connector_credential", "provider_credential"}
_SUPPORTED_CONNECTOR_CLASSES = {"api_connector", "browser_connector", "media_generation_connector"}
_LOCAL_ENV_TOKENS = {"", "dev", "development", "local", "test", "testing"}
_HIGH_RISK_CREDENTIAL_TOKENS = {
    "admin",
    "administrator",
    "root",
    "privileged",
    "financial",
    "finance",
    "billing",
    "payment",
    "payout",
}
_INSECURE_BROKER_SECRETS = {
    "empyralis-dev-tool-broker-secret",
    "empyralis-dev-secrets-broker-secret",
}
_HOSTED_PROVIDER_SECRET_BUNDLE_ENV = "EMPYRALIS_HOSTED_PROVIDER_SECRETS_JSON"
_HOSTED_PROVIDER_ENV_CANDIDATES: Dict[str, List[str]] = {
    "openai": ["ORION_HOSTED_OPENAI_API_KEY", "OPENAI_API_KEY"],
    "anthropic": ["ORION_HOSTED_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
    "gemini": ["ORION_HOSTED_GEMINI_API_KEY", "GEMINI_API_KEY"],
    "qwen": ["ORION_HOSTED_QWEN_API_KEY", "ORION_LOCAL_WORKER_QWEN_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY"],
    "deepseek": ["ORION_HOSTED_DEEPSEEK_API_KEY", "ORION_LOCAL_WORKER_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"],
    "mistral": ["ORION_HOSTED_MISTRAL_API_KEY", "ORION_LOCAL_WORKER_MISTRAL_API_KEY", "MISTRAL_API_KEY"],
    # MAN-131: the platform-owned DigitalOcean PAT that decides which DO
    # account vps_provisioning_service.provision_vps creates a droplet in
    # (see _platform_digitalocean_token). EMPYRALIS_PLATFORM_DIGITALOCEAN_
    # TOKEN is listed FIRST and must stay first — it is the actual env var
    # name that module has read since MAN-133 shipped, so it has to keep
    # winning over the two aliases added here for free (the managed-bundle
    # path and the ops-familiar DIGITALOCEAN_ACCESS_TOKEN name DO's own CLI/
    # Terraform provider uses) or this registration would silently orphan
    # whatever is already configured in production.
    "digitalocean": [
        "EMPYRALIS_PLATFORM_DIGITALOCEAN_TOKEN",
        "ORION_HOSTED_DIGITALOCEAN_TOKEN",
        "DIGITALOCEAN_ACCESS_TOKEN",
    ],
}
_HOSTED_OPENAI_BEARER_KEYS = [
    ("env_codex_oauth_token", "codex_oauth_token"),
    ("env_oauth_token", "oauth_token"),
    ("env_access_token", "access_token"),
    ("env_api_key", "api_key"),
]
_REVOCATION_LOCK = threading.Lock()
_REVOKED_GRANT_IDS: set[str] = set()
_REVOKED_SESSION_IDS: set[str] = set()
_REVOKED_RUN_IDS: set[str] = set()
_SECRET_REFERENCE_NEXT_ACTIONS = {
    "allow": "allow_secret_resolution",
    "require_approval": "request_secret_access_approval",
    "approval_required": "request_secret_access_approval",
}


@dataclass(frozen=True)
class SecretAccessGrant:
    token: str
    claims: Dict[str, Any]


@dataclass(frozen=True)
class HostedProviderSecretResolution:
    provider_id: str
    field: str
    value: str
    source: str
    source_kind: str
    bootstrap_fallback: bool
    owner_kind: str
    owner_label: str


class SecretBrokerError(RuntimeError):
    pass


class SecretAccessDeniedError(SecretBrokerError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = str(code or "secret_access_denied").strip() or "secret_access_denied"
        self.detail = str(detail or "Secret access was denied.").strip() or "Secret access was denied."


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _ordered_unique(values: List[str]) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()
    for raw in values:
        token = str(raw or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _normalize_secret_kind(secret_kind: Any) -> str:
    token = _normalize_token(secret_kind)
    if token not in _SUPPORTED_SECRET_KINDS:
        raise SecretAccessDeniedError("invalid_secret_kind", f"Unsupported secret kind '{secret_kind}'.")
    return token


def _normalize_connector_class(connector_class: Any) -> Optional[str]:
    token = _normalize_token(connector_class)
    if not token:
        return None
    if token not in _SUPPORTED_CONNECTOR_CLASSES:
        raise SecretAccessDeniedError("invalid_connector_class", f"Unsupported connector class '{connector_class}'.")
    return token


def _resolved_environment() -> str:
    return str(os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or os.getenv("ENV") or os.getenv("NODE_ENV") or "").strip().lower()


def _environment_requires_explicit_secret() -> bool:
    return _resolved_environment() not in _LOCAL_ENV_TOKENS


def _signing_secret() -> bytes:
    secret = str(
        os.getenv("EMPYRALIS_SECRETS_BROKER_SECRET")
        or os.getenv("EMPYRALIS_TOOL_BROKER_SECRET")
        or ""
    ).strip()
    if secret and secret not in _INSECURE_BROKER_SECRETS:
        return secret.encode("utf-8")
    raise RuntimeError(
        "Broker secret must be explicitly set. Refusing to start with insecure default."
    )


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64decode(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(f"{token}{padding}")


def _json_dumps(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _normalize_allowed_fields(fields: Optional[List[str]]) -> List[str]:
    return _ordered_unique([str(item or "").strip() for item in list(fields or []) if str(item or "").strip()])


def _normalize_domain(raw_value: Any) -> str:
    token = str(raw_value or "").strip().lower()
    if not token:
        return ""
    if "://" in token:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(token)
            token = str(parsed.hostname or "").strip().lower()
        except Exception:
            token = ""
    if "/" in token:
        token = token.split("/", 1)[0].strip().lower()
    if ":" in token:
        token = token.split(":", 1)[0].strip().lower()
    return token


def _normalize_allowed_domains(domains: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for item in list(domains or []):
        token = _normalize_domain(item)
        if token and token not in out:
            out.append(token)
    return out


def _normalize_allowed_tools(tools: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for item in list(tools or []):
        token = str(item or "").strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def _domain_in_scope(domain: str, allowed_domains: List[str]) -> bool:
    token = _normalize_domain(domain)
    if not token:
        return False
    for entry in allowed_domains:
        scope = _normalize_domain(entry)
        if not scope:
            continue
        if scope.startswith("*."):
            suffix = scope[2:]
            if token.endswith(f".{suffix}"):
                return True
            continue
        if token == scope or token.endswith(f".{scope}"):
            return True
    return False


def _claim_is_revoked(claims: Dict[str, Any]) -> bool:
    access_id = str(claims.get("access_id") or "").strip()
    session_id = str(claims.get("session_id") or "").strip()
    run_id = str(claims.get("run_id") or "").strip()
    with _REVOCATION_LOCK:
        if access_id and access_id in _REVOKED_GRANT_IDS:
            return True
        if session_id and session_id in _REVOKED_SESSION_IDS:
            return True
        if run_id and run_id in _REVOKED_RUN_IDS:
            return True
    return False


def _collect_risk_tokens(*values: Any) -> List[str]:
    tokens: List[str] = []
    for raw in values:
        if isinstance(raw, list):
            for item in raw:
                token = str(item or "").strip().lower()
                if token:
                    tokens.append(token)
            continue
        if isinstance(raw, dict):
            for key, value in raw.items():
                key_token = str(key or "").strip().lower()
                if key_token:
                    tokens.append(key_token)
                val_token = str(value or "").strip().lower()
                if val_token:
                    tokens.append(val_token)
            continue
        token = str(raw or "").strip().lower()
        if token:
            tokens.append(token)
    return tokens


def _requires_high_risk_approval(
    *,
    provider_id: Optional[str],
    connector_id: Optional[str],
    metadata: Optional[Dict[str, Any]],
    explicit_tags: Optional[List[str]],
) -> bool:
    all_tokens = _collect_risk_tokens(
        provider_id,
        connector_id,
        explicit_tags or [],
        metadata or {},
        (metadata or {}).get("risk_tags"),
        (metadata or {}).get("sensitivity"),
        (metadata or {}).get("credential_class"),
        (metadata or {}).get("scope"),
        (metadata or {}).get("privilege"),
        (metadata or {}).get("role"),
    )
    joined = " ".join(all_tokens)
    return any(marker in joined for marker in _HIGH_RISK_CREDENTIAL_TOKENS)


def _inspect_secret_reference_with_rust(
    *,
    name: str,
    action_class: str,
    provider_id: Optional[str] = None,
    connector_id: Optional[str] = None,
    credential_id: Optional[str] = None,
    connector_class: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    risk_tags: Optional[List[str]] = None,
    require_approval: Optional[bool] = None,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": str(name or "").strip() or "secret_reference",
        "action_class": str(action_class or "read").strip().lower() or "read",
        "provider_id": str(provider_id or "").strip().lower() or None,
        "connector_id": str(connector_id or "").strip().lower() or None,
        "credential_id": str(credential_id or "").strip() or None,
        "connector_class": str(connector_class or "").strip().lower() or None,
        "metadata": dict(metadata or {}) if isinstance(metadata, dict) else {},
        "risk_tags": list(risk_tags or []),
        "approval_id": str(approval_id or "").strip() or None,
    }
    if require_approval is not None:
        payload["approval_required"] = bool(require_approval)
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "inspect-secret-reference",
            payload,
            allow_approval_required=bool(str(approval_id or "").strip()),
        )
    except rust_runtime_kernel_client.RustKernelApprovalRequired as exc:
        raise SecretAccessDeniedError(
            "approval_required",
            "High-risk credential access requires explicit approval before injection.",
        ) from exc
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(exc.reason or "secret_access_denied").strip()
        raise SecretAccessDeniedError(reason, "Secret access was denied by the Rust kernel.") from exc
    next_action = str(decision.get("next_action") or "").strip()
    rust_decision = str(decision.get("decision") or "").strip().lower()
    expected_next_action = _SECRET_REFERENCE_NEXT_ACTIONS.get(rust_decision)
    if expected_next_action and next_action != expected_next_action:
        raise SecretAccessDeniedError(
            "unexpected_next_action",
            f"Rust secret inspection returned unexpected next_action:{next_action or 'missing'}",
        )
    return dict(decision)


def issue_connector_secret_grant(
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    credential_id: str,
    connector_id: Optional[str] = None,
    connector_class: Optional[str] = None,
    action_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    run_id: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    allowed_fields: Optional[List[str]] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    purpose: Optional[str] = None,
    allowed_domains: Optional[List[str]] = None,
    allowed_tools: Optional[List[str]] = None,
    target_domain: Optional[str] = None,
    session_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    ttl_seconds: int = DEFAULT_SECRET_GRANT_TTL_SECONDS,
) -> SecretAccessGrant:
    issued_at = int(time.time())
    expires_at = issued_at + max(int(ttl_seconds or DEFAULT_SECRET_GRANT_TTL_SECONDS), 1)
    claims = {
        "version": _TOKEN_VERSION,
        "access_id": f"sgrant_{secrets.token_urlsafe(12)}",
        "secret_kind": "connector_credential",
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": normalize_workspace_id(workspace_id),
        "credential_id": str(credential_id or "").strip(),
        "provider_id": None,
        "connector_id": str(connector_id or "").strip().lower() or None,
        "connector_class": _normalize_connector_class(connector_class),
        "action_id": str(action_id or "").strip().lower() or None,
        "tool_name": str(tool_name or "").strip().lower() or None,
        "run_id": str(run_id or "").strip() or None,
        "runtime_mode": str(runtime_mode or "").strip().lower() or None,
        "allowed_fields": _normalize_allowed_fields(allowed_fields),
        "actor": {
            "type": str(actor_type or "runtime").strip().lower() or "runtime",
            "id": str(actor_id or "").strip() or None,
        },
        "purpose": str(purpose or "").strip().lower() or "connector_secret_resolution",
        "allowed_domains": _normalize_allowed_domains(allowed_domains),
        "allowed_tools": _normalize_allowed_tools(allowed_tools),
        "target_domain": _normalize_domain(target_domain) or None,
        "session_id": str(session_id or "").strip() or None,
        "approval_id": str(approval_id or "").strip() or None,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    payload = _b64encode(_json_dumps(claims).encode("utf-8"))
    signature = _b64encode(hmac.new(_signing_secret(), payload.encode("utf-8"), hashlib.sha256).digest())
    return SecretAccessGrant(token=f"{payload}.{signature}", claims=claims)


def issue_provider_secret_grant(
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    provider_id: str,
    credential_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    run_id: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    allowed_fields: Optional[List[str]] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    purpose: Optional[str] = None,
    allowed_domains: Optional[List[str]] = None,
    allowed_tools: Optional[List[str]] = None,
    target_domain: Optional[str] = None,
    session_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    ttl_seconds: int = DEFAULT_SECRET_GRANT_TTL_SECONDS,
) -> SecretAccessGrant:
    issued_at = int(time.time())
    expires_at = issued_at + max(int(ttl_seconds or DEFAULT_SECRET_GRANT_TTL_SECONDS), 1)
    claims = {
        "version": _TOKEN_VERSION,
        "access_id": f"sgrant_{secrets.token_urlsafe(12)}",
        "secret_kind": "provider_credential",
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": normalize_workspace_id(workspace_id),
        "credential_id": str(credential_id or "").strip() or None,
        "provider_id": str(provider_id or "").strip().lower(),
        "connector_id": None,
        "action_id": None,
        "tool_name": str(tool_name or "").strip().lower() or None,
        "run_id": str(run_id or "").strip() or None,
        "runtime_mode": str(runtime_mode or "").strip().lower() or None,
        "allowed_fields": _normalize_allowed_fields(allowed_fields),
        "actor": {
            "type": str(actor_type or "runtime").strip().lower() or "runtime",
            "id": str(actor_id or "").strip() or None,
        },
        "purpose": str(purpose or "").strip().lower() or "provider_secret_resolution",
        "allowed_domains": _normalize_allowed_domains(allowed_domains),
        "allowed_tools": _normalize_allowed_tools(allowed_tools),
        "target_domain": _normalize_domain(target_domain) or None,
        "session_id": str(session_id or "").strip() or None,
        "approval_id": str(approval_id or "").strip() or None,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    payload = _b64encode(_json_dumps(claims).encode("utf-8"))
    signature = _b64encode(hmac.new(_signing_secret(), payload.encode("utf-8"), hashlib.sha256).digest())
    return SecretAccessGrant(token=f"{payload}.{signature}", claims=claims)


def verify_secret_access_token(
    token: str,
    *,
    expected_secret_kind: str,
    expected_workspace_id: Optional[str] = None,
    expected_credential_id: Optional[str] = None,
    expected_provider_id: Optional[str] = None,
    expected_tool_name: Optional[str] = None,
    expected_domain: Optional[str] = None,
    expected_session_id: Optional[str] = None,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    raw = str(token or "").strip()
    if "." not in raw:
        raise SecretAccessDeniedError("invalid_token", "Secret access token is malformed.")
    payload_part, signature_part = raw.split(".", 1)
    expected_signature = _b64encode(hmac.new(_signing_secret(), payload_part.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature_part, expected_signature):
        raise SecretAccessDeniedError("invalid_token", "Secret access token signature is invalid.")
    try:
        claims = json.loads(_b64decode(payload_part))
    except Exception as error:
        raise SecretAccessDeniedError("invalid_token", "Secret access token payload is invalid.") from error
    if not isinstance(claims, dict):
        raise SecretAccessDeniedError("invalid_token", "Secret access token payload is invalid.")
    if str(claims.get("version") or "").strip() != _TOKEN_VERSION:
        raise SecretAccessDeniedError("invalid_token_version", "Secret access token version is invalid.")
    if _normalize_secret_kind(claims.get("secret_kind")) != _normalize_secret_kind(expected_secret_kind):
        raise SecretAccessDeniedError("secret_kind_mismatch", "Secret access token does not authorize this secret kind.")
    if _claim_is_revoked(claims):
        raise SecretAccessDeniedError("grant_revoked", "Secret access token was revoked.")
    current_time = int(now if now is not None else time.time())
    if current_time >= int(claims.get("expires_at") or 0):
        access_id = str(claims.get("access_id") or "").strip()
        if access_id:
            with _REVOCATION_LOCK:
                _REVOKED_GRANT_IDS.add(access_id)
        raise SecretAccessDeniedError("token_expired", "Secret access token expired before the secret was resolved.")
    if expected_workspace_id is not None:
        actual_workspace_id = normalize_workspace_id(claims.get("workspace_id"))
        if actual_workspace_id != normalize_workspace_id(expected_workspace_id):
            raise SecretAccessDeniedError("workspace_mismatch", "Secret access token does not match the active workspace.")
    if expected_credential_id and str(claims.get("credential_id") or "").strip() != str(expected_credential_id or "").strip():
        raise SecretAccessDeniedError("credential_mismatch", "Secret access token does not match the credential id.")
    if expected_provider_id and str(claims.get("provider_id") or "").strip().lower() != str(expected_provider_id or "").strip().lower():
        raise SecretAccessDeniedError("provider_mismatch", "Secret access token does not match the provider.")
    if expected_tool_name:
        claim_tool = str(claims.get("tool_name") or "").strip().lower()
        if claim_tool and claim_tool != str(expected_tool_name or "").strip().lower():
            raise SecretAccessDeniedError("tool_scope_mismatch", "Secret access token tool scope mismatch.")
        allowed_tools = _normalize_allowed_tools(list(claims.get("allowed_tools") or []))
        expected_tool = str(expected_tool_name or "").strip().lower()
        if allowed_tools and expected_tool not in allowed_tools:
            raise SecretAccessDeniedError("tool_scope_denied", "Tool is outside the secret grant scope.")
    if expected_domain:
        claim_target_domain = _normalize_domain(claims.get("target_domain"))
        normalized_expected_domain = _normalize_domain(expected_domain)
        if claim_target_domain and claim_target_domain != normalized_expected_domain:
            raise SecretAccessDeniedError("domain_scope_mismatch", "Secret access token domain scope mismatch.")
        allowed_domains = _normalize_allowed_domains(list(claims.get("allowed_domains") or []))
        if allowed_domains and not _domain_in_scope(normalized_expected_domain, allowed_domains):
            raise SecretAccessDeniedError("domain_scope_denied", "Domain is outside the secret grant scope.")
    if expected_session_id:
        if str(claims.get("session_id") or "").strip() != str(expected_session_id or "").strip():
            raise SecretAccessDeniedError("session_scope_mismatch", "Secret access token does not match the session.")
    return claims


def revoke_secret_grant_scope(
    *,
    access_id: Optional[str] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    added_access = False
    added_session = False
    added_run = False
    access_token = str(access_id or "").strip()
    session_token = str(session_id or "").strip()
    run_token = str(run_id or "").strip()
    with _REVOCATION_LOCK:
        if access_token and access_token not in _REVOKED_GRANT_IDS:
            _REVOKED_GRANT_IDS.add(access_token)
            added_access = True
        if session_token and session_token not in _REVOKED_SESSION_IDS:
            _REVOKED_SESSION_IDS.add(session_token)
            added_session = True
        if run_token and run_token not in _REVOKED_RUN_IDS:
            _REVOKED_RUN_IDS.add(run_token)
            added_run = True
    return {
        "ok": True,
        "access_revoked": added_access,
        "session_revoked": added_session,
        "run_revoked": added_run,
    }


def reset_secret_grant_revocations_for_tests() -> None:
    with _REVOCATION_LOCK:
        _REVOKED_GRANT_IDS.clear()
        _REVOKED_SESSION_IDS.clear()
        _REVOKED_RUN_IDS.clear()


def _project_secret_payload(payload: Dict[str, Any], allowed_fields: List[str]) -> Dict[str, Any]:
    if not allowed_fields:
        return dict(payload)
    projected: Dict[str, Any] = {}
    allowed_set = set(allowed_fields)
    for key, value in payload.items():
        if key.startswith("_") or key in allowed_set:
            projected[key] = value
    return projected


def _run_coro_sync(coro: Any) -> Any:
    # Delegates to the shared bridge loop — see sync_asyncio_bridge for why
    # the previous per-call asyncio.run() pattern was a pool-init/Postgres-
    # connection leak, and why one persistent loop fixes it.
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


def _resolve_bound_connector_credential_id(
    *, tenant_id: Optional[str], workspace_id: str, agent_id: str, connector_key: str,
) -> Optional[str]:
    """The credential_id an ENABLED agent_connector_bindings row points this
    agent at for `connector_key`, or None. This is the reuse-path half of the
    per-agent connector model (connectors_actions.
    subscribe_agent_to_project_credential writes the binding; this reads it)
    — used by resolve_provider_secret's agent-identified branch as the
    fallback when the agent owns no vault_credentials row of its own (i.e.
    it is a SUBSCRIBED agent, not the one that originally connected the
    credential). Never raises; returns None on any lookup failure so the
    caller's original RuntimeError still surfaces."""
    try:
        from server_modules import agent_bindings_repository
        rows = _run_coro_sync(
            agent_bindings_repository.list_agent_connector_bindings(
                tenant_id=str(tenant_id or "default").strip() or "default",
                workspace_id=str(workspace_id or "").strip(),
                agent_install_id=str(agent_id or "").strip(),
                enabled_only=True,
            )
        )
    except Exception:
        return None
    normalized_key = str(connector_key or "").strip().lower()
    for row in rows or []:
        if str((row or {}).get("key") or "").strip().lower() != normalized_key:
            continue
        candidate_id = str(((row or {}).get("binding") or {}).get("credential_id") or "").strip()
        if candidate_id:
            return candidate_id
    return None


def _sanitize_token(value: Any) -> str:
    token = str(value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _parse_hosted_provider_secret_bundle() -> Dict[str, Any]:
    raw = str(os.getenv(_HOSTED_PROVIDER_SECRET_BUNDLE_ENV) or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _hosted_bundle_provider_payload(provider_id: str) -> Dict[str, Any]:
    bundle = _parse_hosted_provider_secret_bundle()
    if not bundle:
        return {}
    providers = bundle.get("providers")
    if isinstance(providers, dict):
        nested = providers.get(provider_id)
        if isinstance(nested, dict):
            return nested
    direct = bundle.get(provider_id)
    if isinstance(direct, dict):
        return direct
    if provider_id == "openai" and isinstance(bundle.get("openai"), dict):
        return dict(bundle.get("openai") or {})
    return {}


def _append_hosted_provider_secret_audit(
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    provider_id: str,
    field: str,
    source_kind: str,
    source_label: str,
    tool_name: Optional[str],
    run_id: Optional[str],
    actor_type: Optional[str],
    actor_id: Optional[str],
    purpose: Optional[str],
    status: str,
    denial_code: Optional[str] = None,
) -> None:
    resolved_tenant_id = str(tenant_id or "default").strip() or "default"
    resolved_workspace_id = normalize_workspace_id(workspace_id)
    if not resolved_workspace_id:
        from server_modules import workspace_scope as _ws

        resolved_workspace_id = _ws.resolve_workspace(
            workspace_id, site="secrets_broker:hosted_secret_audit"
        )
    try:
        _run_coro_sync(
            control_plane_repository.append_agent_secret_access_event(
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                secret_kind="platform_hosted_provider_secret",
                provider_id=provider_id,
                connector_id=None,
                credential_id=None,
                action_id=None,
                tool_name=str(tool_name or "").strip().lower() or "hosted_provider_secret_resolution",
                run_id=str(run_id or "").strip() or None,
                actor={
                    "type": str(actor_type or "platform_runtime").strip().lower() or "platform_runtime",
                    "id": str(actor_id or "").strip() or None,
                },
                allowed_fields=[str(field or "").strip() or "api_key"],
                metadata={
                    "purpose": str(purpose or "hosted_provider_secret_resolution").strip().lower() or "hosted_provider_secret_resolution",
                    "field": str(field or "api_key").strip().lower() or "api_key",
                    "source_kind": str(source_kind or "").strip().lower() or None,
                    "source_label": str(source_label or "").strip() or None,
                    "ownership": "platform_hosted",
                },
                status=str(status or "allowed").strip().lower() or "allowed",
                denial_code=str(denial_code or "").strip().lower() or None,
            )
        )
    except Exception:
        return


def resolve_hosted_provider_secret(
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    provider_id: str,
    field: str = "api_key",
    env_var_candidates: Optional[List[str]] = None,
    allow_env_fallback: bool = True,
    tool_name: Optional[str] = None,
    run_id: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    purpose: Optional[str] = None,
) -> HostedProviderSecretResolution:
    normalized_provider_id = str(provider_id or "").strip().lower()
    normalized_field = str(field or "api_key").strip().lower() or "api_key"
    bundle_payload = _hosted_bundle_provider_payload(normalized_provider_id)
    bundle_value = _sanitize_token(bundle_payload.get(normalized_field))
    if bundle_value:
        _append_hosted_provider_secret_audit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            provider_id=normalized_provider_id,
            field=normalized_field,
            source_kind="managed_bundle",
            source_label=f"{_HOSTED_PROVIDER_SECRET_BUNDLE_ENV}:{normalized_provider_id}.{normalized_field}",
            tool_name=tool_name,
            run_id=run_id,
            actor_type=actor_type,
            actor_id=actor_id,
            purpose=purpose,
            status="allowed",
        )
        return HostedProviderSecretResolution(
            provider_id=normalized_provider_id,
            field=normalized_field,
            value=bundle_value,
            source=f"bundle-{normalized_provider_id}",
            source_kind="managed_bundle",
            bootstrap_fallback=False,
            owner_kind="platform_hosted",
            owner_label="Empyralis hosted runtime",
        )

    if allow_env_fallback:
        candidates = list(env_var_candidates or _HOSTED_PROVIDER_ENV_CANDIDATES.get(normalized_provider_id) or [])
        for env_name in candidates:
            env_value = _sanitize_token(os.getenv(str(env_name)))
            if not env_value:
                continue
            _append_hosted_provider_secret_audit(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                provider_id=normalized_provider_id,
                field=normalized_field,
                source_kind="env_bootstrap",
                source_label=f"env:{str(env_name)}",
                tool_name=tool_name,
                run_id=run_id,
                actor_type=actor_type,
                actor_id=actor_id,
                purpose=purpose,
                status="allowed",
            )
            return HostedProviderSecretResolution(
                provider_id=normalized_provider_id,
                field=normalized_field,
                value=env_value,
                source=f"env:{str(env_name)}",
                source_kind="env_bootstrap",
                bootstrap_fallback=True,
                owner_kind="platform_hosted",
                owner_label="Empyralis hosted runtime",
            )

    _append_hosted_provider_secret_audit(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        provider_id=normalized_provider_id,
        field=normalized_field,
        source_kind="missing",
        source_label=f"{_HOSTED_PROVIDER_SECRET_BUNDLE_ENV}:{normalized_provider_id}.{normalized_field}",
        tool_name=tool_name,
        run_id=run_id,
        actor_type=actor_type,
        actor_id=actor_id,
        purpose=purpose,
        status="denied",
        denial_code="hosted_provider_secret_missing",
    )
    return HostedProviderSecretResolution(
        provider_id=normalized_provider_id,
        field=normalized_field,
        value="",
        source="missing",
        source_kind="missing",
        bootstrap_fallback=False,
        owner_kind="platform_hosted",
        owner_label="Empyralis hosted runtime",
    )


def resolve_hosted_openai_bearer(
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    fallback_token: Any,
    fallback_source: Any,
    tool_name: Optional[str] = None,
    run_id: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    purpose: Optional[str] = None,
) -> HostedProviderSecretResolution:
    fallback_token_text = _sanitize_token(fallback_token)
    fallback_source_text = str(fallback_source or "").strip().lower() or "none"
    if fallback_token_text:
        _append_hosted_provider_secret_audit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            provider_id="openai",
            field="bearer",
            source_kind="env_bootstrap",
            source_label=fallback_source_text,
            tool_name=tool_name,
            run_id=run_id,
            actor_type=actor_type,
            actor_id=actor_id,
            purpose=purpose,
            status="allowed",
        )
        return HostedProviderSecretResolution(
            provider_id="openai",
            field="bearer",
            value=fallback_token_text,
            source=fallback_source_text,
            source_kind="env_bootstrap",
            bootstrap_fallback=True,
            owner_kind="platform_hosted",
            owner_label="Empyralis hosted runtime",
        )

    bundle_payload = _hosted_bundle_provider_payload("openai")
    for source_label, field in _HOSTED_OPENAI_BEARER_KEYS:
        bundle_value = _sanitize_token(bundle_payload.get(field))
        if not bundle_value:
            continue
        _append_hosted_provider_secret_audit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            provider_id="openai",
            field=field,
            source_kind="managed_bundle",
            source_label=f"{_HOSTED_PROVIDER_SECRET_BUNDLE_ENV}:openai.{field}",
            tool_name=tool_name,
            run_id=run_id,
            actor_type=actor_type,
            actor_id=actor_id,
            purpose=purpose,
            status="allowed",
        )
        return HostedProviderSecretResolution(
            provider_id="openai",
            field=field,
            value=bundle_value,
            source=source_label,
            source_kind="managed_bundle",
            bootstrap_fallback=False,
            owner_kind="platform_hosted",
            owner_label="Empyralis hosted runtime",
        )

    _append_hosted_provider_secret_audit(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        provider_id="openai",
        field="bearer",
        source_kind="missing",
        source_label=f"{_HOSTED_PROVIDER_SECRET_BUNDLE_ENV}:openai",
        tool_name=tool_name,
        run_id=run_id,
        actor_type=actor_type,
        actor_id=actor_id,
        purpose=purpose,
        status="denied",
        denial_code="hosted_provider_secret_missing",
    )
    return HostedProviderSecretResolution(
        provider_id="openai",
        field="bearer",
        value="",
        source="none",
        source_kind="missing",
        bootstrap_fallback=False,
        owner_kind="platform_hosted",
        owner_label="Empyralis hosted runtime",
    )


def _append_secret_access_audit(claims: Dict[str, Any], *, status: str, denial_code: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
    tenant_id = str(claims.get("tenant_id") or "").strip()
    workspace_id = normalize_workspace_id(claims.get("workspace_id"))
    if not tenant_id or not workspace_id:
        return
    _run_coro_sync(
        control_plane_repository.append_agent_secret_access_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            secret_kind=str(claims.get("secret_kind") or "").strip().lower(),
            credential_id=str(claims.get("credential_id") or "").strip() or None,
            provider_id=str(claims.get("provider_id") or "").strip().lower() or None,
            connector_id=str(claims.get("connector_id") or "").strip().lower() or None,
            metadata={
                **(metadata or {}),
                "connector_class": str(claims.get("connector_class") or "").strip().lower() or None,
            },
            action_id=str(claims.get("action_id") or "").strip().lower() or None,
            tool_name=str(claims.get("tool_name") or "").strip().lower() or None,
            run_id=str(claims.get("run_id") or "").strip() or None,
            actor=claims.get("actor") if isinstance(claims.get("actor"), dict) else {},
            allowed_fields=[str(item) for item in list(claims.get("allowed_fields") or [])],
            status=status,
            denial_code=denial_code,
        )
    )


def _raise_if_connector_access_disabled(
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    connector_id: str,
    credential_id: Optional[str] = None,
) -> None:
    state = safe_mode_service.resolve_connector_disable_state(
        tenant_id=str(tenant_id or "").strip() or None,
        workspace_id=workspace_id,
        connector_id=connector_id,
        credential_id=credential_id,
    )
    if not bool(state.get("active")):
        return
    metadata = dict((state.get("matched_chain") or [{}])[-1].get("metadata") or {})
    if bool(metadata.get("requires_rotation")):
        raise SecretAccessDeniedError("credential_rotation_required", "This credential was revoked and must be rotated before reuse.")
    if str(metadata.get("status") or "").strip().lower() == "rotated":
        raise SecretAccessDeniedError("credential_rotated", "This credential was rotated out of service and can no longer be used.")
    raise SecretAccessDeniedError("connector_disabled", "This connector is disabled by a security control.")


def resolve_connector_secret(
    load_vault_fn: LoadVaultFn,
    decrypt_fn: DecryptFn,
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    credential_id: str,
    connector_id: Optional[str] = None,
    connector_class: Optional[str] = None,
    action_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    run_id: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    allowed_fields: Optional[List[str]] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    purpose: Optional[str] = None,
    target_domain: Optional[str] = None,
    allowed_domains: Optional[List[str]] = None,
    allowed_tools: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    require_approval: Optional[bool] = None,
    approval_id: Optional[str] = None,
    approval_actor_id: Optional[str] = None,
    approval_reason: Optional[str] = None,
    credential_risk_tags: Optional[List[str]] = None,
    ttl_seconds: int = DEFAULT_SECRET_GRANT_TTL_SECONDS,
) -> Dict[str, Any]:
    normalized_tool_name = str(tool_name or "").strip().lower() or None
    normalized_target_domain = _normalize_domain(target_domain) or None
    grant = issue_connector_secret_grant(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        credential_id=credential_id,
        connector_id=connector_id,
        connector_class=connector_class,
        action_id=action_id,
        tool_name=normalized_tool_name,
        run_id=run_id,
        runtime_mode=runtime_mode,
        allowed_fields=allowed_fields,
        actor_type=actor_type,
        actor_id=actor_id,
        purpose=purpose,
        allowed_domains=allowed_domains,
        allowed_tools=allowed_tools,
        target_domain=normalized_target_domain,
        session_id=session_id,
        approval_id=approval_id,
        ttl_seconds=ttl_seconds,
    )
    try:
        claims = verify_secret_access_token(
            grant.token,
            expected_secret_kind="connector_credential",
            expected_workspace_id=workspace_id,
            expected_credential_id=credential_id,
            expected_tool_name=normalized_tool_name,
            expected_domain=normalized_target_domain,
            expected_session_id=session_id,
        )
        expected_connector_id = str(claims.get("connector_id") or connector_id or "").strip().lower()
        if expected_connector_id:
            _raise_if_connector_access_disabled(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                connector_id=expected_connector_id,
                credential_id=credential_id,
            )
        secret = _raw_resolve_vault_credential(load_vault_fn, decrypt_fn, credential_id, workspace_id)
        resolved_provider = str(secret.get("_provider") or "").strip().lower()
        if expected_connector_id and resolved_provider and resolved_provider != expected_connector_id:
            raise SecretAccessDeniedError("connector_mismatch", "Resolved credential does not match the requested connector.")
        metadata = secret.get("_metadata") if isinstance(secret.get("_metadata"), dict) else {}
        secret_decision = _inspect_secret_reference_with_rust(
            name=credential_id or expected_connector_id or "connector_credential",
            action_class="read",
            provider_id=resolved_provider or None,
            connector_id=expected_connector_id or None,
            credential_id=credential_id,
            connector_class=claims.get("connector_class"),
            metadata=metadata,
            risk_tags=credential_risk_tags,
            require_approval=require_approval,
            approval_id=approval_id,
        )
        requires_approval = bool(secret_decision.get("approval_required") or secret_decision.get("high_risk_credential"))
        projected = _project_secret_payload(secret, _normalize_allowed_fields(list(claims.get("allowed_fields") or [])))
        _append_secret_access_audit(
            claims,
            status="allowed",
            metadata={
                "purpose": claims.get("purpose"),
                "projected": bool(claims.get("allowed_fields")),
                "resolved_provider": resolved_provider or None,
                "target_domain": normalized_target_domain,
                "allowed_domains": _normalize_allowed_domains(list(claims.get("allowed_domains") or [])),
                "allowed_tools": _normalize_allowed_tools(list(claims.get("allowed_tools") or [])),
                "requires_approval": requires_approval,
                "rust_secret_decision": {
                    "decision_id": secret_decision.get("decision_id"),
                    "decision": secret_decision.get("decision"),
                    "reason": secret_decision.get("reason"),
                    "risk_level": secret_decision.get("risk_level"),
                    "high_risk_credential": bool(secret_decision.get("high_risk_credential")),
                },
                "approval_id": str(approval_id or "").strip() or None,
                "approval_actor_id": str(approval_actor_id or "").strip() or None,
                "approval_reason": str(approval_reason or "").strip() or None,
            },
        )
        return projected
    except SecretAccessDeniedError as error:
        _append_secret_access_audit(
            grant.claims,
            status="denied",
            denial_code=error.code,
            metadata={"purpose": grant.claims.get("purpose")},
        )
        raise


def resolve_provider_secret(
    load_vault_fn: LoadVaultFn,
    decrypt_fn: DecryptFn,
    *,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    provider_id: str,
    credential_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    run_id: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    allowed_fields: Optional[List[str]] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    purpose: Optional[str] = None,
    target_domain: Optional[str] = None,
    allowed_domains: Optional[List[str]] = None,
    allowed_tools: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    require_approval: Optional[bool] = None,
    approval_id: Optional[str] = None,
    approval_actor_id: Optional[str] = None,
    approval_reason: Optional[str] = None,
    credential_risk_tags: Optional[List[str]] = None,
    ttl_seconds: int = DEFAULT_SECRET_GRANT_TTL_SECONDS,
) -> Dict[str, Any]:
    normalized_provider_id = str(provider_id or "").strip().lower()
    normalized_tool_name = str(tool_name or "").strip().lower() or None
    normalized_target_domain = _normalize_domain(target_domain) or None
    grant = issue_provider_secret_grant(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        provider_id=normalized_provider_id,
        credential_id=credential_id,
        tool_name=normalized_tool_name,
        run_id=run_id,
        runtime_mode=runtime_mode,
        allowed_fields=allowed_fields,
        actor_type=actor_type,
        actor_id=actor_id,
        purpose=purpose,
        allowed_domains=allowed_domains,
        allowed_tools=allowed_tools,
        target_domain=normalized_target_domain,
        session_id=session_id,
        approval_id=approval_id,
        ttl_seconds=ttl_seconds,
    )
    try:
        claims = verify_secret_access_token(
            grant.token,
            expected_secret_kind="provider_credential",
            expected_workspace_id=workspace_id,
            expected_credential_id=credential_id,
            expected_provider_id=normalized_provider_id,
            expected_tool_name=normalized_tool_name,
            expected_domain=normalized_target_domain,
            expected_session_id=session_id,
        )
        _raise_if_connector_access_disabled(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            connector_id=normalized_provider_id,
            credential_id=credential_id,
        )
        if credential_id:
            secret = _raw_resolve_vault_credential(load_vault_fn, decrypt_fn, credential_id, workspace_id)
        else:
            # Stage 4B wiring: a tool call made on behalf of a specific AGENT
            # (actor_type="agent") must resolve that agent's own credential —
            # or a genuinely workspace-shared/unassigned one — never a row
            # bound to a DIFFERENT agent. resolve_agent_credential already
            # implements this scoring (exact agent match > unassigned
            # workspace-default > reject); it raises rather than silently
            # falling back to another agent's row. Non-agent actors (the
            # runtime resolving its own model-provider key, workflow runs,
            # provider-profile candidates, etc.) are unaffected — they never
            # pass actor_type="agent", so they keep today's
            # (provider, workspace) "most recently updated" behavior.
            agent_actor_id = str(actor_id or "").strip()
            agent_workspace_id = normalize_workspace_id(workspace_id)
            if str(actor_type or "").strip().lower() == "agent" and agent_actor_id and agent_workspace_id:
                try:
                    secret = _raw_resolve_agent_credential(
                        load_vault_fn,
                        decrypt_fn,
                        normalized_provider_id,
                        agent_workspace_id,
                        agent_actor_id,
                    )
                except RuntimeError:
                    # §1.2 (Multiplayer Projects plan): resolve_agent_credential
                    # only matches vault_credentials.agent_install_id — it has
                    # no idea about the OTHER half of the reuse model, an
                    # enabled agent_connector_bindings row
                    # (connectors_actions.subscribe_agent_to_project_credential)
                    # that points a SUBSCRIBED (non-connecting) agent at
                    # someone else's project-scoped credential. Before giving
                    # up, check for exactly that: an enabled binding for
                    # (workspace_id, agent_id, connector_key=provider_id), and
                    # resolve ITS credential_id directly — never the unscoped
                    # "most recently updated in the workspace" fallback
                    # runs_execution.py's _workflow_tool_connector_secret uses
                    # (that's a separate, milder risk, not copied here).
                    bound_credential_id = _resolve_bound_connector_credential_id(
                        tenant_id=tenant_id,
                        workspace_id=agent_workspace_id,
                        agent_id=agent_actor_id,
                        connector_key=normalized_provider_id,
                    )
                    if not bound_credential_id:
                        raise
                    secret = _raw_resolve_vault_credential(
                        load_vault_fn, decrypt_fn, bound_credential_id, agent_workspace_id,
                    )
            else:
                secret = _raw_resolve_default_vault_credential(load_vault_fn, decrypt_fn, normalized_provider_id, workspace_id)
        resolved_provider = str(secret.get("_provider") or "").strip().lower()
        if resolved_provider and resolved_provider != normalized_provider_id:
            raise SecretAccessDeniedError("provider_mismatch", "Resolved credential does not match the requested provider.")
        metadata = secret.get("_metadata") if isinstance(secret.get("_metadata"), dict) else {}
        secret_decision = _inspect_secret_reference_with_rust(
            name=credential_id or resolved_provider or normalized_provider_id or "provider_credential",
            action_class="read",
            provider_id=resolved_provider or normalized_provider_id,
            credential_id=credential_id,
            metadata=metadata,
            risk_tags=credential_risk_tags,
            require_approval=require_approval,
            approval_id=approval_id,
        )
        requires_approval = bool(secret_decision.get("approval_required") or secret_decision.get("high_risk_credential"))
        projected = _project_secret_payload(secret, _normalize_allowed_fields(list(claims.get("allowed_fields") or [])))
        _append_secret_access_audit(
            claims,
            status="allowed",
            metadata={
                "purpose": claims.get("purpose"),
                "projected": bool(claims.get("allowed_fields")),
                "resolved_provider": resolved_provider or normalized_provider_id,
                "target_domain": normalized_target_domain,
                "allowed_domains": _normalize_allowed_domains(list(claims.get("allowed_domains") or [])),
                "allowed_tools": _normalize_allowed_tools(list(claims.get("allowed_tools") or [])),
                "requires_approval": requires_approval,
                "rust_secret_decision": {
                    "decision_id": secret_decision.get("decision_id"),
                    "decision": secret_decision.get("decision"),
                    "reason": secret_decision.get("reason"),
                    "risk_level": secret_decision.get("risk_level"),
                    "high_risk_credential": bool(secret_decision.get("high_risk_credential")),
                },
                "approval_id": str(approval_id or "").strip() or None,
                "approval_actor_id": str(approval_actor_id or "").strip() or None,
                "approval_reason": str(approval_reason or "").strip() or None,
            },
        )
        return projected
    except SecretAccessDeniedError as error:
        _append_secret_access_audit(
            grant.claims,
            status="denied",
            denial_code=error.code,
            metadata={"purpose": grant.claims.get("purpose")},
        )
        raise

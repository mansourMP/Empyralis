from __future__ import annotations

import asyncio
import contextlib
import contextvars
import time
import uuid
from typing import Any, Dict, Iterator, Optional
from urllib.parse import urlparse

from server_modules import control_plane_repository
from server_modules.url_security import assert_safe_outbound_url


_ACTIVE_EGRESS_POLICY: contextvars.ContextVar[Dict[str, Any] | None] = contextvars.ContextVar(
    "empyralis_active_egress_policy",
    default=None,
)

_DEFAULT_ALLOWED_PROVIDER_IDS = (
    "openai",
    "anthropic",
    "google",
    "groq",
    "openrouter",
    "xai",
    "azure_openai",
    "deepseek",
    "ollama_cloud",
)

_PROVIDER_HOST_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "anthropic": ("api.anthropic.com",),
    "azure_openai": ("*.openai.azure.com",),
    "deepseek": ("api.deepseek.com",),
    "google": (
        "generativelanguage.googleapis.com",
        "aiplatform.googleapis.com",
        "*.aiplatform.googleapis.com",
    ),
    "groq": ("api.groq.com",),
    "openai": ("api.openai.com",),
    "openrouter": ("openrouter.ai", "api.openrouter.ai"),
    "ollama_cloud": ("ollama.com",),
    "xai": ("api.x.ai",),
}

_CONNECTOR_HOST_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "calendar": ("www.googleapis.com", "graph.microsoft.com", "api.nylas.com"),
    "crm": (),
    "email": (
        "gmail.googleapis.com",
        "graph.microsoft.com",
        "api.mailgun.net",
        "api.sendgrid.com",
        "api.resend.com",
        "api.postmarkapp.com",
    ),
    "inventory": (),
    "task_runner": (),
    "web": (),
}

_ACTION_CLASS_ORDER = {"read": 0, "write": 1, "execute": 2}
_HTTP_METHOD_TO_ACTION_CLASS = {
    "DELETE": "write",
    "GET": "read",
    "PATCH": "write",
    "POST": "write",
    "PUT": "write",
}


class EgressPolicyDeniedError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = str(code or "egress_denied").strip().lower() or "egress_denied"
        self.detail = str(detail or "Outbound egress policy denied the request.").strip()


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _ordered_unique(values: list[Any] | tuple[Any, ...]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = _normalize_token(raw)
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _normalize_host_pattern(value: Any) -> str:
    token = _normalize_token(value)
    if token.startswith("https://") or token.startswith("http://"):
        token = _normalize_token(urlparse(token).hostname or token)
    return token


def _merge_lists(*values: list[str] | tuple[str, ...]) -> list[str]:
    combined: list[str] = []
    for items in values:
        combined.extend(list(items))
    return _ordered_unique(combined)


def _host_matches_pattern(host: str, pattern: str) -> bool:
    normalized_host = _normalize_token(host)
    normalized_pattern = _normalize_host_pattern(pattern)
    if not normalized_host or not normalized_pattern:
        return False
    if normalized_pattern.startswith("*."):
        suffix = normalized_pattern[2:]
        return normalized_host.endswith(f".{suffix}")
    return normalized_host == normalized_pattern or normalized_host.endswith(f".{normalized_pattern}")


def _allowed_hosts_for_providers(provider_ids: list[str]) -> list[str]:
    hosts: list[str] = []
    for provider_id in provider_ids:
        hosts.extend(_PROVIDER_HOST_ALLOWLISTS.get(_normalize_token(provider_id), ()))
    return _ordered_unique(hosts)


def _allowed_hosts_for_connector_scopes(scopes: list[str]) -> list[str]:
    hosts: list[str] = []
    for scope in scopes:
        hosts.extend(_CONNECTOR_HOST_ALLOWLISTS.get(_normalize_token(scope), ()))
    return _ordered_unique(hosts)


def current_egress_policy() -> Dict[str, Any] | None:
    value = _ACTIVE_EGRESS_POLICY.get()
    return dict(value) if isinstance(value, dict) else None


def build_egress_policy(
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_mode: str,
    runtime_scope: Dict[str, Any] | None = None,
    agent_install_id: str | None = None,
    run_id: str | None = None,
    tool_name: str | None = None,
    provider_id: str | None = None,
    connector_scopes: list[str] | tuple[str, ...] | None = None,
    allowed_action_classes: list[str] | tuple[str, ...] | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    scope = _coerce_dict(runtime_scope)
    network_policy = _coerce_dict(scope.get("network_policy"))
    normalized_connector_scopes = _ordered_unique(list(connector_scopes or []))
    configured_allowed_hosts = _ordered_unique(
        [
            _normalize_host_pattern(item)
            for item in list(network_policy.get("allowed_hosts") or network_policy.get("allowed_domains") or [])
        ]
    )
    configured_provider_ids = _ordered_unique(
        list(network_policy.get("allowed_providers") or _DEFAULT_ALLOWED_PROVIDER_IDS)
    )
    provider_hosts = _allowed_hosts_for_providers(configured_provider_ids)
    connector_hosts = _allowed_hosts_for_connector_scopes(normalized_connector_scopes)
    return {
        "policy_id": f"egp_{uuid.uuid4().hex[:16]}",
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "agent_install_id": str(agent_install_id or "").strip() or None,
        "run_id": str(run_id or "").strip() or None,
        "runtime_mode": _normalize_token(runtime_mode) or "hosted_secure",
        "tool_name": str(tool_name or "").strip().lower() or None,
        "provider_id": _normalize_token(provider_id) or None,
        "active_connector_scopes": normalized_connector_scopes,
        "allowed_action_classes": _ordered_unique(list(allowed_action_classes or [])),
        "allow_outbound": bool(network_policy.get("allow_outbound", True)),
        "allow_private_hosts": bool(network_policy.get("allow_private_hosts", False)),
        "allowed_hosts": _merge_lists(configured_allowed_hosts, provider_hosts, connector_hosts),
        "allowed_providers": configured_provider_ids,
        "metadata": _coerce_dict(metadata),
        "policy_snapshot": {
            "mode": str(network_policy.get("mode") or "allowlist_hooks").strip() or "allowlist_hooks",
            "hooks_ready": bool(network_policy.get("hooks_ready", False)),
            "configured_allowed_hosts": configured_allowed_hosts,
            "configured_allowed_providers": configured_provider_ids,
        },
    }


def _merge_policy(parent: Dict[str, Any] | None, child: Dict[str, Any]) -> Dict[str, Any]:
    if not parent:
        return dict(child)
    return {
        "policy_id": str(child.get("policy_id") or parent.get("policy_id") or f"egp_{uuid.uuid4().hex[:16]}"),
        "tenant_id": str(child.get("tenant_id") or parent.get("tenant_id") or "").strip(),
        "workspace_id": str(child.get("workspace_id") or parent.get("workspace_id") or "").strip(),
        "agent_install_id": child.get("agent_install_id") or parent.get("agent_install_id"),
        "run_id": child.get("run_id") or parent.get("run_id"),
        "runtime_mode": str(child.get("runtime_mode") or parent.get("runtime_mode") or "hosted_secure"),
        "tool_name": child.get("tool_name") or parent.get("tool_name"),
        "provider_id": child.get("provider_id") or parent.get("provider_id"),
        "active_connector_scopes": _merge_lists(
            list(parent.get("active_connector_scopes") or []),
            list(child.get("active_connector_scopes") or []),
        ),
        "allowed_action_classes": (
            _merge_lists(list(parent.get("allowed_action_classes") or []), list(child.get("allowed_action_classes") or []))
            or []
        ),
        "allow_outbound": bool(parent.get("allow_outbound", True)) and bool(child.get("allow_outbound", True)),
        "allow_private_hosts": bool(parent.get("allow_private_hosts", False)) and bool(child.get("allow_private_hosts", False)),
        "allowed_hosts": _merge_lists(list(parent.get("allowed_hosts") or []), list(child.get("allowed_hosts") or [])),
        "allowed_providers": _merge_lists(list(parent.get("allowed_providers") or []), list(child.get("allowed_providers") or [])),
        "metadata": {
            **_coerce_dict(parent.get("metadata")),
            **_coerce_dict(child.get("metadata")),
        },
        "policy_snapshot": {
            **_coerce_dict(parent.get("policy_snapshot")),
            **_coerce_dict(child.get("policy_snapshot")),
        },
    }


@contextlib.contextmanager
def activate_egress_policy(policy: Dict[str, Any], *, merge_with_current: bool = True) -> Iterator[Dict[str, Any]]:
    active = _merge_policy(current_egress_policy(), policy) if merge_with_current else dict(policy)
    token = _ACTIVE_EGRESS_POLICY.set(active)
    try:
        yield active
    finally:
        _ACTIVE_EGRESS_POLICY.reset(token)


def _dispatch_async(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    loop.create_task(coro)


def _request_action_class(method: str) -> str:
    normalized_method = str(method or "GET").strip().upper() or "GET"
    return _HTTP_METHOD_TO_ACTION_CLASS.get(normalized_method, "execute")


def _action_allowed(policy: Dict[str, Any], action_class: str) -> bool:
    allowed = _ordered_unique(list(policy.get("allowed_action_classes") or []))
    if not allowed:
        return True
    required_rank = _ACTION_CLASS_ORDER.get(_normalize_token(action_class), 99)
    granted_rank = max((_ACTION_CLASS_ORDER.get(token, -1) for token in allowed), default=-1)
    return granted_rank >= required_rank


def _audit_outbound_decision(
    *,
    policy: Dict[str, Any],
    url: str,
    method: str,
    host: str,
    action_class: str,
    status: str,
    denial_code: str | None = None,
) -> None:
    tenant_id = str(policy.get("tenant_id") or "").strip()
    workspace_id = str(policy.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        return
    metadata = {
        "url": str(url or "").strip(),
        "host": _normalize_token(host),
        "method": str(method or "GET").strip().upper() or "GET",
        "action_class": _normalize_token(action_class) or "read",
        "policy": {
            "allow_outbound": bool(policy.get("allow_outbound", True)),
            "allow_private_hosts": bool(policy.get("allow_private_hosts", False)),
            "allowed_hosts": list(policy.get("allowed_hosts") or []),
            "allowed_providers": list(policy.get("allowed_providers") or []),
            "active_connector_scopes": list(policy.get("active_connector_scopes") or []),
            "allowed_action_classes": list(policy.get("allowed_action_classes") or []),
        },
        "metadata": _coerce_dict(policy.get("metadata")),
    }
    _dispatch_async(
        control_plane_repository.append_agent_egress_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_install_id=str(policy.get("agent_install_id") or "").strip() or None,
            run_id=str(policy.get("run_id") or "").strip() or None,
            runtime_mode=str(policy.get("runtime_mode") or "").strip() or None,
            tool_name=str(policy.get("tool_name") or "").strip() or None,
            provider_id=str(policy.get("provider_id") or "").strip() or None,
            connector_scope=(
                list(policy.get("active_connector_scopes") or [None])[0]
                if list(policy.get("active_connector_scopes") or [])
                else None
            ),
            action_class=_normalize_token(action_class) or "read",
            request_method=str(method or "GET").strip().upper() or "GET",
            request_url=str(url or "").strip(),
            request_host=_normalize_token(host) or None,
            status=str(status or "allowed").strip().lower() or "allowed",
            denial_code=str(denial_code or "").strip().lower() or None,
            metadata=metadata,
            event_id=f"eevt_{uuid.uuid4().hex[:16]}",
        )
    )


def _host_allowed_by_policy(policy: Dict[str, Any], host: str) -> bool:
    normalized_host = _normalize_token(host)
    if not normalized_host:
        return False
    for pattern in list(policy.get("allowed_hosts") or []):
        if _host_matches_pattern(normalized_host, str(pattern)):
            return True
    return False


def enforce_outbound_request(
    *,
    url: str,
    method: str,
) -> Dict[str, Any] | None:
    policy = current_egress_policy()
    if not policy:
        return None
    normalized_url = str(url or "").strip()
    parsed = urlparse(normalized_url)
    host = _normalize_token(parsed.hostname or "")
    normalized_method = str(method or "GET").strip().upper() or "GET"
    action_class = _request_action_class(normalized_method)
    try:
        assert_safe_outbound_url(normalized_url, allow_private_host=bool(policy.get("allow_private_hosts", False)))
    except Exception as error:
        _audit_outbound_decision(
            policy=policy,
            url=normalized_url,
            method=normalized_method,
            host=host,
            action_class=action_class,
            status="denied",
            denial_code="private_or_invalid_host",
        )
        raise EgressPolicyDeniedError(
            "private_or_invalid_host",
            f"Outbound request to '{normalized_url}' was denied by the egress policy: {error}",
        ) from error
    if not bool(policy.get("allow_outbound", True)):
        _audit_outbound_decision(
            policy=policy,
            url=normalized_url,
            method=normalized_method,
            host=host,
            action_class=action_class,
            status="denied",
            denial_code="outbound_disabled",
        )
        raise EgressPolicyDeniedError(
            "outbound_disabled",
            "Outbound network access is disabled for this runtime.",
        )
    if not _action_allowed(policy, action_class):
        _audit_outbound_decision(
            policy=policy,
            url=normalized_url,
            method=normalized_method,
            host=host,
            action_class=action_class,
            status="denied",
            denial_code="action_class_not_allowed",
        )
        raise EgressPolicyDeniedError(
            "action_class_not_allowed",
            f"The active egress policy does not allow {action_class} outbound actions.",
        )
    if not _host_allowed_by_policy(policy, host):
        _audit_outbound_decision(
            policy=policy,
            url=normalized_url,
            method=normalized_method,
            host=host,
            action_class=action_class,
            status="denied",
            denial_code="host_not_allowed",
        )
        raise EgressPolicyDeniedError(
            "host_not_allowed",
            f"Outbound request host '{host or normalized_url}' is not allowed by the active egress policy.",
        )
    _audit_outbound_decision(
        policy=policy,
        url=normalized_url,
        method=normalized_method,
        host=host,
        action_class=action_class,
        status="allowed",
    )
    return policy

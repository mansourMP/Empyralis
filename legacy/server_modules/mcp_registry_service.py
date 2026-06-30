from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

import httpx

from server_modules import agent_action_metering_service, rust_runtime_kernel_client
from server_modules.config_loader import config_str
from server_modules.url_security import assert_safe_outbound_url

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
except Exception:  # pragma: no cover - optional until dependency is installed
    ClientSession = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    create_mcp_http_client = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)

McpTransport = Literal["streamable_http"]
_STATE_HOME = Path(
    config_str("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
MCP_SERVER_REGISTRY_FILE = Path(
    config_str(
        "EMPYRALIS_MCP_SERVERS_FILE",
        str(_STATE_HOME / "runtime" / "mcp_servers.json"),
    )
).expanduser()
_SUPPORTED_RUNTIME_MODES = {"hosted_secure", "local_secure", "privileged_device"}
_SUPPORTED_ACTION_CLASSES = {"read", "write", "execute"}
_SUPPORTED_RISK_LEVELS = {"low", "medium", "high", "critical"}
_SUPPORTED_COST_CLASSES = {"free", "standard", "metered", "external"}
_DANGEROUS_TOKENS = {"delete", "remove", "destroy", "drop", "truncate", "reset", "shutdown"}
_ARGUMENT_KEY_CANDIDATES = ("arguments", "args", "input", "payload")

_BLOCKED_MCP_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "[::]",
}


def _validate_mcp_endpoint(endpoint: str) -> None:
    """Validate an MCP server endpoint URL for safety.

    Raises ValueError if the URL is unsafe or unsupported.
    """
    raw = str(endpoint or "").strip()
    if not raw:
        raise ValueError("MCP endpoint URL is required.")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("https", "http"):
        raise ValueError(f"MCP endpoint must use https:// (or http:// in dev). Got scheme: {scheme!r}")
    if scheme == "http" and not os.getenv("EMPYRALIS_DEV_ALLOW_HTTP_MCP"):
        raise ValueError("MCP endpoint must use https://. Set EMPYRALIS_DEV_ALLOW_HTTP_MCP=1 for local dev.")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("MCP endpoint URL has no parseable hostname.")
    if hostname in _BLOCKED_MCP_HOSTS:
        raise ValueError(f"MCP endpoint hostname {hostname!r} is blocked.")
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        raise ValueError(f"MCP endpoint hostname {hostname!r} uses a reserved TLD.")
    try:
        addr = ip_address(hostname)
    except ValueError:
        addr = None
    if addr is not None and (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_private
        or addr.is_unspecified
        or addr.is_reserved
    ):
        raise ValueError(f"MCP endpoint IP address {hostname!r} is not allowed.")
    assert_safe_outbound_url(raw)
    if not parsed.path or parsed.path == "/":
        _log.info("MCP endpoint %s has no explicit path; this may be intentional.", raw)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_server_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-_")


def _normalize_tool_name(value: Any) -> str:
    return str(value or "").strip()


def _normalize_transport(value: Any) -> McpTransport:
    token = str(value or "streamable_http").strip().lower() or "streamable_http"
    return "streamable_http"


def _normalize_list_of_strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        items.append(token)
    return items


def _normalize_runtime_modes(value: Any) -> List[str]:
    modes = []
    for token in _normalize_list_of_strings(value):
        compact = token.lower()
        if compact in _SUPPORTED_RUNTIME_MODES and compact not in modes:
            modes.append(compact)
    return modes or ["hosted_secure", "local_secure", "privileged_device"]


def _normalize_action_class(value: Any) -> str:
    token = str(value or "read").strip().lower() or "read"
    return token if token in _SUPPORTED_ACTION_CLASSES else "read"


def _risk_level_for_action(action_class: str) -> str:
    if action_class == "execute":
        return "critical"
    if action_class == "write":
        return "medium"
    return "low"


def _normalize_risk_level(value: Any, *, action_class: str) -> str:
    token = str(value or "").strip().lower()
    if token in _SUPPORTED_RISK_LEVELS:
        return token
    return _risk_level_for_action(action_class)


def _normalize_cost_class(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in _SUPPORTED_COST_CLASSES else "standard"


def _normalize_input_schema(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class McpRegistryRustGateError(RuntimeError):
    pass


def _enforce_mcp_registry_state_decision(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    try:
        payload_bytes = len(
            json.dumps(
                normalized_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="save_mcp_server_registry",
            state_class="mcp_server_registry",
            actor_id="system",
            status="active",
            payload=normalized_payload,
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "save_mcp_server_registry":
            raise McpRegistryRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise McpRegistryRustGateError(exc.reason) from exc


def _default_registry() -> Dict[str, Any]:
    return {
        "version": 1,
        "workspaces": {},
        "updated_at": None,
    }


def load_mcp_server_registry() -> Dict[str, Any]:
    payload = _read_json(MCP_SERVER_REGISTRY_FILE)
    workspaces = payload.get("workspaces") if isinstance(payload.get("workspaces"), dict) else {}
    return {
        "version": int(payload.get("version") or 1),
        "workspaces": workspaces,
        "updated_at": payload.get("updated_at"),
    }


def save_mcp_server_registry(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _default_registry()
    data["workspaces"] = payload.get("workspaces") if isinstance(payload.get("workspaces"), dict) else {}
    data["updated_at"] = payload.get("updated_at")
    _enforce_mcp_registry_state_decision(data)
    _write_json(MCP_SERVER_REGISTRY_FILE, data)
    return data


def _workspace_bucket(workspace_id: str, registry: Dict[str, Any]) -> Dict[str, Any]:
    workspaces = registry.get("workspaces") if isinstance(registry.get("workspaces"), dict) else {}
    bucket = workspaces.get(workspace_id)
    return dict(bucket) if isinstance(bucket, dict) else {"servers": {}}


def _save_workspace_bucket(workspace_id: str, registry: Dict[str, Any], bucket: Dict[str, Any]) -> None:
    workspaces = registry.get("workspaces") if isinstance(registry.get("workspaces"), dict) else {}
    workspaces[workspace_id] = {"servers": bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}}
    registry["workspaces"] = workspaces
    registry["updated_at"] = _utc_now_iso()


def _normalize_tool_payload(raw: Dict[str, Any], *, server_id: str) -> Dict[str, Any]:
    name = _normalize_tool_name(raw.get("name"))
    if not name:
        raise ValueError("MCP tool name is required.")
    label = str(raw.get("label") or name).strip() or name
    connector_scopes = _normalize_list_of_strings(raw.get("connector_scopes"))
    default_server_scope = f"mcp:{server_id}"
    normalized_scopes = ["mcp"]
    if default_server_scope not in normalized_scopes:
        normalized_scopes.append(default_server_scope)
    for scope in connector_scopes:
        compact = scope.lower()
        if compact not in normalized_scopes:
            normalized_scopes.append(compact)
    trigger_terms = [token.lower() for token in _normalize_list_of_strings(raw.get("trigger_terms"))]
    action_class = _normalize_action_class(raw.get("action_class"))
    risk_level = _normalize_risk_level(raw.get("risk_level"), action_class=action_class)
    permission_scopes = _normalize_list_of_strings(raw.get("permission_scopes")) or list(normalized_scopes)
    requires_approval = bool(raw.get("requires_approval")) or risk_level in {"high", "critical"}
    allowed_runtime_modes = _normalize_runtime_modes(raw.get("allowed_runtime_modes"))
    cost_class = _normalize_cost_class(raw.get("cost_class"))
    audit_event_type = str(raw.get("audit_event_type") or f"mcp.tool.{action_class}").strip() or f"mcp.tool.{action_class}"
    return {
        "name": name,
        "label": label[:160],
        "description": str(raw.get("description") or "").strip()[:500],
        "input_schema": _normalize_input_schema(raw.get("input_schema")),
        "action_class": action_class,
        "risk_level": risk_level,
        "connector_scopes": normalized_scopes,
        "permission_scopes": permission_scopes,
        "trigger_terms": trigger_terms,
        "allowed_runtime_modes": allowed_runtime_modes,
        "requires_approval": requires_approval,
        "audit_event_type": audit_event_type,
        "cost_class": cost_class,
        "permission_manifest": {
            "action_class": action_class,
            "risk_level": risk_level,
            "scopes": permission_scopes,
            "requires_approval": requires_approval,
            "allowed_runtime_modes": allowed_runtime_modes,
            "cost_class": cost_class,
            "audit_event_type": audit_event_type,
        },
        "enabled": bool(raw.get("enabled", True)),
        "approved": bool(raw.get("approved", False)),
    }


def _tool_items_from_list_result(result: Any, *, server_id: str) -> List[Dict[str, Any]]:
    tool_items = []
    raw_tools = None
    if isinstance(result, list):
        raw_tools = result
    elif isinstance(result, dict):
        raw_tools = result.get("tools")
    else:
        raw_tools = getattr(result, "tools", None)
    if not isinstance(raw_tools, list):
        return []
    for raw in raw_tools:
        if isinstance(raw, dict):
            tool = dict(raw)
        else:
            tool = {
                "name": getattr(raw, "name", None),
                "description": getattr(raw, "description", None),
                "input_schema": getattr(raw, "inputSchema", None) or getattr(raw, "input_schema", None),
            }
        try:
            tool_items.append(_normalize_tool_payload(tool, server_id=server_id))
        except ValueError:
            continue
    return tool_items


def _resolve_mcp_credential(server: Dict[str, Any], workspace_id: str) -> Optional[Dict[str, Any]]:
    """Resolve the credential dict for an MCP server from the vault, if credential_id is set.

    Also triggers an OAuth token refresh if the credential is expired or about to expire.
    """
    credential_id = str(server.get("credential_id") or "").strip()
    if not credential_id:
        return None
    try:
        # Attempt token refresh before resolving (no-op if not needed)
        try:
            from server_modules.connection_oauth_service import refresh_oauth_token_if_needed
            refresh_oauth_token_if_needed(credential_id)
        except Exception:
            pass  # refresh is best-effort; proceed with existing credential
        from server_modules.vault_store import _openssl_decrypt, load_vault
        from server_modules.vault_helpers import resolve_vault_credential
        return resolve_vault_credential(load_vault, _openssl_decrypt, credential_id, workspace_id=workspace_id)
    except Exception:
        _log.warning("Failed to resolve credential %s for MCP server %s", credential_id, server.get("id"))
        return None


def _mcp_result_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return result
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        if len(text_parts) == 1:
            try:
                return json.loads(text_parts[0])
            except Exception:
                return {"text": text_parts[0]}
        if text_parts:
            return {"text": "\n".join(text_parts)}
    return {}


def _run_async_from_sync(coro_factory: Any) -> Any:
    try:
        return asyncio.run(coro_factory())
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise

    result: Dict[str, Any] = {}
    failure: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as err:  # pragma: no cover
            failure["error"] = err

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in failure:
        raise failure["error"]
    return result.get("value")


def _is_transient_mcp_error(exc: BaseException) -> bool:
    """Return True if the error is likely transient and worth retrying.

    Retryable: httpx connection/timeout/protocol errors, HTTP 5xx/429,
    asyncio TimeoutError, OSError (connection reset/refused).
    Non-retryable: HTTP 4xx (auth, bad request, not found), and our own
    RuntimeError messages for permanent failures.
    """
    # httpx transport errors — always transient
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    # httpx HTTPStatusError — retry only 5xx and 429
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(exc.response, "status_code", 0)
        return status in {408, 425, 429, 500, 502, 503, 504}
    # asyncio timeout and OS-level network errors
    if isinstance(exc, (TimeoutError, ConnectionRefusedError, ConnectionResetError, OSError)):
        return True
    # RuntimeError — retry only our "timed out" wrapper; never retry permanent failures
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        permanent = (
            "not installed" in msg
            or "not approved" in msg
            or "not found" in msg
            or "disabled" in msg
            or "not supported" in msg
        )
        if permanent:
            return False
        return "timed out" in msg or "timeout" in msg
    return False


async def _list_tools_streamable_http_async(
    *,
    endpoint: str,
    http_client: Any = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
    discover_timeout_seconds: float = 30.0,
) -> List[Dict[str, Any]]:
    if client_session_cls is None or streamable_http_client_fn is None:
        raise RuntimeError("The MCP client dependency is not installed.")
    max_attempts = 2
    base_delay = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            async with asyncio.timeout(discover_timeout_seconds):
                async with streamable_http_client_fn(endpoint, http_client=http_client) as (read_stream, write_stream, _):
                    async with client_session_cls(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.list_tools()
            break
        except TimeoutError:
            if attempt < max_attempts:
                _log.warning(
                    "MCP tool discovery attempt %d/%d timed out after %.0fs for endpoint %s, retrying...",
                    attempt, max_attempts, discover_timeout_seconds, endpoint,
                )
                await asyncio.sleep(base_delay * attempt)
                continue
            raise RuntimeError(
                f"MCP tool discovery timed out after {max_attempts} attempts "
                f"({discover_timeout_seconds:.0f}s each) for endpoint {endpoint}"
            ) from None
        except Exception as exc:
            if attempt < max_attempts and _is_transient_mcp_error(exc):
                _log.warning(
                    "MCP tool discovery attempt %d/%d failed for endpoint %s: %s, retrying...",
                    attempt, max_attempts, endpoint, exc,
                )
                await asyncio.sleep(base_delay * attempt)
                continue
            raise
    return _tool_items_from_list_result(result, server_id="temporary")


def _build_mcp_auth_headers(credential: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Build HTTP Authorization headers from a credential dict for MCP client injection.

    Supports:
    - OAuth2: credential with ``access_token`` → ``Authorization: Bearer <token>``
    - API key: credential with ``api_key`` → ``Authorization: ApiKey <key>``
    - Bot token: credential with ``bot_token`` → ``Authorization: Bot <token>``
    """
    if not isinstance(credential, dict) or not credential:
        return {}
    access_token = str(credential.get("access_token") or "").strip()
    if access_token:
        return {"Authorization": f"Bearer {access_token}"}
    api_key = str(credential.get("api_key") or "").strip()
    if api_key:
        return {"Authorization": f"ApiKey {api_key}"}
    bot_token = str(credential.get("bot_token") or "").strip()
    if bot_token:
        return {"Authorization": f"Bot {bot_token}"}
    return {}


def _build_mcp_http_client(
    credential: Optional[Dict[str, Any]],
    timeout_seconds: float = 60.0,
) -> Any:
    """Build an httpx.AsyncClient with MCP defaults and auth headers from a credential.

    Returns None if no credential is provided or if the SDK is not installed.
    Callers MUST use the returned client as an async context manager.
    """
    if create_mcp_http_client is None:
        return None
    headers = _build_mcp_auth_headers(credential)
    if not headers:
        return None
    timeout = httpx.Timeout(timeout_seconds) if timeout_seconds > 0 else None
    return create_mcp_http_client(headers=headers, timeout=timeout)


def discover_mcp_server_tools(
    *,
    transport: McpTransport,
    endpoint: str,
    server_id: str,
    credential: Optional[Dict[str, Any]] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> List[Dict[str, Any]]:
    if transport != "streamable_http":
        raise RuntimeError(f"Unsupported MCP transport '{transport}'.")
    mcp_http_client = _build_mcp_http_client(credential)
    tools = _run_async_from_sync(
        lambda: _list_tools_streamable_http_async(
            endpoint=endpoint,
            http_client=mcp_http_client,
            client_session_cls=client_session_cls,
            streamable_http_client_fn=streamable_http_client_fn,
        )
    )
    return [_normalize_tool_payload(tool, server_id=server_id) for tool in tools]


async def discover_mcp_server_tools_async(
    *,
    transport: McpTransport,
    endpoint: str,
    server_id: str,
    credential: Optional[Dict[str, Any]] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> List[Dict[str, Any]]:
    if transport != "streamable_http":
        raise RuntimeError(f"Unsupported MCP transport '{transport}'.")
    mcp_http_client = _build_mcp_http_client(credential)
    tools = await _list_tools_streamable_http_async(
        endpoint=endpoint,
        http_client=mcp_http_client,
        client_session_cls=client_session_cls,
        streamable_http_client_fn=streamable_http_client_fn,
    )
    return [_normalize_tool_payload(tool, server_id=server_id) for tool in tools]


def _normalize_server_payload(
    *,
    server_id: str,
    label: Any,
    transport: Any,
    endpoint: Any,
    enabled: Any,
    tools: Any,
    metadata: Any,
    existing: Optional[Dict[str, Any]] = None,
    credential_id: Any = None,
) -> Dict[str, Any]:
    normalized_server_id = _normalize_server_id(server_id)
    if not normalized_server_id:
        raise ValueError("MCP server id is required.")
    normalized_endpoint = str(endpoint or "").strip()
    if not normalized_endpoint:
        raise ValueError("MCP endpoint is required.")
    normalized_tools: List[Dict[str, Any]] = []
    if isinstance(tools, list):
        for raw_tool in tools:
            if not isinstance(raw_tool, dict):
                continue
            normalized_tools.append(_normalize_tool_payload(raw_tool, server_id=normalized_server_id))
    current = dict(existing) if isinstance(existing, dict) else {}
    normalized_credential_id = str(credential_id or "").strip() or None if credential_id is not None else current.get("credential_id")
    return {
        "id": normalized_server_id,
        "label": str(label or normalized_server_id).strip()[:160] or normalized_server_id,
        "transport": _normalize_transport(transport),
        "endpoint": normalized_endpoint,
        "enabled": bool(enabled if enabled is not None else current.get("enabled", True)),
        "advanced_only": True,
        "credential_id": normalized_credential_id,
        "tools": normalized_tools or (current.get("tools") if isinstance(current.get("tools"), list) else []),
        "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        "last_synced_at": current.get("last_synced_at"),
        "created_at": str(current.get("created_at") or _utc_now_iso()),
        "updated_at": _utc_now_iso(),
    }


def list_workspace_mcp_servers(workspace_id: str) -> List[Dict[str, Any]]:
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        return []
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    items: List[Dict[str, Any]] = []
    for server_id, raw in sorted(servers.items()):
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        payload["id"] = _normalize_server_id(payload.get("id") or server_id)
        payload["tool_count"] = len(payload.get("tools") if isinstance(payload.get("tools"), list) else [])
        payload["skill_ids"] = [
            mcp_skill_id(payload["id"], str(tool.get("name") or ""))
            for tool in (payload.get("tools") if isinstance(payload.get("tools"), list) else [])
            if str(tool.get("name") or "").strip()
        ]
        items.append(payload)
    return items


def get_workspace_mcp_server(workspace_id: str, server_id: str) -> Optional[Dict[str, Any]]:
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    if not normalized_workspace_id or not normalized_server_id:
        return None
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    payload = servers.get(normalized_server_id)
    return dict(payload) if isinstance(payload, dict) else None


def upsert_workspace_mcp_server(
    *,
    workspace_id: str,
    server_id: str,
    label: Any,
    transport: Any,
    endpoint: Any,
    enabled: Any = True,
    tools: Any = None,
    metadata: Any = None,
    discover_tools: bool = False,
    credential_id: Any = None,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        raise ValueError("workspace_id is required.")
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    normalized_server_id = _normalize_server_id(server_id)
    existing = servers.get(normalized_server_id) if isinstance(servers.get(normalized_server_id), dict) else None

    _validate_mcp_endpoint(str(endpoint or "").strip())

    payload = _normalize_server_payload(
        server_id=normalized_server_id,
        label=label,
        transport=transport,
        endpoint=endpoint,
        enabled=enabled,
        tools=tools,
        metadata=metadata,
        existing=existing,
        credential_id=credential_id,
    )
    if discover_tools:
        existing_approvals = {
            _normalize_tool_name(tool.get("name")): bool(tool.get("approved", True))
            for tool in (existing.get("tools") if isinstance(existing, dict) and isinstance(existing.get("tools"), list) else [])
            if isinstance(tool, dict) and _normalize_tool_name(tool.get("name"))
        }
        discovery_credential = _resolve_mcp_credential(payload, normalized_workspace_id)
        discovered = discover_mcp_server_tools(
            transport=payload["transport"],
            endpoint=payload["endpoint"],
            server_id=payload["id"],
            credential=discovery_credential,
        )
        if discovered:
            for tool in discovered:
                normalized_name = _normalize_tool_name(tool.get("name"))
                tool["approved"] = bool(existing_approvals.get(normalized_name, False))
            _log.warning(
                "MCP server %s: %d tools discovered but not auto-approved. Use approve_mcp_tool().",
                payload["id"],
                len(discovered),
            )
            payload["tools"] = discovered
        payload["last_synced_at"] = _utc_now_iso()
        payload["updated_at"] = payload["last_synced_at"]
    servers[payload["id"]] = payload
    bucket["servers"] = servers
    _save_workspace_bucket(normalized_workspace_id, registry, bucket)
    save_mcp_server_registry(registry)
    return payload


async def upsert_workspace_mcp_server_async(
    *,
    workspace_id: str,
    server_id: str,
    label: Any,
    transport: Any,
    endpoint: Any,
    enabled: Any = True,
    tools: Any = None,
    metadata: Any = None,
    discover_tools: bool = False,
    credential_id: Any = None,
) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        raise ValueError("workspace_id is required.")
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    normalized_server_id = _normalize_server_id(server_id)
    existing = servers.get(normalized_server_id) if isinstance(servers.get(normalized_server_id), dict) else None

    _validate_mcp_endpoint(str(endpoint or "").strip())

    payload = _normalize_server_payload(
        server_id=normalized_server_id,
        label=label,
        transport=transport,
        endpoint=endpoint,
        enabled=enabled,
        tools=tools,
        metadata=metadata,
        existing=existing,
        credential_id=credential_id,
    )
    if discover_tools:
        existing_approvals = {
            _normalize_tool_name(tool.get("name")): bool(tool.get("approved", True))
            for tool in (existing.get("tools") if isinstance(existing, dict) and isinstance(existing.get("tools"), list) else [])
            if isinstance(tool, dict) and _normalize_tool_name(tool.get("name"))
        }
        discovery_credential = _resolve_mcp_credential(payload, normalized_workspace_id)
        discovered = await discover_mcp_server_tools_async(
            transport=payload["transport"],
            endpoint=payload["endpoint"],
            server_id=payload["id"],
            credential=discovery_credential,
        )
        if discovered:
            for tool in discovered:
                normalized_name = _normalize_tool_name(tool.get("name"))
                tool["approved"] = bool(existing_approvals.get(normalized_name, False))
            _log.warning(
                "MCP server %s: %d tools discovered but not auto-approved. Use approve_mcp_tool().",
                payload["id"],
                len(discovered),
            )
            payload["tools"] = discovered
        payload["last_synced_at"] = _utc_now_iso()
        payload["updated_at"] = payload["last_synced_at"]
    servers[payload["id"]] = payload
    bucket["servers"] = servers
    _save_workspace_bucket(normalized_workspace_id, registry, bucket)
    save_mcp_server_registry(registry)
    return payload


def approve_mcp_tool(*, workspace_id: str, server_id: str, tool_name: str) -> Dict[str, Any]:
    """Approve a single discovered-but-unapproved MCP tool for execution."""
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool_name)
    if not normalized_workspace_id or not normalized_server_id or not normalized_tool_name:
        raise ValueError("workspace_id, server_id, and tool_name are required.")
    server = get_workspace_mcp_server(normalized_workspace_id, normalized_server_id)
    if server is None:
        raise FileNotFoundError(f"MCP server '{server_id}' not found in workspace.")
    tools = server.get("tools") if isinstance(server.get("tools"), list) else []
    found = False
    for tool in tools:
        if _normalize_tool_name(tool.get("name")) == normalized_tool_name:
            tool["approved"] = True
            found = True
            break
    if not found:
        raise FileNotFoundError(f"MCP tool '{tool_name}' not found on server '{server_id}'.")
    return upsert_workspace_mcp_server(
        workspace_id=normalized_workspace_id,
        server_id=normalized_server_id,
        label=server.get("label"),
        transport=server.get("transport"),
        endpoint=server.get("endpoint"),
        enabled=server.get("enabled", True),
        tools=tools,
        metadata=server.get("metadata"),
        discover_tools=False,
        credential_id=server.get("credential_id"),
    )


def deny_mcp_tool(*, workspace_id: str, server_id: str, tool_name: str) -> Dict[str, Any]:
    """Deny (revoke approval of) a single MCP tool for execution."""
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool_name)
    if not normalized_workspace_id or not normalized_server_id or not normalized_tool_name:
        raise ValueError("workspace_id, server_id, and tool_name are required.")
    server = get_workspace_mcp_server(normalized_workspace_id, normalized_server_id)
    if server is None:
        raise FileNotFoundError(f"MCP server '{server_id}' not found in workspace.")
    tools = server.get("tools") if isinstance(server.get("tools"), list) else []
    found = False
    for tool in tools:
        if _normalize_tool_name(tool.get("name")) == normalized_tool_name:
            tool["approved"] = False
            found = True
            break
    if not found:
        raise FileNotFoundError(f"MCP tool '{tool_name}' not found on server '{server_id}'.")
    return upsert_workspace_mcp_server(
        workspace_id=normalized_workspace_id,
        server_id=normalized_server_id,
        label=server.get("label"),
        transport=server.get("transport"),
        endpoint=server.get("endpoint"),
        enabled=server.get("enabled", True),
        tools=tools,
        metadata=server.get("metadata"),
        discover_tools=False,
        credential_id=server.get("credential_id"),
    )


def list_mcp_server_tools(*, workspace_id: str, server_id: str) -> List[Dict[str, Any]]:
    """Return all discovered tools for a server with their approval status."""
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    if not normalized_workspace_id or not normalized_server_id:
        return []
    server = get_workspace_mcp_server(normalized_workspace_id, normalized_server_id)
    if server is None:
        raise FileNotFoundError(f"MCP server '{server_id}' not found in workspace.")
    tools = server.get("tools") if isinstance(server.get("tools"), list) else []
    return [
        {
            "name": _normalize_tool_name(tool.get("name")),
            "label": str(tool.get("label") or tool.get("name") or "").strip(),
            "description": str(tool.get("description") or "").strip(),
            "action_class": _normalize_action_class(tool.get("action_class")),
            "risk_level": _normalize_risk_level(tool.get("risk_level"), action_class=_normalize_action_class(tool.get("action_class"))),
            "approved": bool(tool.get("approved", False)),
            "enabled": bool(tool.get("enabled", True)),
            "input_schema": _normalize_input_schema(tool.get("input_schema")),
        }
        for tool in tools
        if isinstance(tool, dict)
    ]


def refresh_workspace_mcp_server_tools(*, workspace_id: str, server_id: str) -> Dict[str, Any]:
    current = get_workspace_mcp_server(workspace_id, server_id)
    if current is None:
        raise FileNotFoundError(f"MCP server '{server_id}' is not registered for this workspace.")
    return upsert_workspace_mcp_server(
        workspace_id=workspace_id,
        server_id=server_id,
        label=current.get("label"),
        transport=current.get("transport"),
        endpoint=current.get("endpoint"),
        enabled=current.get("enabled", True),
        tools=current.get("tools"),
        metadata=current.get("metadata"),
        discover_tools=True,
    )


async def refresh_workspace_mcp_server_tools_async(*, workspace_id: str, server_id: str) -> Dict[str, Any]:
    current = get_workspace_mcp_server(workspace_id, server_id)
    if current is None:
        raise FileNotFoundError(f"MCP server '{server_id}' is not registered for this workspace.")
    return await upsert_workspace_mcp_server_async(
        workspace_id=workspace_id,
        server_id=server_id,
        label=current.get("label"),
        transport=current.get("transport"),
        endpoint=current.get("endpoint"),
        enabled=current.get("enabled", True),
        tools=current.get("tools"),
        metadata=current.get("metadata"),
        discover_tools=True,
    )


def delete_workspace_mcp_server(*, workspace_id: str, server_id: str) -> Dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "").strip()
    normalized_server_id = _normalize_server_id(server_id)
    registry = load_mcp_server_registry()
    bucket = _workspace_bucket(normalized_workspace_id, registry)
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    if normalized_server_id not in servers:
        raise FileNotFoundError(f"MCP server '{server_id}' is not registered for this workspace.")
    removed = dict(servers.pop(normalized_server_id))
    bucket["servers"] = servers
    _save_workspace_bucket(normalized_workspace_id, registry, bucket)
    save_mcp_server_registry(registry)
    return removed


def mcp_skill_id(server_id: str, tool_name: str) -> str:
    return f"mcp:{_normalize_server_id(server_id)}:{_normalize_tool_name(tool_name)}"


def parse_mcp_skill_id(skill_id: str) -> Optional[Dict[str, str]]:
    raw = str(skill_id or "").strip()
    if not raw.startswith("mcp:"):
        return None
    _prefix, server_id, tool_name = raw.split(":", 2) if raw.count(":") >= 2 else ("", "", "")
    normalized_server_id = _normalize_server_id(server_id)
    normalized_tool_name = _normalize_tool_name(tool_name)
    if not normalized_server_id or not normalized_tool_name:
        return None
    return {"server_id": normalized_server_id, "tool_name": normalized_tool_name}


def list_workspace_mcp_skill_entries(workspace_id: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for server in list_workspace_mcp_servers(workspace_id):
        if not bool(server.get("enabled", True)):
            continue
        server_id = str(server.get("id") or "").strip()
        for raw_tool in server.get("tools") if isinstance(server.get("tools"), list) else []:
            if not isinstance(raw_tool, dict) or not bool(raw_tool.get("enabled", True)):
                continue
            if not bool(raw_tool.get("approved", True)):
                continue
            tool_name = _normalize_tool_name(raw_tool.get("name"))
            if not tool_name:
                continue
            entries.append(
                {
                    "id": mcp_skill_id(server_id, tool_name),
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "label": str(raw_tool.get("label") or tool_name).strip() or tool_name,
                    "description": str(raw_tool.get("description") or "").strip(),
                    "skill_class": "specialist_local",
                    "permission_label": f"MCP server {server.get('label') or server_id}",
                    "execution_mode": "live",
                    "action_class": _normalize_action_class(raw_tool.get("action_class")),
                    "connector_scopes": _normalize_list_of_strings(raw_tool.get("connector_scopes")) or ["mcp", f"mcp:{server_id}"],
                    "trigger_terms": [token.lower() for token in _normalize_list_of_strings(raw_tool.get("trigger_terms"))],
                    "allowed_runtime_modes": _normalize_runtime_modes(raw_tool.get("allowed_runtime_modes")),
                    "requires_approval": bool(raw_tool.get("requires_approval")),
                    "execution_adapter": "mcp_tool",
                    "source": "mcp_registry",
                    "path": str(server.get("endpoint") or "").strip(),
                    "enabled": True,
                    "metadata": {
                        "server_label": str(server.get("label") or server_id).strip() or server_id,
                        "transport": str(server.get("transport") or "streamable_http").strip() or "streamable_http",
                        "endpoint": str(server.get("endpoint") or "").strip(),
                        "input_schema": _normalize_input_schema(raw_tool.get("input_schema")),
                    },
                }
            )
    return entries


def _tool_from_server(server: Dict[str, Any], tool_name: str) -> Optional[Dict[str, Any]]:
    for raw_tool in server.get("tools") if isinstance(server.get("tools"), list) else []:
        if not isinstance(raw_tool, dict):
            continue
        if _normalize_tool_name(raw_tool.get("name")) == tool_name:
            return dict(raw_tool)
    return None


def _assert_tool_approved_for_execution(tool_payload: Dict[str, Any], *, server_id: str, tool_name: str) -> None:
    if bool(tool_payload.get("approved", False)):
        return
    raise PermissionError(f"MCP tool '{tool_name}' on server '{server_id}' is not approved for execution.")


def _parse_goal_arguments(goal: str, tool_payload: Dict[str, Any]) -> Dict[str, Any]:
    compact = str(goal or "").strip()
    if not compact:
        return {}
    try:
        parsed = json.loads(compact)
        if isinstance(parsed, dict):
            for key in _ARGUMENT_KEY_CANDIDATES:
                candidate = parsed.get(key)
                if isinstance(candidate, dict):
                    return dict(candidate)
            return parsed
    except Exception:
        pass
    schema = _normalize_input_schema(tool_payload.get("input_schema"))
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = [
        str(item).strip()
        for item in (schema.get("required") if isinstance(schema.get("required"), list) else [])
        if str(item).strip()
    ]
    candidate_keys = required or [str(key).strip() for key in properties.keys() if str(key).strip()]
    for key in candidate_keys:
        if key in {"question", "query", "goal", "input", "text", "prompt", "message", "url"}:
            return {key: compact}
    if len(candidate_keys) == 1:
        return {candidate_keys[0]: compact}
    if not candidate_keys:
        return {}
    return {"input": compact}


_MAX_ARGUMENT_STRING_LENGTH = 10_000


def _validate_mcp_arguments(
    arguments: Dict[str, Any],
    input_schema: Dict[str, Any],
    *,
    tool_name: str = "",
) -> Dict[str, Any]:
    """Validate and sanitize arguments against the tool's input_schema.

    Returns a cleaned arguments dict.  When *input_schema* is empty or
    missing, the original arguments are returned unchanged.
    """
    if not isinstance(arguments, dict) or not arguments:
        return dict(arguments) if isinstance(arguments, dict) else {}

    schema = _normalize_input_schema(input_schema)
    if not schema:
        return dict(arguments)

    properties: Dict[str, Any] = (
        schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    )
    required: List[str] = [
        str(item).strip()
        for item in (schema.get("required") if isinstance(schema.get("required"), list) else [])
        if str(item).strip()
    ]

    cleaned: Dict[str, Any] = {}

    # 1. Strip unknown keys
    for key, value in arguments.items():
        key_str = str(key).strip()
        if properties and key_str not in properties:
            _log.warning(
                "MCP argument validation: stripping unknown key '%s' for tool '%s'",
                key_str,
                tool_name or "(unknown)",
            )
            continue
        cleaned[key_str] = value

    # 2. Check required keys are present
    for req_key in required:
        if req_key not in cleaned:
            raise RuntimeError(
                f"Missing required parameter: {req_key}"
                + (f" for tool {tool_name}" if tool_name else "")
            )

    # 3. Type-check and convert basic types
    for key, value in list(cleaned.items()):
        prop_schema = properties.get(key)
        if not isinstance(prop_schema, dict):
            continue
        expected_type = str(prop_schema.get("type") or "").strip().lower()
        if not expected_type:
            continue

        if expected_type == "string":
            if not isinstance(value, str):
                cleaned[key] = str(value)
            # 4. Cap string length
            if isinstance(cleaned[key], str) and len(cleaned[key]) > _MAX_ARGUMENT_STRING_LENGTH:
                _log.warning(
                    "MCP argument validation: truncating string argument '%s' (%d chars) for tool '%s'",
                    key,
                    len(cleaned[key]),
                    tool_name or "(unknown)",
                )
                cleaned[key] = cleaned[key][:_MAX_ARGUMENT_STRING_LENGTH]

        elif expected_type in ("integer", "number"):
            if isinstance(value, bool):
                cleaned[key] = int(value)
            elif isinstance(value, str):
                stripped = value.strip()
                try:
                    if expected_type == "integer":
                        cleaned[key] = int(stripped)
                    else:
                        cleaned[key] = float(stripped)
                except (ValueError, TypeError):
                    pass  # keep original string if conversion fails
            elif isinstance(value, (int, float)):
                if expected_type == "integer":
                    cleaned[key] = int(value)
                # else keep as-is (already a number)

    return cleaned


def _mcp_reply(payload: Any, *, agent_label: str, tool_name: str) -> str:
    if isinstance(payload, dict):
        for key in ("reply", "response", "answer", "text", "message", "summary"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return f"{agent_label} called MCP tool {tool_name} successfully."
    if isinstance(payload, list):
        return json.dumps(payload, ensure_ascii=False)[:4000]
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return f"{agent_label} called MCP tool {tool_name} successfully."


def _tool_kind(tool_payload: Dict[str, Any]) -> str:
    tool_name = str(tool_payload.get("name") or "").lower()
    action_class = _normalize_action_class(tool_payload.get("action_class"))
    if action_class != "read" or any(token in tool_name for token in _DANGEROUS_TOKENS):
        return "mcp-tool-result"
    return "mcp-live-data"


async def _call_streamable_http_tool_async(
    *,
    endpoint: str,
    tool_name: str,
    arguments: Dict[str, Any],
    http_client: Any = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
    call_timeout_seconds: float = 60.0,
) -> Any:
    if client_session_cls is None or streamable_http_client_fn is None:
        raise RuntimeError("The MCP client dependency is not installed.")
    max_attempts = 3
    base_delay = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            async with asyncio.timeout(call_timeout_seconds):
                async with streamable_http_client_fn(endpoint, http_client=http_client) as (read_stream, write_stream, _):
                    async with client_session_cls(read_stream, write_stream) as session:
                        await session.initialize()
                        return await session.call_tool(tool_name, arguments)
        except TimeoutError:
            if attempt < max_attempts:
                _log.warning(
                    "MCP tool call %s attempt %d/%d timed out after %.0fs, retrying...",
                    tool_name, attempt, max_attempts, call_timeout_seconds,
                )
                await asyncio.sleep(base_delay * attempt)
                continue
            raise RuntimeError(
                f"MCP tool call timed out after {max_attempts} attempts "
                f"({call_timeout_seconds:.0f}s each): {tool_name} on {endpoint}"
            ) from None
        except Exception as exc:
            if attempt < max_attempts and _is_transient_mcp_error(exc):
                _log.warning(
                    "MCP tool call %s attempt %d/%d failed: %s, retrying...",
                    tool_name, attempt, max_attempts, exc,
                )
                await asyncio.sleep(base_delay * attempt)
                continue
            raise


def invoke_workspace_mcp_skill(
    *,
    workspace_id: str,
    skill_id: str,
    goal: str,
    agent_label: str,
    tenant_id: str = "default",
    surface: str = "sage",
    source_surface: str = "mcp_skill",
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> Dict[str, Any]:
    parsed = parse_mcp_skill_id(skill_id)
    if parsed is None:
        raise RuntimeError("Invalid MCP skill id.")
    server = get_workspace_mcp_server(workspace_id, parsed["server_id"])
    if server is None:
        raise FileNotFoundError(f"MCP server '{parsed['server_id']}' is not registered for this workspace.")
    if not bool(server.get("enabled", True)):
        raise RuntimeError(f"MCP server '{parsed['server_id']}' is disabled for this workspace.")
    tool_payload = _tool_from_server(server, parsed["tool_name"])
    if tool_payload is None or not bool(tool_payload.get("enabled", True)):
        raise FileNotFoundError(f"MCP tool '{parsed['tool_name']}' is not available on server '{parsed['server_id']}'.")
    execution_call_id = run_id or thread_id or f"mcp_{uuid.uuid4().hex}"
    source_event_id = agent_action_metering_service.build_source_event_id(
        source_surface=source_surface,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=execution_call_id,
        action_name=parsed["tool_name"],
    )
    try:
        _assert_tool_approved_for_execution(
            tool_payload,
            server_id=parsed["server_id"],
            tool_name=parsed["tool_name"],
        )
    except Exception as exc:
        agent_action_metering_service.record_blocked_sync(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            surface=surface,
            source_surface=source_surface,
            action_domain="mcp",
            action_type=_normalize_action_class(tool_payload.get("action_class")),
            action_name=parsed["tool_name"],
            tool_kind="mcp_tool",
            mcp_server_id=parsed["server_id"],
            mcp_tool_id=parsed["tool_name"],
            connector_id=f"mcp:{parsed['server_id']}",
            skill_id=skill_id,
            risk_level=str(tool_payload.get("risk_level") or "").strip() or None,
            policy_decision="blocked",
            error_code=type(exc).__name__,
            payer="platform_credits",
            billing_mode="none",
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            agent_id=agent_id,
            app_id=app_id,
            input_summary=goal,
            output_summary=str(exc),
            source_table="mcp_tool_calls",
            source_event_id=source_event_id,
        )
        raise
    arguments = _parse_goal_arguments(goal, tool_payload)
    arguments = _validate_mcp_arguments(
        arguments,
        tool_payload.get("input_schema"),
        tool_name=parsed["tool_name"],
    )
    credential = _resolve_mcp_credential(server, workspace_id)
    mcp_http_client = _build_mcp_http_client(credential)
    common_event = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "surface": surface,
        "source_surface": source_surface,
        "action_domain": "mcp",
        "action_type": _normalize_action_class(tool_payload.get("action_class")),
        "action_name": parsed["tool_name"],
        "tool_kind": "mcp_tool",
        "mcp_server_id": parsed["server_id"],
        "mcp_tool_id": parsed["tool_name"],
        "connector_id": f"mcp:{parsed['server_id']}",
        "skill_id": skill_id,
        "risk_level": str(tool_payload.get("risk_level") or "").strip() or None,
        "policy_decision": "allowed",
        "payer": "platform_credits",
        "billing_mode": "transparency",
        "user_id": user_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "app_id": app_id,
        "input_summary": goal,
        "source_table": "mcp_tool_calls",
        "source_event_id": source_event_id,
        "metadata": {"server_label": server.get("label"), "argument_keys": sorted(arguments.keys())},
    }
    agent_action_metering_service.record_started_sync(**common_event)
    try:
        result = asyncio.run(
            _call_streamable_http_tool_async(
                endpoint=str(server.get("endpoint") or "").strip(),
                tool_name=parsed["tool_name"],
                arguments=arguments,
                http_client=mcp_http_client,
                client_session_cls=client_session_cls,
                streamable_http_client_fn=streamable_http_client_fn,
            )
        )
    except Exception as exc:
        agent_action_metering_service.record_failed_sync(
            **common_event,
            error_code=type(exc).__name__,
            output_summary=str(exc),
        )
        raise
    payload = _mcp_result_payload(result)
    agent_action_metering_service.record_completed_sync(
        **common_event,
        output_summary=_mcp_reply(payload, agent_label=agent_label, tool_name=parsed["tool_name"]),
    )
    return {
        "status": "ok",
        "reply": _mcp_reply(payload, agent_label=agent_label, tool_name=parsed["tool_name"]),
        "artifact": {
            "label": str(tool_payload.get("label") or parsed["tool_name"]).strip() or parsed["tool_name"],
            "kind": _tool_kind(tool_payload),
            "summary": f"MCP tool {parsed['tool_name']} on server {server.get('label') or parsed['server_id']}",
            "media_type": "application/json",
            "preview_content": json.dumps(
                {
                    "server_id": parsed["server_id"],
                    "tool_name": parsed["tool_name"],
                    "arguments": arguments,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )[:12000],
        },
        "steps": [
            {"label": "Resolving MCP server", "detail": str(server.get("label") or parsed["server_id"]).strip() or parsed["server_id"], "status": "done", "kind": "thinking"},
            {"label": "Invoking MCP tool", "detail": parsed["tool_name"], "status": "done", "kind": "connector"},
        ],
        "mcp": {
            "server_id": parsed["server_id"],
            "tool_name": parsed["tool_name"],
            "arguments": arguments,
            "payload": payload,
        },
    }


async def invoke_workspace_mcp_skill_async(
    *,
    workspace_id: str,
    skill_id: str,
    goal: str,
    agent_label: str,
    tenant_id: str = "default",
    surface: str = "sage",
    source_surface: str = "mcp_skill",
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> Dict[str, Any]:
    parsed = parse_mcp_skill_id(skill_id)
    if parsed is None:
        raise RuntimeError("Invalid MCP skill id.")
    server = get_workspace_mcp_server(workspace_id, parsed["server_id"])
    if server is None:
        raise FileNotFoundError(f"MCP server '{parsed['server_id']}' is not registered for this workspace.")
    if not bool(server.get("enabled", True)):
        raise RuntimeError(f"MCP server '{parsed['server_id']}' is disabled for this workspace.")
    tool_payload = _tool_from_server(server, parsed["tool_name"])
    if tool_payload is None or not bool(tool_payload.get("enabled", True)):
        raise FileNotFoundError(f"MCP tool '{parsed['tool_name']}' is not available on server '{parsed['server_id']}'.")
    execution_call_id = run_id or thread_id or f"mcp_{uuid.uuid4().hex}"
    source_event_id = agent_action_metering_service.build_source_event_id(
        source_surface=source_surface,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=execution_call_id,
        action_name=parsed["tool_name"],
    )
    try:
        _assert_tool_approved_for_execution(
            tool_payload,
            server_id=parsed["server_id"],
            tool_name=parsed["tool_name"],
        )
    except Exception as exc:
        await agent_action_metering_service.record_blocked(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            surface=surface,
            source_surface=source_surface,
            action_domain="mcp",
            action_type=_normalize_action_class(tool_payload.get("action_class")),
            action_name=parsed["tool_name"],
            tool_kind="mcp_tool",
            mcp_server_id=parsed["server_id"],
            mcp_tool_id=parsed["tool_name"],
            connector_id=f"mcp:{parsed['server_id']}",
            skill_id=skill_id,
            risk_level=str(tool_payload.get("risk_level") or "").strip() or None,
            policy_decision="blocked",
            error_code=type(exc).__name__,
            payer="platform_credits",
            billing_mode="none",
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            agent_id=agent_id,
            app_id=app_id,
            input_summary=goal,
            output_summary=str(exc),
            source_table="mcp_tool_calls",
            source_event_id=source_event_id,
        )
        raise
    arguments = _parse_goal_arguments(goal, tool_payload)
    arguments = _validate_mcp_arguments(
        arguments,
        tool_payload.get("input_schema"),
        tool_name=parsed["tool_name"],
    )
    credential = _resolve_mcp_credential(server, workspace_id)
    mcp_http_client = _build_mcp_http_client(credential)
    common_event = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "surface": surface,
        "source_surface": source_surface,
        "action_domain": "mcp",
        "action_type": _normalize_action_class(tool_payload.get("action_class")),
        "action_name": parsed["tool_name"],
        "tool_kind": "mcp_tool",
        "mcp_server_id": parsed["server_id"],
        "mcp_tool_id": parsed["tool_name"],
        "connector_id": f"mcp:{parsed['server_id']}",
        "skill_id": skill_id,
        "risk_level": str(tool_payload.get("risk_level") or "").strip() or None,
        "policy_decision": "allowed",
        "payer": "platform_credits",
        "billing_mode": "transparency",
        "user_id": user_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "app_id": app_id,
        "input_summary": goal,
        "source_table": "mcp_tool_calls",
        "source_event_id": source_event_id,
        "metadata": {"server_label": server.get("label"), "argument_keys": sorted(arguments.keys())},
    }
    await agent_action_metering_service.record_started(**common_event)
    try:
        result = await _call_streamable_http_tool_async(
            endpoint=str(server.get("endpoint") or "").strip(),
            tool_name=parsed["tool_name"],
            arguments=arguments,
            http_client=mcp_http_client,
            client_session_cls=client_session_cls,
            streamable_http_client_fn=streamable_http_client_fn,
        )
    except Exception as exc:
        await agent_action_metering_service.record_failed(
            **common_event,
            error_code=type(exc).__name__,
            output_summary=str(exc),
        )
        raise
    payload = _mcp_result_payload(result)
    await agent_action_metering_service.record_completed(
        **common_event,
        output_summary=_mcp_reply(payload, agent_label=agent_label, tool_name=parsed["tool_name"]),
    )
    return {
        "status": "ok",
        "reply": _mcp_reply(payload, agent_label=agent_label, tool_name=parsed["tool_name"]),
        "artifact": {
            "label": str(tool_payload.get("label") or parsed["tool_name"]).strip() or parsed["tool_name"],
            "kind": _tool_kind(tool_payload),
            "summary": f"MCP tool {parsed['tool_name']} on server {server.get('label') or parsed['server_id']}",
            "media_type": "application/json",
            "preview_content": json.dumps(
                {
                    "server_id": parsed["server_id"],
                    "tool_name": parsed["tool_name"],
                    "arguments": arguments,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )[:12000],
        },
        "steps": [
            {"label": "Resolving MCP server", "detail": str(server.get("label") or parsed["server_id"]).strip() or parsed["server_id"], "status": "done", "kind": "thinking"},
            {"label": "Invoking MCP tool", "detail": parsed["tool_name"], "status": "done", "kind": "connector"},
        ],
        "mcp": {
            "server_id": parsed["server_id"],
            "tool_name": parsed["tool_name"],
            "arguments": arguments,
            "payload": payload,
        },
    }

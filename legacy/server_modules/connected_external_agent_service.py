from __future__ import annotations

import ipaddress
import json
import asyncio
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException

from server_modules import control_plane_repository
from server_modules import gateway_execution_service
from server_modules import provider_profiles as provider_profiles_service
from server_modules import runtime_attachment_service


SURFACE_KIND_CONNECTED_EXTERNAL_AGENT = "connected_external_agent"
SURFACE_KIND_NATIVE_STUDIO_AGENT = "native_studio_agent"
SURFACE_KIND_AGENT_COMPUTER = "agent_computer"
SURFACE_KIND_AGENT_GROUP_RESERVED = "agent_group_reserved"

CONNECTION_STATE_UNVERIFIED = "unverified"
CONNECTION_STATE_VERIFIED = "verified"
CONNECTION_STATE_ERROR = "error"
CONNECTION_STATE_REVOKED = "revoked"

EXTERNAL_AGENT_MANIFEST_SCHEMA_VERSION = "studio.external_agent.v1"
ALLOWED_PROVIDER_KINDS = {"openclaw", "hermes", "nemoclaw", "custom"}
EXTERNAL_AGENT_CAPABILITY_CHAT = "chat"
ALLOWED_EXTERNAL_AGENT_CAPABILITIES = {
    "actions",
    "activity",
    "artifacts",
    "channels",
    "chat",
    "devices",
    "events",
    "health",
    "knowledge",
    "knowledge_read",
    "knowledge_write",
    "logs",
    "mcp",
    "memory",
    "memory_read",
    "memory_write",
    "nodes",
    "sub_agents",
    "skills",
    "tools",
    "voice_channels",
    "workflows",
}
ALLOWED_PROTOCOL_KINDS = {"custom_http", "a2a", "mcp", "openclaw", "hermes", "nemoclaw"}
ALLOWED_SECTION_DISPLAY_KINDS = {
    "approval_queue",
    "artifact_list",
    "cards",
    "key_value",
    "logs",
    "table",
    "timeline",
}
ALLOWED_SECTION_CATEGORIES = {"activity", "configuration", "outputs", "resources", "security"}
ALLOWED_SECTION_ICONS = {
    "actions",
    "artifacts",
    "channels",
    "devices",
    "key_value",
    "logs",
    "memory",
    "nodes",
    "skills",
    "sub_agents",
    "tools",
    "workflows",
}
ALLOWED_EXTERNAL_OBJECT_TYPES = {
    "external_agent_action",
    "external_agent_artifact",
    "external_agent_channel",
    "external_agent_event",
    "external_agent_knowledge_source",
    "external_agent_memory_item",
    "external_agent_node",
    "external_agent_run_result",
    "external_agent_skill",
    "external_agent_sub_agent",
    "external_agent_tool",
    "external_agent_workflow",
}
ALLOWED_MANIFEST_KEYS = {
    "actions",
    "auth",
    "auth_mode",
    "auth_scheme",
    "auth_type",
    "artifacts",
    "capabilities",
    "description",
    "endpoint_refs",
    "endpoints",
    "events",
    "features",
    "health",
    "id",
    "local_connector",
    "metadata",
    "model",
    "name",
    "object_types",
    "objects",
    "protocol",
    "protocol_version",
    "protocols",
    "provider_kind",
    "runtime",
    "schema_version",
    "sub_agents",
    "surface_sections",
    "summary",
    "trust",
    "version",
}
ALLOWED_AUTH_MANIFEST_KEYS = {"auth_mode", "auth_scheme", "auth_type", "header_name", "scheme", "secret_ref", "type"}
ALLOWED_ENDPOINT_KEYS = {
    "actions",
    "actions_url",
    "artifacts",
    "artifacts_url",
    "base_url",
    "channels",
    "channels_url",
    "chat",
    "chat_url",
    "events",
    "events_url",
    "health",
    "health_url",
    "knowledge",
    "knowledge_url",
    "logs",
    "logs_url",
    "manifest",
    "manifest_url",
    "memory",
    "memory_url",
    "mcp",
    "mcp_url",
    "nodes",
    "nodes_url",
    "runs",
    "runs_url",
    "skills",
    "skills_url",
    "sub_agents",
    "sub_agents_url",
    "tools",
    "tools_url",
    "voice_channels",
    "voice_channels_url",
    "workflows",
    "workflows_url",
}
TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SECRET_FIELD_MARKERS = {"secret", "token", "password", "api_key", "apikey", "authorization", "bearer"}
SECRET_FIELD_ALLOWLIST = {"auth", "auth_type", "auth_scheme", "auth_mode", "authentication", "secret_ref"}
SECRET_RESOLUTION_FIELDS = ["api_key", "access_token", "oauth_token", "token", "authorization", "auth_header", "header_value"]
MAX_MANIFEST_RESPONSE_BYTES = 256 * 1024
MAX_CHAT_RESPONSE_BYTES = 256 * 1024
MAX_SECTION_RESPONSE_BYTES = 256 * 1024
MAX_CHAT_MESSAGE_CHARS = 16_000
MAX_RECENT_MESSAGES = 16
MAX_RECENT_MESSAGE_CHARS = 4_000
LOCAL_CONNECTOR_PROXY_CAPABILITY = "external_agent_proxy"
LOCAL_STORE: Dict[tuple[str, str, str], Dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_string(value: Any, default: str = "") -> str:
    return str(value or "").strip() or default


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _coerce_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _to_json(value: Any, *, default: Any) -> str:
    return json.dumps(value if value is not None else default, ensure_ascii=False, separators=(",", ":"), default=str)


def _normalize_provider_kind(value: Any) -> str:
    token = _read_string(value, "custom").lower().replace("-", "_").replace(" ", "_")
    if token in {"open_claw", "openclaw_gateway"}:
        token = "openclaw"
    if token in {"nemo_claw", "nvidia_nemoclaw"}:
        token = "nemoclaw"
    if token not in ALLOWED_PROVIDER_KINDS:
        return "custom"
    return token


def _redact_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key or "").lower()
            if lowered not in SECRET_FIELD_ALLOWLIST and any(marker in lowered for marker in SECRET_FIELD_MARKERS):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


def _contains_raw_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key or "").lower()
            if lowered not in SECRET_FIELD_ALLOWLIST and any(marker in lowered for marker in SECRET_FIELD_MARKERS):
                if _read_string(item):
                    return True
            if _contains_raw_secret(item):
                return True
    elif isinstance(value, list):
        return any(_contains_raw_secret(item) for item in value)
    return False


def _normalize_capability_id(value: Any) -> str:
    return _read_string(value).lower().replace("-", "_").replace(" ", "_")


def _normalize_token(value: Any, *, field_name: str) -> str:
    token = _read_string(value).lower().replace("-", "_").replace(" ", "_")
    if not token or not TOKEN_RE.match(token):
        raise ValueError(f"{field_name} must be a lowercase token.")
    return token


def _normalize_capabilities(value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    if not payload and isinstance(value, list):
        payload = {"capabilities": value}

    raw_capabilities = payload.get("capabilities")
    if raw_capabilities is None:
        raw_capabilities = payload.get("features")
    if isinstance(raw_capabilities, dict):
        capability_ids = [
            _normalize_capability_id(key)
            for key, enabled in raw_capabilities.items()
            if bool(enabled) and _normalize_capability_id(key)
        ]
    else:
        capability_ids = [_normalize_capability_id(item) for item in _coerce_list(raw_capabilities)]

    if _coerce_dict(payload.get("endpoints")).get("chat") or _coerce_dict(payload.get("endpoints")).get("chat_url"):
        capability_ids.append(EXTERNAL_AGENT_CAPABILITY_CHAT)

    unique = sorted({item for item in capability_ids if item in ALLOWED_EXTERNAL_AGENT_CAPABILITIES})
    return {
        "capabilities": unique,
        "chat": EXTERNAL_AGENT_CAPABILITY_CHAT in unique,
        "knowledge_read": "knowledge_read" in unique or "knowledge" in unique,
        "knowledge_write": "knowledge_write" in unique,
        "memory_read": "memory_read" in unique or "memory" in unique,
        "memory_write": "memory_write" in unique,
        "actions": "actions" in unique or "tools" in unique,
        "artifacts": "artifacts" in unique,
        "events": "events" in unique or "activity" in unique,
        "logs": "logs" in unique or "activity" in unique,
        "nodes": "nodes" in unique or "devices" in unique,
        "skills": "skills" in unique,
        "sub_agents": "sub_agents" in unique,
        "tools": "tools" in unique,
        "workflows": "workflows" in unique,
    }


def _is_ip_private_or_loopback(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname)
        return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
    except ValueError:
        return False


def _resolve_host_is_private(hostname: str) -> bool:
    if _is_ip_private_or_loopback(hostname):
        return True
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        address = info[4][0]
        if _is_ip_private_or_loopback(address):
            return True
    return False


def _environment_token() -> str:
    return str(os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or os.getenv("ENV") or os.getenv("NODE_ENV") or "").strip().lower()


def _local_store_allowed() -> bool:
    token = _environment_token()
    if token in {"prod", "production", "staging"}:
        return False
    explicit = str(os.getenv("EMPYRALIS_EXTERNAL_AGENT_LOCAL_STORE") or "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    return token in {"dev", "development", "local", "test", "testing"}


def _require_control_plane_or_local_store(connection: Any) -> None:
    if connection is not None:
        return
    if _local_store_allowed():
        return
    raise RuntimeError("Connected external agents require control-plane storage; local store fallback is disabled.")


def validate_public_https_url(value: Any, *, field_name: str = "endpoint", check_dns: bool = False) -> str:
    url = _read_string(value)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"{field_name} must use HTTPS unless routed through an Agent Computer bridge.")
    hostname = _read_string(parsed.hostname)
    if not hostname:
        raise ValueError(f"{field_name} must include a hostname.")
    lowered = hostname.lower()
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        raise ValueError(f"{field_name} cannot target localhost/private-network hosts directly.")
    if check_dns and _resolve_host_is_private(hostname):
        raise ValueError(f"{field_name} cannot target private-network hosts directly.")
    return url


def validate_agent_computer_proxy_url(value: Any, *, field_name: str = "endpoint") -> str:
    url = _read_string(value)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must use HTTP or HTTPS behind an Agent Computer bridge.")
    hostname = _read_string(parsed.hostname)
    if not hostname:
        raise ValueError(f"{field_name} must include a hostname.")
    lowered = hostname.lower()
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        return url
    if _is_ip_private_or_loopback(hostname):
        return url
    raise ValueError(f"{field_name} must stay on localhost/private-network hosts when routed through Agent Computer.")


def _canonical_endpoint_key(key: Any) -> str:
    token = _normalize_token(key, field_name="endpoint_ref")
    if token == "manifest":
        return "manifest_url"
    if token == "chat":
        return "chat_url"
    if token == "health":
        return "health_url"
    if not token.endswith("_url") and token != "base_url":
        return f"{token}_url"
    return token


async def _validate_public_https_url_dns(value: Any, *, field_name: str = "endpoint") -> str:
    url = validate_public_https_url(value, field_name=field_name, check_dns=False)
    if not url:
        return ""
    hostname = _read_string(urlparse(url).hostname)
    if await asyncio.to_thread(_resolve_host_is_private, hostname):
        raise ValueError(f"{field_name} cannot target private-network hosts directly.")
    return url


def _normalize_endpoints(value: Any, *, allow_agent_computer_proxy: bool = False) -> Dict[str, str]:
    payload = _coerce_dict(value)
    normalized: Dict[str, str] = {}
    for source_key, source_value in payload.items():
        target_key = _canonical_endpoint_key(source_key)
        if target_key not in ALLOWED_ENDPOINT_KEYS:
            raise ValueError(f"External agent endpoint ref is unsupported: {source_key}.")
        normalized[target_key] = (
            validate_agent_computer_proxy_url(source_value, field_name=str(source_key))
            if allow_agent_computer_proxy
            else validate_public_https_url(source_value, field_name=str(source_key))
        )
    if normalized.get("base_url") and not normalized.get("manifest_url"):
        normalized["manifest_url"] = urljoin(normalized["base_url"].rstrip("/") + "/", ".well-known/agent-manifest.json")
    return {key: value for key, value in normalized.items() if value}


async def _validate_endpoint_map_dns(endpoints: Dict[str, str]) -> Dict[str, str]:
    validated: Dict[str, str] = {}
    for key, value in dict(endpoints or {}).items():
        validated[key] = await _validate_public_https_url_dns(value, field_name=key)
    return {key: value for key, value in validated.items() if value}


def _manifest_endpoints(manifest: Any, *, allow_agent_computer_proxy: bool = False) -> Dict[str, str]:
    payload = _coerce_dict(manifest)
    endpoints = _coerce_dict(payload.get("endpoints"))
    if not endpoints:
        endpoints = _coerce_dict(payload.get("endpoint_refs"))
    return _normalize_endpoints(endpoints, allow_agent_computer_proxy=allow_agent_computer_proxy)


def _normalize_protocols(value: Any) -> List[Dict[str, str]]:
    raw_items = _coerce_list(value)
    if not raw_items:
        return []
    out: List[Dict[str, str]] = []
    for item in raw_items:
        payload = _coerce_dict(item)
        kind = _normalize_token(payload.get("kind"), field_name="protocol.kind")
        if kind not in ALLOWED_PROTOCOL_KINDS:
            raise ValueError(f"External agent protocol is unsupported: {kind}.")
        protocol: Dict[str, str] = {"kind": kind}
        for key in ("version", "agent_card_ref", "server_ref"):
            value = _read_string(payload.get(key))
            if value:
                protocol[key] = value[:240]
        out.append(protocol)
    return sorted(out, key=lambda section: (int(section.get("priority") or 50), str(section.get("title") or "")))


def _normalize_external_object_types(value: Any) -> List[str]:
    raw_items = _coerce_list(value)
    if not raw_items:
        return []
    out: List[str] = []
    for item in raw_items:
        token = _normalize_token(item, field_name="external_object_type")
        if token not in ALLOWED_EXTERNAL_OBJECT_TYPES:
            raise ValueError(f"External object type is unsupported: {token}.")
        out.append(token)
    return sorted(set(out))


def _normalize_external_sub_agents(value: Any) -> List[Dict[str, Any]]:
    raw_items = _coerce_list(value)
    if not raw_items:
        return []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items[:50]:
        payload = _coerce_dict(item)
        external_id = _read_string(payload.get("external_id") or payload.get("id"))
        if not external_id:
            continue
        normalized_id = external_id[:160]
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        display_name = _read_string(payload.get("name") or payload.get("label") or payload.get("title"), normalized_id)
        role = _read_string(payload.get("role") or payload.get("kind"))[:120] or None
        capabilities = [
            capability
            for capability in (
                _normalize_capability_id(raw_capability)
                for raw_capability in _coerce_list(payload.get("capabilities"))
            )
            if capability in ALLOWED_EXTERNAL_AGENT_CAPABILITIES
        ][:24]
        out.append(_redact_secret_fields({
            "id": normalized_id,
            "external_id": normalized_id,
            "name": display_name[:120],
            "label": display_name[:120],
            "summary": _read_string(payload.get("summary") or payload.get("description"))[:500] or None,
            "status": _read_string(payload.get("status"))[:80] or None,
            "role": role,
            "capabilities": sorted(set(capabilities)),
            "ownership": "external",
            "object_type": "external_agent_sub_agent",
        }))
    return out


def _endpoint_key_from_ref(value: Any, *, field_name: str) -> str:
    token = _canonical_endpoint_key(value)
    if token not in ALLOWED_ENDPOINT_KEYS:
        raise ValueError(f"{field_name} references an unsupported endpoint.")
    return token


def _normalize_surface_sections(value: Any, capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_items = _coerce_list(value)
    if not raw_items:
        return []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    capability_ids = set(_coerce_list(capabilities.get("capabilities")))
    for item in raw_items:
        payload = _coerce_dict(item)
        section_id = _normalize_token(payload.get("id"), field_name="surface_sections.id")
        if section_id in seen:
            raise ValueError(f"Duplicate external surface section id: {section_id}.")
        seen.add(section_id)
        title = _read_string(payload.get("title"))
        if not title:
            raise ValueError("surface_sections.title is required.")
        description = _read_string(payload.get("description"))[:160] or None
        empty_state = _read_string(payload.get("empty_state"))[:160] or None
        category = _normalize_token(payload.get("category") or "activity", field_name="surface_sections.category")
        if category not in ALLOWED_SECTION_CATEGORIES:
            raise ValueError(f"Surface section category is unsupported: {category}.")
        try:
            priority = int(payload.get("priority", 50))
        except (TypeError, ValueError):
            priority = 50
        priority = max(0, min(priority, 100))
        display_kind = _normalize_token(payload.get("display_kind"), field_name="surface_sections.display_kind")
        if display_kind not in ALLOWED_SECTION_DISPLAY_KINDS:
            raise ValueError(f"Surface section display_kind is unsupported: {display_kind}.")
        icon = _normalize_token(payload.get("icon") or display_kind, field_name="surface_sections.icon")
        if icon not in ALLOWED_SECTION_ICONS:
            icon = display_kind if display_kind in ALLOWED_SECTION_ICONS else "key_value"
        capability_required = _read_string(payload.get("capability_required"))
        if capability_required:
            capability_required = _normalize_capability_id(capability_required)
            if capability_required not in capability_ids and not bool(capabilities.get(capability_required)):
                raise ValueError(f"Surface section requires undeclared capability: {capability_required}.")
        data_endpoint_ref = _endpoint_key_from_ref(payload.get("data_endpoint_ref"), field_name="surface_sections.data_endpoint_ref")
        actions_endpoint_ref = _read_string(payload.get("actions_endpoint_ref"))
        normalized_actions_endpoint_ref = (
            _endpoint_key_from_ref(actions_endpoint_ref, field_name="surface_sections.actions_endpoint_ref")
            if actions_endpoint_ref
            else None
        )
        out.append({
            "id": section_id,
            "title": title[:120],
            "description": description,
            "empty_state": empty_state,
            "category": category,
            "priority": priority,
            "icon": icon,
            "capability_required": capability_required or None,
            "data_endpoint_ref": data_endpoint_ref,
            "actions_endpoint_ref": normalized_actions_endpoint_ref,
            "display_kind": display_kind,
            "interaction": "read_only",
        })
    return out


def _normalize_local_connector(value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    if not payload:
        return {
            "required": False,
            "mode": "none",
            "binding_state": "not_required",
            "bound": False,
            "proxy_available": False,
        }
    required = bool(payload.get("required"))
    capability = _read_string(payload.get("agent_computer_capability"))[:120] or None
    if required and not capability:
        capability = LOCAL_CONNECTOR_PROXY_CAPABILITY
    return {
        "required": required,
        "mode": "agent_computer_proxy" if required else "none",
        "reason": _read_string(payload.get("reason"))[:240] or None,
        "agent_computer_id": _read_string(payload.get("agent_computer_id"))[:160] or None,
        "agent_computer_capability": capability,
        "binding_state": "missing_agent_computer" if required else "not_required",
        "bound": False,
        "proxy_available": False,
    }


def _normalize_manifest_projection(payload: Dict[str, Any]) -> Dict[str, Any]:
    schema_version = _read_string(payload.get("schema_version"))
    if schema_version and schema_version != EXTERNAL_AGENT_MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"External agent manifest schema_version is unsupported: {schema_version}.")
    capabilities = _normalize_capabilities(payload)
    protocols = _normalize_protocols(payload.get("protocols"))
    if not protocols and _read_string(payload.get("protocol")):
        protocols = [{
            "kind": _normalize_token(payload.get("protocol"), field_name="protocol"),
            "version": _read_string(payload.get("protocol_version"))[:80] or "",
        }]
        if protocols[0]["kind"] not in ALLOWED_PROTOCOL_KINDS:
            raise ValueError(f"External agent protocol is unsupported: {protocols[0]['kind']}.")
    objects = _normalize_external_object_types(payload.get("objects") or payload.get("object_types"))
    return {
        "schema_version": schema_version or EXTERNAL_AGENT_MANIFEST_SCHEMA_VERSION,
        "protocols": protocols,
        "capability_manifest": capabilities,
        "surface_sections": _normalize_surface_sections(payload.get("surface_sections"), capabilities),
        "object_types": objects,
        "external_sub_agents": _normalize_external_sub_agents(payload.get("sub_agents")),
        "local_connector": _normalize_local_connector(payload.get("local_connector")),
    }


def _local_connector_attachment_candidates(attachment: Dict[str, Any]) -> set[str]:
    identity = _coerce_dict(attachment.get("gateway_identity"))
    candidates = {
        attachment.get("id"),
        attachment.get("attachment_id"),
        attachment.get("runtime_profile_id"),
        attachment.get("runtime_node_id"),
        attachment.get("runtime_id"),
        attachment.get("machine_id"),
        attachment.get("instance_id"),
        identity.get("gateway_id"),
        identity.get("device_id"),
    }
    return {_read_string(item) for item in candidates if _read_string(item)}


def _local_connector_state(
    connector: Dict[str, Any],
    *,
    state: str,
    attachment: Optional[Dict[str, Any]] = None,
    message: Optional[str] = None,
    gate_reason: Optional[str] = None,
) -> Dict[str, Any]:
    capability = _read_string(connector.get("agent_computer_capability"))
    payload = {
        **connector,
        "binding_state": state,
        "bound": state == "bound",
        "proxy_available": bool(state == "bound" and capability == LOCAL_CONNECTOR_PROXY_CAPABILITY),
    }
    if message:
        payload["binding_message"] = message[:240]
    if gate_reason:
        payload["gate_reason"] = gate_reason[:120]
    if attachment:
        identity = _coerce_dict(attachment.get("gateway_identity"))
        payload.update({
            "agent_computer_label": _read_string(attachment.get("label"), "Agent Computer"),
            "attachment_id": _read_string(attachment.get("attachment_id")) or None,
            "attachment_kind": _read_string(attachment.get("attachment_kind")) or None,
            "runtime_attachment_id": _read_string(attachment.get("attachment_id")) or None,
            "runtime_profile_id": _read_string(attachment.get("runtime_profile_id")) or None,
            "runtime_node_id": _read_string(attachment.get("runtime_node_id")) or None,
            "runtime_id": _read_string(attachment.get("runtime_id")) or None,
            "machine_id": _read_string(attachment.get("machine_id")) or None,
            "gateway_id": _read_string(identity.get("gateway_id")) or None,
            "device_id": _read_string(identity.get("device_id")) or None,
        })
    return payload


def _local_connector_gate_state_from_error(reason: str) -> str:
    lowered = _read_string(reason).lower()
    if "revoked" in lowered:
        return "revoked"
    if "offline" in lowered:
        return "offline"
    if "unhealthy" in lowered:
        return "unhealthy"
    if "capability" in lowered:
        return "missing_capability"
    if "scope" in lowered:
        return "scope_mismatch"
    if "owner_approved" in lowered or "not_owner" in lowered:
        return "unapproved"
    return "unavailable"


def _local_companion_binding_state(
    *,
    attachment: Dict[str, Any],
    workspace_id: str,
    required_capability: Optional[str],
) -> Dict[str, Any]:
    connector_workspace = _read_string(attachment.get("workspace_id"))
    if connector_workspace and connector_workspace != _read_string(workspace_id):
        return {"state": "scope_mismatch", "message": "Agent Computer is outside this workspace."}
    identity = _coerce_dict(attachment.get("gateway_identity"))
    status = _read_string(attachment.get("status")).lower()
    control_state = _read_string(attachment.get("control_state")).lower()
    trust_state = _read_string(identity.get("device_trust_state")).lower()
    if status == "revoked" or control_state == "revoked" or trust_state == "revoked":
        return {"state": "revoked", "message": "Agent Computer is revoked."}
    if not bool(attachment.get("online")):
        return {"state": "offline", "message": "Agent Computer is offline."}
    if not bool(attachment.get("healthy")):
        return {"state": "unhealthy", "message": "Agent Computer is unhealthy."}
    if required_capability:
        if not runtime_attachment_service._local_companion_ready_capability_match(attachment, [required_capability]):
            return {
                "state": "missing_capability",
                "message": "Agent Computer is missing external-agent bridge readiness.",
            }
    return {"state": "bound", "message": "Agent Computer is ready to bridge approved local runtimes."}


async def _resolve_local_connector_binding(
    *,
    tenant_id: str,
    workspace_id: str,
    connector: Dict[str, Any],
) -> Dict[str, Any]:
    if not bool(connector.get("required")):
        return _local_connector_state(connector, state="not_required")
    requested_agent_computer_id = _read_string(connector.get("agent_computer_id"))
    if not requested_agent_computer_id:
        return _local_connector_state(
            connector,
            state="missing_agent_computer",
            message="Choose an Agent Computer before this connected agent can use a private local runtime.",
        )
    required_capability = _read_string(connector.get("agent_computer_capability")) or None
    try:
        inventory = await runtime_attachment_service.list_workspace_runtime_attachments(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except Exception as error:
        return _local_connector_state(
            connector,
            state="unavailable",
            message="Agent Computer inventory is unavailable.",
            gate_reason=type(error).__name__,
        )
    attachments = [
        _coerce_dict(item)
        for item in _coerce_list(_coerce_dict(inventory).get("attachments"))
        if _coerce_dict(item)
    ]
    selected = next(
        (
            attachment
            for attachment in attachments
            if requested_agent_computer_id in _local_connector_attachment_candidates(attachment)
        ),
        None,
    )
    if selected is None:
        return _local_connector_state(
            connector,
            state="not_found",
            message="The requested Agent Computer is not registered in this workspace.",
        )
    attachment_kind = _read_string(selected.get("attachment_kind"))
    if attachment_kind == "self_hosted_business_node":
        try:
            runtime_attachment_service.ensure_self_hosted_node_gate(
                attachment=selected,
                workspace_id=workspace_id,
                required_capabilities=[required_capability] if required_capability else None,
            )
        except runtime_attachment_service.RuntimeAttachmentSelectionError as error:
            return _local_connector_state(
                connector,
                state=_local_connector_gate_state_from_error(error.reason),
                attachment=selected,
                message=error.message,
                gate_reason=error.reason,
            )
        return _local_connector_state(
            connector,
            state="bound",
            attachment=selected,
            message="Agent Computer is ready to bridge approved local runtimes.",
        )
    if attachment_kind == "local_companion":
        gate = _local_companion_binding_state(
            attachment=selected,
            workspace_id=workspace_id,
            required_capability=required_capability,
        )
        return _local_connector_state(
            connector,
            state=_read_string(gate.get("state"), "unavailable"),
            attachment=selected,
            message=_read_string(gate.get("message")) or None,
        )
    return _local_connector_state(
        connector,
        state="unsupported_attachment_kind",
        attachment=selected,
        message="Only local companion and self-hosted Agent Computers can bridge private external agents.",
    )


async def _manifest_projection_for_workspace(
    *,
    tenant_id: str,
    workspace_id: str,
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    projection = _normalize_manifest_projection(manifest)
    projection["local_connector"] = await _resolve_local_connector_binding(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        connector=_coerce_dict(projection.get("local_connector")),
    )
    return projection


def _sanitize_manifest(value: Any) -> Dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        payload = dict(value)
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as error:
            raise ValueError("External agent manifest must be valid JSON.") from error
        if not isinstance(parsed, dict):
            raise ValueError("External agent manifest must be a JSON object.")
        payload = dict(parsed)
    else:
        raise ValueError("External agent manifest must be a JSON object.")
    unexpected = sorted(set(payload.keys()) - ALLOWED_MANIFEST_KEYS)
    if unexpected:
        raise ValueError(f"External agent manifest includes unsupported fields: {', '.join(unexpected)}.")
    auth_payload = payload.get("auth")
    if auth_payload is not None:
        auth = _coerce_dict(auth_payload)
        if not auth:
            raise ValueError("External agent manifest auth must be an object.")
        unexpected_auth = sorted(set(auth.keys()) - ALLOWED_AUTH_MANIFEST_KEYS)
        if unexpected_auth:
            raise ValueError(f"External agent manifest auth includes unsupported fields: {', '.join(unexpected_auth)}.")
    if _contains_raw_secret(payload):
        raise ValueError("External agent manifests may not contain raw secrets; store a secret_ref instead.")
    _normalize_manifest_projection(payload)
    return _redact_secret_fields(payload)


def _json_response_payload(response: Any, *, max_bytes: int, label: str) -> Dict[str, Any]:
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)) and len(content) > max_bytes:
        raise ValueError(f"{label} response exceeded {max_bytes} bytes.")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{label} response must be a JSON object.")
    if content is None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        if len(encoded) > max_bytes:
            raise ValueError(f"{label} response exceeded {max_bytes} bytes.")
    return payload


def _credential_id_from_secret_ref(secret_ref: Any) -> str:
    token = _read_string(secret_ref)
    if not token:
        return ""
    if token.startswith("vault://"):
        parsed = urlparse(token)
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            return path_parts[-1].strip()
        if parsed.netloc and parsed.netloc not in {"credential", "credentials", "workspace", "vault"}:
            return parsed.netloc.strip()
        return ""
    return token


def _auth_config_from_manifest(manifest: Any) -> Dict[str, str]:
    payload = _coerce_dict(manifest)
    auth = _coerce_dict(payload.get("auth"))
    return {
        "header_name": _read_string(auth.get("header_name"), "Authorization"),
        "scheme": _read_string(auth.get("scheme") or auth.get("auth_scheme") or payload.get("auth_scheme") or payload.get("auth_type") or payload.get("auth_mode"), "bearer").lower(),
    }


def _auth_headers_from_credentials(credentials: Dict[str, Any], manifest: Any) -> Dict[str, str]:
    config = _auth_config_from_manifest(manifest)
    header_name = config["header_name"] or "Authorization"
    direct_value = _read_string(credentials.get("authorization") or credentials.get("auth_header") or credentials.get("header_value"))
    if direct_value:
        return {header_name: direct_value}
    token = _read_string(
        credentials.get("api_key")
        or credentials.get("access_token")
        or credentials.get("oauth_token")
        or credentials.get("token")
    )
    if not token:
        raise ValueError("External agent credential payload does not contain an injectable auth token.")
    scheme = config["scheme"]
    if header_name.lower() == "authorization" and scheme not in {"raw", "none", "no_auth"}:
        prefix = "Bearer" if scheme in {"", "api_key", "bearer", "oauth", "oauth_token", "token"} else scheme.title()
        return {header_name: f"{prefix} {token}"}
    return {header_name: token}


def _target_domain(value: str) -> str:
    return _read_string(urlparse(value).hostname).lower()


def _resolve_auth_headers_for_call(
    *,
    tenant_id: str,
    workspace_id: str,
    agent: Dict[str, Any],
    target_url: str,
) -> Dict[str, str]:
    secret_ref = _read_string(agent.get("secret_ref"))
    if not secret_ref:
        return {}
    credential_id = _credential_id_from_secret_ref(secret_ref)
    if not credential_id:
        raise ValueError("External agent credential reference is invalid.")
    domain = _target_domain(target_url)
    try:
        credentials = provider_profiles_service.resolve_vault_credential(
            credential_id,
            workspace_id,
            tenant_id=tenant_id,
            allowed_fields=SECRET_RESOLUTION_FIELDS,
            actor_type="studio_connected_external_agent",
            actor_id=_read_string(agent.get("id")),
            purpose="connected_external_agent_proxy",
            target_domain=domain,
            allowed_domains=[domain] if domain else [],
            ttl_seconds=30,
        )
    except Exception as error:
        raise ValueError("External agent credential could not be resolved.") from error
    return _auth_headers_from_credentials(credentials, agent.get("manifest"))


def _validate_recent_messages(value: Any) -> List[Dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("recent_messages must be a list.")
    if len(value) > MAX_RECENT_MESSAGES:
        raise ValueError(f"recent_messages cannot include more than {MAX_RECENT_MESSAGES} messages.")
    out: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("recent_messages entries must be objects.")
        role = _read_string(item.get("role")).lower()
        content = _read_string(item.get("content") or item.get("text"))
        if role not in {"user", "assistant", "system"}:
            raise ValueError("recent_messages role must be user, assistant, or system.")
        if not content:
            raise ValueError("recent_messages content is required.")
        if len(content) > MAX_RECENT_MESSAGE_CHARS:
            raise ValueError(f"recent_messages content cannot exceed {MAX_RECENT_MESSAGE_CHARS} characters.")
        out.append({"role": role, "content": content})
    return out


def _surface_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _coerce_dict(record.get("metadata"))
    manifest = _coerce_dict(record.get("manifest"))
    projection = _coerce_dict(metadata.get("manifest_projection")) or _normalize_manifest_projection(manifest)
    local_connector = _coerce_dict(projection.get("local_connector"))
    endpoints = _normalize_endpoints(
        metadata.get("endpoint_refs") or {},
        allow_agent_computer_proxy=bool(local_connector.get("required")),
    )
    capability_manifest = _coerce_dict(projection.get("capability_manifest")) or _normalize_capabilities(metadata.get("capability_manifest") or manifest)
    display_name = _read_string(record.get("label") or record.get("name"), "Connected Agent")
    status = _read_string(record.get("status"), "active")
    connection_state = _read_string(metadata.get("connection_state"), CONNECTION_STATE_UNVERIFIED)
    if status in {"revoked", "disabled"}:
        connection_state = CONNECTION_STATE_REVOKED
    return {
        "id": _read_string(record.get("id")),
        "surface_kind": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT,
        "studio_object_type": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT,
        "workspace_id": _read_string(record.get("workspace_id")),
        "tenant_id": _read_string(record.get("tenant_id")),
        "name": display_name,
        "label": display_name,
        "description": _read_string(record.get("description") or metadata.get("description")),
        "provider_kind": _normalize_provider_kind(metadata.get("provider_kind")),
        "status": status,
        "enabled": bool(record.get("enabled", status != "revoked")),
        "connection_state": connection_state,
        "trust_state": _read_string(metadata.get("trust_state"), connection_state),
        "endpoint_refs": endpoints,
        "secret_ref": _read_string(_coerce_dict(metadata.get("auth")).get("secret_ref")) or None,
        "capability_manifest": capability_manifest,
        "manifest_projection": projection,
        "surface_sections": _coerce_list(projection.get("surface_sections")),
        "object_types": _coerce_list(projection.get("object_types")),
        "external_sub_agents": _coerce_list(projection.get("external_sub_agents")),
        "protocols": _coerce_list(projection.get("protocols")),
        "local_connector": _coerce_dict(projection.get("local_connector")),
        "manifest": manifest,
        "last_error": _read_string(metadata.get("last_error")) or None,
        "last_manifest_refresh_at": _read_string(metadata.get("last_manifest_refresh_at")) or None,
        "created_at": _read_string(record.get("created_at")) or None,
        "updated_at": _read_string(record.get("updated_at")) or None,
    }


def _local_key(tenant_id: str, workspace_id: str, external_agent_id: str) -> tuple[str, str, str]:
    return (_read_string(tenant_id), _read_string(workspace_id), _read_string(external_agent_id))


def _local_insert(surface: Dict[str, Any]) -> Dict[str, Any]:
    LOCAL_STORE[_local_key(surface["tenant_id"], surface["workspace_id"], surface["id"])] = dict(surface)
    return dict(surface)


async def list_connected_external_agents(*, tenant_id: str, workspace_id: str) -> Dict[str, Any]:
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        _require_control_plane_or_local_store(connection)
        if connection is None:
            items = [
                dict(item)
                for (item_tenant, item_workspace, _), item in LOCAL_STORE.items()
                if item_tenant == tenant_id and item_workspace == workspace_id
            ]
            return {"workspace_id": workspace_id, "tenant_id": tenant_id, "items": items}
        rows = await connection.fetch(
            """
            SELECT
                wai.id,
                wai.tenant_id,
                wai.workspace_id,
                wai.label,
                wai.status,
                wai.enabled,
                wai.metadata,
                wai.created_at,
                wai.updated_at,
                ad.name,
                ad.description,
                am.manifest
            FROM workspace_agent_installs wai
            JOIN agent_definitions ad
                ON ad.id = wai.agent_definition_id
                AND ad.tenant_id = wai.tenant_id
                AND ad.workspace_id = wai.workspace_id
            LEFT JOIN agent_manifests am
                ON am.agent_install_id = wai.id
                AND am.tenant_id = wai.tenant_id
                AND am.workspace_id = wai.workspace_id
            WHERE wai.tenant_id = $1
              AND wai.workspace_id = $2
              AND wai.metadata->>'surface_kind' = $3
            ORDER BY wai.updated_at DESC
            """,
            tenant_id,
            workspace_id,
            SURFACE_KIND_CONNECTED_EXTERNAL_AGENT,
        )
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "items": [_surface_from_record(dict(row)) for row in rows],
    }


async def get_connected_external_agent(*, tenant_id: str, workspace_id: str, external_agent_id: str) -> Dict[str, Any]:
    token = _read_string(external_agent_id)
    if not token:
        raise ValueError("external_agent_id is required.")
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        _require_control_plane_or_local_store(connection)
        if connection is None:
            item = LOCAL_STORE.get(_local_key(tenant_id, workspace_id, token))
            if not item:
                raise LookupError("Connected external agent not found.")
            return dict(item)
        row = await connection.fetchrow(
            """
            SELECT
                wai.id,
                wai.tenant_id,
                wai.workspace_id,
                wai.label,
                wai.status,
                wai.enabled,
                wai.metadata,
                wai.created_at,
                wai.updated_at,
                ad.name,
                ad.description,
                am.manifest
            FROM workspace_agent_installs wai
            JOIN agent_definitions ad
                ON ad.id = wai.agent_definition_id
                AND ad.tenant_id = wai.tenant_id
                AND ad.workspace_id = wai.workspace_id
            LEFT JOIN agent_manifests am
                ON am.agent_install_id = wai.id
                AND am.tenant_id = wai.tenant_id
                AND am.workspace_id = wai.workspace_id
            WHERE wai.tenant_id = $1
              AND wai.workspace_id = $2
              AND wai.id = $3
              AND wai.metadata->>'surface_kind' = $4
            LIMIT 1
            """,
            tenant_id,
            workspace_id,
            token,
            SURFACE_KIND_CONNECTED_EXTERNAL_AGENT,
        )
    if row is None:
        raise LookupError("Connected external agent not found.")
    return _surface_from_record(dict(row))


def _agent_uses_local_connector(agent: Dict[str, Any]) -> bool:
    connector = _coerce_dict(agent.get("local_connector"))
    return bool(connector.get("required"))


def _gateway_id_for_local_connector(agent: Dict[str, Any]) -> str:
    connector = _coerce_dict(agent.get("local_connector"))
    gateway_id = _read_string(connector.get("gateway_id"))
    if not gateway_id:
        raise ValueError("External agent local connector is missing a gateway id.")
    return gateway_id


def _json_from_gateway_proxy_result(result: Dict[str, Any], *, label: str, max_bytes: int) -> Dict[str, Any]:
    status = int(result.get("status") or 0)
    if status >= 400:
        raise ValueError(f"{label} proxy returned HTTP {status}.")
    payload = result.get("body_json")
    if isinstance(payload, dict):
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        if len(encoded) > max_bytes:
            raise ValueError(f"{label} response exceeded {max_bytes} bytes.")
        return payload
    text = _read_string(result.get("body_text"))
    if text:
        if len(text.encode("utf-8")) > max_bytes:
            raise ValueError(f"{label} response exceeded {max_bytes} bytes.")
        try:
            parsed = json.loads(text)
        except Exception as error:
            raise ValueError(f"{label} response must be JSON.") from error
        if not isinstance(parsed, dict):
            raise ValueError(f"{label} response must be a JSON object.")
        return parsed
    raise ValueError(f"{label} response must include body_json.")


async def _proxy_external_agent_json_request(
    *,
    tenant_id: str,
    workspace_id: str,
    agent: Dict[str, Any],
    method: str,
    endpoint_url: str,
    label: str,
    max_bytes: int,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _ensure_local_connector_proxy_ready(agent)
    target_url = validate_agent_computer_proxy_url(endpoint_url, field_name=label.lower().replace(" ", "_"))
    auth_headers = _resolve_auth_headers_for_call(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent=agent,
        target_url=target_url,
    )
    result = await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=_gateway_id_for_local_connector(agent),
        capability_id=LOCAL_CONNECTOR_PROXY_CAPABILITY,
        arguments={
            "method": method,
            "url": target_url,
            "headers": {"Accept": "application/json", **auth_headers},
            "json": json_body or {},
            "max_bytes": max_bytes,
        },
        run_id=f"external-agent-proxy-{uuid.uuid4().hex}",
        trace_id=f"external-agent-proxy-{uuid.uuid4().hex}",
        workspace_id=workspace_id,
        timeout_seconds=30,
        runtime_access_mode="agent_computer_private_proxy",
        empyralis_approved=True,
        actor_id=_read_string(agent.get("id"), "connected_external_agent"),
        agent_scope="external_agent",
    )
    return _json_from_gateway_proxy_result(result, label=label, max_bytes=max_bytes)


async def _fetch_external_agent_json(
    *,
    tenant_id: str,
    workspace_id: str,
    agent: Dict[str, Any],
    endpoint_url: Any,
    field_name: str,
    label: str,
    max_bytes: int,
    method: str = "GET",
    json_body: Optional[Dict[str, Any]] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    if _agent_uses_local_connector(agent):
        target_url = validate_agent_computer_proxy_url(endpoint_url, field_name=field_name)
        if not target_url:
            raise ValueError(f"External agent has no {field_name} endpoint.")
        return await _proxy_external_agent_json_request(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent=agent,
            method=method,
            endpoint_url=target_url,
            label=label,
            max_bytes=max_bytes,
            json_body=json_body,
        )

    target_url = await _validate_public_https_url_dns(endpoint_url, field_name=field_name)
    if not target_url:
        raise ValueError(f"External agent has no {field_name} endpoint.")
    auth_headers = _resolve_auth_headers_for_call(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent=agent,
        target_url=target_url,
    )
    close_client = False
    client = http_client
    if client is None:
        client = httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        close_client = True
    try:
        if method.upper() == "POST":
            response = await client.post(target_url, json=json_body or {}, headers={"Accept": "application/json", **auth_headers})
        else:
            response = await client.get(target_url, headers={"Accept": "application/json", **auth_headers})
        response.raise_for_status()
        return _json_response_payload(response, max_bytes=max_bytes, label=label)
    finally:
        if close_client:
            await client.aclose()


def _build_metadata(
    *,
    provider_kind: str,
    endpoints: Dict[str, str],
    manifest: Dict[str, Any],
    secret_ref: Optional[str],
    connection_state: str,
    existing: Optional[Dict[str, Any]] = None,
    projection: Optional[Dict[str, Any]] = None,
    last_error: Optional[str] = None,
) -> Dict[str, Any]:
    current = _coerce_dict(existing)
    auth_payload = _coerce_dict(current.get("auth"))
    if secret_ref is not None:
        auth_payload = {"secret_ref": _read_string(secret_ref)}
    resolved_projection = _coerce_dict(projection) or _coerce_dict(current.get("manifest_projection")) or _normalize_manifest_projection(manifest)
    return {
        **current,
        "surface_kind": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT,
        "provider_kind": provider_kind,
        "endpoint_refs": dict(endpoints),
        "auth": auth_payload,
        "capability_manifest": resolved_projection["capability_manifest"],
        "manifest_projection": resolved_projection,
        "connection_state": connection_state,
        "trust_state": connection_state,
        "last_error": last_error,
    }


async def create_connected_external_agent(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    provider_kind: Any = "custom",
    endpoints: Any = None,
    manifest: Any = None,
    secret_ref: Optional[str] = None,
    created_by_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    display_name = _read_string(name)
    if not display_name:
        raise ValueError("name is required.")
    if _contains_raw_secret({"endpoints": endpoints, "manifest": manifest}):
        raise ValueError("Raw external-agent secrets are not allowed; provide secret_ref.")
    normalized_provider = _normalize_provider_kind(provider_kind)
    normalized_manifest = _sanitize_manifest(manifest)
    local_connector_required = bool(_coerce_dict(normalized_manifest.get("local_connector")).get("required"))
    normalized_endpoints = _normalize_endpoints(
        endpoints or {},
        allow_agent_computer_proxy=local_connector_required,
    )
    normalized_endpoints = {
        **normalized_endpoints,
        **_manifest_endpoints(normalized_manifest, allow_agent_computer_proxy=local_connector_required),
    }
    if not local_connector_required:
        normalized_endpoints = await _validate_endpoint_map_dns(normalized_endpoints)
    projection = await _manifest_projection_for_workspace(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=normalized_manifest,
    )
    install_id = f"extagent_{uuid.uuid4().hex}"
    definition_id = f"extdef_{uuid.uuid4().hex}"
    version_id = f"extver_{uuid.uuid4().hex}"
    now = _now_iso()
    metadata = _build_metadata(
        provider_kind=normalized_provider,
        endpoints=normalized_endpoints,
        manifest=normalized_manifest,
        secret_ref=secret_ref,
        connection_state=CONNECTION_STATE_UNVERIFIED,
        projection=projection,
    )

    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        _require_control_plane_or_local_store(connection)
        if connection is None:
            return _local_insert(_surface_from_record({
                "id": install_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "label": display_name,
                "name": display_name,
                "description": _read_string(normalized_manifest.get("description") or normalized_manifest.get("summary")),
                "status": "active",
                "enabled": True,
                "metadata": metadata,
                "manifest": normalized_manifest,
                "created_at": now,
                "updated_at": now,
            }))
        await connection.execute(
            """
            INSERT INTO agent_definitions (
                id, tenant_id, workspace_id, slug, name, description, agent_kind, visibility, status,
                category, icon, created_by_user_id, current_version_id, published_version_id,
                source_workflow_definition_id, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, 'specialist', 'workspace', 'draft',
                'Connected Agent', NULL, $7, $8, $8,
                NULL, $9::jsonb, NOW(), NOW()
            )
            """,
            definition_id,
            tenant_id,
            workspace_id,
            f"external-{install_id}",
            display_name,
            _read_string(normalized_manifest.get("description") or normalized_manifest.get("summary")),
            created_by_user_id,
            version_id,
            _to_json({"surface_kind": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT}, default={}),
        )
        await connection.execute(
            """
            INSERT INTO agent_definition_versions (
                id, tenant_id, workspace_id, agent_definition_id, version_number, status, manifest,
                compiled_workflow_version_id, capability_manifest, memory_scope_manifest, policy_manifest,
                placement_manifest, template_inputs_schema, metadata, created_by_user_id, created_at
            ) VALUES (
                $1, $2, $3, $4, 1, 'draft', $5::jsonb,
                NULL, $6::jsonb, '{}'::jsonb, $7::jsonb,
                $8::jsonb, '{}'::jsonb, $9::jsonb, $10, NOW()
            )
            """,
            version_id,
            tenant_id,
            workspace_id,
            definition_id,
            _to_json(normalized_manifest, default={}),
            _to_json(_normalize_capabilities(normalized_manifest), default={}),
            _to_json({"approval_default": "owner_approval"}, default={}),
            _to_json({"external_endpoint_refs": normalized_endpoints}, default={}),
            _to_json({"surface_kind": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT}, default={}),
            created_by_user_id,
        )
        await connection.execute(
            """
            INSERT INTO workspace_agent_installs (
                id, tenant_id, workspace_id, agent_definition_id, agent_definition_version_id, installed_by_user_id,
                install_scope, owner_user_id, thread_id, label, status, enabled, runtime_profile_id, compiled_workflow_version_id,
                root_folder_uri, tool_toggles, folder_grants, connector_bindings, memory_scope_overrides,
                policy_context_overrides, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                'workspace', $6, NULL, $7, 'active', TRUE, NULL, NULL,
                NULL, '{}'::jsonb, '[]'::jsonb, '{}'::jsonb, '{}'::jsonb,
                $8::jsonb, $9::jsonb, NOW(), NOW()
            )
            """,
            install_id,
            tenant_id,
            workspace_id,
            definition_id,
            version_id,
            created_by_user_id,
            display_name,
            _to_json({"approval_default": "owner_approval"}, default={}),
            _to_json(metadata, default={}),
        )
        await connection.execute(
            """
            INSERT INTO agent_manifests (
                id, tenant_id, workspace_id, agent_install_id, manifest_id, status, manifest, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, 'draft', $6::jsonb, $7::jsonb, NOW(), NOW()
            )
            """,
            f"amanifest_{install_id}",
            tenant_id,
            workspace_id,
            install_id,
            _read_string(normalized_manifest.get("id"), f"external:{install_id}"),
            _to_json(normalized_manifest, default={}),
            _to_json({"surface_kind": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT}, default={}),
        )
    return await get_connected_external_agent(tenant_id=tenant_id, workspace_id=workspace_id, external_agent_id=install_id)


async def update_connected_external_agent(
    *,
    tenant_id: str,
    workspace_id: str,
    external_agent_id: str,
    name: Optional[str] = None,
    provider_kind: Any = None,
    endpoints: Any = None,
    manifest: Any = None,
    secret_ref: Optional[str] = None,
) -> Dict[str, Any]:
    current = await get_connected_external_agent(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        external_agent_id=external_agent_id,
    )
    if _contains_raw_secret({"endpoints": endpoints, "manifest": manifest}):
        raise ValueError("Raw external-agent secrets are not allowed; provide secret_ref.")
    next_name = _read_string(name, current["name"])
    next_provider = _normalize_provider_kind(provider_kind or current.get("provider_kind"))
    next_manifest = _sanitize_manifest(manifest if manifest is not None else current.get("manifest"))
    local_connector_required = bool(_coerce_dict(next_manifest.get("local_connector")).get("required"))
    next_endpoints = dict(current.get("endpoint_refs") or {})
    if endpoints is not None:
        next_endpoints = _normalize_endpoints(
            endpoints,
            allow_agent_computer_proxy=local_connector_required,
        )
    next_endpoints = {
        **next_endpoints,
        **_manifest_endpoints(next_manifest, allow_agent_computer_proxy=local_connector_required),
    }
    if not local_connector_required:
        next_endpoints = await _validate_endpoint_map_dns(next_endpoints)
    projection = await _manifest_projection_for_workspace(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=next_manifest,
    )
    endpoint_changed = next_endpoints != (current.get("endpoint_refs") or {})
    next_connection_state = CONNECTION_STATE_UNVERIFIED if endpoint_changed else _read_string(current.get("connection_state"), CONNECTION_STATE_UNVERIFIED)
    metadata = _build_metadata(
        provider_kind=next_provider,
        endpoints=next_endpoints,
        manifest=next_manifest,
        secret_ref=secret_ref,
        connection_state=next_connection_state,
        existing=current,
        projection=projection,
    )
    now = _now_iso()
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        _require_control_plane_or_local_store(connection)
        if connection is None:
            updated = {
                **current,
                "name": next_name,
                "label": next_name,
                "provider_kind": next_provider,
                "endpoint_refs": next_endpoints,
                "manifest": next_manifest,
                "capability_manifest": projection["capability_manifest"],
                "manifest_projection": projection,
                "surface_sections": projection["surface_sections"],
                "object_types": projection["object_types"],
                "external_sub_agents": projection["external_sub_agents"],
                "protocols": projection["protocols"],
                "local_connector": projection["local_connector"],
                "connection_state": next_connection_state,
                "trust_state": next_connection_state,
                "updated_at": now,
            }
            _local_insert(updated)
            return updated
        await connection.execute(
            """
            UPDATE workspace_agent_installs
            SET label = $4, metadata = $5::jsonb, updated_at = NOW()
            WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
            """,
            tenant_id,
            workspace_id,
            external_agent_id,
            next_name,
            _to_json(metadata, default={}),
        )
        await connection.execute(
            """
            UPDATE agent_manifests
            SET manifest = $4::jsonb, metadata = $5::jsonb, updated_at = NOW()
            WHERE tenant_id = $1 AND workspace_id = $2 AND agent_install_id = $3
            """,
            tenant_id,
            workspace_id,
            external_agent_id,
            _to_json(next_manifest, default={}),
            _to_json({"surface_kind": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT}, default={}),
        )
    return await get_connected_external_agent(tenant_id=tenant_id, workspace_id=workspace_id, external_agent_id=external_agent_id)


async def refresh_connected_external_agent_manifest(
    *,
    tenant_id: str,
    workspace_id: str,
    external_agent_id: str,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    current = await get_connected_external_agent(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        external_agent_id=external_agent_id,
    )
    endpoints = dict(current.get("endpoint_refs") or {})
    local_connector_required = _agent_uses_local_connector(current)
    refresh_client = http_client
    close_refresh_client = False
    if refresh_client is None and not local_connector_required:
        refresh_client = httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        close_refresh_client = True
    try:
        try:
            manifest_payload = _sanitize_manifest(await _fetch_external_agent_json(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                agent=current,
                endpoint_url=endpoints.get("manifest_url"),
                field_name="manifest_url",
                label="Manifest",
                max_bytes=MAX_MANIFEST_RESPONSE_BYTES,
                http_client=refresh_client,
            ))
        except Exception as error:
            await _mark_connection_error(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                external_agent_id=external_agent_id,
                message=str(error),
            )
            raise ValueError(f"External agent manifest refresh failed: {error}") from error

        manifest_endpoints = _manifest_endpoints(manifest_payload, allow_agent_computer_proxy=local_connector_required)
        next_endpoints = {**endpoints, **manifest_endpoints}
        if not local_connector_required:
            next_endpoints = await _validate_endpoint_map_dns(next_endpoints)
        health_url = next_endpoints.get("health_url")
        if health_url:
            try:
                await _fetch_external_agent_json(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    agent={**current, "endpoint_refs": next_endpoints},
                    endpoint_url=health_url,
                    field_name="health_url",
                    label="Health",
                    max_bytes=MAX_MANIFEST_RESPONSE_BYTES,
                    http_client=refresh_client,
                )
            except Exception as error:
                await _mark_connection_error(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    external_agent_id=external_agent_id,
                    message="External agent health verification failed.",
                )
                raise ValueError("External agent health verification failed.") from error
    finally:
        if close_refresh_client and refresh_client is not None:
            await refresh_client.aclose()
    metadata = _build_metadata(
        provider_kind=_normalize_provider_kind(current.get("provider_kind")),
        endpoints=next_endpoints,
        manifest=manifest_payload,
        secret_ref=current.get("secret_ref"),
        connection_state=CONNECTION_STATE_VERIFIED,
        existing=current,
        projection=await _manifest_projection_for_workspace(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            manifest=manifest_payload,
        ),
    )
    metadata["last_manifest_refresh_at"] = _now_iso()
    metadata["handshake_status"] = "verified"
    metadata["handshake_verified_at"] = metadata["last_manifest_refresh_at"]
    metadata["last_error"] = None
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        _require_control_plane_or_local_store(connection)
        if connection is None:
            projection = _coerce_dict(metadata.get("manifest_projection"))
            updated = {
                **current,
                "manifest": manifest_payload,
                "endpoint_refs": next_endpoints,
                "capability_manifest": projection["capability_manifest"],
                "manifest_projection": projection,
                "surface_sections": projection["surface_sections"],
                "object_types": projection["object_types"],
                "external_sub_agents": projection["external_sub_agents"],
                "protocols": projection["protocols"],
                "local_connector": projection["local_connector"],
                "connection_state": CONNECTION_STATE_VERIFIED,
                "trust_state": CONNECTION_STATE_VERIFIED,
                "last_manifest_refresh_at": metadata["last_manifest_refresh_at"],
                "handshake_status": metadata["handshake_status"],
                "handshake_verified_at": metadata["handshake_verified_at"],
                "last_error": None,
                "updated_at": _now_iso(),
            }
            _local_insert(updated)
            return updated
        await connection.execute(
            """
            UPDATE workspace_agent_installs
            SET metadata = $4::jsonb, updated_at = NOW()
            WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
            """,
            tenant_id,
            workspace_id,
            external_agent_id,
            _to_json(metadata, default={}),
        )
        await connection.execute(
            """
            UPDATE agent_manifests
            SET manifest = $4::jsonb, metadata = $5::jsonb, status = 'active', updated_at = NOW()
            WHERE tenant_id = $1 AND workspace_id = $2 AND agent_install_id = $3
            """,
            tenant_id,
            workspace_id,
            external_agent_id,
            _to_json(manifest_payload, default={}),
            _to_json({"surface_kind": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT}, default={}),
        )
    return await get_connected_external_agent(tenant_id=tenant_id, workspace_id=workspace_id, external_agent_id=external_agent_id)


async def _mark_connection_error(*, tenant_id: str, workspace_id: str, external_agent_id: str, message: str) -> None:
    current = await get_connected_external_agent(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        external_agent_id=external_agent_id,
    )
    metadata = _build_metadata(
        provider_kind=_normalize_provider_kind(current.get("provider_kind")),
        endpoints=dict(current.get("endpoint_refs") or {}),
        manifest=_coerce_dict(current.get("manifest")),
        secret_ref=current.get("secret_ref"),
        connection_state=CONNECTION_STATE_ERROR,
        existing=current,
        last_error=message,
    )
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        _require_control_plane_or_local_store(connection)
        if connection is None:
            _local_insert({
                **current,
                "connection_state": CONNECTION_STATE_ERROR,
                "trust_state": CONNECTION_STATE_ERROR,
                "last_error": message,
                "updated_at": _now_iso(),
            })
            return
        await connection.execute(
            """
            UPDATE workspace_agent_installs
            SET metadata = $4::jsonb, updated_at = NOW()
            WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
            """,
            tenant_id,
            workspace_id,
            external_agent_id,
            _to_json(metadata, default={}),
        )


def _extract_chat_reply(payload: Any) -> str:
    data = _coerce_dict(payload)
    for key in ("reply", "message", "content", "text", "output"):
        value = _read_string(data.get(key))
        if value:
            return value
    choices = _coerce_list(data.get("choices"))
    if choices:
        first = _coerce_dict(choices[0])
        message = _coerce_dict(first.get("message"))
        value = _read_string(message.get("content") or first.get("text"))
        if value:
            return value
    return ""


def _find_surface_section(agent: Dict[str, Any], section_id: str) -> Dict[str, Any]:
    token = _normalize_token(section_id, field_name="section_id")
    sections = _coerce_list(agent.get("surface_sections"))
    if not sections:
        projection = _normalize_manifest_projection(_coerce_dict(agent.get("manifest")))
        sections = _coerce_list(projection.get("surface_sections"))
    for section in sections:
        payload = _coerce_dict(section)
        if _read_string(payload.get("id")) == token:
            return payload
    raise LookupError("External agent section not found.")


def _normalize_external_object(item: Any, *, object_type: str, external_agent_id: str) -> Dict[str, Any]:
    payload = _coerce_dict(item)
    external_id = _read_string(payload.get("external_id") or payload.get("id"))
    if not external_id:
        external_id = f"{object_type}_{uuid.uuid4().hex}"
    title = _read_string(payload.get("title") or payload.get("name") or payload.get("label"), external_id)
    return _redact_secret_fields({
        "id": f"{external_agent_id}:{object_type}:{external_id}",
        "external_id": external_id,
        "object_type": object_type,
        "ownership": "external",
        "source_agent_id": external_agent_id,
        "title": title[:200],
        "status": _read_string(payload.get("status")) or None,
        "summary": _read_string(payload.get("summary") or payload.get("description"))[:500] or None,
        "created_at": _read_string(payload.get("created_at")) or None,
        "updated_at": _read_string(payload.get("updated_at")) or None,
        "observed_at": _now_iso(),
        "raw_redacted": payload,
    })


def _validate_section_payload(payload: Dict[str, Any], *, section: Dict[str, Any], external_agent_id: str) -> Dict[str, Any]:
    display_kind = _read_string(section.get("display_kind"), "key_value")
    if display_kind not in ALLOWED_SECTION_DISPLAY_KINDS:
        raise ValueError("External section display kind is unsupported.")
    response_kind = _read_string(payload.get("display_kind"), display_kind)
    if response_kind != display_kind:
        raise ValueError("External section response display kind does not match the manifest.")
    item_type = _read_string(payload.get("object_type"))
    items = _coerce_list(payload.get("items"))
    if len(items) > 100:
        raise ValueError("External section response cannot include more than 100 items.")
    normalized_items: List[Any]
    if item_type:
        object_type = _normalize_token(item_type, field_name="object_type")
        if object_type not in ALLOWED_EXTERNAL_OBJECT_TYPES:
            raise ValueError(f"External object type is unsupported: {object_type}.")
        normalized_items = [
            _normalize_external_object(item, object_type=object_type, external_agent_id=external_agent_id)
            for item in items
        ]
    else:
        normalized_items = [_redact_secret_fields(item) for item in items]
    return {
        "section": section,
        "display_kind": display_kind,
        "ownership": "external",
        "items": normalized_items,
        "columns": _redact_secret_fields(_coerce_list(payload.get("columns"))[:24]),
        "summary": _redact_secret_fields(_coerce_dict(payload.get("summary"))),
        "raw_response": _redact_secret_fields({
            key: value
            for key, value in payload.items()
            if key not in {"items", "columns", "summary"}
        }),
    }


def _ensure_local_connector_proxy_ready(agent: Dict[str, Any]) -> None:
    connector = _coerce_dict(agent.get("local_connector"))
    if not bool(connector.get("required")):
        return
    binding_state = _read_string(connector.get("binding_state"), "missing_agent_computer")
    if binding_state != "bound":
        raise ValueError(f"External agent Agent Computer bridge is not ready: {binding_state}.")
    if not bool(connector.get("proxy_available")):
        raise ValueError("External agent Agent Computer bridge is not enabled yet.")


async def get_connected_external_agent_section_data(
    *,
    tenant_id: str,
    workspace_id: str,
    external_agent_id: str,
    section_id: str,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    agent = await get_connected_external_agent(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        external_agent_id=external_agent_id,
    )
    if _read_string(agent.get("connection_state")) != CONNECTION_STATE_VERIFIED:
        raise ValueError("External agent must be verified before section data is available.")
    if _read_string(agent.get("status")) == CONNECTION_STATE_REVOKED or not bool(agent.get("enabled", True)):
        raise ValueError("External agent is disconnected.")
    _ensure_local_connector_proxy_ready(agent)
    section = _find_surface_section(agent, section_id)
    capability_required = _read_string(section.get("capability_required"))
    capabilities = _coerce_dict(agent.get("capability_manifest"))
    if capability_required and not (bool(capabilities.get(capability_required)) or capability_required in _coerce_list(capabilities.get("capabilities"))):
        raise ValueError("External agent section capability is not enabled.")
    endpoint_ref = _read_string(section.get("data_endpoint_ref"))
    endpoints = _coerce_dict(agent.get("endpoint_refs"))
    try:
        payload = await _fetch_external_agent_json(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent=agent,
            endpoint_url=endpoints.get(endpoint_ref),
            field_name=endpoint_ref,
            label="External section",
            max_bytes=MAX_SECTION_RESPONSE_BYTES,
            http_client=http_client,
        )
    except Exception as error:
        raise ValueError(f"External agent section fetch failed: {error}") from error
    normalized = _validate_section_payload(payload, section=section, external_agent_id=external_agent_id)
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "external_agent_id": external_agent_id,
        **normalized,
    }


async def chat_with_connected_external_agent(
    *,
    tenant_id: str,
    workspace_id: str,
    external_agent_id: str,
    message: str,
    recent_messages: Optional[List[Dict[str, Any]]] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    normalized_message = _read_string(message)
    if not normalized_message:
        raise ValueError("message is required.")
    if len(normalized_message) > MAX_CHAT_MESSAGE_CHARS:
        raise ValueError(f"message cannot exceed {MAX_CHAT_MESSAGE_CHARS} characters.")
    validated_recent_messages = _validate_recent_messages(recent_messages)
    agent = await get_connected_external_agent(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        external_agent_id=external_agent_id,
    )
    if _read_string(agent.get("connection_state")) != CONNECTION_STATE_VERIFIED:
        raise ValueError("External agent must be verified before private chat is available.")
    if _read_string(agent.get("status")) == CONNECTION_STATE_REVOKED or not bool(agent.get("enabled", True)):
        raise ValueError("External agent is disconnected.")
    _ensure_local_connector_proxy_ready(agent)
    capabilities = _coerce_dict(agent.get("capability_manifest"))
    if not bool(capabilities.get("chat")):
        raise ValueError("External agent manifest does not expose private chat.")
    endpoints = _coerce_dict(agent.get("endpoint_refs"))
    body = {
        "message": normalized_message,
        "workspace_id": workspace_id,
        "agent_id": external_agent_id,
        "channel": "private_workspace",
        "recent_messages": validated_recent_messages,
        "metadata": {
            "source": "empyralis_connected_external_agent_chat",
        },
    }
    try:
        payload = _redact_secret_fields(await _fetch_external_agent_json(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent=agent,
            endpoint_url=endpoints.get("chat_url"),
            field_name="chat_url",
            label="Chat",
            max_bytes=MAX_CHAT_RESPONSE_BYTES,
            method="POST",
            json_body=body,
            http_client=http_client,
        ))
    except Exception as error:
        raise ValueError(f"External agent chat failed: {error}") from error

    reply = _extract_chat_reply(payload)
    if not reply:
        raise ValueError("External agent chat response did not include a reply.")
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "external_agent_id": external_agent_id,
        "reply": reply,
        "run_details": {
            "surface_kind": SURFACE_KIND_CONNECTED_EXTERNAL_AGENT,
            "provider_kind": agent.get("provider_kind"),
            "connection_state": agent.get("connection_state"),
            "endpoint": "chat_url",
        },
        "raw_response": payload,
    }


async def disconnect_connected_external_agent(
    *,
    tenant_id: str,
    workspace_id: str,
    external_agent_id: str,
) -> Dict[str, Any]:
    current = await get_connected_external_agent(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        external_agent_id=external_agent_id,
    )
    metadata = _build_metadata(
        provider_kind=_normalize_provider_kind(current.get("provider_kind")),
        endpoints=dict(current.get("endpoint_refs") or {}),
        manifest=_coerce_dict(current.get("manifest")),
        secret_ref=current.get("secret_ref"),
        connection_state=CONNECTION_STATE_REVOKED,
        existing=current,
    )
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        _require_control_plane_or_local_store(connection)
        if connection is None:
            updated = {
                **current,
                "status": "revoked",
                "enabled": False,
                "connection_state": CONNECTION_STATE_REVOKED,
                "trust_state": CONNECTION_STATE_REVOKED,
                "updated_at": _now_iso(),
            }
            _local_insert(updated)
            return updated
        await connection.execute(
            """
            UPDATE workspace_agent_installs
            SET status = 'revoked', enabled = FALSE, metadata = $4::jsonb, updated_at = NOW()
            WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
            """,
            tenant_id,
            workspace_id,
            external_agent_id,
            _to_json(metadata, default={}),
        )
    return await get_connected_external_agent(tenant_id=tenant_id, workspace_id=workspace_id, external_agent_id=external_agent_id)


async def build_agent_surfaces_payload(
    *,
    tenant_id: str,
    workspace_id: str,
    native_agents: Optional[List[Dict[str, Any]]] = None,
    agent_computers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    external_payload = await list_connected_external_agents(tenant_id=tenant_id, workspace_id=workspace_id)
    native_items = [
        {
            "id": _read_string(item.get("id")),
            "surface_kind": SURFACE_KIND_NATIVE_STUDIO_AGENT,
            "name": _read_string(item.get("name"), "Business Agent"),
            "status": _read_string(item.get("deployment_state"), "draft"),
            "record": item,
        }
        for item in list(native_agents or [])
        if isinstance(item, dict)
    ]
    computer_items = [
        {
            "id": _read_string(item.get("attachment_id") or item.get("id")),
            "surface_kind": SURFACE_KIND_AGENT_COMPUTER,
            "name": _read_string(item.get("label") or item.get("runtime_profile_label"), "Agent Computer"),
            "status": _read_string(item.get("status") or item.get("self_hosted_node_status"), "unknown"),
            "record": item,
        }
        for item in list(agent_computers or [])
        if isinstance(item, dict)
    ]
    external_items = list(external_payload.get("items") or [])
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "native_studio_agents": native_items,
        "connected_external_agents": external_items,
        "agent_computers": computer_items,
        "items": native_items + external_items + computer_items,
    }


def _reset_for_tests() -> None:
    LOCAL_STORE.clear()


def http_error_from_exception(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import studio_app_boundary_service


_CONTRACT_MAP_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "application_runtime_contract_map.json"
)
_FORBIDDEN_IMPLICIT_METADATA_KEYS = {
    "captain_context",
    "captain_identity",
    "captain_profile",
    "sage_context",
    "sage_memory",
    "specialist_context",
    "specialist_memory",
    "specialist_mode",
    "specialist_mode_contract",
    "private_context",
    "personal_context",
    "unified_memory",
    "raw_context_imports",
    "implicit_memory_imports",
}
_FORBIDDEN_BRIDGE_FIELDS = {
    "memory_scope",
    "memory_query",
    "memory_bucket",
    "raw_memory",
    "private_context",
    "captain_memory",
    "specialist_memory",
    "screenshot",
    "computer",
    "shell",
    "mcp",
    "skill",
    "local_companion",
    "runtime_session_id",
    "runtime_target",
    "tool_call",
    "tool_calls",
}
_FORBIDDEN_BRIDGE_ACTION_VALUES = {
    "screenshot",
    "screenshot.capture",
    "computer",
    "computer.click",
    "computer_control.click",
    "shell",
    "shell.run",
    "terminal",
    "terminal.exec",
    "mcp",
    "mcp.invoke",
    "skill",
    "skill.execute",
    "codex_cli",
    "claude_code_cli",
    "local_companion",
}
_BRIDGE_ACTION_VALUE_KEYS = {
    "action",
    "action_id",
    "action_name",
    "capability",
    "capability_id",
    "connector",
    "connector_id",
    "mcp_server_id",
    "mcp_tool_id",
    "provider",
    "runtime",
    "runtime_target",
    "tool",
    "tool_id",
    "tool_name",
}
_ENVELOPE_ALIASES = {
    "app_context": "app_owned_history",
    "app_history": "app_owned_history",
    "scoped_documents": "scoped_documents_and_data",
    "scoped_data": "scoped_documents_and_data",
    "documents": "scoped_documents_and_data",
    "workflow_state": "app_workflow_state",
    "explicit_imports": "explicit_imports_from_sage",
    "specialist_summaries": "explicit_summaries_from_specialists",
    "shared_artifacts": "explicit_shared_artifacts",
}


def _forbidden_bridge_key(raw_key: Any) -> Optional[str]:
    key = str(raw_key or "").strip().lower()
    if key in _FORBIDDEN_IMPLICIT_METADATA_KEYS or key in _FORBIDDEN_BRIDGE_FIELDS:
        return key
    boundary_key = studio_app_boundary_service.forbidden_owner_resource_key(raw_key)
    if boundary_key:
        return boundary_key
    return None


def _find_forbidden_bridge_key_path(payload: Any, *, root_path: str = "") -> Optional[tuple[str, str]]:
    if isinstance(payload, dict):
        for raw_key, raw_value in payload.items():
            key_text = str(raw_key or "").strip()
            path = f"{root_path}.{key_text}" if root_path else key_text
            forbidden = _forbidden_bridge_key(raw_key)
            if forbidden:
                return forbidden, path
            normalized_key = key_text.lower()
            if normalized_key in _BRIDGE_ACTION_VALUE_KEYS:
                normalized_value = str(raw_value or "").strip().lower()
                if normalized_value in _FORBIDDEN_BRIDGE_ACTION_VALUES:
                    return normalized_value, path
            nested = _find_forbidden_bridge_key_path(raw_value, root_path=path)
            if nested:
                return nested
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            path = f"{root_path}[{index}]" if root_path else f"[{index}]"
            nested = _find_forbidden_bridge_key_path(item, root_path=path)
            if nested:
                return nested
    return None


def _assert_no_forbidden_bridge_keys(payload: Any, *, root_label: str) -> None:
    violation = _find_forbidden_bridge_key_path(payload, root_path=root_label)
    if not violation:
        return
    forbidden_key, path = violation
    raise HTTPException(
        status_code=400,
        detail=f"{root_label} contains forbidden field '{forbidden_key}' at '{path}'.",
    )


def _read_contract_map() -> Dict[str, Any]:
    try:
        raw = json.loads(_CONTRACT_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return raw


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    return [value]


def _bridge_contracts_map() -> Dict[str, List[str]]:
    raw = _coerce_dict(_read_contract_map().get("bridge_contracts"))
    normalized: Dict[str, List[str]] = {}
    for key, value in raw.items():
        items = [str(item or "").strip().lower() for item in _coerce_list(value) if str(item or "").strip()]
        if items:
            normalized[str(key or "").strip().lower()] = items
    return normalized


def _context_envelope_map() -> Dict[str, Any]:
    raw = _coerce_dict(_read_contract_map().get("context_envelope"))
    defaults = [
        str(item or "").strip().lower()
        for item in _coerce_list(raw.get("default_classes"))
        if str(item or "").strip()
    ]
    optional = [
        str(item or "").strip().lower()
        for item in _coerce_list(raw.get("optional_classes"))
        if str(item or "").strip()
    ]
    return {
        "default_classes": defaults,
        "optional_classes": optional,
        "inherits_sage_memory_by_default": bool(raw.get("inherits_sage_memory_by_default")),
        "inherits_specialist_memory_by_default": bool(raw.get("inherits_specialist_memory_by_default")),
    }


def _app_denials() -> List[str]:
    raw = _coerce_dict(_read_contract_map().get("direct_capabilities"))
    return [
        str(item or "").strip().lower()
        for item in _coerce_list(raw.get("denied_by_default"))
        if str(item or "").strip()
    ]


def _resolve_registry_app_item(app_id: str) -> Optional[Dict[str, Any]]:
    from server_modules import app_registry_api

    try:
        with app_registry_api.APP_REGISTRY_LOCK:
            data = app_registry_api._load_app_registry()
            app_item = app_registry_api._find_app(data, app_id)
    except Exception:
        try:
            import server as _server

            for key, value in _server.__dict__.items():
                if key not in app_registry_api.__dict__:
                    setattr(app_registry_api, key, value)
            with app_registry_api.APP_REGISTRY_LOCK:
                data = app_registry_api._load_app_registry()
                app_item = app_registry_api._find_app(data, app_id)
        except Exception:
            data = app_registry_api._default_app_registry()
            app_item = app_registry_api._find_app(data, app_id)
    return dict(app_item) if isinstance(app_item, dict) else None


def resolve_app_runtime_contract(app_id: str, *, installed_only: bool = False) -> Dict[str, Any]:
    token = str(app_id or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="app_id required.")
    app_item = _resolve_registry_app_item(token)
    if not app_item:
        if installed_only:
            raise HTTPException(status_code=404, detail="App not found.")
        return {
            "app_id": token,
            "status": "external",
            "permissions": [],
            "bridge_contracts": _bridge_contracts_map(),
            "context_envelope": _context_envelope_map(),
            "denied_by_default": _app_denials(),
        }
    status = str(app_item.get("status") or "").strip().lower() or "available"
    if installed_only and status != "installed":
        raise HTTPException(status_code=409, detail="App must be installed before requesting bridge access.")
    permissions = app_item.get("permissions") if isinstance(app_item.get("permissions"), list) else []
    bridge_contracts = _bridge_contracts_map()
    context_envelope = _context_envelope_map()
    return {
        "app_id": token,
        "status": status,
        "permissions": [str(item or "").strip().lower() for item in permissions if str(item or "").strip()],
        "bridge_contracts": bridge_contracts,
        "context_envelope": context_envelope,
        "denied_by_default": _app_denials(),
    }


def normalize_app_context_envelope(
    envelope: Optional[Dict[str, Any]],
    *,
    contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if envelope is None:
        return {"classes": [], "payload": {}}
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=400, detail="context_envelope must be an object.")
    envelope_map = _context_envelope_map() if not isinstance(contract, dict) else _coerce_dict(contract.get("context_envelope"))
    allowed_classes = {
        str(item or "").strip().lower()
        for item in _coerce_list(envelope_map.get("default_classes"))
        + _coerce_list(envelope_map.get("optional_classes"))
        if str(item or "").strip()
    }
    normalized_payload: Dict[str, Any] = {}
    for raw_key, raw_value in envelope.items():
        key = _ENVELOPE_ALIASES.get(str(raw_key or "").strip().lower(), str(raw_key or "").strip().lower())
        if key in _FORBIDDEN_IMPLICIT_METADATA_KEYS or key in _FORBIDDEN_BRIDGE_FIELDS:
            raise HTTPException(status_code=400, detail=f"context_envelope.{raw_key} is not allowed for applications.")
        if key not in allowed_classes:
            raise HTTPException(status_code=400, detail=f"context_envelope.{raw_key} is not supported.")
        if key in {"app_owned_history", "scoped_documents_and_data", "explicit_imports_from_sage", "explicit_summaries_from_specialists", "explicit_shared_artifacts"}:
            normalized_payload[key] = _coerce_list(raw_value)
        elif key in {"user_selected_inputs", "app_workflow_state"}:
            normalized_payload[key] = raw_value if isinstance(raw_value, (dict, list, str, int, float, bool)) or raw_value is None else str(raw_value)
        else:
            normalized_payload[key] = raw_value
        _assert_no_forbidden_bridge_keys(normalized_payload[key], root_label=f"context_envelope.{key}")
    classes = sorted(key for key, value in normalized_payload.items() if value not in (None, "", [], {}))
    return {"classes": classes, "payload": normalized_payload}


def normalize_bridge_contract(
    *,
    app_id: str,
    bridge_kind: str,
    bridge_type: str,
    target: Optional[Dict[str, Any]] = None,
    context_envelope: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    installed_only: bool = True,
) -> Dict[str, Any]:
    contract = resolve_app_runtime_contract(app_id, installed_only=installed_only)
    kind = str(bridge_kind or "").strip().lower()
    bridge_token = str(bridge_type or "").strip().lower()
    allowed_types = _coerce_list(_coerce_dict(contract.get("bridge_contracts")).get(kind))
    if not kind or kind not in _coerce_dict(contract.get("bridge_contracts")):
        raise HTTPException(status_code=400, detail="Unsupported bridge_kind.")
    if not bridge_token or bridge_token not in allowed_types:
        raise HTTPException(status_code=400, detail="Unsupported bridge_type for this bridge_kind.")
    target_payload = _coerce_dict(target)
    _assert_no_forbidden_bridge_keys(target_payload, root_label="bridge.target")
    if kind == "app_to_specialist" and not (
        str(target_payload.get("target_install_id") or "").strip()
        or str(target_payload.get("target_capability") or "").strip()
    ):
        raise HTTPException(status_code=400, detail="App -> specialist bridges require target_install_id or target_capability.")
    if kind == "sage_to_app":
        target_app_id = str(target_payload.get("target_app_id") or app_id).strip()
        if not target_app_id:
            raise HTTPException(status_code=400, detail="Sage -> app bridges require target_app_id.")
        target_payload["target_app_id"] = target_app_id
    if kind == "app_to_connector_runtime" and not (
        str(target_payload.get("connector_id") or "").strip()
        or str(target_payload.get("workflow_id") or "").strip()
        or str(target_payload.get("route_key") or "").strip()
    ):
        raise HTTPException(status_code=400, detail="App -> connector/runtime bridges require connector_id, workflow_id, or route_key.")
    normalized_metadata = _coerce_dict(metadata)
    _assert_no_forbidden_bridge_keys(normalized_metadata, root_label="bridge.metadata")
    normalized_envelope = normalize_app_context_envelope(context_envelope, contract=contract)
    return {
        "app_id": contract["app_id"],
        "bridge_kind": kind,
        "bridge_type": bridge_token,
        "target": target_payload,
        "context_envelope": normalized_envelope,
        "metadata": normalized_metadata,
        "denied_by_default": list(contract.get("denied_by_default") or []),
        "allowed_bridge_types": allowed_types,
    }


def enforce_app_metadata_contract(*, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = _coerce_dict(metadata)
    app_id = str(normalized.get("app_id") or "").strip()
    if not app_id:
        return normalized
    for key in list(normalized):
        if key in _FORBIDDEN_IMPLICIT_METADATA_KEYS:
            raise HTTPException(status_code=400, detail=f"metadata.{key} is not allowed for applications without an explicit bridge.")
    _assert_no_forbidden_bridge_keys(normalized, root_label="metadata")
    contract = resolve_app_runtime_contract(app_id, installed_only=False)
    envelope = normalized.get("app_context_envelope")
    if envelope is None and isinstance(normalized.get("app_context"), dict):
        envelope = normalized.get("app_context")
    normalized_envelope = normalize_app_context_envelope(envelope, contract=contract)
    normalized["app_context_envelope"] = normalized_envelope
    normalized.pop("app_context", None)
    bridge = normalized.get("app_bridge")
    if bridge is not None:
        if not isinstance(bridge, dict):
            raise HTTPException(status_code=400, detail="metadata.app_bridge must be an object.")
        _assert_no_forbidden_bridge_keys(bridge, root_label="metadata.app_bridge")
        normalized["app_bridge"] = normalize_bridge_contract(
            app_id=app_id,
            bridge_kind=str(bridge.get("bridge_kind") or bridge.get("kind") or "").strip(),
            bridge_type=str(bridge.get("bridge_type") or bridge.get("type") or "").strip(),
            target=_coerce_dict(bridge.get("target")),
            context_envelope=_coerce_dict(
                bridge.get("context_envelope")
                if isinstance(bridge.get("context_envelope"), dict)
                else normalized_envelope.get("payload")
            ),
            metadata=_coerce_dict(bridge.get("metadata")),
            installed_only=False,
        )
    normalized["app_runtime_contract"] = {
        "app_id": app_id,
        "status": contract.get("status"),
        "denied_by_default": list(contract.get("denied_by_default") or []),
        "context_classes": list(normalized_envelope.get("classes") or []),
        "has_explicit_bridge": isinstance(normalized.get("app_bridge"), dict),
    }
    return normalized


async def record_app_bridge_audit(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_type: str,
    actor_id: str,
    app_id: str,
    bridge_kind: str,
    bridge_type: str,
    target: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    from server_modules import activity_ledger_service

    target_payload = _coerce_dict(target)
    metadata_payload = _coerce_dict(metadata)
    summary = f"Bridge {bridge_kind}:{bridge_type}"
    if str(target_payload.get("target_install_id") or "").strip():
        summary += f" -> {str(target_payload.get('target_install_id')).strip()}"
    elif str(target_payload.get("target_app_id") or "").strip():
        summary += f" -> {str(target_payload.get('target_app_id')).strip()}"
    return await activity_ledger_service.append_activity_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        app_id=app_id,
        event_class="application_activity",
        detail_level="timeline_detail",
        action=f"{bridge_kind}:{bridge_type}",
        title="Application bridge request",
        summary=summary,
        status="validated",
        payload={
            "bridge_kind": bridge_kind,
            "bridge_type": bridge_type,
            "target": target_payload,
        },
        metadata=metadata_payload,
    )

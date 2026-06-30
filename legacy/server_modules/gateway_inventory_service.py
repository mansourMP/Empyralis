from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules import secret_redaction_service


SERVICE_STATUSES = {"ready", "degraded", "offline", "missing", "unknown", "blocked"}
MAX_SERVICE_ITEMS = 50
MAX_TEXT_LENGTH = 240
PERMISSION_STATES = {"granted", "promptable", "denied", "restricted", "unknown", "not_applicable"}
PERMISSION_IDS = {"screen_recording", "accessibility", "clipboard", "automation", "browser", "not_applicable"}
READY_CAPABILITY_STATES = {"ready", "healthy", "online", "available", "ok", "true"}
EXECUTION_CAPABILITY_ALIASES = {
    "filesystem.read": {"filesystem.read_write", "file.read"},
    "filesystem.write": {"filesystem.read_write", "file.write"},
    "file.read": {"filesystem.read", "filesystem.read_write"},
    "file.write": {"filesystem.write", "filesystem.read_write"},
    "screen.read": {"screenshot.capture"},
    "screenshot.capture": {"screen.read"},
}


def _text(value: Any, fallback: str = "") -> str:
    token = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    token = " ".join(token.split())
    if len(token) > MAX_TEXT_LENGTH:
        token = f"{token[: MAX_TEXT_LENGTH - 3]}..."
    return token or fallback


def _public_text(value: Any, fallback: str = "") -> str:
    return secret_redaction_service.redact_text(_text(value, fallback))


def _safe_metadata(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe: Dict[str, Any] = {}
    for key, item in list(value.items())[:20]:
        clean_key = _text(key, "")[:80]
        if not clean_key:
            continue
        item = secret_redaction_service.sanitize_value(item, key=clean_key)
        if item is None or isinstance(item, (bool, int, float)):
            safe[clean_key] = item
        elif isinstance(item, str):
            safe[clean_key] = _public_text(item)
        elif isinstance(item, list):
            safe[clean_key] = [
                _public_text(entry)
                for entry in item[:20]
                if entry is None or isinstance(entry, (str, int, float, bool))
            ]
    return safe


def sanitize_service_inventory(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: List[Dict[str, Any]] = []
    for raw in value[:MAX_SERVICE_ITEMS]:
        if not isinstance(raw, dict):
            continue
        service_id = _text(raw.get("id"), "")
        if not service_id:
            continue
        status = _text(raw.get("status"), "unknown").lower()
        if status not in SERVICE_STATUSES:
            status = "unknown"
        item = {
            "id": service_id[:80],
            "label": _public_text(raw.get("label"), service_id)[:120],
            "kind": _public_text(raw.get("kind"), "service")[:80],
            "status": status,
            "detected": bool(raw.get("detected")),
            "passive": True,
            "execution_enabled": False,
            "check": _public_text(raw.get("check"), "passive_probe"),
            "summary": _public_text(raw.get("summary"), "Passive service inventory reported."),
            "last_checked_at": _public_text(raw.get("last_checked_at"), ""),
        }
        metadata = _safe_metadata(raw.get("metadata"))
        if metadata:
            item["metadata"] = metadata
        items.append(item)
    return items


def sanitize_native_runtime(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    system_service_mode = bool(value.get("system_service_mode"))
    desktop_session = "system_service" if system_service_mode else "user_session"
    return {
        "os": _public_text(value.get("os"), "")[:80],
        "arch": _public_text(value.get("arch"), "")[:80],
        "release": _public_text(value.get("release"), "")[:120],
        "hostname": _public_text(value.get("hostname"), "")[:120],
        "desktop_session": desktop_session,
        "system_service_mode": system_service_mode,
    }


def _capability_list(value: Any, *, limit: int = 100) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for item in value[:limit]:
        token = _public_text(item, "")[:120]
        if token:
            items.append(token)
    return items


def sanitize_capability_readiness(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    payload: Dict[str, Any] = {}
    for key in ("requested", "ready", "blocked", "passive_services"):
        items = _capability_list(value.get(key))
        if items:
            payload[key] = items
    service_statuses: Dict[str, str] = {}
    raw_statuses = value.get("service_statuses")
    if isinstance(raw_statuses, dict):
        for key, item in list(raw_statuses.items())[:MAX_SERVICE_ITEMS]:
            service_id = _public_text(key, "")[:80]
            status = _text(item, "unknown").lower()
            if service_id:
                service_statuses[service_id] = status if status in SERVICE_STATUSES else "unknown"
    if service_statuses:
        payload["service_statuses"] = service_statuses
    raw_permissions = value.get("permission_states")
    permission_states: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_permissions, dict):
        for key, raw in list(raw_permissions.items())[:100]:
            capability_id = _public_text(key, "")[:120]
            if not capability_id or not isinstance(raw, dict):
                continue
            state = _text(raw.get("state"), "unknown").lower()
            permission = _text(raw.get("permission"), "not_applicable").lower()
            permission_states[capability_id] = {
                "capability_id": _public_text(raw.get("capability_id"), capability_id)[:120],
                "permission": permission if permission in PERMISSION_IDS else "not_applicable",
                "state": state if state in PERMISSION_STATES else "unknown",
                "prompt_available": bool(raw.get("prompt_available")),
                "summary": _public_text(raw.get("summary"), "Permission state reported."),
            }
    if permission_states:
        payload["permission_states"] = permission_states
    for key, item in value.items():
        key_token = _text(key, "").lower()
        if not key_token or key_token in payload or key_token in {"permission_states", "service_statuses"}:
            continue
        if isinstance(item, bool):
            payload[key_token[:120]] = item
        elif isinstance(item, str):
            payload[key_token[:120]] = _public_text(item)
    return payload


def execution_capability_aliases(capability_id: Any) -> set[str]:
    capability = _text(capability_id, "").lower()
    if not capability:
        return set()
    aliases = {capability}
    aliases.update(EXECUTION_CAPABILITY_ALIASES.get(capability, set()))
    for key, values in EXECUTION_CAPABILITY_ALIASES.items():
        if capability in values:
            aliases.add(key)
            aliases.update(values)
    return aliases


def registration_has_execution_capability(registration: Dict[str, Any], capability_id: Any) -> bool:
    aliases = execution_capability_aliases(capability_id)
    if not aliases:
        return False
    raw_capabilities = registration.get("capabilities")
    if not isinstance(raw_capabilities, list):
        return False
    registered = {_text(item, "").lower() for item in raw_capabilities if _text(item, "")}
    return bool(registered.intersection(aliases))


def _readiness_ready_ids(readiness: Any) -> set[str]:
    ready: set[str] = set()
    if isinstance(readiness, list):
        return {_text(item, "").lower() for item in readiness if _text(item, "")}
    if not isinstance(readiness, dict):
        return ready
    ready_list = readiness.get("ready")
    if isinstance(ready_list, list):
        ready.update(_text(item, "").lower() for item in ready_list if _text(item, ""))
    for key, value in readiness.items():
        key_token = _text(key, "").lower()
        if not key_token or key_token in {"requested", "ready", "passive_services", "service_statuses"}:
            continue
        if isinstance(value, bool):
            if value:
                ready.add(key_token)
            continue
        value_token = _text(value, "").lower()
        if value_token in READY_CAPABILITY_STATES:
            ready.add(key_token)
    return ready


def capability_ready_from_metadata(metadata: Dict[str, Any], capability_id: Any) -> bool:
    aliases = execution_capability_aliases(capability_id)
    if not aliases or not isinstance(metadata, dict):
        return False
    readiness = metadata.get("capability_readiness")
    ready_ids = _readiness_ready_ids(readiness)
    return bool(ready_ids.intersection(aliases))


def capability_ready_from_any_metadata(capability_id: Any, *sources: Optional[Dict[str, Any]]) -> bool:
    return any(
        capability_ready_from_metadata(source, capability_id)
        for source in sources
        if isinstance(source, dict)
    )


def inventory_from_metadata(*sources: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for source in sources:
        if not isinstance(source, dict):
            continue
        items = sanitize_service_inventory(source.get("service_inventory"))
        if items:
            return items
    return []


def native_runtime_from_metadata(*sources: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    for source in sources:
        if not isinstance(source, dict):
            continue
        native_runtime = sanitize_native_runtime(source.get("native_runtime"))
        if any(native_runtime.values()):
            return native_runtime
    return {}


def service_inventory_payload(
    *,
    session_metadata: Optional[Dict[str, Any]],
    registration_metadata: Optional[Dict[str, Any]],
    heartbeat_fresh: bool,
) -> Dict[str, Any]:
    items = inventory_from_metadata(session_metadata, registration_metadata)
    native_runtime = native_runtime_from_metadata(session_metadata, registration_metadata)
    counts: Dict[str, int] = {status: 0 for status in SERVICE_STATUSES}
    for item in items:
        status = _text(item.get("status"), "unknown").lower()
        counts[status if status in SERVICE_STATUSES else "unknown"] += 1
    if items:
        status = "pass"
        summary = f"{len(items)} passive service inventory item(s) reported."
    elif heartbeat_fresh:
        status = "warn"
        summary = "Agent Computer heartbeat is fresh, but passive service inventory is not reported yet."
    else:
        status = "warn"
        summary = "Passive service inventory is unavailable until Agent Computer sends a fresh heartbeat."
    return {
        "status": status,
        "summary": summary,
        "count": len(items),
        "counts": {key: value for key, value in counts.items() if value},
        "items": items,
        "native_runtime": native_runtime,
    }

from __future__ import annotations

import re
from typing import Any, Optional


FORBIDDEN_OWNER_RESOURCE_KEYS = {
    "agent_computer_policy",
    "agent_computer_profile",
    "captain_context",
    "captain_identity",
    "captain_memory",
    "captain_profile",
    "connected_computer",
    "connected_computer_id",
    "gateway_id",
    "host_filesystem",
    "implicit_memory_imports",
    "local_gateway_attachment",
    "owner_files",
    "owner_private_memory",
    "personal_channel",
    "personal_channel_key",
    "personal_channels",
    "personal_context",
    "private_context",
    "raw_context_imports",
    "raw_memory",
    "runtime_attachment",
    "runtime_attachment_id",
    "runtime_node_id",
    "runtime_profile_id",
    "runtime_session_binding",
    "runtime_session_id",
    "sage_context",
    "sage_memory",
    "sage_private_memory",
    "sage_profile",
    "specialist_context",
    "specialist_memory",
    "specialist_mode",
    "specialist_mode_contract",
    "unified_memory",
}

FORBIDDEN_OWNER_RESOURCE_PERMISSION_MARKERS = {
    "agent_computer_policy",
    "agent_computer_profile",
    "connected_computer",
    "gateway_direct",
    "gateway_id",
    "host_filesystem",
    "local_gateway_attachment",
    "owner_files",
    "owner_private_memory",
    "personal_channel",
    "personal_channels",
    "read_sage_memory",
    "runtime_attachment_id",
    "runtime_node_id",
    "sage_memory",
    "sage_memory_read",
    "sage_private_memory",
}


class StudioAppBoundaryError(ValueError):
    pass


def _normalized_key(value: Any) -> str:
    token = str(value or "").strip()
    token = re.sub(r"(?<!^)(?=[A-Z])", "_", token)
    token = re.sub(r"[^a-zA-Z0-9]+", "_", token).strip("_").lower()
    return token


def forbidden_owner_resource_key(raw_key: Any) -> Optional[str]:
    key = _normalized_key(raw_key)
    if key in FORBIDDEN_OWNER_RESOURCE_KEYS:
        return key
    return None


def find_forbidden_owner_resource_key_path(
    payload: Any,
    *,
    root_path: str = "",
) -> Optional[tuple[str, str]]:
    if isinstance(payload, dict):
        for raw_key, raw_value in payload.items():
            key_text = str(raw_key or "").strip()
            path = f"{root_path}.{key_text}" if root_path else key_text
            forbidden = forbidden_owner_resource_key(raw_key)
            if forbidden:
                return forbidden, path
            nested = find_forbidden_owner_resource_key_path(raw_value, root_path=path)
            if nested:
                return nested
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            path = f"{root_path}[{index}]" if root_path else f"[{index}]"
            nested = find_forbidden_owner_resource_key_path(item, root_path=path)
            if nested:
                return nested
    return None


def assert_no_forbidden_owner_resource_keys(payload: Any, *, root_label: str) -> None:
    violation = find_forbidden_owner_resource_key_path(payload, root_path=root_label)
    if not violation:
        return
    forbidden_key, path = violation
    raise StudioAppBoundaryError(
        f"{root_label} contains forbidden owner-resource field '{forbidden_key}' at '{path}'."
    )


def permission_requests_owner_resource(permission: Any) -> bool:
    token = _normalized_key(permission)
    if not token:
        return False
    if token in FORBIDDEN_OWNER_RESOURCE_PERMISSION_MARKERS:
        return True
    return any(
        marker and (token.startswith(f"{marker}_") or token.endswith(f"_{marker}"))
        for marker in FORBIDDEN_OWNER_RESOURCE_PERMISSION_MARKERS
    )

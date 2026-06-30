from __future__ import annotations

from typing import Any, Dict, List

try:
    from server_modules.runtime_policy import (
        EXECUTION_TARGET_LOCAL_COMPANION,
        normalize_execution_target,
        normalize_trust_mode,
        evaluate_tool_policy_decision,
        tool_policy_snapshot,
    )
except ImportError:  # pragma: no cover - used by local worker scripts
    from runtime_policy import (  # type: ignore[no-redef]
        EXECUTION_TARGET_LOCAL_COMPANION,
        normalize_execution_target,
        normalize_trust_mode,
        evaluate_tool_policy_decision,
        tool_policy_snapshot,
    )


FILE_MOUNTS = [
    "artifacts",
    "project",
    "shared",
    "knowledge",
    "local_root",
    "connector_files",
]
FILE_GRANTS = {"none", "read", "read_write", "create_only", "append_only"}
TRUST_PRESETS = {"standard_local", "trusted_workflow", "elevated_local"}


def _dedupe_strings(value: Any) -> List[str]:
    items = value if isinstance(value, list) else []
    out: List[str] = []
    seen = set()
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def normalize_trust_preset(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    if token in TRUST_PRESETS:
        return token
    return "standard_local"


def normalize_remembered_grants(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "folders": _dedupe_strings(source.get("folders")),
        "browser_session": bool(source.get("browser_session")),
        "shell_capabilities": _dedupe_strings(source.get("shell_capabilities")),
    }


def default_file_mount_grants() -> List[Dict[str, str]]:
    return [
        {"mount": "artifacts", "grant": "read_write"},
        {"mount": "project", "grant": "read"},
        {"mount": "shared", "grant": "read"},
        {"mount": "knowledge", "grant": "read"},
        {"mount": "local_root", "grant": "none"},
        {"mount": "connector_files", "grant": "read"},
    ]


def normalize_file_mount_grants(raw: Any, execution_target: Any) -> List[Dict[str, str]]:
    resolved = {item["mount"]: item["grant"] for item in default_file_mount_grants()}
    items = raw if isinstance(raw, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        mount = str(item.get("mount") or "").strip().lower()
        grant = str(item.get("grant") or "").strip().lower()
        if mount not in FILE_MOUNTS or grant not in FILE_GRANTS:
            continue
        resolved[mount] = grant
    return [{"mount": mount, "grant": resolved.get(mount, "none")} for mount in FILE_MOUNTS]


def validate_file_mount_grants(raw: Any, execution_target: Any) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    target = normalize_execution_target(execution_target)
    grants = normalize_file_mount_grants(raw, target)
    local_root = next((item for item in grants if item.get("mount") == "local_root"), {"grant": "none"})
    if str(local_root.get("grant") or "none").strip().lower() != "none" and target != EXECUTION_TARGET_LOCAL_COMPANION:
        issues.append(
            {
                "code": "local_root_requires_local_companion",
                "message": "local_root mount grants require the local_companion execution target.",
            }
        )
    return issues


def map_builder_permissions_to_runtime_metadata(
    permissions: Dict[str, Any] | None,
    *,
    execution_target: Any = "auto",
) -> Dict[str, Any]:
    source = permissions if isinstance(permissions, dict) else {}
    target = normalize_execution_target(execution_target)
    trust_preset = normalize_trust_preset(source.get("trust_preset"))
    trust_mode = normalize_trust_mode(source.get("action_policy") or "guarded")
    file_mount_grants = normalize_file_mount_grants(source.get("file_mount_grants"), target)
    metadata = {
        "trust_preset": trust_preset,
        "trust_mode": trust_mode,
        "execution_target": target,
        "connector_permissions": _dedupe_strings(source.get("connector_permissions") or source.get("connectors")),
        "browser_permissions": source.get("browser_permissions") if isinstance(source.get("browser_permissions"), dict) else {"allow": False},
        "file_mount_grants": file_mount_grants,
        "remembered_grants": normalize_remembered_grants(source.get("remembered_grants")),
        "approval_rules": {
            "approve_on_connector_actions": True,
        },
    }
    metadata["tool_policy"] = tool_policy_snapshot(metadata)
    return metadata


def evaluate_builder_tool_permissions(
    tool_ids: List[str],
    permissions: Dict[str, Any] | None,
    *,
    execution_target: Any = "auto",
) -> Dict[str, Any]:
    metadata = map_builder_permissions_to_runtime_metadata(permissions, execution_target=execution_target)
    items = [
        evaluate_tool_policy_decision(
            tool_id=tool_id,
            trust_mode=str(metadata.get("trust_mode") or "guarded"),
            target=str(metadata.get("execution_target") or "auto"),
            metadata=metadata,
        )
        for tool_id in _dedupe_strings(tool_ids)
    ]
    return {
        "metadata": metadata,
        "items": items,
    }

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import unquote

try:
    from server_modules.builder_runtime_mapping import normalize_file_mount_grants
    from server_modules.runtime_policy import EXECUTION_TARGET_LOCAL_COMPANION, normalize_execution_target
except ImportError:  # pragma: no cover - used by local worker scripts
    from builder_runtime_mapping import normalize_file_mount_grants  # type: ignore[no-redef]
    from runtime_policy import EXECUTION_TARGET_LOCAL_COMPANION, normalize_execution_target  # type: ignore[no-redef]


FILE_OPERATION_MODE_READ = "read"
FILE_OPERATION_MODE_WRITE = "write"
FILE_OPERATION_MODE_APPEND = "append"
FILE_OPERATION_MODE_DELETE = "delete"

_FILE_MOUNTS = {"artifacts", "project", "shared", "knowledge", "local_root", "connector_files"}
_READ_ALLOWED = {"read", "read_write"}
_WRITE_ALLOWED = {"read_write", "create_only"}
_APPEND_ALLOWED = {"read_write", "append_only"}
_DELETE_ALLOWED = {"read_write"}


def normalize_file_operation_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {FILE_OPERATION_MODE_WRITE, "create"}:
        return FILE_OPERATION_MODE_WRITE
    if token in {FILE_OPERATION_MODE_APPEND}:
        return FILE_OPERATION_MODE_APPEND
    if token in {FILE_OPERATION_MODE_DELETE, "remove", "unlink"}:
        return FILE_OPERATION_MODE_DELETE
    return FILE_OPERATION_MODE_READ


def _is_path_traversal(path: str) -> bool:
    normalized = unquote(path).replace("\\", "/").lower()
    parts = normalized.split("/")
    for part in parts:
        if part == "..":
            return True
    return False


def infer_file_mount_for_path(raw_path: Any) -> Tuple[str, str]:
    text = str(raw_path or "").strip()
    if not text:
        return "project", ""
    if text in {".", "./"}:
        return "project", "."
    if _is_path_traversal(text):
        raise RuntimeError(f"Path traversal is forbidden: {text}")

    try:
        candidate = Path(text).expanduser()
    except Exception:
        candidate = Path(str(text).replace("\\", "/"))
    normalized = str(text).replace("\\", "/").strip()
    if candidate.is_absolute():
        return "local_root", normalized
    trimmed = normalized.lstrip("./")
    first = str(trimmed.split("/", 1)[0] if trimmed else "").strip().lower()
    if first in _FILE_MOUNTS:
        return first, trimmed
    return "project", trimmed


def _grant_allows_mode(grant: str, mode: str) -> bool:
    token = str(grant or "none").strip().lower()
    if mode == FILE_OPERATION_MODE_READ:
        return token in _READ_ALLOWED
    if mode == FILE_OPERATION_MODE_WRITE:
        return token in _WRITE_ALLOWED
    if mode == FILE_OPERATION_MODE_APPEND:
        return token in _APPEND_ALLOWED
    if mode == FILE_OPERATION_MODE_DELETE:
        return token in _DELETE_ALLOWED
    return False


def assert_file_mount_access(
    raw_path: Any,
    raw_mode: Any,
    raw_grants: Any,
    execution_target: Any,
) -> Dict[str, str]:
    mode = normalize_file_operation_mode(raw_mode)
    mount, normalized_path = infer_file_mount_for_path(raw_path)
    if not normalized_path:
        raise RuntimeError("File tool node requires path or file_path.")
    target = normalize_execution_target(execution_target)
    grants = normalize_file_mount_grants(raw_grants, target)
    grant = next((item.get("grant") for item in grants if str(item.get("mount") or "").strip().lower() == mount), "none")
    grant_token = str(grant or "none").strip().lower()

    if mount == "local_root" and target != EXECUTION_TARGET_LOCAL_COMPANION:
        raise RuntimeError("local_root mount grants require the local_companion execution target.")
    if not _grant_allows_mode(grant_token, mode):
        raise RuntimeError(
            f"File {mode} is not allowed for mount '{mount}' with grant '{grant_token or 'none'}'."
        )
    return {
        "mount": mount,
        "grant": grant_token or "none",
        "mode": mode,
        "path": normalized_path,
    }

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

from server_modules import artifact_service, execution_sandbox_service, outbox_service


FILE_BRIDGE_MODE_EXPORT_BRIDGE = "export_bridge"
FILE_BRIDGE_MODE_SYNC_FOLDER = "sync_folder"
FILE_BRIDGE_MODE_PRIVILEGED_DEVICE = "privileged_device"
SUPPORTED_CONNECTOR_ARTIFACT_CLASSES = {"api_connector", "browser_connector", "media_generation_connector"}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FileBridgeValidationError(ValueError):
    pass


class FileBridgePermissionError(PermissionError):
    pass


class FileBridgeArtifactNotFoundError(FileNotFoundError):
    pass


def _safe_token(value: Any, *, default: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-.")
    return token or default


def _safe_file_name(value: Any, *, default: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-.")
    return token or default


def connector_artifact_bridge_contract(connector_class: str, *, surface_role: Optional[str] = None) -> Dict[str, Any]:
    normalized_class = str(connector_class or "").strip().lower()
    if normalized_class not in SUPPORTED_CONNECTOR_ARTIFACT_CLASSES:
        raise FileBridgeValidationError(f"Unsupported connector artifact class '{connector_class}'.")
    normalized_surface_role = str(surface_role or "").strip().lower() or "deep_application_connector"
    return {
        "connector_class": normalized_class,
        "default_bridge_mode": FILE_BRIDGE_MODE_EXPORT_BRIDGE,
        "managed_export_required": normalized_class == "media_generation_connector",
        "sync_folder_allowed": normalized_surface_role != "channel_shell",
        "channel_shell_direct_export_allowed": False,
    }


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        token = str(item or "").strip()
        if token:
            out.append(token)
    return out


def _path_from_value(value: Any) -> Optional[Path]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        raw = parsed.path or ""
    try:
        path = Path(raw).expanduser()
    except Exception:
        return None
    if not path.is_absolute():
        return None
    return path.resolve()


def _path_is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _managed_bridge_root(*, workspace_id: str, install_id: str) -> Path:
    configured = str(
        os.getenv("EMPYRALIS_FILE_BRIDGE_ROOT")
        or os.getenv("ORION_FILE_BRIDGE_ROOT")
        or (_PROJECT_ROOT / ".orion-stack" / "file-bridge")
    ).strip()
    root = Path(configured).expanduser().resolve()
    path = (
        root
        / "exports"
        / _safe_token(workspace_id, default="default")
        / "agents"
        / _safe_token(install_id, default="install")
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_sync_folders(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            path = _path_from_value(item.get("path"))
            if path is None:
                continue
            folder_id = str(item.get("id") or item.get("slug") or path.name or "").strip()
            label = str(item.get("label") or path.name or folder_id).strip() or "Sync Folder"
        else:
            path = _path_from_value(item)
            if path is None:
                continue
            folder_id = path.name
            label = path.name
        normalized_id = _safe_token(folder_id, default="sync-folder")
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        out.append(
            {
                "id": normalized_id,
                "label": label,
                "path": str(path),
            }
        )
    return out


def build_file_bridge_policy(
    *,
    workspace_id: str,
    install_id: str,
    runtime_mode: str,
    runtime_profile: Mapping[str, Any] | None = None,
    install: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    runtime_profile_payload = dict(runtime_profile or {})
    runtime_profile_metadata = _coerce_dict(runtime_profile_payload.get("metadata"))
    install_payload = dict(install or {})
    install_metadata = _coerce_dict(install_payload.get("metadata"))
    runtime_scope = execution_sandbox_service.runtime_scope(
        runtime_mode=runtime_mode,
        runtime_profile=runtime_profile_payload,
        install=install_payload,
    )

    approved_roots: List[str] = []
    for raw in (
        install_payload.get("root_folder_uri"),
        runtime_profile_payload.get("root_folder_uri"),
    ):
        path = _path_from_value(raw)
        if path is not None:
            approved_roots.append(str(path))
    for raw in _list_strings(runtime_scope.get("approved_folders")):
        path = _path_from_value(raw)
        if path is not None and str(path) not in approved_roots:
            approved_roots.append(str(path))
    for raw in _list_strings(runtime_profile_metadata.get("export_roots")) + _list_strings(install_metadata.get("export_roots")):
        path = _path_from_value(raw)
        if path is not None and str(path) not in approved_roots:
            approved_roots.append(str(path))

    sync_folders = _parse_sync_folders(runtime_profile_metadata.get("sync_folders")) + _parse_sync_folders(
        install_metadata.get("sync_folders")
    )
    deduped_sync_folders: List[Dict[str, str]] = []
    seen_sync_ids: set[str] = set()
    for item in sync_folders:
        folder_id = str(item.get("id") or "").strip()
        if not folder_id or folder_id in seen_sync_ids:
            continue
        seen_sync_ids.add(folder_id)
        deduped_sync_folders.append(item)

    return {
        "default_mode": FILE_BRIDGE_MODE_EXPORT_BRIDGE,
        "runtime_mode": str(runtime_mode or "").strip() or "hosted_secure",
        "managed_root": str(_managed_bridge_root(workspace_id=workspace_id, install_id=install_id)),
        "approved_roots": approved_roots,
        "sync_folders": deduped_sync_folders,
        "requires_privileged_export_approval": bool(
            runtime_scope.get("requires_explicit_approval")
            or runtime_profile_metadata.get("requires_privileged_approval")
            or install_metadata.get("requires_privileged_approval")
        ),
    }


def _artifact_default_name(payload: Mapping[str, Any]) -> str:
    return _safe_file_name(
        payload.get("file_name")
        or payload.get("label")
        or payload.get("artifact_id")
        or "artifact.bin",
        default="artifact.bin",
    )


def _artifact_default_relative_path(payload: Mapping[str, Any]) -> Path:
    run_id = _safe_token(payload.get("run_id"), default="run")
    artifact_id = _safe_token(payload.get("artifact_id"), default="artifact")
    file_name = _artifact_default_name(payload)
    return Path(f"run-{run_id}") / f"artifact-{artifact_id}" / file_name


def _sync_folder_by_id(policy: Mapping[str, Any], sync_folder_id: str | None) -> Optional[Dict[str, str]]:
    normalized_id = str(sync_folder_id or "").strip()
    if not normalized_id:
        return None
    for item in policy.get("sync_folders") if isinstance(policy.get("sync_folders"), list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip() == normalized_id:
            return {str(key): str(value) for key, value in item.items() if str(key).strip()}
    return None


def _relative_target_path(destination_path: str | None, default_relative: Path) -> Path:
    raw = str(destination_path or "").strip()
    if not raw:
        return default_relative
    normalized = raw.replace("\\", "/")
    candidate = Path(normalized)
    if normalized.endswith("/"):
        return candidate / default_relative.name
    if candidate.suffix:
        return candidate
    return candidate / default_relative.name


def _resolve_target_path(
    *,
    policy: Mapping[str, Any],
    artifact_payload: Mapping[str, Any],
    destination_path: str | None,
    file_name: str | None,
    sync_folder_id: str | None,
    runtime_mode: str,
    privileged_export_approved: bool,
) -> Dict[str, Any]:
    managed_root = Path(str(policy.get("managed_root") or "").strip()).resolve()
    approved_roots = [Path(str(item)).resolve() for item in policy.get("approved_roots") if str(item).strip()] if isinstance(policy.get("approved_roots"), list) else []
    sync_folder = _sync_folder_by_id(policy, sync_folder_id)
    default_name = _safe_file_name(file_name or _artifact_default_name(artifact_payload), default=_artifact_default_name(artifact_payload))
    default_relative = _artifact_default_relative_path({**dict(artifact_payload), "file_name": default_name})

    if sync_folder is not None:
        sync_root = Path(str(sync_folder.get("path") or "").strip()).expanduser().resolve()
        sync_root.mkdir(parents=True, exist_ok=True)
        relative_target = _relative_target_path(destination_path, default_relative)
        target = (sync_root / relative_target).resolve()
        if not _path_is_within(sync_root, target):
            raise FileBridgePermissionError("Sync-folder export path escapes the configured sync folder.")
        return {
            "mode": FILE_BRIDGE_MODE_SYNC_FOLDER,
            "target_path": target,
            "root_path": sync_root,
            "sync_folder_id": str(sync_folder.get("id") or "").strip() or None,
        }

    raw_destination = str(destination_path or "").strip()
    if not raw_destination:
        target = (managed_root / default_relative).resolve()
        return {
            "mode": FILE_BRIDGE_MODE_EXPORT_BRIDGE,
            "target_path": target,
            "root_path": managed_root,
            "sync_folder_id": None,
        }

    destination = _path_from_value(raw_destination)
    if destination is None:
        relative_target = _relative_target_path(raw_destination, default_relative)
        target = (managed_root / relative_target).resolve()
        if not _path_is_within(managed_root, target):
            raise FileBridgePermissionError("Export path escapes the managed export bridge.")
        return {
            "mode": FILE_BRIDGE_MODE_EXPORT_BRIDGE,
            "target_path": target,
            "root_path": managed_root,
            "sync_folder_id": None,
        }

    if raw_destination.endswith(("/", "\\")) or destination.is_dir():
        destination = (destination / default_name).resolve()

    if _path_is_within(managed_root, destination):
        return {
            "mode": FILE_BRIDGE_MODE_EXPORT_BRIDGE,
            "target_path": destination,
            "root_path": managed_root,
            "sync_folder_id": None,
        }

    for item in policy.get("sync_folders") if isinstance(policy.get("sync_folders"), list) else []:
        if not isinstance(item, dict):
            continue
        sync_root = _path_from_value(item.get("path"))
        if sync_root is None:
            continue
        if _path_is_within(sync_root, destination):
            return {
                "mode": FILE_BRIDGE_MODE_SYNC_FOLDER,
                "target_path": destination,
                "root_path": sync_root,
                "sync_folder_id": str(item.get("id") or "").strip() or None,
            }

    for root in approved_roots:
        if _path_is_within(root, destination):
            return {
                "mode": FILE_BRIDGE_MODE_EXPORT_BRIDGE,
                "target_path": destination,
                "root_path": root,
                "sync_folder_id": None,
            }

    if str(runtime_mode or "").strip() == "privileged_device":
        if not privileged_export_approved:
            raise FileBridgePermissionError("Privileged file export requires explicit owner approval.")
        return {
            "mode": FILE_BRIDGE_MODE_PRIVILEGED_DEVICE,
            "target_path": destination,
            "root_path": destination.parent,
            "sync_folder_id": None,
        }

    raise FileBridgePermissionError("Export destination is outside the approved bridge and sync-folder roots.")


def _copy_to_target(source: Path, target: Path) -> Dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    stat = target.stat()
    return {
        "byte_size": int(stat.st_size),
        "content_path": str(target),
    }


def export_artifact_for_install(
    *,
    install: Mapping[str, Any],
    tenant_id: str,
    workspace_id: str,
    artifact_reference: str,
    destination_path: str | None = None,
    file_name: str | None = None,
    sync_folder_id: str | None = None,
    policy_context: Mapping[str, Any] | None = None,
    actor: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    install_payload = dict(install or {})
    install_id = str(install_payload.get("id") or "").strip()
    if not install_id:
        raise FileBridgeValidationError("Install ID is required for artifact export.")
    runtime_mode = str(install_payload.get("runtime_mode") or "").strip() or "hosted_secure"
    runtime_profile = install_payload.get("runtime_profile") if isinstance(install_payload.get("runtime_profile"), dict) else {}
    policy = build_file_bridge_policy(
        workspace_id=workspace_id,
        install_id=install_id,
        runtime_mode=runtime_mode,
        runtime_profile=runtime_profile,
        install=install_payload,
    )

    artifact_payload = artifact_service.load_artifact_metadata(
        artifact_reference,
        agent_install_id=install_id,
        include_shared=True,
    )
    if not isinstance(artifact_payload, dict):
        raise FileBridgeArtifactNotFoundError("Artifact not found for this specialist.")
    source_path = artifact_service.resolve_artifact_content_path(
        artifact_reference,
        agent_install_id=install_id,
        include_shared=True,
    )
    if source_path is None or not source_path.exists():
        raise FileBridgeArtifactNotFoundError("Artifact content is unavailable for export.")

    policy_context_payload = dict(policy_context or {})
    privileged_export_approved = bool(
        policy_context_payload.get("privileged_file_export_approved")
        or policy_context_payload.get("file_export_approved")
        or policy_context_payload.get("privileged_runtime_approved")
    )
    target_plan = _resolve_target_path(
        policy=policy,
        artifact_payload=artifact_payload,
        destination_path=destination_path,
        file_name=file_name,
        sync_folder_id=sync_folder_id,
        runtime_mode=runtime_mode,
        privileged_export_approved=privileged_export_approved,
    )
    copy_result = _copy_to_target(source_path, target_plan["target_path"])
    audit_event = outbox_service.emit_runtime_event(
        event_type="artifact_exported",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=str(artifact_payload.get("run_id") or "").strip() or None,
        idempotency_key=(
            f"artifact_exported:{install_id}:{str(artifact_payload.get('artifact_id') or '').strip()}:"
            f"{str(target_plan['target_path'])}"
        ),
        payload={
            "install_id": install_id,
            "artifact_id": str(artifact_payload.get("artifact_id") or "").strip(),
            "artifact_uri": str(artifact_payload.get("uri") or artifact_reference).strip(),
            "bridge_mode": str(target_plan.get("mode") or FILE_BRIDGE_MODE_EXPORT_BRIDGE),
            "destination_path": str(target_plan["target_path"]),
            "sync_folder_id": str(target_plan.get("sync_folder_id") or "").strip() or None,
            "root_path": str(target_plan["root_path"]),
            "runtime_mode": runtime_mode,
            "privileged_export_approved": privileged_export_approved,
            "actor": dict(actor or {}),
            "emitted_at": outbox_service._utc_now_iso(),
        },
    )
    return {
        "artifact_id": str(artifact_payload.get("artifact_id") or "").strip(),
        "artifact_uri": str(artifact_payload.get("uri") or artifact_reference).strip(),
        "bridge_mode": str(target_plan.get("mode") or FILE_BRIDGE_MODE_EXPORT_BRIDGE),
        "destination_path": str(target_plan["target_path"]),
        "byte_size": int(copy_result["byte_size"]),
        "runtime_mode": runtime_mode,
        "policy": {
            "managed_root": str(policy.get("managed_root") or ""),
            "approved_roots": list(policy.get("approved_roots") or []),
            "sync_folders": list(policy.get("sync_folders") or []),
            "requires_privileged_export_approval": bool(policy.get("requires_privileged_export_approval")),
        },
        "audit": {
            "event_id": audit_event.event_id,
            "event_type": audit_event.event_type,
        },
    }

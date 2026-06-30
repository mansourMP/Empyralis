from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse
import uuid

try:
    import boto3 as _boto3
except Exception:  # pragma: no cover - optional dependency at runtime
    _boto3 = None

try:
    from botocore.config import Config as _BotocoreConfig
except Exception:  # pragma: no cover - optional dependency at runtime
    _BotocoreConfig = None

from server_modules import rust_runtime_kernel_client
from server_modules.telemetry import record_reliability_latency_sample


ARTIFACT_URI_SCHEME = "artifact"
FILESYSTEM_ARTIFACT_STORAGE_BACKEND = "filesystem_object_store"
S3_ARTIFACT_STORAGE_BACKEND = "s3_compatible_object_store"
ARTIFACT_STORAGE_BACKEND = FILESYSTEM_ARTIFACT_STORAGE_BACKEND
ARTIFACT_AUTHORITY_SCOPE = "artifact_records_and_objects_only"
DEFAULT_RETENTION_POLICY = {
    "mode": "default",
    "retention_days": None,
    "expires_at": None,
    "policy_status": "placeholder",
}

# Artifact storage is authoritative only for artifact object/blob storage and
# canonical artifact records. It is not a source of truth for runtime sessions,
# live runs, approvals, outbox delivery, or local claim ownership.


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_first(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _env_flag(*names: str) -> bool:
    value = _env_first(*names).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-.")
    return text or "artifact.bin"


def _safe_scope_token(value: str, *, default: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-.")
    return text or default


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def configured_artifact_storage_backend() -> str:
    configured = _env_first("EMPYRALIS_OBJECT_STORAGE_BACKEND", "ORION_OBJECT_STORAGE_BACKEND").lower()
    if configured in {"s3", "s3-compatible", "s3_compatible", S3_ARTIFACT_STORAGE_BACKEND}:
        return S3_ARTIFACT_STORAGE_BACKEND
    return FILESYSTEM_ARTIFACT_STORAGE_BACKEND


def _artifact_store_root() -> Path:
    configured = (
        _env_first("EMPYRALIS_OBJECT_STORAGE_ROOT", "ORION_OBJECT_STORAGE_ROOT")
        or str((_project_root() / ".orion-object-store").resolve())
    )
    target = Path(configured).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _artifact_records_root() -> Path:
    target = _artifact_store_root() / "records"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _artifact_objects_root() -> Path:
    target = _artifact_store_root() / "objects"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _artifact_cache_root() -> Path:
    configured = (
        _env_first("EMPYRALIS_OBJECT_STORAGE_CACHE_ROOT", "ORION_OBJECT_STORAGE_CACHE_ROOT")
        or str((_artifact_store_root() / "cache").resolve())
    )
    target = Path(configured).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def is_artifact_uri(value: str) -> bool:
    return str(value or "").strip().lower().startswith(f"{ARTIFACT_URI_SCHEME}://")


def artifact_uri(artifact_id: str, file_name: str) -> str:
    return f"{ARTIFACT_URI_SCHEME}://{str(artifact_id or '').strip()}/{_safe_filename(file_name)}"


def artifact_id_from_reference(value: str) -> Optional[str]:
    token = str(value or "").strip()
    if not token:
        return None
    if is_artifact_uri(token):
        parsed = urlparse(token)
        return str(parsed.netloc or "").strip() or None
    if "/" not in token and "\\" not in token and " " not in token:
        return token
    return None


def canonical_artifact_reference(value: Mapping[str, Any]) -> Optional[str]:
    if not isinstance(value, Mapping):
        return None
    uri = str(value.get("uri") or value.get("uri_or_path") or value.get("file_path") or value.get("path") or "").strip()
    if is_artifact_uri(uri):
        return uri
    return None


def is_canonical_artifact_payload(value: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping):
        return False
    artifact_id = str(value.get("artifact_id") or "").strip()
    uri = canonical_artifact_reference(value)
    return bool(artifact_id and uri)


def retention_placeholder(retention_days: Optional[int] = None, expires_at: Optional[str] = None) -> Dict[str, Any]:
    payload = dict(DEFAULT_RETENTION_POLICY)
    payload["retention_days"] = int(retention_days) if isinstance(retention_days, int) and retention_days > 0 else None
    payload["expires_at"] = str(expires_at or "").strip() or None
    return payload


def _guess_content_type(file_name: str, explicit: Optional[str] = None) -> str:
    declared = str(explicit or "").strip()
    if declared:
        return declared
    guessed = mimetypes.guess_type(file_name)[0]
    return guessed or "application/octet-stream"


def _object_key_prefix() -> str:
    return _env_first("EMPYRALIS_OBJECT_STORAGE_PREFIX", "ORION_OBJECT_STORAGE_PREFIX").strip().strip("/")


def _artifact_object_key(run_id: str, artifact_id: str, file_name: str, *, agent_install_id: str | None = None) -> str:
    run_token = _safe_filename(str(run_id or "").strip() or "run")
    stored_name = _safe_filename(file_name)
    normalized_agent_install_id = str(agent_install_id or "").strip()
    if normalized_agent_install_id:
        object_key = (
            f"runs/{run_token}/agents/{_safe_scope_token(normalized_agent_install_id, default='install')}/"
            f"{artifact_id}/{stored_name}"
        )
    else:
        object_key = f"runs/{run_token}/{artifact_id}/{stored_name}"
    prefix = _object_key_prefix()
    return f"{prefix}/{object_key}" if prefix else object_key


def _safe_relative_path(root: Path, relative_path: str, *, ensure_parent: bool = False) -> Path:
    candidate = (root / str(relative_path or "").strip()).resolve()
    try:
        candidate.relative_to(root)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(f"Artifact object path escaped configured root: {relative_path}") from exc
    if ensure_parent:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _artifact_object_path_for_key(object_key: str, *, ensure_parent: bool = False) -> Path:
    return _safe_relative_path(_artifact_objects_root(), object_key, ensure_parent=ensure_parent)


def _artifact_cached_path(payload: Mapping[str, Any]) -> Path:
    bucket = _safe_filename(str(payload.get("storage_bucket") or _configured_s3_bucket() or "artifact-bucket"))
    object_key = str(payload.get("object_key") or "").strip()
    if object_key:
        relative = f"{bucket}/{object_key}"
    else:
        artifact_token = _safe_filename(str(payload.get("artifact_id") or "artifact"))
        file_name = _safe_filename(str(payload.get("file_name") or payload.get("label") or "artifact.bin"))
        relative = f"{bucket}/{artifact_token}/{file_name}"
    return _safe_relative_path(_artifact_cache_root(), relative, ensure_parent=True)


def _artifact_record_path(artifact_id: str) -> Path:
    return (_artifact_records_root() / f"{artifact_id}.json").resolve()


def _configured_s3_bucket() -> str:
    return _env_first("EMPYRALIS_OBJECT_STORAGE_BUCKET", "ORION_OBJECT_STORAGE_BUCKET")


def _configured_s3_region() -> str:
    return _env_first(
        "EMPYRALIS_OBJECT_STORAGE_REGION",
        "ORION_OBJECT_STORAGE_REGION",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    ) or "us-east-1"


def _configured_s3_endpoint() -> Optional[str]:
    value = _env_first("EMPYRALIS_OBJECT_STORAGE_ENDPOINT", "ORION_OBJECT_STORAGE_ENDPOINT")
    return value or None


def _build_s3_client():
    if _boto3 is None:
        raise RuntimeError("boto3 is not installed. Add the 'boto3' package to use the S3-compatible artifact backend.")
    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": _configured_s3_region(),
    }
    endpoint = _configured_s3_endpoint()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    access_key_id = _env_first("EMPYRALIS_OBJECT_STORAGE_ACCESS_KEY_ID", "ORION_OBJECT_STORAGE_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
    secret_access_key = _env_first(
        "EMPYRALIS_OBJECT_STORAGE_SECRET_ACCESS_KEY",
        "ORION_OBJECT_STORAGE_SECRET_ACCESS_KEY",
        "AWS_SECRET_ACCESS_KEY",
    )
    session_token = _env_first("EMPYRALIS_OBJECT_STORAGE_SESSION_TOKEN", "ORION_OBJECT_STORAGE_SESSION_TOKEN", "AWS_SESSION_TOKEN")
    if access_key_id:
        kwargs["aws_access_key_id"] = access_key_id
    if secret_access_key:
        kwargs["aws_secret_access_key"] = secret_access_key
    if session_token:
        kwargs["aws_session_token"] = session_token
    if _env_flag(
        "EMPYRALIS_OBJECT_STORAGE_PATH_STYLE",
        "ORION_OBJECT_STORAGE_PATH_STYLE",
        "EMPYRALIS_OBJECT_STORAGE_FORCE_PATH_STYLE",
        "S3_ENABLE_PATH_STYLE",
    ) and _BotocoreConfig is not None:
        kwargs["config"] = _BotocoreConfig(s3={"addressing_style": "path"})
    return _boto3.client(**kwargs)


def _upload_file_to_s3(source: Path, *, bucket: str, key: str, content_type: str) -> None:
    client = _build_s3_client()
    client.upload_file(
        str(source),
        str(bucket),
        str(key),
        ExtraArgs={"ContentType": content_type},
    )


def _upload_bytes_to_s3(content: bytes, *, bucket: str, key: str, content_type: str) -> None:
    client = _build_s3_client()
    client.put_object(Bucket=str(bucket), Key=str(key), Body=bytes(content), ContentType=content_type)


def _download_s3_object_to_cache(payload: Mapping[str, Any], target: Path) -> None:
    bucket = str(payload.get("storage_bucket") or _configured_s3_bucket() or "").strip()
    key = str(payload.get("object_key") or "").strip()
    if not bucket:
        raise RuntimeError("S3-compatible artifact backend requires EMPYRALIS_OBJECT_STORAGE_BUCKET (or recorded storage_bucket metadata).")
    if not key:
        raise RuntimeError("Artifact record is missing object_key for S3-compatible storage.")
    client = _build_s3_client()
    temp_target = target.with_name(f"{target.name}.part")
    if temp_target.exists():
        temp_target.unlink()
    client.download_file(str(bucket), str(key), str(temp_target))
    temp_target.replace(target)


@dataclass(slots=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    kind: str
    uri: str
    tenant_id: str = "default"
    workspace_id: str = "default"
    agent_install_id: Optional[str] = None
    label: str = ""
    mime_type: str = ""
    content_type: str = ""
    byte_size: int = 0
    created_at: str = ""
    machine_id: Optional[str] = None
    step_id: Optional[str] = None
    step_index: Optional[int] = None
    step_number: Optional[int] = None
    storage_backend: str = ARTIFACT_STORAGE_BACKEND
    storage_bucket: Optional[str] = None
    storage_region: Optional[str] = None
    storage_endpoint: Optional[str] = None
    object_key: str = ""
    file_name: str = ""
    retention: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RETENTION_POLICY))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> Dict[str, Any]:
        payload = {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "agent_install_id": self.agent_install_id,
            "kind": self.kind,
            "uri": self.uri,
            "uri_or_path": self.uri,
            "path": self.uri,
            "file_path": self.uri,
            "label": self.label,
            "mime_type": self.mime_type or self.content_type,
            "content_type": self.content_type or self.mime_type,
            "byte_size": int(self.byte_size or 0),
            "created_at": self.created_at,
            "machine_id": self.machine_id,
            "step_id": self.step_id,
            "step_index": self.step_index,
            "step_number": self.step_number,
            "storage_backend": self.storage_backend,
            "storage_bucket": self.storage_bucket,
            "storage_region": self.storage_region,
            "storage_endpoint": self.storage_endpoint,
            "object_key": self.object_key,
            "file_name": self.file_name,
            "retention": dict(self.retention or DEFAULT_RETENTION_POLICY),
            "metadata": dict(self.metadata or {}),
        }
        return {key: value for key, value in payload.items() if value is not None}


class ArtifactServiceRustGateError(RuntimeError):
    pass


def _enforce_artifact_record_state_decision(payload: Dict[str, Any]) -> Dict[str, Any]:
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
            operation="persist_artifact_record",
            state_class="artifact_records",
            tenant_id=str(normalized_payload.get("tenant_id") or "default").strip() or "default",
            workspace_id=str(normalized_payload.get("workspace_id") or "").strip(),
            run_id=str(normalized_payload.get("run_id") or "").strip(),
            actor_id="system",
            status="active",
            payload=normalized_payload,
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        enforced = rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(enforced.get("next_action") or "").strip()
        if next_action != "persist_artifact_record":
            raise ArtifactServiceRustGateError(
                "unexpected next_action for persist_artifact_record: "
                f"{next_action or 'missing'}"
            )
        return enforced
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise ArtifactServiceRustGateError(exc.reason) from exc


def _enforce_artifact_content_file_decision(
    *,
    artifact_id: str,
    run_id: str,
    tenant_id: str,
    workspace_id: str,
    object_key: str,
    target: Path,
    content_bytes: int,
) -> Dict[str, Any]:
    payload = {
        "artifact_id": str(artifact_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "tenant_id": str(tenant_id or "").strip() or "default",
        "workspace_id": str(workspace_id or "").strip(),
        "object_key": str(object_key or "").strip(),
        "target": str(target),
        "content_bytes": max(0, int(content_bytes or 0)),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_artifact_content_file",
            state_class="artifact_content_files",
            tenant_id=str(payload["tenant_id"]),
            workspace_id=str(payload["workspace_id"]),
            run_id=str(payload["run_id"]),
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=int(payload["content_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        enforced = rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(enforced.get("next_action") or "").strip()
        if next_action != "write_artifact_content_file":
            raise ArtifactServiceRustGateError(
                "unexpected next_action for write_artifact_content_file: "
                f"{next_action or 'missing'}"
            )
        return enforced
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise ArtifactServiceRustGateError(exc.reason) from exc


def _persist_record(
    record: ArtifactRecord,
    *,
    stored_path: Optional[Path] = None,
    source_path: Optional[Path] = None,
) -> None:
    payload = record.as_payload()
    if stored_path is not None:
        payload["stored_path"] = str(stored_path.resolve())
    if source_path is not None:
        payload["source_path"] = str(source_path.resolve())
    _enforce_artifact_record_state_decision(payload)
    _artifact_record_path(record.artifact_id).write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _build_artifact_record(
    *,
    artifact_id: str,
    run_id: str,
    kind: str,
    resolved_label: str,
    mime: str,
    byte_size: int,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    agent_install_id: Optional[str],
    machine_id: Optional[str],
    step_id: Optional[str],
    step_index: Optional[int],
    step_number: Optional[int],
    retention_days: Optional[int],
    retention_expires_at: Optional[str],
    metadata: Optional[Mapping[str, Any]],
    created_at: Optional[str],
    storage_backend: str,
    object_key: str,
    file_name: str,
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        run_id=str(run_id or "").strip(),
        tenant_id=str(tenant_id or "default").strip() or "default",
        workspace_id=str(workspace_id or "default").strip() or "default",
        agent_install_id=str(agent_install_id or "").strip() or None,
        kind=str(kind or "artifact").strip() or "artifact",
        uri=artifact_uri(artifact_id, resolved_label),
        label=resolved_label,
        mime_type=mime,
        content_type=mime,
        byte_size=int(byte_size),
        created_at=str(created_at or _utc_now_iso()).strip() or _utc_now_iso(),
        machine_id=str(machine_id or "").strip() or None,
        step_id=str(step_id or "").strip() or None,
        step_index=int(step_index) if isinstance(step_index, int) else None,
        step_number=int(step_number) if isinstance(step_number, int) else None,
        storage_backend=storage_backend,
        storage_bucket=_configured_s3_bucket() if storage_backend == S3_ARTIFACT_STORAGE_BACKEND else None,
        storage_region=_configured_s3_region() if storage_backend == S3_ARTIFACT_STORAGE_BACKEND else None,
        storage_endpoint=_configured_s3_endpoint() if storage_backend == S3_ARTIFACT_STORAGE_BACKEND else None,
        object_key=object_key,
        file_name=file_name,
        retention=retention_placeholder(retention_days, retention_expires_at),
        metadata=dict(_json_safe(dict(metadata or {})) or {}),
    )


def store_artifact_file(
    source_path: Path | str,
    *,
    run_id: str,
    kind: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    agent_install_id: Optional[str] = None,
    label: Optional[str] = None,
    machine_id: Optional[str] = None,
    step_id: Optional[str] = None,
    step_index: Optional[int] = None,
    step_number: Optional[int] = None,
    content_type: Optional[str] = None,
    retention_days: Optional[int] = None,
    retention_expires_at: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    artifact_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> ArtifactRecord:
    started_mono = time.monotonic()
    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(source)
    resolved_label = str(label or source.name).strip() or source.name
    artifact_token = str(artifact_id or uuid.uuid4().hex).strip() or uuid.uuid4().hex
    mime = _guess_content_type(source.name, explicit=content_type)
    object_key = _artifact_object_key(
        str(run_id or "").strip(),
        artifact_token,
        resolved_label,
        agent_install_id=agent_install_id,
    )
    backend = configured_artifact_storage_backend()

    if backend == S3_ARTIFACT_STORAGE_BACKEND:
        bucket = _configured_s3_bucket()
        if not bucket:
            raise RuntimeError("S3-compatible artifact backend requires EMPYRALIS_OBJECT_STORAGE_BUCKET or ORION_OBJECT_STORAGE_BUCKET.")
        _upload_file_to_s3(source, bucket=bucket, key=object_key, content_type=mime)
        record = _build_artifact_record(
            artifact_id=artifact_token,
            run_id=run_id,
            kind=kind,
            resolved_label=resolved_label,
            mime=mime,
            byte_size=int(source.stat().st_size),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
            machine_id=machine_id,
            step_id=step_id,
            step_index=step_index,
            step_number=step_number,
            retention_days=retention_days,
            retention_expires_at=retention_expires_at,
            metadata=metadata,
            created_at=created_at,
            storage_backend=S3_ARTIFACT_STORAGE_BACKEND,
            object_key=object_key,
            file_name=_safe_filename(resolved_label),
        )
        _persist_record(record, source_path=source)
        record_reliability_latency_sample(
            "artifact_availability_latency_ms",
            max(0.0, (time.monotonic() - started_mono) * 1000.0),
            recorded_at=record.created_at,
            metadata={
                "artifact_id": record.artifact_id,
                "run_id": record.run_id,
                "storage_backend": record.storage_backend,
            },
        )
        return record

    target = _artifact_object_path_for_key(object_key, ensure_parent=True)
    if source != target:
        shutil.copy2(source, target)
    stat = target.stat()
    record = _build_artifact_record(
        artifact_id=artifact_token,
        run_id=run_id,
        kind=kind,
        resolved_label=resolved_label,
        mime=mime,
        byte_size=int(stat.st_size),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
        machine_id=machine_id,
        step_id=step_id,
        step_index=step_index,
        step_number=step_number,
        retention_days=retention_days,
        retention_expires_at=retention_expires_at,
        metadata=metadata,
        created_at=created_at,
        storage_backend=FILESYSTEM_ARTIFACT_STORAGE_BACKEND,
        object_key=object_key,
        file_name=target.name,
    )
    _persist_record(record, stored_path=target, source_path=source)
    record_reliability_latency_sample(
        "artifact_availability_latency_ms",
        max(0.0, (time.monotonic() - started_mono) * 1000.0),
        recorded_at=record.created_at,
        metadata={
            "artifact_id": record.artifact_id,
            "run_id": record.run_id,
            "storage_backend": record.storage_backend,
        },
    )
    return record


def store_artifact_bytes(
    content: bytes,
    *,
    run_id: str,
    kind: str,
    file_name: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    agent_install_id: Optional[str] = None,
    label: Optional[str] = None,
    machine_id: Optional[str] = None,
    step_id: Optional[str] = None,
    step_index: Optional[int] = None,
    step_number: Optional[int] = None,
    content_type: Optional[str] = None,
    retention_days: Optional[int] = None,
    retention_expires_at: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    artifact_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> ArtifactRecord:
    started_mono = time.monotonic()
    artifact_token = str(artifact_id or uuid.uuid4().hex).strip() or uuid.uuid4().hex
    resolved_name = str(label or file_name or "artifact.bin").strip() or "artifact.bin"
    mime = _guess_content_type(resolved_name, explicit=content_type)
    object_key = _artifact_object_key(
        str(run_id or "").strip(),
        artifact_token,
        resolved_name,
        agent_install_id=agent_install_id,
    )
    backend = configured_artifact_storage_backend()

    if backend == S3_ARTIFACT_STORAGE_BACKEND:
        bucket = _configured_s3_bucket()
        if not bucket:
            raise RuntimeError("S3-compatible artifact backend requires EMPYRALIS_OBJECT_STORAGE_BUCKET or ORION_OBJECT_STORAGE_BUCKET.")
        _upload_bytes_to_s3(bytes(content), bucket=bucket, key=object_key, content_type=mime)
        record = _build_artifact_record(
            artifact_id=artifact_token,
            run_id=run_id,
            kind=kind,
            resolved_label=resolved_name,
            mime=mime,
            byte_size=len(bytes(content)),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
            machine_id=machine_id,
            step_id=step_id,
            step_index=step_index,
            step_number=step_number,
            retention_days=retention_days,
            retention_expires_at=retention_expires_at,
            metadata=metadata,
            created_at=created_at,
            storage_backend=S3_ARTIFACT_STORAGE_BACKEND,
            object_key=object_key,
            file_name=_safe_filename(resolved_name),
        )
        _persist_record(record)
        record_reliability_latency_sample(
            "artifact_availability_latency_ms",
            max(0.0, (time.monotonic() - started_mono) * 1000.0),
            recorded_at=record.created_at,
            metadata={
                "artifact_id": record.artifact_id,
                "run_id": record.run_id,
                "storage_backend": record.storage_backend,
            },
        )
        return record

    target = _artifact_object_path_for_key(object_key, ensure_parent=True)
    content_blob = bytes(content)
    _enforce_artifact_content_file_decision(
        artifact_id=artifact_token,
        run_id=run_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        object_key=object_key,
        target=target,
        content_bytes=len(content_blob),
    )
    target.write_bytes(content_blob)
    record = _build_artifact_record(
        artifact_id=artifact_token,
        run_id=run_id,
        kind=kind,
        resolved_label=resolved_name,
        mime=mime,
        byte_size=int(target.stat().st_size),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
        machine_id=machine_id,
        step_id=step_id,
        step_index=step_index,
        step_number=step_number,
        retention_days=retention_days,
        retention_expires_at=retention_expires_at,
        metadata=metadata,
        created_at=created_at,
        storage_backend=FILESYSTEM_ARTIFACT_STORAGE_BACKEND,
        object_key=object_key,
        file_name=target.name,
    )
    _persist_record(record, stored_path=target)
    record_reliability_latency_sample(
        "artifact_availability_latency_ms",
        max(0.0, (time.monotonic() - started_mono) * 1000.0),
        recorded_at=record.created_at,
        metadata={
            "artifact_id": record.artifact_id,
            "run_id": record.run_id,
            "storage_backend": record.storage_backend,
        },
    )
    return record


def load_artifact_metadata(
    reference: str,
    *,
    agent_install_id: str | None = None,
    include_shared: bool = True,
) -> Optional[Dict[str, Any]]:
    artifact_id = artifact_id_from_reference(reference)
    if not artifact_id:
        return None
    target = _artifact_record_path(artifact_id)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    normalized_agent_install_id = str(agent_install_id or "").strip()
    if not normalized_agent_install_id:
        return payload
    payload_agent_install_id = str(
        payload.get("agent_install_id")
        or (payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}).get("agent_install_id")
        or ""
    ).strip()
    if payload_agent_install_id:
        return payload if payload_agent_install_id == normalized_agent_install_id else None
    if include_shared:
        return payload
    return None


def load_artifact_metadata_by_id(
    artifact_id: str,
    *,
    agent_install_id: str | None = None,
    include_shared: bool = True,
) -> Optional[Dict[str, Any]]:
    token = str(artifact_id or "").strip()
    if not token:
        return None
    return load_artifact_metadata(
        token,
        agent_install_id=agent_install_id,
        include_shared=include_shared,
    )


def resolve_artifact_content_path(
    reference: str,
    *,
    agent_install_id: str | None = None,
    include_shared: bool = True,
) -> Optional[Path]:
    payload = load_artifact_metadata(reference, agent_install_id=agent_install_id, include_shared=include_shared)
    if not isinstance(payload, dict):
        return None
    backend = str(payload.get("storage_backend") or FILESYSTEM_ARTIFACT_STORAGE_BACKEND).strip() or FILESYSTEM_ARTIFACT_STORAGE_BACKEND
    if backend == S3_ARTIFACT_STORAGE_BACKEND:
        try:
            target = _artifact_cached_path(payload)
            expected_size = int(payload.get("byte_size") or 0)
            if target.exists() and target.is_file():
                if expected_size <= 0 or target.stat().st_size == expected_size:
                    return target
            _download_s3_object_to_cache(payload, target)
            if target.exists() and target.is_file():
                return target
            return None
        except Exception:
            return None

    stored_path = str(payload.get("stored_path") or "").strip()
    if stored_path:
        candidate = Path(stored_path).expanduser().resolve()
        return candidate if candidate.exists() and candidate.is_file() else None
    object_key = str(payload.get("object_key") or "").strip()
    if not object_key:
        return None
    try:
        candidate = _artifact_object_path_for_key(object_key)
    except Exception:
        return None
    return candidate if candidate.exists() and candidate.is_file() else None


def resolve_artifact_content_path_by_id(
    artifact_id: str,
    *,
    agent_install_id: str | None = None,
    include_shared: bool = True,
) -> Optional[Path]:
    token = str(artifact_id or "").strip()
    if not token:
        return None
    return resolve_artifact_content_path(
        token,
        agent_install_id=agent_install_id,
        include_shared=include_shared,
    )


def artifact_download_filename(payload: Mapping[str, Any], fallback: str = "artifact.bin") -> str:
    if not isinstance(payload, Mapping):
        return _safe_filename(fallback)
    candidate = (
        str(payload.get("file_name") or "").strip()
        or str(payload.get("label") or "").strip()
        or str(payload.get("artifact_id") or "").strip()
        or fallback
    )
    return _safe_filename(candidate or fallback)


def artifact_content_type(payload: Mapping[str, Any], fallback_name: str = "artifact.bin") -> str:
    if not isinstance(payload, Mapping):
        return _guess_content_type(fallback_name)
    explicit = (
        str(payload.get("content_type") or "").strip()
        or str(payload.get("mime_type") or "").strip()
    )
    return _guess_content_type(
        artifact_download_filename(payload, fallback_name),
        explicit=explicit or None,
    )


def _iter_artifact_record_payloads() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(_artifact_records_root().glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def _artifact_payload_matches_scope(
    payload: Mapping[str, Any],
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if str(tenant_id or "").strip() and str(payload.get("tenant_id") or "").strip() != str(tenant_id or "").strip():
        return False
    if str(workspace_id or "").strip() and str(payload.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        return False
    if str(run_id or "").strip() and str(payload.get("run_id") or "").strip() != str(run_id or "").strip():
        return False
    return True


def list_artifact_records(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return [
        dict(item)
        for item in _iter_artifact_record_payloads()
        if _artifact_payload_matches_scope(
            item,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
    ]


def build_artifact_backup_manifest() -> Dict[str, Any]:
    return {
        "store_id": "artifact_store",
        "authority_scope": ARTIFACT_AUTHORITY_SCOPE,
        "storage_backend": configured_artifact_storage_backend(),
        "records_root": str(_artifact_records_root()),
        "objects_root": str(_artifact_objects_root()),
        "cache_root": str(_artifact_cache_root()),
        "export_fn": "server_modules.artifact_service.export_artifact_scope",
        "retention_plan_fn": "server_modules.artifact_service.plan_artifact_retention",
        "restore_rehearsal": "server_modules.artifact_service.rehearse_artifact_restore",
    }


def export_artifact_scope(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    records = list_artifact_records(tenant_id=tenant_id, workspace_id=workspace_id, run_id=run_id)
    return {
        "tenant_id": str(tenant_id or "").strip() or None,
        "workspace_id": str(workspace_id or "").strip() or None,
        "run_id": str(run_id or "").strip() or None,
        "record_count": len(records),
        "records": records,
    }


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _artifact_retention_deadline(payload: Mapping[str, Any]) -> Optional[datetime]:
    retention = payload.get("retention")
    if not isinstance(retention, Mapping):
        retention = {}
    explicit_expires_at = _parse_iso_timestamp(retention.get("expires_at"))
    if explicit_expires_at is not None:
        return explicit_expires_at
    retention_days = retention.get("retention_days")
    if isinstance(retention_days, int) and retention_days > 0:
        created_at = _parse_iso_timestamp(payload.get("created_at"))
        if created_at is not None:
            return created_at + timedelta(days=int(retention_days))
    return None


def plan_artifact_retention(
    *,
    now_iso: Optional[str] = None,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    now_dt = _parse_iso_timestamp(now_iso) or datetime.now(timezone.utc)
    expired: List[Dict[str, Any]] = []
    retained: List[Dict[str, Any]] = []
    for payload in list_artifact_records(tenant_id=tenant_id, workspace_id=workspace_id):
        deadline = _artifact_retention_deadline(payload)
        item = {
            "artifact_id": str(payload.get("artifact_id") or "").strip(),
            "run_id": str(payload.get("run_id") or "").strip() or None,
            "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
            "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
            "expires_at": deadline.isoformat().replace("+00:00", "Z") if deadline is not None else None,
            "retention": dict(payload.get("retention") or {}),
        }
        if deadline is not None and deadline <= now_dt:
            expired.append(item)
        else:
            retained.append(item)
    return {
        "evaluated_at": now_dt.isoformat().replace("+00:00", "Z"),
        "tenant_id": str(tenant_id or "").strip() or None,
        "workspace_id": str(workspace_id or "").strip() or None,
        "expired_candidates": expired,
        "expired_count": len(expired),
        "retained_count": len(retained),
    }


def rehearse_artifact_restore(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    records = list_artifact_records(tenant_id=tenant_id, workspace_id=workspace_id)
    checked_ids: List[str] = []
    missing_content_ids: List[str] = []
    for payload in records:
        reference = str(payload.get("uri") or payload.get("artifact_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not reference or not artifact_id:
            continue
        checked_ids.append(artifact_id)
        if resolve_artifact_content_path(reference) is None:
            missing_content_ids.append(artifact_id)
    return {
        "ok": len(missing_content_ids) == 0,
        "store_id": "artifact_store",
        "tenant_id": str(tenant_id or "").strip() or None,
        "workspace_id": str(workspace_id or "").strip() or None,
        "checked_count": len(checked_ids),
        "checked_ids": checked_ids,
        "missing_content_ids": missing_content_ids,
    }

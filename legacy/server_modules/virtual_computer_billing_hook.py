from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules import credit_ledger_contract


_RUNTIME_TYPE_ALIASES: Dict[str, str] = {
    "virtual_browser": "virtual_browser",
    "browser": "virtual_browser",
    "cloud_browser": "virtual_browser",
    "virtual_desktop": "virtual_desktop",
    "desktop": "virtual_desktop",
    "cloud_desktop": "virtual_desktop",
    "virtual_code_sandbox": "virtual_code_sandbox",
    "code_sandbox": "virtual_code_sandbox",
    "sandbox": "virtual_code_sandbox",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _non_negative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _normalize_runtime_type(value: Any) -> str:
    token = _text(value).lower().replace("-", "_")
    return _RUNTIME_TYPE_ALIASES.get(token, "virtual_browser")


def _to_minutes(duration_ms: int) -> float:
    if duration_ms <= 0:
        return 0.0
    return round(float(duration_ms) / 60000.0, 6)


def _to_mb(byte_count: int) -> float:
    if byte_count <= 0:
        return 0.0
    return round(float(byte_count) / (1024.0 * 1024.0), 6)


def _build_line_items(
    *,
    runtime_type: str,
    session_id: str,
    duration_ms: int,
    artifact_bytes: int,
    snapshot_bytes: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    payload = dict(metadata or {}) if isinstance(metadata, dict) else {}
    line_items: List[Dict[str, Any]] = []

    runtime_minutes = _to_minutes(duration_ms)
    if runtime_minutes > 0:
        line_items.append(
            credit_ledger_contract.build_credit_ledger_line_item(
                metadata={
                    **payload,
                    "runtime_type": runtime_type,
                    "session_id": session_id,
                },
                runtime_minutes=runtime_minutes,
            )
        )

    artifact_mb = _to_mb(artifact_bytes)
    if artifact_mb > 0:
        line_items.append(
            credit_ledger_contract.build_credit_ledger_line_item(
                metadata={
                    **payload,
                    "credit_item_type": "artifact_storage",
                    "session_id": session_id,
                    "runtime_type": runtime_type,
                    "storage_mb": artifact_mb,
                },
            )
        )

    snapshot_mb = _to_mb(snapshot_bytes)
    if snapshot_mb > 0:
        line_items.append(
            credit_ledger_contract.build_credit_ledger_line_item(
                metadata={
                    **payload,
                    "credit_item_type": "snapshot_storage",
                    "session_id": session_id,
                    "runtime_type": runtime_type,
                    "storage_mb": snapshot_mb,
                },
            )
        )

    return line_items


def build_virtual_computer_billing_hook_payload(
    *,
    runtime_type: Any,
    session_id: Any,
    duration_ms: Any,
    artifact_bytes: Any,
    snapshot_bytes: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_runtime_type = _normalize_runtime_type(runtime_type)
    normalized_session_id = _text(session_id)
    normalized_duration_ms = _non_negative_int(duration_ms)
    normalized_artifact_bytes = _non_negative_int(artifact_bytes)
    normalized_snapshot_bytes = _non_negative_int(snapshot_bytes)

    return {
        "runtime_type": normalized_runtime_type,
        "session_id": normalized_session_id,
        "duration_ms": normalized_duration_ms,
        "artifact_bytes": normalized_artifact_bytes,
        "snapshot_bytes": normalized_snapshot_bytes,
        "credit_line_items": _build_line_items(
            runtime_type=normalized_runtime_type,
            session_id=normalized_session_id,
            duration_ms=normalized_duration_ms,
            artifact_bytes=normalized_artifact_bytes,
            snapshot_bytes=normalized_snapshot_bytes,
            metadata=metadata,
        ),
    }

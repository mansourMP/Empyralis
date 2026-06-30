from __future__ import annotations

from typing import Any, Dict, Optional


def resolve_connector_workspace_scope(
    entry: Dict[str, Any],
    *,
    normalize_workspace_id,
    fallback_workspace_id: Optional[str] = None,
    detail_prefix: str = "Connector",
) -> str:
    workspace_id = str(normalize_workspace_id(entry.get("workspace_id")) or "").strip()
    if workspace_id and workspace_id != "default":
        return workspace_id
    fallback = str(normalize_workspace_id(fallback_workspace_id) or "").strip()
    if fallback and fallback != "default":
        return fallback
    connector_id = str(entry.get("id") or "").strip() or "unknown"
    label = str(entry.get("label") or connector_id).strip() or connector_id
    raise RuntimeError(f"{detail_prefix} '{label}' is not scoped to an explicit workspace.")

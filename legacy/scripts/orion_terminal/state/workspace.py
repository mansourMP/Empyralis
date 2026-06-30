from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .schemas import STATE_SCHEMA_VERSION, WorkspaceState


_SAFE_WORKSPACE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _sanitize_workspace_id(workspace_id: str) -> str:
    raw = str(workspace_id or "default").strip() or "default"
    cleaned = _SAFE_WORKSPACE_RE.sub("_", raw).strip("._")
    return cleaned or "default"


def _state_root() -> Path:
    configured = str(os.getenv("ORION_CLI_STATE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(".orion") / "workspaces"


def workspace_state_path(workspace_id: str) -> Path:
    safe = _sanitize_workspace_id(workspace_id)
    return _state_root() / safe / "terminal_state.json"


def load_workspace_state(workspace_id: str) -> WorkspaceState:
    path = workspace_state_path(workspace_id)
    if not path.exists():
        return WorkspaceState(workspace_id=_sanitize_workspace_id(workspace_id))

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return WorkspaceState(workspace_id=_sanitize_workspace_id(workspace_id))

    state = WorkspaceState.from_dict(_sanitize_workspace_id(workspace_id), payload if isinstance(payload, dict) else {})
    # Forward-compat fallback: if newer file schema appears, keep known fields and reset version marker.
    if int(state.schema_version) != STATE_SCHEMA_VERSION:
        state.schema_version = STATE_SCHEMA_VERSION
    return state


def save_workspace_state(workspace_id: str, state: WorkspaceState) -> str:
    path = workspace_state_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.workspace_id = _sanitize_workspace_id(workspace_id)
    payload = json.dumps(state.to_dict(), indent=2, sort_keys=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return str(path)


from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict


STATE_SCHEMA_VERSION = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PreflightState:
    execution_target_key: str = "local_companion"
    trust_mode_key: str = "auto"
    provider_key: str = ""
    credential_source: str = "runtime"
    model_source: str = "runtime"
    file_scope_key: str = "workspace"
    connectors_choice: str = "later"
    operator_safety_profile_key: str = "default"

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "PreflightState":
        if not isinstance(value, dict):
            return cls()
        return cls(
            execution_target_key=str(value.get("execution_target_key") or "local_companion"),
            trust_mode_key=str(value.get("trust_mode_key") or "auto"),
            provider_key=str(value.get("provider_key") or ""),
            credential_source=str(value.get("credential_source") or "runtime"),
            model_source=str(value.get("model_source") or "runtime"),
            file_scope_key=str(value.get("file_scope_key") or "workspace"),
            connectors_choice=str(value.get("connectors_choice") or "later"),
            operator_safety_profile_key=str(value.get("operator_safety_profile_key") or "default"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_target_key": self.execution_target_key,
            "trust_mode_key": self.trust_mode_key,
            "provider_key": self.provider_key,
            "credential_source": self.credential_source,
            "model_source": self.model_source,
            "file_scope_key": self.file_scope_key,
            "connectors_choice": self.connectors_choice,
            "operator_safety_profile_key": self.operator_safety_profile_key,
        }


@dataclass
class LiveTuiState:
    session_filter: str = ""
    channel_filter: str = ""
    input_route: str = "local_run"
    engine: str = ""

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "LiveTuiState":
        if not isinstance(value, dict):
            return cls()
        return cls(
            session_filter=str(value.get("session_filter") or ""),
            channel_filter=str(value.get("channel_filter") or ""),
            input_route=str(value.get("input_route") or "local_run"),
            engine=str(value.get("engine") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_filter": self.session_filter,
            "channel_filter": self.channel_filter,
            "input_route": self.input_route,
            "engine": self.engine,
        }


@dataclass
class WorkspaceState:
    schema_version: int = STATE_SCHEMA_VERSION
    workspace_id: str = "default"
    updated_at: str = field(default_factory=_now_iso)
    preflight: PreflightState = field(default_factory=PreflightState)
    live_tui: LiveTuiState = field(default_factory=LiveTuiState)

    @classmethod
    def from_dict(cls, workspace_id: str, value: Dict[str, Any]) -> "WorkspaceState":
        if not isinstance(value, dict):
            return cls(workspace_id=workspace_id)
        schema_version = int(value.get("schema_version") or 1)
        preflight = PreflightState.from_dict(value.get("preflight") if isinstance(value.get("preflight"), dict) else {})
        if schema_version < 2:
            if str(preflight.execution_target_key or "").strip().lower() in {"", "auto"}:
                preflight.execution_target_key = "local_companion"
            if str(preflight.trust_mode_key or "").strip().lower() in {"", "guarded"}:
                preflight.trust_mode_key = "auto"
        return cls(
            schema_version=STATE_SCHEMA_VERSION,
            workspace_id=workspace_id,
            updated_at=str(value.get("updated_at") or _now_iso()),
            preflight=preflight,
            live_tui=LiveTuiState.from_dict(value.get("live_tui") if isinstance(value.get("live_tui"), dict) else {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        self.updated_at = _now_iso()
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "updated_at": self.updated_at,
            "preflight": self.preflight.to_dict(),
            "live_tui": self.live_tui.to_dict(),
        }

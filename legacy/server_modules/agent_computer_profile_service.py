from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from server_modules import rust_runtime_kernel_client, workspace_context


PROFILE_ID_PREFIX = "acp_"

ENVIRONMENT_PERSONAL_COMPUTER = "personal_computer"
ENVIRONMENT_DEDICATED_WORKSTATION = "dedicated_workstation"
ENVIRONMENT_CLOUD_COMPUTER = "cloud_computer"
ENVIRONMENT_SELF_HOSTED_RUNTIME = "self_hosted_runtime"
ENVIRONMENT_KINDS = {
    ENVIRONMENT_PERSONAL_COMPUTER,
    ENVIRONMENT_DEDICATED_WORKSTATION,
    ENVIRONMENT_CLOUD_COMPUTER,
    ENVIRONMENT_SELF_HOSTED_RUNTIME,
}
ENVIRONMENT_ALIASES = {
    "personal": ENVIRONMENT_PERSONAL_COMPUTER,
    "my_computer": ENVIRONMENT_PERSONAL_COMPUTER,
    "local": ENVIRONMENT_PERSONAL_COMPUTER,
    "local_computer": ENVIRONMENT_PERSONAL_COMPUTER,
    "dedicated": ENVIRONMENT_DEDICATED_WORKSTATION,
    "workstation": ENVIRONMENT_DEDICATED_WORKSTATION,
    "mac_mini": ENVIRONMENT_DEDICATED_WORKSTATION,
    "cloud": ENVIRONMENT_CLOUD_COMPUTER,
    "cloud_agent": ENVIRONMENT_CLOUD_COMPUTER,
    "cloud_computer_agent": ENVIRONMENT_CLOUD_COMPUTER,
    "self_hosted": ENVIRONMENT_SELF_HOSTED_RUNTIME,
    "self_hosted_agent": ENVIRONMENT_SELF_HOSTED_RUNTIME,
}

HEALTH_PENDING = "pending"
HEALTH_ONLINE = "online"
HEALTH_OFFLINE = "offline"
HEALTH_UNHEALTHY = "unhealthy"
HEALTH_REVOKED = "revoked"
HEALTH_STATES = {
    HEALTH_PENDING,
    HEALTH_ONLINE,
    HEALTH_OFFLINE,
    HEALTH_UNHEALTHY,
    HEALTH_REVOKED,
}

RECORDING_OFF = "off"
RECORDING_SESSION_ONLY = "session_only"
RECORDING_ALWAYS = "always"
RECORDING_POLICIES = {
    RECORDING_OFF,
    RECORDING_SESSION_ONLY,
    RECORDING_ALWAYS,
}


class AgentComputerProfileError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AgentComputerProfile:
    profile_id: str
    workspace_id: str
    owner_user_id: str
    policy_id: str
    machine_label: str
    environment_kind: str
    gateway_id: Optional[str] = None
    runtime_attachment_id: Optional[str] = None
    runtime_profile_id: Optional[str] = None
    dedicated_to_agent: bool = False
    health_state: str = HEALTH_PENDING
    last_seen_at: Optional[str] = None
    recording_policy: str = RECORDING_SESSION_ONLY
    filesystem_roots: tuple[str, ...] = field(default_factory=tuple)
    browser_profiles: tuple[str, ...] = field(default_factory=tuple)
    channel_access: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "workspace_id": self.workspace_id,
            "owner_user_id": self.owner_user_id,
            "policy_id": self.policy_id,
            "machine_label": self.machine_label,
            "environment_kind": self.environment_kind,
            "gateway_id": self.gateway_id,
            "runtime_attachment_id": self.runtime_attachment_id,
            "runtime_profile_id": self.runtime_profile_id,
            "dedicated_to_agent": self.dedicated_to_agent,
            "health_state": self.health_state,
            "last_seen_at": self.last_seen_at,
            "recording_policy": self.recording_policy,
            "filesystem_roots": list(self.filesystem_roots),
            "browser_profiles": list(self.browser_profiles),
            "channel_access": list(self.channel_access),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _ordered_strings(values: Iterable[Any]) -> tuple[str, ...]:
    out: List[str] = []
    for item in values:
        token = _coerce_text(item)
        if token and token not in out:
            out.append(token)
    return tuple(out)


def _generate_profile_id() -> str:
    return PROFILE_ID_PREFIX + uuid.uuid4().hex


def normalize_environment_kind(value: Any, *, default: str = ENVIRONMENT_PERSONAL_COMPUTER) -> str:
    token = _coerce_text(value).lower().replace(" ", "_").replace("-", "_")
    token = ENVIRONMENT_ALIASES.get(token, token)
    if token in ENVIRONMENT_KINDS:
        return token
    if default in ENVIRONMENT_KINDS:
        return default
    return ENVIRONMENT_PERSONAL_COMPUTER


def normalize_health_state(value: Any, *, default: str = HEALTH_PENDING) -> str:
    token = _coerce_text(value).lower().replace(" ", "_").replace("-", "_")
    if token in HEALTH_STATES:
        return token
    if default in HEALTH_STATES:
        return default
    return HEALTH_PENDING


def normalize_recording_policy(value: Any, *, default: str = RECORDING_SESSION_ONLY) -> str:
    token = _coerce_text(value).lower().replace(" ", "_").replace("-", "_")
    if token in RECORDING_POLICIES:
        return token
    if default in RECORDING_POLICIES:
        return default
    return RECORDING_SESSION_ONLY


def _state_file(workspace_id: str) -> Path:
    return workspace_context.workspace_scope_dir(workspace_id) / "agent_computer_profiles.json"


def _default_state() -> Dict[str, Any]:
    return {"version": 1, "updated_at": _utc_now_iso(), "profiles": {}}


def _read_state(workspace_id: str) -> Dict[str, Any]:
    path = _state_file(workspace_id)
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to read agent computer profile state.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("Agent computer profile state must be an object.")
    if not isinstance(raw.get("profiles"), dict):
        raw["profiles"] = {}
    return raw


def _write_state(workspace_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    path = _state_file(workspace_id)
    next_payload = dict(payload)
    next_payload["updated_at"] = _utc_now_iso()
    _enforce_agent_computer_profile_state_decision(workspace_id=workspace_id, payload=next_payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(next_payload, indent=2), encoding="utf-8")
    return next_payload


class AgentComputerProfileRustGateError(AgentComputerProfileError):
    pass


def _enforce_agent_computer_profile_state_decision(
    *,
    workspace_id: str,
    payload: Mapping[str, Any],
) -> None:
    normalized_payload = dict(payload or {})
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
            operation="save_agent_computer_profile_state",
            state_class="agent_computer_profiles",
            workspace_id=_coerce_text(workspace_id),
            actor_id="system",
            status="active",
            payload=normalized_payload,
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = _coerce_text(decision.get("next_action"))
        if next_action != "save_agent_computer_profile_state":
            raise AgentComputerProfileRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise AgentComputerProfileRustGateError(exc.reason) from exc


def normalize_agent_computer_profile(payload: Mapping[str, Any] | None) -> AgentComputerProfile:
    data = dict(payload or {})
    if "autonomy_mode" in data or "profile_mode" in data:
        raise AgentComputerProfileError("AgentComputerProfile describes the machine; autonomy belongs to AgentComputerPolicy.")

    workspace_id = _coerce_text(data.get("workspace_id"))
    policy_id = _coerce_text(data.get("policy_id"))
    environment_kind = normalize_environment_kind(data.get("environment_kind"))
    gateway_id = _coerce_text(data.get("gateway_id")) or None
    if not workspace_id:
        raise AgentComputerProfileError("workspace_id is required.")
    if not policy_id:
        raise AgentComputerProfileError("policy_id is required.")
    if environment_kind in {ENVIRONMENT_PERSONAL_COMPUTER, ENVIRONMENT_DEDICATED_WORKSTATION} and not gateway_id:
        raise AgentComputerProfileError(f"{environment_kind} requires a bound gateway_id.")

    now = _utc_now_iso()
    return AgentComputerProfile(
        profile_id=_coerce_text(data.get("profile_id")) or _generate_profile_id(),
        workspace_id=workspace_id,
        owner_user_id=_coerce_text(data.get("owner_user_id")),
        policy_id=policy_id,
        machine_label=_coerce_text(data.get("machine_label")) or environment_kind.replace("_", " ").title(),
        environment_kind=environment_kind,
        gateway_id=gateway_id,
        runtime_attachment_id=_coerce_text(data.get("runtime_attachment_id")) or None,
        runtime_profile_id=_coerce_text(data.get("runtime_profile_id")) or None,
        dedicated_to_agent=bool(data.get("dedicated_to_agent")),
        health_state=normalize_health_state(data.get("health_state")),
        last_seen_at=_coerce_text(data.get("last_seen_at")) or None,
        recording_policy=normalize_recording_policy(data.get("recording_policy")),
        filesystem_roots=_ordered_strings(data.get("filesystem_roots") or []),
        browser_profiles=_ordered_strings(data.get("browser_profiles") or []),
        channel_access=_ordered_strings(data.get("channel_access") or []),
        created_at=_coerce_text(data.get("created_at")) or now,
        updated_at=_coerce_text(data.get("updated_at")) or now,
    )


def create_agent_computer_profile(payload: Mapping[str, Any]) -> AgentComputerProfile:
    profile = normalize_agent_computer_profile(payload)
    try:
        state = _read_state(profile.workspace_id)
        profiles = state.setdefault("profiles", {})
        if profile.profile_id in profiles:
            raise AgentComputerProfileError(f"Agent computer profile already exists: {profile.profile_id}")
        profiles[profile.profile_id] = profile.as_dict()
        _write_state(profile.workspace_id, state)
    except AgentComputerProfileError:
        raise
    except Exception as exc:
        raise RuntimeError("Failed to persist agent computer profile.") from exc
    return profile


def upsert_agent_computer_profile(payload: Mapping[str, Any]) -> AgentComputerProfile:
    profile = normalize_agent_computer_profile(payload)
    try:
        state = _read_state(profile.workspace_id)
        profiles = state.setdefault("profiles", {})
        existing = profiles.get(profile.profile_id)
        next_payload = profile.as_dict()
        if isinstance(existing, dict):
            next_payload["created_at"] = _coerce_text(existing.get("created_at")) or profile.created_at
        profiles[profile.profile_id] = next_payload
        _write_state(profile.workspace_id, state)
    except AgentComputerProfileError:
        raise
    except Exception as exc:
        raise RuntimeError("Failed to persist agent computer profile.") from exc
    return normalize_agent_computer_profile(next_payload)


def get_agent_computer_profile(*, workspace_id: str, profile_id: str) -> AgentComputerProfile | None:
    workspace = _coerce_text(workspace_id)
    profile_token = _coerce_text(profile_id)
    if not workspace or not profile_token:
        return None
    raw = _read_state(workspace).get("profiles", {}).get(profile_token)
    if not isinstance(raw, dict):
        return None
    profile = normalize_agent_computer_profile(raw)
    if profile.workspace_id != workspace:
        return None
    return profile


def list_agent_computer_profiles(*, workspace_id: str) -> List[Dict[str, Any]]:
    workspace = _coerce_text(workspace_id)
    if not workspace:
        return []
    profiles = []
    for raw in _read_state(workspace).get("profiles", {}).values():
        if not isinstance(raw, dict):
            continue
        profile = normalize_agent_computer_profile(raw)
        if profile.workspace_id == workspace:
            profiles.append(profile.as_dict())
    return sorted(profiles, key=lambda item: (str(item.get("machine_label") or ""), str(item.get("profile_id") or "")))


def update_agent_computer_profile_health(
    *,
    workspace_id: str,
    profile_id: str,
    health_state: Any,
    last_seen_at: Any = None,
) -> AgentComputerProfile:
    workspace = _coerce_text(workspace_id)
    token = _coerce_text(profile_id)
    state = _read_state(workspace)
    raw = state.get("profiles", {}).get(token)
    if not isinstance(raw, dict):
        raise AgentComputerProfileError("Agent computer profile not found.")
    next_payload = dict(raw)
    next_payload["health_state"] = normalize_health_state(health_state)
    next_payload["last_seen_at"] = _coerce_text(last_seen_at) or _utc_now_iso()
    next_payload["updated_at"] = _utc_now_iso()
    profile = normalize_agent_computer_profile(next_payload)
    state["profiles"][token] = profile.as_dict()
    try:
        _write_state(workspace, state)
    except Exception as exc:
        raise RuntimeError("Failed to update agent computer profile.") from exc
    return profile


def agent_computer_profile_from_runtime_attachment(
    attachment: Mapping[str, Any],
    *,
    workspace_id: str,
    owner_user_id: str = "",
    policy_id: str,
) -> AgentComputerProfile:
    item = dict(attachment or {})
    attachment_kind = _coerce_text(item.get("attachment_kind")).lower()
    if attachment_kind == "local_companion":
        environment_kind = ENVIRONMENT_PERSONAL_COMPUTER
    elif attachment_kind == "cloud_computer":
        environment_kind = ENVIRONMENT_CLOUD_COMPUTER
    elif attachment_kind in {"self_hosted_business_node", "self_hosted_runtime"}:
        environment_kind = ENVIRONMENT_SELF_HOSTED_RUNTIME
    else:
        environment_kind = normalize_environment_kind(item.get("environment_kind"), default=ENVIRONMENT_CLOUD_COMPUTER)
    gateway_identity = item.get("gateway_identity") if isinstance(item.get("gateway_identity"), Mapping) else {}
    return normalize_agent_computer_profile(
        {
            "profile_id": item.get("runtime_profile_id") or item.get("runtime_id") or item.get("attachment_id"),
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id or gateway_identity.get("user_id") or "",
            "policy_id": policy_id,
            "machine_label": item.get("label") or item.get("runtime_profile_label") or item.get("machine_id"),
            "environment_kind": environment_kind,
            "gateway_id": gateway_identity.get("gateway_id") or item.get("runtime_id"),
            "runtime_attachment_id": item.get("attachment_id"),
            "runtime_profile_id": item.get("runtime_profile_id"),
            "dedicated_to_agent": environment_kind == ENVIRONMENT_DEDICATED_WORKSTATION,
            "health_state": HEALTH_ONLINE if item.get("healthy") else HEALTH_OFFLINE,
            "last_seen_at": item.get("last_seen_at"),
            "recording_policy": item.get("recording_policy") or RECORDING_SESSION_ONLY,
            "filesystem_roots": item.get("filesystem_roots") or [],
            "browser_profiles": item.get("browser_profiles") or [],
            "channel_access": item.get("channel_access") or [],
        }
    )

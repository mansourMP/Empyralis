from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping

from server_modules import rust_runtime_kernel_client
from server_modules.agent_computer_policy_service import (
    AgentComputerPolicy,
    AUTONOMY_ASK_EVERY_TIME,
    AUTONOMY_EMERGENCY_STOP,
    AUTONOMY_READ_ONLY,
    AUTONOMY_SAFE_AUTOPILOT,
    AUTONOMY_TRUSTED_WORKSTATION,
    AUTONOMY_MODES,
    CAPABILITY_BROWSER_READ,
    CAPABILITY_BROWSER_CLICK,
    CAPABILITY_BROWSER_FORM_SUBMIT,
    CAPABILITY_SCREEN_READ,
    CAPABILITY_FILE_METADATA,
    CAPABILITY_FILE_READ,
    CAPABILITY_FILE_WRITE,
    CAPABILITY_FILE_DELETE,
    CAPABILITY_APP_CONTROL,
    CAPABILITY_TERMINAL_COMMAND,
    CAPABILITY_TERMINAL_APPROVED_SCRIPT,
    CAPABILITY_COMMUNICATION_DRAFT,
    CAPABILITY_COMMUNICATION_SEND,
    CAPABILITY_MEMORY_READ,
    CAPABILITY_MEMORY_WRITE,
    CAPABILITY_SCHEDULER_CREATE,
    CAPABILITY_NOTIFICATION_SEND,
    CAPABILITY_VISION_SCREEN,
    CAPABILITY_CREDENTIAL_ACCESS,
    CAPABILITY_INSTALL_SOFTWARE,
    CAPABILITY_INSTALL_EXTENSION,
    CAPABILITY_CHANGE_SYSTEM_SETTING,
    CAPABILITY_CHANGE_PERMISSION,
    CAPABILITY_PAYMENT,
    CAPABILITY_PRODUCTION_DEPLOY,
    CAPABILITY_CLOUD_STORAGE_ACCESS,
    CAPABILITY_CLASSES,
    build_default_agent_computer_policy,
    normalize_agent_computer_policy,
)

AUTONOMY_YOLO = "yolo"
AUTONOMY_YOLO_ALIASES = {"full_send", "full-send", "fullsend", "no_limits", "nolimits", "unrestricted"}

PRESET_YOLO = "yolo"
PRESET_CAUTIOUS = "cautious"
PRESET_DENY_ALL = "deny_all"

ALL_PRESETS = {PRESET_YOLO, PRESET_CAUTIOUS, PRESET_DENY_ALL}
PRESET_ALIASES: dict[str, str] = {
    "full-send": PRESET_YOLO,
    "full_send": PRESET_YOLO,
    "full send": PRESET_YOLO,
    "fullsend": PRESET_YOLO,
    "send_it": PRESET_YOLO,
    "sendit": PRESET_YOLO,
    "send it": PRESET_YOLO,
    "no-limits": PRESET_YOLO,
    "no_limits": PRESET_YOLO,
    "nolimits": PRESET_YOLO,
    "no limits": PRESET_YOLO,
    "unrestricted": PRESET_YOLO,
    "balanced": PRESET_CAUTIOUS,
    "moderate": PRESET_CAUTIOUS,
    "safe": PRESET_CAUTIOUS,
    "paranoid": PRESET_DENY_ALL,
    "blocked": PRESET_DENY_ALL,
    "locked": PRESET_DENY_ALL,
    "lockdown": PRESET_DENY_ALL,
    "kill": PRESET_DENY_ALL,
    "stop": PRESET_DENY_ALL,
}
_POLICY_PRESET_NEXT_ACTION = "apply_policy_preset"


class PolicyPresetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PolicyPreset:
    preset_id: str
    label: str
    description: str
    autonomy_mode: str
    allowed_capabilities: tuple[str, ...] = field(default_factory=tuple)
    approval_required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    blocked_capabilities: tuple[str, ...] = field(default_factory=tuple)
    terminal_policy: str = "blocked"
    external_message_policy: str = "draft_only"
    credential_policy: str = "never_expose"
    screenshot_retention: str = "off"
    cloud_storage_policy: str = "approval_required"
    browser_access_policy: str = "approval_required"
    app_access_policy: str = "approval_required"
    network_policy: str = "approval_required"
    max_runtime_seconds: int = 0
    max_budget_cents: int = 0
    emergency_stop_enabled: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "label": self.label,
            "description": self.description,
            "autonomy_mode": self.autonomy_mode,
            "allowed_capabilities": list(self.allowed_capabilities),
            "approval_required_capabilities": list(self.approval_required_capabilities),
            "blocked_capabilities": list(self.blocked_capabilities),
            "terminal_policy": self.terminal_policy,
            "external_message_policy": self.external_message_policy,
            "credential_policy": self.credential_policy,
            "screenshot_retention": self.screenshot_retention,
            "cloud_storage_policy": self.cloud_storage_policy,
            "browser_access_policy": self.browser_access_policy,
            "app_access_policy": self.app_access_policy,
            "network_policy": self.network_policy,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_budget_cents": self.max_budget_cents,
            "emergency_stop_enabled": self.emergency_stop_enabled,
        }

    def to_agent_computer_policy(self) -> AgentComputerPolicy:
        return AgentComputerPolicy(
            policy_id=f"preset:{self.preset_id}",
            policy_version=1,
            autonomy_mode=self.autonomy_mode,
            allowed_capabilities=self.allowed_capabilities,
            approval_required_capabilities=self.approval_required_capabilities,
            blocked_capabilities=self.blocked_capabilities,
            terminal_policy=self.terminal_policy,
            external_message_policy=self.external_message_policy,
            credential_policy=self.credential_policy,
            screenshot_retention=self.screenshot_retention,
            cloud_storage_policy=self.cloud_storage_policy,
            browser_access_policy=self.browser_access_policy,
            app_access_policy=self.app_access_policy,
            network_policy=self.network_policy,
            max_runtime_seconds=self.max_runtime_seconds,
            max_budget_cents=self.max_budget_cents,
            emergency_stop_enabled=self.emergency_stop_enabled,
        )


_ALL_CAPABILITIES = tuple(sorted(CAPABILITY_CLASSES))
_SAFE_READ_ONLY = tuple(sorted({
    CAPABILITY_BROWSER_READ,
    CAPABILITY_SCREEN_READ,
    CAPABILITY_FILE_METADATA,
    CAPABILITY_FILE_READ,
    CAPABILITY_MEMORY_READ,
    CAPABILITY_NOTIFICATION_SEND,
    CAPABILITY_VISION_SCREEN,
}))
_READS_PLUS_DRAFT = tuple(sorted(set(_SAFE_READ_ONLY) | {CAPABILITY_COMMUNICATION_DRAFT}))
_EMPTY_CAPABILITIES: tuple[str, ...] = tuple()


def _build_yolo_preset() -> PolicyPreset:
    return PolicyPreset(
        preset_id=PRESET_YOLO,
        label="YOLO",
        description=(
            "Maximum autonomy — no safety filters, no approvals, all tools allowed, "
            "full network access, unrestricted terminal and browser. "
            "Use only in fully trusted environments."
        ),
        autonomy_mode=AUTONOMY_YOLO,
        allowed_capabilities=_ALL_CAPABILITIES,
        approval_required_capabilities=_EMPTY_CAPABILITIES,
        blocked_capabilities=_EMPTY_CAPABILITIES,
        terminal_policy="allow",
        external_message_policy="approved_contacts",
        credential_policy="allow",
        screenshot_retention="session_only",
        cloud_storage_policy="allow",
        browser_access_policy="allow",
        app_access_policy="allow",
        network_policy="allow",
        max_runtime_seconds=0,
        max_budget_cents=0,
        emergency_stop_enabled=True,
    )


def _build_cautious_preset() -> PolicyPreset:
    approved_for_cautious = tuple(sorted({
        CAPABILITY_BROWSER_CLICK,
        CAPABILITY_BROWSER_FORM_SUBMIT,
        CAPABILITY_FILE_READ,
        CAPABILITY_FILE_WRITE,
        CAPABILITY_TERMINAL_COMMAND,
        CAPABILITY_COMMUNICATION_SEND,
        CAPABILITY_MEMORY_WRITE,
        CAPABILITY_SCHEDULER_CREATE,
        CAPABILITY_CLOUD_STORAGE_ACCESS,
    }))
    blocked_for_cautious = tuple(sorted({
        CAPABILITY_CREDENTIAL_ACCESS,
        CAPABILITY_INSTALL_SOFTWARE,
        CAPABILITY_INSTALL_EXTENSION,
        CAPABILITY_CHANGE_SYSTEM_SETTING,
        CAPABILITY_CHANGE_PERMISSION,
        CAPABILITY_PAYMENT,
        CAPABILITY_PRODUCTION_DEPLOY,
        CAPABILITY_FILE_DELETE,
        CAPABILITY_APP_CONTROL,
        CAPABILITY_TERMINAL_APPROVED_SCRIPT,
    }))
    return PolicyPreset(
        preset_id=PRESET_CAUTIOUS,
        label="Cautious",
        description=(
            "Balanced safety — read operations are auto-allowed, "
            "write and execute actions require approval, "
            "critical system changes are blocked. "
            "Safe default for most workspaces."
        ),
        autonomy_mode=AUTONOMY_ASK_EVERY_TIME,
        allowed_capabilities=_READS_PLUS_DRAFT,
        approval_required_capabilities=approved_for_cautious,
        blocked_capabilities=blocked_for_cautious,
        terminal_policy="review_required",
        external_message_policy="draft_only",
        credential_policy="never_expose",
        screenshot_retention="off",
        cloud_storage_policy="approval_required",
        browser_access_policy="approval_required",
        app_access_policy="approval_required",
        network_policy="approval_required",
        max_runtime_seconds=600,
        max_budget_cents=500,
        emergency_stop_enabled=True,
    )


def _build_deny_all_preset() -> PolicyPreset:
    return PolicyPreset(
        preset_id=PRESET_DENY_ALL,
        label="Deny All",
        description=(
            "Complete lockdown — no tool execution allowed, read-only replies. "
            "Emergency stop preset. All capabilities blocked."
        ),
        autonomy_mode=AUTONOMY_EMERGENCY_STOP,
        allowed_capabilities=_EMPTY_CAPABILITIES,
        approval_required_capabilities=_EMPTY_CAPABILITIES,
        blocked_capabilities=_ALL_CAPABILITIES,
        terminal_policy="blocked",
        external_message_policy="draft_only",
        credential_policy="never_expose",
        screenshot_retention="off",
        cloud_storage_policy="approval_required",
        browser_access_policy="blocked",
        app_access_policy="blocked",
        network_policy="blocked",
        max_runtime_seconds=0,
        max_budget_cents=0,
        emergency_stop_enabled=True,
    )


_PRESET_REGISTRY: dict[str, PolicyPreset] = {
    PRESET_YOLO: _build_yolo_preset(),
    PRESET_CAUTIOUS: _build_cautious_preset(),
    PRESET_DENY_ALL: _build_deny_all_preset(),
}


def resolve_preset_name(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in ALL_PRESETS:
        return raw
    token = raw.replace(" ", "_").replace("-", "_")
    token = PRESET_ALIASES.get(raw, PRESET_ALIASES.get(token, token))
    if token not in ALL_PRESETS:
        raise PolicyPresetError(
            f"Unknown policy preset '{value}'. Valid presets: {', '.join(sorted(ALL_PRESETS))}"
        )
    return token


def list_presets() -> list[PolicyPreset]:
    return list(_PRESET_REGISTRY.values())


def get_preset(name: Any) -> PolicyPreset:
    preset_id = resolve_preset_name(name)
    return _PRESET_REGISTRY[preset_id]


def apply_preset(name: Any, overrides: Mapping[str, Any] | None = None) -> AgentComputerPolicy:
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "policy-preset",
            {"preset": str(name or "").strip()},
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise PolicyPresetError(str(exc.reason or "policy_preset_denied")) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action and next_action != _POLICY_PRESET_NEXT_ACTION:
        raise PolicyPresetError(f"unexpected_next_action:{next_action}")
    policy_payload = decision.get("policy") if isinstance(decision.get("policy"), Mapping) else {}
    policy = normalize_agent_computer_policy(dict(policy_payload))
    if overrides:
        return normalize_agent_computer_policy({**policy.as_dict(), **dict(overrides)})
    return policy


def is_yolo_policy(policy: AgentComputerPolicy) -> bool:
    return (
        policy.autonomy_mode == AUTONOMY_YOLO
        or policy.policy_id == "preset:yolo"
        or set(policy.blocked_capabilities) == set()
        and set(policy.approval_required_capabilities) == set()
        and set(policy.allowed_capabilities) == set(CAPABILITY_CLASSES)
    )

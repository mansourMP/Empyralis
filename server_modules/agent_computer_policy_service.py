from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from server_modules.deployed_agent_runtime_contract_service import (
    COMPUTER_SAFETY_ALLOWED_TERMINAL_POLICIES,
    COMPUTER_SAFETY_SAFE_FILESYSTEM_DEFAULTS,
    STUDIO_AGENT_MODE_TEXT,
    normalize_studio_agent_mode,
)
from server_modules import rust_runtime_kernel_client, workspace_context


AUTONOMY_READ_ONLY = "read_only"
AUTONOMY_ASK_EVERY_TIME = "ask_every_time"
AUTONOMY_SAFE_AUTOPILOT = "safe_autopilot"
AUTONOMY_TRUSTED_WORKSTATION = "trusted_workstation"
AUTONOMY_EMERGENCY_STOP = "emergency_stop"
AUTONOMY_YOLO = "yolo"
AUTONOMY_MODES = {
    AUTONOMY_READ_ONLY,
    AUTONOMY_ASK_EVERY_TIME,
    AUTONOMY_SAFE_AUTOPILOT,
    AUTONOMY_TRUSTED_WORKSTATION,
    AUTONOMY_EMERGENCY_STOP,
    AUTONOMY_YOLO,
}
AUTONOMY_ALIASES = {
    "readonly": AUTONOMY_READ_ONLY,
    "read-only": AUTONOMY_READ_ONLY,
    "ask": AUTONOMY_ASK_EVERY_TIME,
    "ask_everytime": AUTONOMY_ASK_EVERY_TIME,
    "ask-every-time": AUTONOMY_ASK_EVERY_TIME,
    "autopilot": AUTONOMY_SAFE_AUTOPILOT,
    "safe": AUTONOMY_SAFE_AUTOPILOT,
    "trusted": AUTONOMY_TRUSTED_WORKSTATION,
    "trusted_workstation_mode": AUTONOMY_TRUSTED_WORKSTATION,
    "stop": AUTONOMY_EMERGENCY_STOP,
    "killed": AUTONOMY_EMERGENCY_STOP,
    "full_send": AUTONOMY_YOLO,
    "full-send": AUTONOMY_YOLO,
    "fullsend": AUTONOMY_YOLO,
    "send_it": AUTONOMY_YOLO,
    "sendit": AUTONOMY_YOLO,
    "no_limits": AUTONOMY_YOLO,
    "nolimits": AUTONOMY_YOLO,
    "unrestricted": AUTONOMY_YOLO,
}

DECISION_ALLOW = "allow"
DECISION_BLOCK = "block"
DECISION_APPROVAL_REQUIRED = "approval_required"  # Approval gates removed — retained for import compatibility
DECISIONS = {DECISION_ALLOW, DECISION_BLOCK, DECISION_APPROVAL_REQUIRED}

CAPABILITY_BROWSER_READ = "browser.read"
CAPABILITY_BROWSER_CLICK = "browser.click"
CAPABILITY_BROWSER_FORM_SUBMIT = "browser.form_submit"
CAPABILITY_SCREEN_READ = "screen.read"
CAPABILITY_FILE_METADATA = "file.metadata"
CAPABILITY_FILE_READ = "file.read"
CAPABILITY_FILE_WRITE = "file.write"
CAPABILITY_FILE_DELETE = "file.delete"
CAPABILITY_APP_CONTROL = "app.control"
CAPABILITY_TERMINAL_COMMAND = "terminal.command"
CAPABILITY_TERMINAL_APPROVED_SCRIPT = "terminal.approved_script"
CAPABILITY_COMMUNICATION_DRAFT = "communication.draft"
CAPABILITY_COMMUNICATION_SEND = "communication.send"
CAPABILITY_MEMORY_READ = "memory.read"
CAPABILITY_MEMORY_WRITE = "memory.write"
CAPABILITY_SCHEDULER_CREATE = "scheduler.create"
CAPABILITY_NOTIFICATION_SEND = "notification.send"
CAPABILITY_VISION_SCREEN = "vision.screen"
CAPABILITY_CREDENTIAL_ACCESS = "credential.access"
CAPABILITY_INSTALL_SOFTWARE = "install.software"
CAPABILITY_INSTALL_EXTENSION = "install.extension"
CAPABILITY_CHANGE_SYSTEM_SETTING = "system.change_setting"
CAPABILITY_CHANGE_PERMISSION = "system.change_permission"
CAPABILITY_PAYMENT = "commerce.payment"
CAPABILITY_PRODUCTION_DEPLOY = "production.deploy"
CAPABILITY_CLOUD_STORAGE_ACCESS = "cloud_storage.access"

CAPABILITY_CLASSES = {
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
}

_SAFE_READ_CAPABILITIES = {
    CAPABILITY_BROWSER_READ,
    CAPABILITY_SCREEN_READ,
    CAPABILITY_FILE_METADATA,
    CAPABILITY_MEMORY_READ,
    CAPABILITY_NOTIFICATION_SEND,
}
_MUTATING_CAPABILITIES = {
    CAPABILITY_BROWSER_CLICK,
    CAPABILITY_BROWSER_FORM_SUBMIT,
    CAPABILITY_FILE_READ,
    CAPABILITY_FILE_WRITE,
    CAPABILITY_FILE_DELETE,
    CAPABILITY_APP_CONTROL,
    CAPABILITY_TERMINAL_COMMAND,
    CAPABILITY_TERMINAL_APPROVED_SCRIPT,
    CAPABILITY_COMMUNICATION_SEND,
    CAPABILITY_MEMORY_WRITE,
    CAPABILITY_SCHEDULER_CREATE,
    CAPABILITY_CLOUD_STORAGE_ACCESS,
}
_CRITICAL_CAPABILITIES = {
    CAPABILITY_CREDENTIAL_ACCESS,
    CAPABILITY_INSTALL_SOFTWARE,
    CAPABILITY_INSTALL_EXTENSION,
    CAPABILITY_CHANGE_SYSTEM_SETTING,
    CAPABILITY_CHANGE_PERMISSION,
    CAPABILITY_PAYMENT,
    CAPABILITY_PRODUCTION_DEPLOY,
}


class AgentComputerPolicyError(ValueError):
    pass


class AgentComputerPolicyRustGateError(AgentComputerPolicyError):
    pass


@dataclass(frozen=True, slots=True)
class AgentComputerPolicy:
    policy_id: str
    policy_version: int
    autonomy_mode: str
    allowed_capabilities: tuple[str, ...] = field(default_factory=tuple)
    blocked_capabilities: tuple[str, ...] = field(default_factory=tuple)
    domain_allowlist: tuple[str, ...] = field(default_factory=tuple)
    filesystem_scope: tuple[str, ...] = field(default_factory=tuple)
    blocked_filesystem_scope: tuple[str, ...] = field(default_factory=tuple)
    terminal_policy: str = "blocked"
    external_message_policy: str = "draft_only"
    credential_policy: str = "never_expose"
    screenshot_retention: str = "off"
    cloud_storage_policy: str = "blocked"
    browser_access_policy: str = "blocked"
    app_access_policy: str = "blocked"
    network_policy: str = "blocked"
    max_runtime_seconds: int = 0
    max_budget_cents: int = 0
    emergency_stop_enabled: bool = True
    approval_ttl_seconds: int = 900
    remembered_approval_rules: tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    sandbox_type: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "autonomy_mode": self.autonomy_mode,
            "allowed_capabilities": list(self.allowed_capabilities),
            "blocked_capabilities": list(self.blocked_capabilities),
            "domain_allowlist": list(self.domain_allowlist),
            "filesystem_scope": list(self.filesystem_scope),
            "blocked_filesystem_scope": list(self.blocked_filesystem_scope),
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
            "approval_ttl_seconds": self.approval_ttl_seconds,
            "remembered_approval_rules": list(self.remembered_approval_rules),
            "sandbox_type": self.sandbox_type,
        }


@dataclass(frozen=True, slots=True)
class AgentComputerPolicyDecision:
    decision: str
    capability: str
    reason: str
    approval_scope: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == DECISION_ALLOW

    @property
    def blocked(self) -> bool:
        return self.decision == DECISION_BLOCK

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "capability": self.capability,
            "reason": self.reason,
            "approval_scope": self.approval_scope,
        }


def normalize_autonomy_mode(value: Any, *, default: str = AUTONOMY_ASK_EVERY_TIME) -> str:
    token = str(value or "").strip().lower().replace(" ", "_")
    token = AUTONOMY_ALIASES.get(token, token)
    if token in AUTONOMY_MODES:
        return token
    if default in AUTONOMY_MODES:
        return default
    return AUTONOMY_ASK_EVERY_TIME


def normalize_capability_class(value: Any) -> str:
    token = str(value or "").strip().lower().replace(" ", "_")
    aliases = {
        "browser.navigate": CAPABILITY_BROWSER_READ,
        "browser.submit": CAPABILITY_BROWSER_FORM_SUBMIT,
        "browser.form.submit": CAPABILITY_BROWSER_FORM_SUBMIT,
        "computer.screen": CAPABILITY_SCREEN_READ,
        "computer.click": CAPABILITY_BROWSER_CLICK,
        "file.list": CAPABILITY_FILE_METADATA,
        "shell.command": CAPABILITY_TERMINAL_COMMAND,
        "terminal.execute": CAPABILITY_TERMINAL_COMMAND,
        "terminal.approved.script": CAPABILITY_TERMINAL_APPROVED_SCRIPT,
        "external.message.send": CAPABILITY_COMMUNICATION_SEND,
        "send.external.message": CAPABILITY_COMMUNICATION_SEND,
        "software.install": CAPABILITY_INSTALL_SOFTWARE,
        "extension.install": CAPABILITY_INSTALL_EXTENSION,
        "permission.change": CAPABILITY_CHANGE_PERMISSION,
        "cloud.storage": CAPABILITY_CLOUD_STORAGE_ACCESS,
        "cloud.storage.access": CAPABILITY_CLOUD_STORAGE_ACCESS,
    }
    token = aliases.get(token, token)
    if token not in CAPABILITY_CLASSES:
        raise AgentComputerPolicyError(f"Unsupported agent computer capability '{value}'.")
    return token


def _ordered_capabilities(values: Iterable[Any]) -> tuple[str, ...]:
    out: List[str] = []
    for item in values:
        token = normalize_capability_class(item)
        if token not in out:
            out.append(token)
    return tuple(out)


def _ordered_strings(values: Iterable[Any]) -> tuple[str, ...]:
    out: List[str] = []
    for item in values:
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token)
    return tuple(out)


def _ordered_dicts(values: Any) -> tuple[Dict[str, Any], ...]:
    if not isinstance(values, list):
        return tuple()
    out: List[Dict[str, Any]] = []
    for item in values:
        if isinstance(item, Mapping):
            out.append(dict(item))
    return tuple(out)


def _bounded_int(value: Any, *, default: int = 0, minimum: int = 0, maximum: int = 2_147_483_647) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_capability_sets(mode: str) -> tuple[set[str], set[str], set[str]]:
    if mode == AUTONOMY_EMERGENCY_STOP:
        return set(), set(), set(CAPABILITY_CLASSES)
    if mode == AUTONOMY_YOLO:
        return set(CAPABILITY_CLASSES), set(), set()
    if mode == AUTONOMY_READ_ONLY:
        return set(_SAFE_READ_CAPABILITIES), set(), set(CAPABILITY_CLASSES - _SAFE_READ_CAPABILITIES)
    if mode == AUTONOMY_ASK_EVERY_TIME:
        return set(_SAFE_READ_CAPABILITIES), set(_MUTATING_CAPABILITIES), set(_CRITICAL_CAPABILITIES)
    if mode == AUTONOMY_SAFE_AUTOPILOT:
        allowed = set(_SAFE_READ_CAPABILITIES) | {CAPABILITY_COMMUNICATION_DRAFT}
        approval = {
            CAPABILITY_BROWSER_CLICK,
            CAPABILITY_BROWSER_FORM_SUBMIT,
            CAPABILITY_FILE_READ,
            CAPABILITY_FILE_WRITE,
            CAPABILITY_TERMINAL_COMMAND,
            CAPABILITY_COMMUNICATION_SEND,
            CAPABILITY_MEMORY_WRITE,
            CAPABILITY_SCHEDULER_CREATE,
            CAPABILITY_CLOUD_STORAGE_ACCESS,
        }
        blocked = set(_CRITICAL_CAPABILITIES) | {CAPABILITY_FILE_DELETE, CAPABILITY_APP_CONTROL}
        return allowed, approval, blocked
    if mode == AUTONOMY_TRUSTED_WORKSTATION:
        allowed = set(_SAFE_READ_CAPABILITIES) | {
            CAPABILITY_BROWSER_CLICK,
            CAPABILITY_BROWSER_FORM_SUBMIT,
            CAPABILITY_APP_CONTROL,
            CAPABILITY_COMMUNICATION_DRAFT,
            CAPABILITY_FILE_READ,
            CAPABILITY_FILE_WRITE,
            CAPABILITY_TERMINAL_COMMAND,
            CAPABILITY_TERMINAL_APPROVED_SCRIPT,
            CAPABILITY_MEMORY_WRITE,
            CAPABILITY_SCHEDULER_CREATE,
        }
        approval = {
            CAPABILITY_COMMUNICATION_SEND,
            CAPABILITY_FILE_DELETE,
            CAPABILITY_CLOUD_STORAGE_ACCESS,
            CAPABILITY_PAYMENT,
            CAPABILITY_CREDENTIAL_ACCESS,
            CAPABILITY_PRODUCTION_DEPLOY,
            CAPABILITY_CHANGE_PERMISSION,
        }
        blocked = {
            CAPABILITY_INSTALL_SOFTWARE,
            CAPABILITY_INSTALL_EXTENSION,
            CAPABILITY_CHANGE_SYSTEM_SETTING,
        }
        return allowed, approval, blocked
    return _default_capability_sets(AUTONOMY_ASK_EVERY_TIME)


def build_default_agent_computer_policy(
    *,
    autonomy_mode: str = AUTONOMY_ASK_EVERY_TIME,
    policy_id: str = "default",
    domain_allowlist: Iterable[Any] = (),
    filesystem_scope: Iterable[Any] = (),
) -> AgentComputerPolicy:
    mode = normalize_autonomy_mode(autonomy_mode)
    allowed, approval, blocked = _default_capability_sets(mode)
    return AgentComputerPolicy(
        policy_id=str(policy_id or f"default:{mode}").strip() or f"default:{mode}",
        policy_version=1,
        autonomy_mode=mode,
        allowed_capabilities=tuple(sorted(allowed)),
        blocked_capabilities=tuple(sorted((blocked | approval) - allowed)),
        domain_allowlist=_ordered_strings(domain_allowlist),
        filesystem_scope=_ordered_strings(filesystem_scope),
        blocked_filesystem_scope=tuple(),
        terminal_policy=(
            "allow"
            if mode in {AUTONOMY_YOLO, AUTONOMY_TRUSTED_WORKSTATION}
            else "blocked"
            if mode in {AUTONOMY_READ_ONLY, AUTONOMY_EMERGENCY_STOP}
            else "review_required"
        ),
        external_message_policy="approved_contacts" if mode in {AUTONOMY_YOLO, AUTONOMY_TRUSTED_WORKSTATION} else "draft_only",
        screenshot_retention="session_only" if mode in {AUTONOMY_YOLO, AUTONOMY_SAFE_AUTOPILOT, AUTONOMY_TRUSTED_WORKSTATION} else "off",
        cloud_storage_policy="allow" if mode == AUTONOMY_YOLO else "blocked",
        browser_access_policy="allow" if mode in {AUTONOMY_YOLO, AUTONOMY_TRUSTED_WORKSTATION} else "blocked",
        app_access_policy="allow" if mode in {AUTONOMY_YOLO, AUTONOMY_TRUSTED_WORKSTATION} else "blocked",
        network_policy="allow" if mode in {AUTONOMY_YOLO, AUTONOMY_TRUSTED_WORKSTATION} else ("allowlist" if domain_allowlist else "blocked"),
        max_runtime_seconds=0,
        max_budget_cents=0,
        emergency_stop_enabled=True,
    )


def normalize_agent_computer_policy(payload: Mapping[str, Any] | None) -> AgentComputerPolicy:
    data = dict(payload or {})
    base = build_default_agent_computer_policy(
        autonomy_mode=data.get("autonomy_mode") or AUTONOMY_ASK_EVERY_TIME,
        policy_id=str(data.get("policy_id") or "custom").strip() or "custom",
        domain_allowlist=list(data.get("domain_allowlist") or []),
        filesystem_scope=list(data.get("filesystem_scope") or []),
    )
    allowed = _ordered_capabilities(data.get("allowed_capabilities") or base.allowed_capabilities)
    blocked = _ordered_capabilities(data.get("blocked_capabilities") or base.blocked_capabilities)
    overlap = set(allowed) & set(blocked)
    if overlap:
        raise AgentComputerPolicyError(f"Capabilities cannot be both allowed and blocked: {sorted(overlap)}")
    return AgentComputerPolicy(
        policy_id=base.policy_id,
        policy_version=max(int(data.get("policy_version") or base.policy_version), 1),
        autonomy_mode=base.autonomy_mode,
        allowed_capabilities=allowed,
        blocked_capabilities=blocked,
        domain_allowlist=base.domain_allowlist,
        filesystem_scope=base.filesystem_scope,
        blocked_filesystem_scope=_ordered_strings(data.get("blocked_filesystem_scope") or data.get("blocked_folders") or []),
        terminal_policy=str(data.get("terminal_policy") or base.terminal_policy).strip() or base.terminal_policy,
        external_message_policy=str(data.get("external_message_policy") or base.external_message_policy).strip() or base.external_message_policy,
        credential_policy=str(data.get("credential_policy") or base.credential_policy).strip() or base.credential_policy,
        screenshot_retention=str(data.get("screenshot_retention") or base.screenshot_retention).strip() or base.screenshot_retention,
        cloud_storage_policy=str(data.get("cloud_storage_policy") or base.cloud_storage_policy).strip() or base.cloud_storage_policy,
        browser_access_policy=str(data.get("browser_access_policy") or base.browser_access_policy).strip() or base.browser_access_policy,
        app_access_policy=str(data.get("app_access_policy") or base.app_access_policy).strip() or base.app_access_policy,
        network_policy=str(data.get("network_policy") or base.network_policy).strip() or base.network_policy,
        max_runtime_seconds=_bounded_int(data.get("max_runtime_seconds"), default=base.max_runtime_seconds, maximum=30 * 24 * 60 * 60),
        max_budget_cents=_bounded_int(data.get("max_budget_cents"), default=base.max_budget_cents, maximum=1_000_000_00),
        emergency_stop_enabled=bool(data.get("emergency_stop_enabled", base.emergency_stop_enabled)),
        approval_ttl_seconds=max(int(data.get("approval_ttl_seconds") or base.approval_ttl_seconds), 60),
        remembered_approval_rules=_ordered_dicts(data.get("remembered_approval_rules")),
        sandbox_type=str(data.get("sandbox_type") or base.sandbox_type).strip() or base.sandbox_type,
    )


def decision_for_capability(policy: AgentComputerPolicy, capability: Any) -> str:
    contract = policy if isinstance(policy, AgentComputerPolicy) else normalize_agent_computer_policy(policy)
    decision = evaluate_agent_computer_request(
        contract,
        capability=capability,
    )
    return decision.decision


def _domain_allowed(policy: AgentComputerPolicy, requested_domain: Any) -> bool:
    domain = str(requested_domain or "").strip().lower()
    if not domain or not policy.domain_allowlist:
        return True
    for allowed in policy.domain_allowlist:
        allowed_domain = str(allowed or "").strip().lower()
        if domain == allowed_domain or domain.endswith(f".{allowed_domain}"):
            return True
    return False


def _path_matches_scope(path: str, scope: str) -> bool:
    clean_path = str(path or "").strip()
    clean_scope = str(scope or "").strip()
    if not clean_path or not clean_scope:
        return False
    normalized_path = clean_path.rstrip("/")
    normalized_scope = clean_scope.rstrip("/")
    return normalized_path == normalized_scope or normalized_path.startswith(f"{normalized_scope}/")


def _path_allowed(policy: AgentComputerPolicy, requested_path: Any) -> bool:
    path = str(requested_path or "").strip()
    if not path:
        return True
    for blocked in policy.blocked_filesystem_scope:
        if _path_matches_scope(path, blocked):
            return False
    explicit_scopes = [
        scope
        for scope in policy.filesystem_scope
        if str(scope or "").startswith(("/", "~"))
    ]
    if not explicit_scopes:
        return True
    return any(_path_matches_scope(path, scope) for scope in explicit_scopes)


def _explicit_filesystem_roots(values: Iterable[Any]) -> List[str]:
    roots: List[str] = []
    for value in values:
        token = str(value or "").strip()
        if not token or token == "*":
            continue
        if token.startswith(("/", "~")) and token not in roots:
            roots.append(token)
    return roots


def _rust_path_containment_decision(
    contract: AgentComputerPolicy,
    *,
    capability: str,
    requested_path: Any,
) -> AgentComputerPolicyDecision | None:
    path = str(requested_path or "").strip()
    if not path or not capability.startswith("file."):
        return None
    allowed_roots = _explicit_filesystem_roots(contract.filesystem_scope)
    blocked_roots = _explicit_filesystem_roots(contract.blocked_filesystem_scope)
    if not allowed_roots and not blocked_roots:
        return None
    if not allowed_roots and path.startswith("/"):
        allowed_roots = ["/"]
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "check-path-containment",
            {
                "path": path,
                "allowed_roots": allowed_roots,
                "blocked_roots": blocked_roots,
            },
        )
    except rust_runtime_kernel_client.RustKernelDecisionError:
        return AgentComputerPolicyDecision(
            decision=DECISION_BLOCK,
            capability=capability,
            reason="filesystem_scope_not_allowed",
        )
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "allow_path_access":
        return AgentComputerPolicyDecision(
            decision=DECISION_BLOCK,
            capability=capability,
            reason="unexpected_next_action:allow_path_access",
        )
    return None


def _rust_policy_decision(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token == "allow":
        return DECISION_ALLOW
    if token in {"require_approval", "approval_required"}:
        return DECISION_BLOCK
    return DECISION_BLOCK


def _rust_policy_reason(value: Any, decision: str) -> str:
    token = str(value or "").strip()
    if decision == DECISION_ALLOW:
        return "policy_allowed"
    if decision == DECISION_BLOCK:
        return "policy_blocked"
    if token == "domain_outside_policy_scope":
        return "domain_not_allowed"
    if token in {"path_outside_policy_scope", "path_blocked_by_policy_scope"}:
        return "filesystem_scope_not_allowed"
    if token == "unknown_capability":
        return "policy_blocked"
    return token or "policy_blocked"


def _expected_policy_next_action(decision: str) -> str:
    if decision == DECISION_ALLOW:
        return "allow_agent_computer_request"
    return ""


def evaluate_agent_computer_request(
    policy: AgentComputerPolicy | Mapping[str, Any] | None,
    *,
    capability: Any,
    requested_domain: Any = None,
    requested_path: Any = None,
) -> AgentComputerPolicyDecision:
    contract = policy if isinstance(policy, AgentComputerPolicy) else normalize_agent_computer_policy(policy)
    token = normalize_capability_class(capability)
    path_decision = _rust_path_containment_decision(
        contract,
        capability=token,
        requested_path=requested_path,
    )
    if path_decision is not None:
        return path_decision
    rust_payload = {
        "policy": contract.as_dict(),
        "capability": token,
        "requested_domain": str(requested_domain or "").strip() or None,
        "requested_path": str(requested_path or "").strip() or None,
    }
    try:
        rust_decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "validate-policy",
            rust_payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        rust_decision = exc.decision
    decision = _rust_policy_decision(rust_decision.get("decision"))
    expected_next_action = _expected_policy_next_action(decision)
    if expected_next_action:
        next_action = str(rust_decision.get("next_action") or "").strip()
        if next_action != expected_next_action:
            return AgentComputerPolicyDecision(
                decision=DECISION_BLOCK,
                capability=token,
                reason=f"unexpected_next_action:{expected_next_action}",
            )
    return AgentComputerPolicyDecision(
        decision=decision,
        capability=token,
        reason=_rust_policy_reason(rust_decision.get("reason"), decision),
        approval_scope=token if decision == DECISION_APPROVAL_REQUIRED else "",
    )


def validate_agent_computer_policy(
    policy: AgentComputerPolicy | Mapping[str, Any] | None,
    *,
    studio_agent_mode: Any = None,
) -> AgentComputerPolicy:
    contract = policy if isinstance(policy, AgentComputerPolicy) else normalize_agent_computer_policy(policy)
    if contract.terminal_policy not in COMPUTER_SAFETY_ALLOWED_TERMINAL_POLICIES:
        raise AgentComputerPolicyError(f"Unsupported terminal policy '{contract.terminal_policy}'.")
    for scope in contract.filesystem_scope:
        if scope in COMPUTER_SAFETY_SAFE_FILESYSTEM_DEFAULTS:
            continue
        if not str(scope).startswith(("/", "~")):
            raise AgentComputerPolicyError(f"Filesystem scope must be explicit, received '{scope}'.")
    for scope in contract.blocked_filesystem_scope:
        if not str(scope).startswith(("/", "~")):
            raise AgentComputerPolicyError(f"Blocked filesystem scope must be explicit, received '{scope}'.")
    if contract.network_policy not in {"allow", "allowlist", "approval_required", "blocked"}:
        raise AgentComputerPolicyError(f"Unsupported network policy '{contract.network_policy}'.")
    if contract.browser_access_policy not in {"allow", "approval_required", "blocked"}:
        raise AgentComputerPolicyError(f"Unsupported browser access policy '{contract.browser_access_policy}'.")
    if contract.app_access_policy not in {"allow", "approval_required", "blocked"}:
        raise AgentComputerPolicyError(f"Unsupported app access policy '{contract.app_access_policy}'.")
    if studio_agent_mode is not None:
        mode = normalize_studio_agent_mode(studio_agent_mode, strict=True)
        if mode == STUDIO_AGENT_MODE_TEXT:
            non_read = set(contract.allowed_capabilities) - _SAFE_READ_CAPABILITIES
            non_read |= set(contract.approval_required_capabilities)
            if non_read:
                raise AgentComputerPolicyError("text_agent cannot attach computer automation policy.")
    return contract


def agent_computer_policy_from_deployed_agent_record(record: Mapping[str, Any] | None) -> AgentComputerPolicy:
    data = dict(record or {})
    config = data.get("config") if isinstance(data.get("config"), Mapping) else {}
    metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
    candidate = (
        config.get("agent_computer_policy")
        or metadata.get("agent_computer_policy")
        or config.get("computer_policy")
        or metadata.get("computer_policy")
    )
    if candidate:
        return normalize_agent_computer_policy(candidate)

    automation = config.get("computer_automation") if isinstance(config.get("computer_automation"), Mapping) else {}
    return build_default_agent_computer_policy(
        autonomy_mode=automation.get("autonomy_mode") or AUTONOMY_ASK_EVERY_TIME,
        policy_id=f"deployed-agent:{data.get('id') or data.get('deployed_agent_id') or 'unknown'}",
        domain_allowlist=automation.get("allowed_domains") or (),
        filesystem_scope=automation.get("filesystem_scope") or (),
    )


def _policy_state_file(workspace_id: str) -> Path:
    return workspace_context.workspace_scope_dir(str(workspace_id or "").strip()) / "agent_computer_policies.json"


def _default_policy_state() -> Dict[str, Any]:
    return {"version": 1, "updated_at": _utc_now_iso(), "policies": {}}


def _read_policy_state(workspace_id: str) -> Dict[str, Any]:
    path = _policy_state_file(workspace_id)
    if not path.exists():
        return _default_policy_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to read agent computer policy state.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("Agent computer policy state must be an object.")
    if not isinstance(raw.get("policies"), dict):
        raw["policies"] = {}
    return raw


def _enforce_agent_computer_policy_state_decision(
    *,
    workspace_id: str,
    policy_id: str,
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
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
            operation="upsert_agent_computer_policy",
            state_class="agent_computer_policy",
            workspace_id=str(workspace_id or "").strip(),
            actor_id="system",
            status=str(normalized_payload.get("autonomy_mode") or "policy_update"),
            payload={
                "policy_id": str(policy_id or "").strip(),
                "policy": normalized_payload,
            },
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "upsert_agent_computer_policy":
            raise AgentComputerPolicyRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise AgentComputerPolicyRustGateError(exc.reason) from exc


def _write_policy_state(workspace_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    path = _policy_state_file(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    next_payload = dict(payload)
    next_payload["updated_at"] = _utc_now_iso()
    path.write_text(json.dumps(next_payload, indent=2, sort_keys=True), encoding="utf-8")
    return next_payload


def get_saved_agent_computer_policy(*, workspace_id: str, policy_id: str) -> AgentComputerPolicy | None:
    workspace = str(workspace_id or "").strip()
    token = str(policy_id or "").strip()
    if not workspace or not token:
        return None
    raw = _read_policy_state(workspace).get("policies", {}).get(token)
    if not isinstance(raw, Mapping):
        return None
    return validate_agent_computer_policy(raw)


def upsert_agent_computer_policy(*, workspace_id: str, policy: Mapping[str, Any] | AgentComputerPolicy) -> AgentComputerPolicy:
    workspace = str(workspace_id or "").strip()
    if not workspace:
        raise AgentComputerPolicyError("workspace_id is required.")
    contract = validate_agent_computer_policy(policy)
    try:
        state = _read_policy_state(workspace)
        policies = state.setdefault("policies", {})
        existing = policies.get(contract.policy_id)
        payload = contract.as_dict()
        if isinstance(existing, Mapping):
            payload["created_at"] = str(existing.get("created_at") or "").strip() or _utc_now_iso()
        else:
            payload["created_at"] = _utc_now_iso()
        payload["updated_at"] = _utc_now_iso()
        policies[contract.policy_id] = payload
        _enforce_agent_computer_policy_state_decision(
            workspace_id=workspace,
            policy_id=contract.policy_id,
            payload=payload,
        )
        _write_policy_state(workspace, state)
    except AgentComputerPolicyError:
        raise
    except Exception as exc:
        raise RuntimeError("Failed to persist agent computer policy.") from exc
    return contract


def effective_agent_computer_policy(
    *,
    workspace_id: str,
    policy_id: str,
    runtime_access_mode: Any = None,
) -> tuple[AgentComputerPolicy, bool]:
    saved = get_saved_agent_computer_policy(workspace_id=workspace_id, policy_id=policy_id)
    if saved is not None:
        return saved, True
    mode = str(runtime_access_mode or "").strip().lower()
    autonomy_mode = AUTONOMY_YOLO if mode == "full_access" else AUTONOMY_ASK_EVERY_TIME
    return build_default_agent_computer_policy(autonomy_mode=autonomy_mode, policy_id=policy_id), False

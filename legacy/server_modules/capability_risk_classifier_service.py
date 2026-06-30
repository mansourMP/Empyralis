from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import urlparse

from server_modules import rust_runtime_kernel_client, secret_redaction_service
from server_modules.agent_computer_policy_service import (
    AgentComputerPolicy,
    AUTONOMY_YOLO,
    DECISION_ALLOW,
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCK,
    evaluate_agent_computer_request,
    normalize_agent_computer_policy,
)
from server_modules.agent_computer_profile_service import AgentComputerProfile, normalize_agent_computer_profile


RISK_CLASS_LOW = "low"
RISK_CLASS_MEDIUM = "medium"
RISK_CLASS_HIGH = "high"
RISK_CLASS_CRITICAL = "critical"
RISK_LEVELS = {
    RISK_CLASS_LOW: 1,
    RISK_CLASS_MEDIUM: 2,
    RISK_CLASS_HIGH: 3,
    RISK_CLASS_CRITICAL: 4,
}
_RISK_DECISION_NEXT_ACTIONS = {
    DECISION_ALLOW: "allow_capability_execution",
    DECISION_APPROVAL_REQUIRED: "request_capability_risk_approval",
    DECISION_BLOCK: "block_capability_execution",
}

ACTION_READ = "read"
ACTION_WRITE = "write"
ACTION_EXECUTE = "execute"
ACTION_CLASSES = {ACTION_READ, ACTION_WRITE, ACTION_EXECUTE}

AUDIT_VISIBILITY_SUMMARY = "summary"
AUDIT_VISIBILITY_PAYLOAD_REDACTED = "payload_redacted"
AUDIT_VISIBILITY_SECURITY_EVENT = "security_event"

RETENTION_SHORT = "short"
RETENTION_STANDARD = "standard"
RETENTION_SECURITY = "security"

KILL_STATES = {"active", "killed", "kill", "paused", "suspended", "emergency_stop", "workspace_emergency_stop"}
UNHEALTHY_PROFILE_STATES = {"offline", "unhealthy", "revoked"}

CAPABILITY_ALIASES = {
    "browser.session.start": "browser.read",
    "browser.session.action": "browser.click",
    "browser.session.takeover": "app.control",
    "browser.session.resume": "browser.read",
    "browser.session.interrupt": "app.control",
    "computer_control.click": "browser.click",
    "computer_control.type": "browser.form_submit",
    "computer_control.key": "browser.form_submit",
    "computer_control.clipboard_write": "browser.form_submit",
    "computer_control.clipboard_read": "credential.access",
    "computer_control.list_windows": "app.control",
    "computer_control.list_apps": "app.control",
    "computer_control.notify": "notification.send",
    "computer_control.speak": "notification.send",
    "computer_control.launch": "app.control",
    "computer_control.launch_app": "app.control",
    "computer_control.applescript": "terminal.command",
    "screenshot.capture": "screen.read",
    "system.presence": "screen.read",
    "screen.read": "screen.read",
    "file.list": "file.metadata",
    "file.read": "file.read",
    "file.write": "file.write",
    "file.delete": "file.delete",
    "shell.command": "terminal.command",
    "terminal.execute": "terminal.command",
    "message.draft": "communication.draft",
    "message.send": "communication.send",
}

RISKY_BROWSER_ACTIONS = {
    "click",
    "fill",
    "select",
    "upload_files",
    "execute_js",
    "download_file",
    "new_tab",
    "close_tab",
}


class CapabilityRiskClassifierError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilityRiskDecision:
    decision_id: str
    policy_version: int
    risk_level: int
    risk_class: str
    action_class: str
    capability: str
    target_summary: str
    decision: str
    approval_scopes_required: tuple[str, ...] = field(default_factory=tuple)
    blocked_reason: str = ""
    audit_visibility: str = AUDIT_VISIBILITY_SUMMARY
    recording_required: bool = False
    retention_class: str = RETENTION_STANDARD
    cacheable: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "policy_version": self.policy_version,
            "risk_level": self.risk_level,
            "risk_class": self.risk_class,
            "action_class": self.action_class,
            "capability": self.capability,
            "target_summary": self.target_summary,
            "decision": self.decision,
            "approval_scopes_required": list(self.approval_scopes_required),
            "blocked_reason": self.blocked_reason,
            "audit_visibility": self.audit_visibility,
            "recording_required": self.recording_required,
            "retention_class": self.retention_class,
            "cacheable": self.cacheable,
        }


def _decision_id() -> str:
    return "crd_" + uuid.uuid4().hex


def normalize_action_class(value: Any, *, default: str = ACTION_READ) -> str:
    token = str(value or "").strip().lower()
    if token in ACTION_CLASSES:
        return token
    return default


def normalize_capability_for_risk(capability: Any) -> str:
    token = str(capability or "").strip().lower()
    token = CAPABILITY_ALIASES.get(token, token)
    if token == "browser.read":
        return token
    try:
        normalized = evaluate_agent_computer_request(None, capability=token).capability
    except Exception as exc:
        raise CapabilityRiskClassifierError(f"Unsupported capability for risk classification: {capability}") from exc
    return normalized


def _target_domain(target_url: Any = None, target: Any = None, payload: Any = None) -> str:
    candidates = [target_url, target]
    if isinstance(payload, Mapping):
        candidates.extend([payload.get("url"), payload.get("target_url"), payload.get("domain")])
    for candidate in candidates:
        raw = str(candidate or "").strip()
        if not raw:
            continue
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = str(parsed.hostname or "").strip().lower()
        if host:
            return host
    return ""


def _target_summary(*, target_url: Any = None, target_path: Any = None, target_channel: Any = None, payload: Any = None) -> str:
    parts = []
    if target_url:
        parts.append(f"url={target_url}")
    if target_path:
        parts.append(f"path={target_path}")
    if target_channel:
        parts.append(f"channel={target_channel}")
    if isinstance(payload, Mapping):
        for key in ("url", "target_url", "path", "channel", "recipient", "action"):
            if key in payload:
                parts.append(f"{key}={payload.get(key)}")
    summary = "; ".join(str(part) for part in parts if str(part or "").strip()) or "unspecified"
    return secret_redaction_service.redact_text(summary)[:500]


def _policy(policy: AgentComputerPolicy | Mapping[str, Any] | None) -> AgentComputerPolicy:
    return policy if isinstance(policy, AgentComputerPolicy) else normalize_agent_computer_policy(policy)


def _profile(computer_profile: AgentComputerProfile | Mapping[str, Any] | None) -> AgentComputerProfile | None:
    if computer_profile is None:
        return None
    return computer_profile if isinstance(computer_profile, AgentComputerProfile) else normalize_agent_computer_profile(computer_profile)


def _audit_visibility(risk_class: str) -> str:
    if risk_class == RISK_CLASS_CRITICAL:
        return AUDIT_VISIBILITY_SECURITY_EVENT
    if risk_class in {RISK_CLASS_HIGH, RISK_CLASS_MEDIUM}:
        return AUDIT_VISIBILITY_PAYLOAD_REDACTED
    return AUDIT_VISIBILITY_SUMMARY


def _retention_class(risk_class: str) -> str:
    if risk_class in {RISK_CLASS_CRITICAL, RISK_CLASS_HIGH}:
        return RETENTION_SECURITY
    if risk_class == RISK_CLASS_MEDIUM:
        return RETENTION_STANDARD
    return RETENTION_SHORT


def _recording_required(capability: str, risk_class: str) -> bool:
    if risk_class in {RISK_CLASS_HIGH, RISK_CLASS_CRITICAL}:
        return True
    return capability.startswith("browser.") or capability in {"screen.read", "app.control"}


def _risk_class_from_rust(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in RISK_LEVELS else RISK_CLASS_CRITICAL


def _risk_decision_from_rust(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"allow", DECISION_ALLOW}:
        return DECISION_ALLOW
    if token in {"require_approval", "approval_required", DECISION_APPROVAL_REQUIRED}:
        return DECISION_APPROVAL_REQUIRED
    return DECISION_BLOCK


def _audit_visibility_from_rust(value: Any, risk_class: str, decision: str) -> str:
    token = str(value or "").strip().lower()
    if decision == DECISION_BLOCK or token == AUDIT_VISIBILITY_SECURITY_EVENT:
        return AUDIT_VISIBILITY_SECURITY_EVENT
    if token == "security":
        return AUDIT_VISIBILITY_SECURITY_EVENT if risk_class == RISK_CLASS_CRITICAL else _audit_visibility(risk_class)
    if token in {"payload_redacted", AUDIT_VISIBILITY_PAYLOAD_REDACTED}:
        return AUDIT_VISIBILITY_PAYLOAD_REDACTED
    return AUDIT_VISIBILITY_SECURITY_EVENT


def _retention_class_from_rust(value: Any, risk_class: str) -> str:
    token = str(value or "").strip().lower()
    if token in {RETENTION_SHORT, RETENTION_STANDARD, RETENTION_SECURITY}:
        return token
    if token in {"extended", "security_event"}:
        return RETENTION_SECURITY
    return RETENTION_SECURITY


def _profile_payload(computer_profile: AgentComputerProfile | Mapping[str, Any] | None) -> Dict[str, Any] | None:
    profile = _profile(computer_profile)
    if profile is None:
        return None
    if hasattr(profile, "as_dict"):
        try:
            payload = profile.as_dict()
            return dict(payload) if isinstance(payload, Mapping) else None
        except Exception:
            return None
    if isinstance(profile, Mapping):
        return dict(profile)
    return {
        "health_state": str(getattr(profile, "health_state", "") or "").strip(),
    }


def _rust_blocked_reason(
    *,
    reason: str,
    current_kill_state: Any = None,
    computer_profile: AgentComputerProfile | Mapping[str, Any] | None = None,
) -> str:
    token = str(reason or "").strip()
    if token.startswith("kill_state:") or token.startswith("profile_"):
        return token
    kill_state = str(current_kill_state or "").strip().lower()
    if token == "kill_state_active" and kill_state:
        return f"kill_state:{kill_state}"
    profile = _profile(computer_profile)
    health_state = str(getattr(profile, "health_state", "") or "").strip().lower() if profile else ""
    if token == "computer_profile_unhealthy" and health_state:
        return f"profile_{health_state}"
    if token == "domain_outside_policy_scope":
        return "domain_not_allowed"
    if token in {"path_outside_policy_scope", "path_blocked_by_policy_scope"}:
        return "filesystem_scope_not_allowed"
    return token or "rust_risk_classifier_blocked"


def _decision_from_rust_classifier(
    *,
    rust_decision: Mapping[str, Any],
    contract: AgentComputerPolicy,
    normalized_capability: str,
    normalized_action: str,
    target_summary: str,
    current_kill_state: Any = None,
    computer_profile: AgentComputerProfile | Mapping[str, Any] | None = None,
) -> CapabilityRiskDecision:
    risk_class = _risk_class_from_rust(rust_decision.get("risk_class") or rust_decision.get("risk_level"))
    decision = _risk_decision_from_rust(rust_decision.get("decision"))
    next_action = str(rust_decision.get("next_action") or "").strip()
    expected_next_action = _RISK_DECISION_NEXT_ACTIONS.get(decision, "")
    if not next_action and expected_next_action:
        next_action = expected_next_action
    if next_action != expected_next_action:
        raise CapabilityRiskClassifierError(f"unexpected_next_action:{next_action or 'missing'}")
    scopes = tuple(
        str(item or "").strip()
        for item in list(rust_decision.get("approval_scopes_required") or [])
        if str(item or "").strip()
    )
    if decision == DECISION_APPROVAL_REQUIRED and not scopes:
        scopes = (normalized_capability,)
    blocked_reason = ""
    if decision == DECISION_BLOCK:
        blocked_reason = _rust_blocked_reason(
            reason=str(rust_decision.get("blocked_reason") or rust_decision.get("reason") or ""),
            current_kill_state=current_kill_state,
            computer_profile=computer_profile,
        )
    return CapabilityRiskDecision(
        decision_id=str(rust_decision.get("decision_id") or _decision_id()),
        policy_version=contract.policy_version,
        risk_level=RISK_LEVELS[risk_class],
        risk_class=risk_class,
        action_class=normalized_action,
        capability=normalized_capability,
        target_summary=target_summary,
        decision=decision,
        approval_scopes_required=scopes,
        blocked_reason=blocked_reason,
        audit_visibility=_audit_visibility_from_rust(rust_decision.get("audit_visibility"), risk_class, decision),
        recording_required=bool(rust_decision.get("recording_required", True)),
        retention_class=_retention_class_from_rust(rust_decision.get("retention_class"), risk_class),
        cacheable=bool(rust_decision.get("cacheable")) and contract.autonomy_mode == AUTONOMY_YOLO,
    )


def classify_capability_risk(
    *,
    policy: AgentComputerPolicy | Mapping[str, Any] | None,
    capability: Any,
    action_class: Any = None,
    target_url: Any = None,
    target_path: Any = None,
    target_channel: Any = None,
    payload: Any = None,
    computer_profile: AgentComputerProfile | Mapping[str, Any] | None = None,
    current_kill_state: Any = None,
) -> CapabilityRiskDecision:
    contract = _policy(policy)
    profile = _profile(computer_profile)
    normalized_capability = normalize_capability_for_risk(capability)
    normalized_action = normalize_action_class(action_class)
    summary = _target_summary(
        target_url=target_url,
        target_path=target_path,
        target_channel=target_channel,
        payload=payload,
    )

    rust_payload = {
        "policy": contract.as_dict(),
        "capability": normalized_capability,
        "action_class": normalized_action,
        "target_summary": summary,
        "requested_domain": _target_domain(target_url=target_url, payload=payload),
        "requested_path": str(target_path or "").strip() or None,
        "payload": payload if isinstance(payload, Mapping) else {"value": payload} if payload is not None else {},
        "computer_profile": _profile_payload(profile),
        "current_kill_state": str(current_kill_state or "").strip() or None,
    }
    try:
        rust_decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "classify-risk",
            rust_payload,
            allow_approval_required=True,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        rust_decision = exc.decision
    return _decision_from_rust_classifier(
        rust_decision=rust_decision,
        contract=contract,
        normalized_capability=normalized_capability,
        normalized_action=normalized_action,
        target_summary=summary,
        current_kill_state=current_kill_state,
        computer_profile=profile,
    )


def classify_gateway_tool_risk(
    *,
    policy: AgentComputerPolicy | Mapping[str, Any] | None,
    capability_id: Any,
    arguments: Optional[Mapping[str, Any]] = None,
    computer_profile: AgentComputerProfile | Mapping[str, Any] | None = None,
    current_kill_state: Any = None,
) -> CapabilityRiskDecision:
    args = dict(arguments or {})
    return classify_capability_risk(
        policy=policy,
        capability=capability_id,
        action_class=args.get("action_class"),
        target_url=args.get("url") or args.get("target_url"),
        target_path=args.get("path"),
        target_channel=args.get("channel"),
        payload=args,
        computer_profile=computer_profile,
        current_kill_state=current_kill_state,
    )


def classify_gateway_browser_action_risk(
    *,
    policy: AgentComputerPolicy | Mapping[str, Any] | None,
    browser_action: Any,
    payload: Optional[Mapping[str, Any]] = None,
    computer_profile: AgentComputerProfile | Mapping[str, Any] | None = None,
    reviewed_approval_required: bool = False,
    current_kill_state: Any = None,
) -> CapabilityRiskDecision:
    action = str(browser_action or "").strip().lower()
    if reviewed_approval_required:
        capability = "browser.form_submit"
        action_class = ACTION_WRITE
    elif action in RISKY_BROWSER_ACTIONS:
        capability = "browser.click" if action in {"click", "new_tab", "close_tab"} else "browser.form_submit"
        action_class = ACTION_WRITE
    else:
        capability = "browser.read"
        action_class = ACTION_READ
    args = dict(payload or {})
    args["action"] = action
    return classify_capability_risk(
        policy=policy,
        capability=capability,
        action_class=action_class,
        target_url=args.get("url") or args.get("target_url"),
        payload=args,
        computer_profile=computer_profile,
        current_kill_state=current_kill_state,
    )

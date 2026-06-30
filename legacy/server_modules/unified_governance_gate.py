"""
Unified governance gate — single mandatory choke point for ALL Main Agent actions.

This module WRAPS the existing governance checks; it does NOT reimplement them.
Every execution path (Sage chat, gateway tools, self-hosted node, tool broker)
calls evaluate_action_policy() and receives a structured ActionPolicyDecision.

Architecture note for Option B readiness:
  The function accepts a plain dict/Mapping for policy and profile parameters
  so it can be called with serialized data from a node-side caller in the
  future. The Rust runtime kernel decision functions are the underlying
  authority — this module is the Python-side orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from server_modules import (
    kill_switch_gate,
    safe_mode_service,
    secret_redaction_service,
)
from server_modules.agent_computer_policy_service import (
    DECISION_ALLOW,
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCK,
)
from server_modules.capability_registry import resolve_capability as _resolve_registry_capability

__all__ = [
    "ActionPolicyDecision",
    "evaluate_action_policy",
    "DECISION_ALLOW",
    "DECISION_APPROVAL_REQUIRED",
    "DECISION_BLOCK",
]


@dataclass(frozen=True, slots=True)
class ActionPolicyDecision:
    """Structured decision from the unified governance gate.

    Callers inspect `decision` ("allow" | "approval_required" | "block")
    and act accordingly. The accompanying fields provide full transparency
    for audit trails and approval flows.
    """

    decision: str  # "allow", "approval_required", or "block"
    reason: str
    workspace_id: str = ""
    actor_user_id: str = ""
    agent_id: str = ""

    # Rich context — populated based on what the gate evaluated
    kill_decision: Optional[Dict[str, Any]] = None
    risk_decision: Optional[Dict[str, Any]] = None
    approval_card: Optional[Dict[str, Any]] = None
    remembered_rule: Optional[Dict[str, Any]] = None
    audit_payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == DECISION_ALLOW

    @property
    def approval_required(self) -> bool:
        return self.decision == DECISION_APPROVAL_REQUIRED

    @property
    def blocked(self) -> bool:
        return self.decision == DECISION_BLOCK

    def as_dict(self) -> Dict[str, Any]:
        """Safe serializable form (secrets redacted)."""
        payload = {
            "decision": self.decision,
            "allowed": self.allowed,
            "approval_required": self.approval_required,
            "blocked": self.blocked,
            "reason": self.reason,
            "workspace_id": self.workspace_id,
            "actor_user_id": self.actor_user_id,
            "agent_id": self.agent_id,
            "kill_decision": self.kill_decision,
            "risk_decision": self.risk_decision,
            "approval_card": self.approval_card,
            "remembered_rule": self.remembered_rule,
            "audit_payload": self.audit_payload,
        }
        return secret_redaction_service.sanitize_value(payload)


def evaluate_action_policy(
    *,
    # ── Identity context ──
    workspace_id: str,
    actor_user_id: str = "",
    agent_id: str = "",
    tenant_id: str = "",
    gateway_id: str = "",
    # ── Action context ──
    capability: Any = None,
    action_class: Any = None,
    target_url: Any = None,
    target_path: Any = None,
    target_channel: Any = None,
    payload: Any = None,
    # ── Policy context ──
    policy: Any = None,  # AgentComputerPolicy | Mapping | None
    computer_profile: Any = None,  # AgentComputerProfile | Mapping | None
    consume_approval_memory: bool = True,
    # ── Metadata ──
    surface: str = "",  # "sage_chat" | "gateway_tool" | "self_hosted" | "tool_broker"
) -> ActionPolicyDecision:
    """Single mandatory governance gate for ALL Main Agent actions.

    Evaluates every action through a fixed, ordered pipeline:

      1. Kill switch  — is the system / workspace / agent / gateway stopped?
      2. Safe mode     — is this specific capability disabled at any scope?
      3. Risk + approval — classify risk, check remembered approvals, build
         approval card if needed.

    Steps 1-2 are done here.  Step 3 delegates to the existing
    decide_agent_computer_action() which internally calls the
    capability_risk_classifier and agent_approval_memory_service.

    Returns a structured ActionPolicyDecision.  The caller decides how to
    act on it (raise HTTPException, return approval card, proceed, etc.).

    This function is a WRAPPER — it does not reimplement any existing
    governance logic.  It orchestrates the same functions that were
    previously called separately at each call site.
    """
    # ── Step 1: Kill switch evaluation ──────────────────────────────
    kill_decision = kill_switch_gate.evaluate_kill_switch(
        tenant_id=tenant_id or "",
        workspace_id=workspace_id or "",
        agent_id=agent_id or "",
        gateway_id=gateway_id or "",
    )
    if kill_decision.blocked:
        audit = _build_audit("block", kill_decision.reason, kill_decision=kill_decision)
        return ActionPolicyDecision(
            decision=DECISION_BLOCK,
            reason=kill_decision.reason,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            agent_id=agent_id,
            kill_decision={
                "blocked": kill_decision.blocked,
                "reason": kill_decision.reason,
                "scope": kill_decision.scope,
                "detail": kill_decision.detail,
            },
            audit_payload=audit,
        )

    # ── Step 2: Capability disable state (safe mode / scoped kill) ──
    cap_str = _normalize_capability(capability)
    if cap_str:
        disable_state = safe_mode_service.resolve_capability_disable_state(
            cap_str,
            tenant_id=tenant_id or None,
            workspace_id=workspace_id or None,
            machine_id=gateway_id or None,
        )
        if bool(disable_state.get("disabled")):
            reason = str(
                disable_state.get("reason")
                or f"capability_disabled:{cap_str}"
            )
            audit = _build_audit(
                "block",
                reason,
                kill_decision=kill_decision,
                extra={"disable_state": disable_state},
            )
            return ActionPolicyDecision(
                decision=DECISION_BLOCK,
                reason=reason,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                agent_id=agent_id,
                kill_decision={
                    "blocked": kill_decision.blocked,
                    "reason": kill_decision.reason,
                    "scope": kill_decision.scope,
                    "detail": kill_decision.detail,
                },
                audit_payload=audit,
            )

    # ── Step 3: Risk classification + approval decision ─────────────
    # This single call handles:
    #   - capability risk classification (via classify_capability_risk)
    #   - approval memory matching (if consume_approval_memory=True)
    #   - approval card building (if approval is required)
    #   - policy-based blocking (if risk is too high for autonomy mode)
    #
    # The risk classifier only handles "agent computer" capabilities
    # (shell, file, browser, screen, mouse, keyboard). For other
    # capabilities (web, memory, SaaS connectors), we skip risk
    # classification and return "allow" — those are governed by
    # policy_service.evaluate_tool_policy_decision() separately.
    try:
        from server_modules.agent_computer_approval_decision_service import (
            decide_agent_computer_action,
        )
        approval_decision = decide_agent_computer_action(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id or "owner",
            agent_id=agent_id or "sage",
            policy=policy,
            capability=capability,
            action_class=action_class,
            target_url=target_url,
            target_path=target_path,
            target_channel=target_channel,
            payload=payload,
            computer_profile=computer_profile,
            current_kill_state=(
                "active" if kill_decision.blocked else None
            ),
            consume_approval_memory=consume_approval_memory,
        )
    except Exception:
        # Capability not recognized by the agent-computer risk classifier.
        # Fall back to the capability registry contract check — this mirrors
        # what gateway_approval_service.capability_requires_owner_approval()
        # does in the old gateway path.  We must not return "allow" for a
        # capability that the registry itself considers risky.
        registry_decision = _evaluate_from_registry_contract(
            capability=capability,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            agent_id=agent_id,
            kill_decision=kill_decision,
        )
        if registry_decision is not None:
            return registry_decision

        # Truly unknown capability — no registry contract either.
        # Allow it to proceed; downstream tool-level policy checks
        # (policy_service) still apply.
        audit = _build_audit(
            "allow",
            "unknown_capability_passed_kill_and_safe_mode",
            kill_decision=kill_decision,
        )
        return ActionPolicyDecision(
            decision=DECISION_ALLOW,
            reason="unknown_capability_passed_kill_and_safe_mode",
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            agent_id=agent_id,
            kill_decision={
                "blocked": kill_decision.blocked,
                "reason": kill_decision.reason,
                "scope": kill_decision.scope,
                "detail": kill_decision.detail,
            },
            audit_payload=audit,
        )

    audit = _build_audit(
        approval_decision.decision,
        approval_decision.reason,
        kill_decision=kill_decision,
        risk_decision=approval_decision.risk_decision.as_dict()
        if approval_decision.risk_decision
        else None,
        approval_card=approval_decision.approval_card,
    )

    # ── Post-classifier registry contract check ──────────────────────
    # The risk classifier may return "allow" for a capability (e.g.
    # screenshot.capture → screen.read → safe-read) while the capability
    # registry contract itself requires approval.  Mirror what the old
    # gateway path does: whichever check is stricter wins.
    final_decision = approval_decision.decision
    final_reason = approval_decision.reason
    final_approval_card = approval_decision.approval_card

    if final_decision == DECISION_ALLOW:
        registry_stricter = _registry_requires_stricter(capability)
        if registry_stricter:
            final_decision = DECISION_APPROVAL_REQUIRED
            final_reason = f"registry_contract_requires_approval:{_normalize_capability(capability)}"
            # Build an approval card for the registry-required capability
            final_approval_card = {
                "status": "approval_required",
                "action": _normalize_capability(capability),
                "description": f"{_normalize_capability(capability)} requires owner approval per capability registry.",
                "reason": final_reason,
                "risk_class": "registry_override",
                "approval_scopes_required": ["owner"],
            }
            audit = _build_audit(
                DECISION_APPROVAL_REQUIRED,
                final_reason,
                kill_decision=kill_decision,
                risk_decision=approval_decision.risk_decision.as_dict()
                if approval_decision.risk_decision
                else None,
                approval_card=final_approval_card,
            )

    return ActionPolicyDecision(
        decision=final_decision,
        reason=final_reason,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        agent_id=agent_id,
        kill_decision={
            "blocked": kill_decision.blocked,
            "reason": kill_decision.reason,
            "scope": kill_decision.scope,
            "detail": kill_decision.detail,
        },
        risk_decision=(
            approval_decision.risk_decision.as_dict()
            if approval_decision.risk_decision
            else None
        ),
        approval_card=final_approval_card,
        remembered_rule=approval_decision.remembered_approval_rule,
        audit_payload=audit,
    )


# ── Internal helpers ────────────────────────────────────────────────────

def _normalize_capability(capability: Any) -> str:
    """Extract a normalized capability id string from any form."""
    if not capability:
        return ""
    if isinstance(capability, str):
        return capability.strip().lower()
    # It's a skill/contract object — look for common id attributes
    for attr in ("id", "capability_id", "name", "label"):
        val = getattr(capability, attr, None)
        if val and isinstance(val, str) and val.strip():
            return val.strip().lower()
    return ""


# Capabilities that the risk classifier may classify as low/medium risk (or
# cannot classify at all) but that semantically involve interactive write
# actions — click, fill, execute JS — matching computer_control.click in
# effective risk.  These always require owner approval.
_INTERACTIVE_WRITE_CAPABILITIES = {
    "browser_automation.interactive",
}


def _registry_requires_stricter(capability: Any) -> bool:
    """Check if the capability registry contract requires stricter treatment.

    Returns True if:
    - The capability is an interactive write (browser click/fill/JS) — these
      always require approval, matching computer_control.click.
    - The registry contract says requires_approval=True or risk_level is
      high/critical — even if the risk classifier said "allow".

    This mirrors what gateway_approval_service.capability_requires_owner_approval()
    does, PLUS the semantic override for browser automation.
    """
    cap_str = _normalize_capability(capability)
    if not cap_str:
        return False

    # Semantic override: browser automation can click, fill forms, and
    # execute arbitrary JavaScript — interactive write actions that
    # should always require owner approval, same as native clicks.
    if cap_str in _INTERACTIVE_WRITE_CAPABILITIES:
        return True

    try:
        contract = _resolve_registry_capability(cap_str, enforce_kill_switch=False)
    except Exception:
        return False
    if contract is None:
        return False
    requires_approval = bool(getattr(contract, "requires_approval", False))
    risk_level = str(getattr(contract, "risk_level", "") or "").strip().lower()
    return requires_approval or risk_level in {"high", "critical"}


def _evaluate_from_registry_contract(
    *,
    capability: Any,
    workspace_id: str,
    actor_user_id: str,
    agent_id: str,
    kill_decision: Any,
) -> Optional[ActionPolicyDecision]:
    """Fallback: check the capability registry contract for risky capabilities.

    When the risk classifier doesn't recognize a capability (e.g. registry-level
    IDs like ``shell.execute``, ``computer_control.click``), we fall back to the
    capability registry contract to decide whether approval is required.

    Returns an ActionPolicyDecision, or None if the capability isn't in the
    registry at all (truly unknown).
    """
    cap_str = _normalize_capability(capability)
    if not cap_str:
        return None

    # Semantic override: interactive-write capabilities always require
    # approval even if the registry contract doesn't say so.
    if cap_str in _INTERACTIVE_WRITE_CAPABILITIES:
        reason = f"interactive_write_requires_approval:{cap_str}"
        audit = _build_audit(
            "approval_required",
            reason,
            kill_decision=kill_decision,
            extra={"capability": cap_str, "override": "interactive_write"},
        )
        return ActionPolicyDecision(
            decision=DECISION_APPROVAL_REQUIRED,
            reason=reason,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            agent_id=agent_id,
            kill_decision={
                "blocked": kill_decision.blocked,
                "reason": kill_decision.reason,
                "scope": kill_decision.scope,
                "detail": kill_decision.detail,
            }
            if hasattr(kill_decision, "blocked")
            else None,
            audit_payload=audit,
        )

    try:
        contract = _resolve_registry_capability(cap_str, enforce_kill_switch=False)
    except Exception:
        return None

    if contract is None:
        return None

    requires_approval = bool(getattr(contract, "requires_approval", False))
    risk_level = str(getattr(contract, "risk_level", "") or "").strip().lower()

    if requires_approval or risk_level in {"high", "critical"}:
        reason = f"registry_contract_requires_approval:{cap_str}"
        audit = _build_audit(
            "approval_required",
            reason,
            kill_decision=kill_decision,
            extra={"capability": cap_str, "risk_level": risk_level},
        )
        return ActionPolicyDecision(
            decision=DECISION_APPROVAL_REQUIRED,
            reason=reason,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            agent_id=agent_id,
            kill_decision={
                "blocked": kill_decision.blocked,
                "reason": kill_decision.reason,
                "scope": kill_decision.scope,
                "detail": kill_decision.detail,
            }
            if hasattr(kill_decision, "blocked")
            else None,
            audit_payload=audit,
        )

    # Registry contract exists and says this is safe (low/medium risk,
    # no explicit approval requirement).
    audit = _build_audit(
        "allow",
        f"registry_contract_allows:{cap_str}",
        kill_decision=kill_decision,
        extra={"capability": cap_str, "risk_level": risk_level},
    )
    return ActionPolicyDecision(
        decision=DECISION_ALLOW,
        reason=f"registry_contract_allows:{cap_str}",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        agent_id=agent_id,
        kill_decision={
            "blocked": kill_decision.blocked,
            "reason": kill_decision.reason,
            "scope": kill_decision.scope,
            "detail": kill_decision.detail,
        }
        if hasattr(kill_decision, "blocked")
        else None,
        audit_payload=audit,
    )


def _build_audit(
    decision: str,
    reason: str,
    *,
    kill_decision: Any = None,
    risk_decision: Any = None,
    approval_card: Any = None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a redacted audit payload for the activity ledger."""
    payload: Dict[str, Any] = {
        "decision": decision,
        "reason": reason,
    }
    if kill_decision is not None:
        if hasattr(kill_decision, "blocked"):
            payload["kill_switch"] = {
                "blocked": kill_decision.blocked,
                "scope": getattr(kill_decision, "scope", ""),
                "reason": getattr(kill_decision, "reason", ""),
            }
        elif isinstance(kill_decision, dict):
            payload["kill_switch"] = kill_decision
    if risk_decision is not None:
        payload["risk_decision"] = (
            risk_decision if isinstance(risk_decision, dict)
            else {"summary": str(risk_decision)}
        )
    if approval_card is not None:
        payload["approval_card"] = (
            approval_card if isinstance(approval_card, dict)
            else {"summary": str(approval_card)}
        )
    if extra:
        payload.update(extra)
    return secret_redaction_service.sanitize_value(payload)

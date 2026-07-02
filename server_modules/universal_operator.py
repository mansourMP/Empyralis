from __future__ import annotations

import re
from typing import Any

from server_modules.agent_manifest import AgentManifest, manifest_has_skill
from server_modules import egress_policy
from server_modules import execution_sandbox_service
from server_modules import safe_mode_service
from server_modules import skill_registry
from server_modules import tool_broker


def build_system_prompt(manifest: AgentManifest) -> str:
    bound_skills = [skill.label for skill in skill_registry.list_skill_definitions() if manifest_has_skill(manifest, skill.id)]
    lines = [
        f"You are {manifest.identity.name}.",
        "",
        "Job To Be Done",
        manifest.role.job_to_be_done,
        "",
        "Voice",
        f"{manifest.voice.tone} {manifest.voice.response_style}".strip(),
        "",
        "Mission",
        manifest.bible.mission,
        "",
        "Hard Context",
        manifest.bible.hard_context,
        "",
        "Operational Policy",
        manifest.bible.operational_policy,
        "",
        "Bound Skills",
    ]
    lines.extend([f"- {label}" for label in bound_skills] if bound_skills else ["- No skills are currently bound."])
    return "\n".join(lines)


def _policy_text(manifest: AgentManifest) -> str:
    return " ".join([
        manifest.bible.hard_context,
        manifest.bible.operational_policy,
        manifest.bible.guardrails,
        manifest.bible.escalation_triggers,
    ]).lower()


def _direct_reply(manifest: AgentManifest, goal: str) -> str:
    normalized = str(goal or "").strip()
    if not normalized:
        return f"{manifest.identity.name} is ready."
    if re.search(r"\b(refund|discount|legal|privacy)\b", normalized, re.IGNORECASE):
        return "This request should stay in Owner Mode so the final decision follows the agent policy and approval boundary."
    if re.search(r"\b(order|book|schedule|charge)\b", normalized, re.IGNORECASE):
        return "One clarifying detail is needed before taking that action, to stay within the owner policy."
    return (
        f"{manifest.identity.name} is operating from its owner-authored Bible. "
        "Direct answers are available when the request stays inside that context, or a bound skill can be used when live business facts are required."
    )


def _requires_inventory_live_fact(goal: str) -> bool:
    return skill_registry.detect_skill_need(goal or "") is not None and skill_registry.detect_skill_need(goal or "").id == "inventory-tool"


def _tool_broker_denial_reply(
    *,
    manifest: AgentManifest,
    needed_skill: skill_registry.SkillDefinition,
    error: tool_broker.ToolExecutionDeniedError,
) -> str:
    if error.code == "skill_not_granted":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but the owner has not bound that skill to this manifest yet."
        )
    if error.code == "elevated_action_blocked":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but that capability is not available in the current safety mode. "
            "Switch to a less restrictive mode or rephrase the request."
        )
    if error.code == "skill_disabled":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but that skill is disabled for this workspace right now."
        )
    if error.code == "connector_scope_not_granted":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but the required connector scope is not currently authorized for this run."
        )
    if error.code == "connector_disabled":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but the required connector is disabled by a security control."
        )
    if error.code in {"workspace_paused", "workspace_draining", "workspace_rejected"}:
        return "This workspace is temporarily under an operator incident control and cannot process this request right now."
    if error.code == "connector_paused":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but the required connector is temporarily paused by an operator incident control."
        )
    if error.code == "connector_draining":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but the required connector is draining its backlog before it accepts new work."
        )
    if error.code == "connector_rejected":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but the required connector is temporarily rejecting new work during an incident."
        )
    if error.code in {"workspace_disabled", "agent_disabled"}:
        return "This agent is temporarily disabled by a security control and cannot execute right now."
    if error.code in {"runtime_not_allowed", "runtime_mismatch"}:
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but the current runtime profile does not allow that capability."
        )
    if error.code == "token_expired":
        return "The capability grant expired before the tool call executed. Please try again."
    if error.code == "egress_denied":
        return (
            f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
            "but outbound network policy denied the required destination for this run."
        )
    return (
        f"{manifest.identity.name} recognizes that this request needs {needed_skill.label}, "
        "but the tool broker denied the capability for this run."
    )


def _security_gate_result(*, manifest: AgentManifest, reason: str, violation: str) -> dict[str, Any]:
    return {
        "status": "security_blocked",
        "reply": reason,
        "artifact": None,
        "steps": [
            {"label": "Security control", "detail": reason, "status": "error", "kind": "thinking"},
        ],
        "system_prompt": build_system_prompt(manifest),
        "needed_skill_id": None,
        "critic": {
            "mode": "escalate",
            "reply": reason,
            "violations": [violation],
        },
    }


def _incident_gate_result(
    *,
    manifest: AgentManifest,
    mode: str,
    scope: str,
    reason: str,
) -> dict[str, Any]:
    normalized_mode = str(mode or "pause").strip().lower() or "pause"
    normalized_scope = str(scope or "workspace").strip().lower() or "workspace"
    status = {
        "pause": "paused",
        "drain": "draining",
        "reject": "rejected",
    }.get(normalized_mode, "paused")
    reply = (
        "This workspace is temporarily paused while the owner resolves an incident. Please try again shortly."
        if normalized_mode == "pause"
        else "This workspace is temporarily draining existing work and is not accepting new requests right now."
        if normalized_mode == "drain"
        else "This workspace is temporarily rejecting new work while the owner handles an incident."
    )
    return {
        "status": status,
        "reply": reply,
        "artifact": None,
        "steps": [
            {
                "label": "Incident control",
                "detail": reason or f"{normalized_scope} incident control {normalized_mode} is active.",
                "status": "error",
                "kind": "thinking",
            },
        ],
        "system_prompt": build_system_prompt(manifest),
        "needed_skill_id": None,
        "incident_mode": normalized_mode,
        "incident_scope": normalized_scope,
        "critic": {
            "mode": "escalate",
            "reply": reply,
            "violations": [f"{normalized_scope}_incident_{normalized_mode}"],
        },
    }


def run_policy_critic(
    *,
    manifest: AgentManifest,
    goal: str,
    draft_reply: str,
    skill_id: str | None,
    skill_result: dict[str, Any] | None,
) -> dict[str, Any]:
    violations: list[str] = []
    lower_goal = str(goal or "").lower()
    lower_reply = str(draft_reply or "").lower()
    policy_text = _policy_text(manifest)

    if (
        _requires_inventory_live_fact(lower_goal)
        and re.search(r"\bin stock\b|\bavailable\b|\$\d", lower_reply)
    ):
        items = skill_result.get("items") if isinstance(skill_result, dict) else None
        if not isinstance(items, list) or len(items) == 0:
            violations.append("inventory_claim_without_live_evidence")
            draft_reply = "The live inventory tool must be checked before stock or price can be confirmed. One moment."

    if (
        re.search(r"\b(order|book|schedule|charge)\b", lower_goal)
        and "clarif" in policy_text
        and "?" not in draft_reply
    ):
        violations.append("clarification_required")
        draft_reply = "One clarifying detail is needed before taking that action, to stay within the owner policy."

    if re.search(r"\b(refund|discount|legal|privacy)\b", lower_goal) and "escalat" in policy_text:
        violations.append("owner_escalation_required")
        return {
            "mode": "escalate",
            "reply": "This request should move to Owner Mode before answering — the manifest marks it as an escalation case.",
            "violations": violations,
        }

    if violations:
        return {
            "mode": "rewrite",
            "reply": draft_reply,
            "violations": violations,
        }

    return {
        "mode": "pass",
        "reply": draft_reply,
        "violations": [],
    }


async def execute_customer_turn(
    *,
    manifest: AgentManifest,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    runtime_mode: str = "hosted_secure",
    runtime_profile: dict[str, Any] | None = None,
    privileged_runtime_approved: bool = False,
    active_agent_install_id: str | None = None,
    active_agent_id: str = "",
) -> dict[str, Any]:
    normalized_runtime_mode = str(runtime_mode or "").strip().lower() or "hosted_secure"
    machine_policy = safe_mode_service.resolve_machine_policy_status(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if bool((machine_policy.get("kill_switch") or {}).get("active")):
        return _security_gate_result(
            manifest=manifest,
            reason="This workspace is temporarily disabled by a security kill switch.",
            violation="workspace_kill_switch_active",
        )
    if active_agent_install_id and safe_mode_service.is_agent_disabled(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_install_id=active_agent_install_id,
    ):
        return _security_gate_result(
            manifest=manifest,
            reason="This agent is temporarily disabled by a security kill switch.",
            violation="agent_kill_switch_active",
        )
    workspace_incident = safe_mode_service.resolve_workspace_incident_state(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if bool(workspace_incident.get("active")):
        return _incident_gate_result(
            manifest=manifest,
            mode=str(workspace_incident.get("mode") or "pause"),
            scope=str(workspace_incident.get("scope") or "workspace"),
            reason=str(workspace_incident.get("reason") or ""),
        )
    if normalized_runtime_mode == "hosted_secure":
        return await execution_sandbox_service.execute_hosted_customer_turn(
            manifest=manifest,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            goal=goal,
            runtime_profile=runtime_profile,
        )
    return await execute_customer_turn_in_process(
        manifest=manifest,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        goal=goal,
        runtime_mode=normalized_runtime_mode,
        runtime_profile=runtime_profile,
        privileged_runtime_approved=privileged_runtime_approved,
        active_agent_install_id=active_agent_install_id,
        runtime_scope=execution_sandbox_service.runtime_scope(
            runtime_mode=normalized_runtime_mode,
            runtime_profile=runtime_profile,
        ),
    )


async def execute_customer_turn_in_process(
    *,
    manifest: AgentManifest,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    runtime_mode: str = "hosted_secure",
    runtime_profile: dict[str, Any] | None = None,
    privileged_runtime_approved: bool = False,
    active_agent_install_id: str | None = None,
    runtime_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_runtime_mode = str(runtime_mode or "").strip().lower() or "hosted_secure"
    if active_agent_install_id and safe_mode_service.is_agent_disabled(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_install_id=active_agent_install_id,
    ):
        return _security_gate_result(
            manifest=manifest,
            reason="This agent is temporarily disabled by a security kill switch.",
            violation="agent_kill_switch_active",
        )
    workspace_incident = safe_mode_service.resolve_workspace_incident_state(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if bool(workspace_incident.get("active")):
        return _incident_gate_result(
            manifest=manifest,
            mode=str(workspace_incident.get("mode") or "pause"),
            scope=str(workspace_incident.get("scope") or "workspace"),
            reason=str(workspace_incident.get("reason") or ""),
        )
    runtime_label = str((runtime_profile or {}).get("label") or "").strip() or (
        "Hosted Secure" if normalized_runtime_mode == "hosted_secure"
        else "Local Secure" if normalized_runtime_mode == "local_secure"
        else "Privileged Device"
    )
    resolved_runtime_scope = runtime_scope or execution_sandbox_service.runtime_scope(
        runtime_mode=normalized_runtime_mode,
        runtime_profile=runtime_profile,
    )
    system_prompt = build_system_prompt(manifest)
    steps: list[dict[str, Any]] = [
        {"label": "Loading agent manifest", "detail": manifest.manifest_id, "status": "done", "kind": "thinking"},
        {"label": "Assembling Bible context", "detail": manifest.bible.hard_context or "No hard context configured", "status": "done", "kind": "thinking"},
        {"label": "Runtime profile", "detail": f"{runtime_label} · {normalized_runtime_mode}", "status": "done", "kind": "thinking"},
        {
            "label": "Runtime isolation",
            "detail": execution_sandbox_service.summarize_runtime_scope(resolved_runtime_scope),
            "status": "done",
            "kind": "thinking",
        },
        {"label": "Loading skill manifest", "detail": ", ".join(skill.id for skill in manifest.skills if skill.enabled) or "No bound skills", "status": "done", "kind": "thinking"},
    ]

    if normalized_runtime_mode == "privileged_device" and not privileged_runtime_approved:
        steps.append({
            "label": "Privileged runtime gate",
            "detail": "Privileged device execution is not available in the current trust configuration. Switch to a less restrictive mode or connect the device explicitly.",
            "status": "error",
            "kind": "thinking",
        })
        return {
            "status": "blocked",
            "reply": "Privileged device execution is not available in the current trust configuration. Switch to a less restrictive mode or connect the device explicitly.",
            "artifact": None,
            "steps": steps,
            "system_prompt": system_prompt,
            "needed_skill_id": None,
            "critic": {
                "mode": "escalate",
                "reply": "Privileged device execution is not available in the current trust configuration. Switch to a less restrictive mode or connect the device explicitly.",
                "violations": ["privileged_runtime_configuration_required"],
            },
        }

    capability_grant = tool_broker.issue_capability_token(
        manifest=manifest,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_install_id=active_agent_install_id,
        agent_id=str(active_agent_id or "").strip(),
        runtime_mode=normalized_runtime_mode,
        runtime_scope=resolved_runtime_scope,
        privileged_runtime_approved=privileged_runtime_approved,
    )
    steps.append({
        "label": "Issuing capability grant",
        "detail": tool_broker.summarize_capability_grant(capability_grant.claims),
        "status": "done",
        "kind": "thinking",
    })
    runtime_egress_policy = egress_policy.build_egress_policy(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_mode=normalized_runtime_mode,
        runtime_scope=resolved_runtime_scope,
        agent_install_id=active_agent_install_id,
        allowed_action_classes=list(capability_grant.claims.get("allowed_action_classes") or []),
        metadata={
            "manifest_id": manifest.manifest_id,
            "agent_name": manifest.identity.name,
        },
    )
    steps.append({
        "label": "Egress policy",
        "detail": (
            f"{len(list(runtime_egress_policy.get('allowed_hosts') or []))} allowlisted host pattern(s) · "
            f"{len(list(runtime_egress_policy.get('allowed_providers') or []))} provider allowlist(s)"
        ),
        "status": "done",
        "kind": "thinking",
    })

    needed_skill = skill_registry.detect_skill_need(goal)
    skill_result: dict[str, Any] | None = None
    tool_status: str | None = None

    with egress_policy.activate_egress_policy(runtime_egress_policy, merge_with_current=True):
        if needed_skill:
            try:
                skill_result = await tool_broker.execute_skill(
                    capability_token=capability_grant.token,
                    manifest_id=manifest.manifest_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    runtime_mode=normalized_runtime_mode,
                    skill_id=needed_skill.id,
                    goal=goal,
                    agent_label=manifest.identity.name,
                    hard_context=manifest.bible.hard_context,
                    operational_policy=manifest.bible.operational_policy,
                    agent_id=str(manifest.manifest_id or "").strip(),
                    agent_install_id=str(active_agent_install_id or "").strip(),
                )
                tool_status = str(skill_result.get("status") or "").strip() or None
                draft_reply = str(skill_result.get("reply") or "").strip() or _direct_reply(manifest, goal)
                steps.extend(list(skill_result.get("steps") or []))
            except tool_broker.ToolExecutionDeniedError as error:
                tool_status = "denied"
                draft_reply = _tool_broker_denial_reply(
                    manifest=manifest,
                    needed_skill=needed_skill,
                    error=error,
                )
                steps.append({
                    "label": "Tool broker denied skill",
                    "detail": error.detail,
                    "status": "error",
                    "kind": "connector",
                })
        else:
            draft_reply = _direct_reply(manifest, goal)
            steps.append({
                "label": "No skill required",
                "detail": "Reply stays inside the manifest context without a live tool call.",
                "status": "done",
                "kind": "thinking",
            })

    critic = run_policy_critic(
        manifest=manifest,
        goal=goal,
        draft_reply=draft_reply,
        skill_id=needed_skill.id if needed_skill else None,
        skill_result=skill_result,
    )
    steps.append({
        "label": "Reflection shield",
        "detail": "No violations detected" if not critic["violations"] else ", ".join(critic["violations"]),
        "status": "done" if critic["mode"] == "pass" else "error" if critic["mode"] == "escalate" else "done",
        "kind": "thinking",
    })

    return {
        "status": str(tool_status or critic["mode"]),
        "reply": critic["reply"],
        "artifact": skill_result.get("artifact") if isinstance(skill_result, dict) else None,
        "steps": steps,
        "system_prompt": system_prompt,
        "needed_skill_id": needed_skill.id if needed_skill else None,
        "critic": critic,
        "runtime_scope": resolved_runtime_scope,
    }

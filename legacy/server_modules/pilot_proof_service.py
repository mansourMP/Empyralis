from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository, pilot_operations_service


PROOF_STATUS_INSUFFICIENT = "insufficient_data"
PROOF_STATUS_CASE_STUDY = "ready_for_case_study"
PROOF_STATUS_INVESTOR_MEMO = "ready_for_investor_memo"

MIN_CASE_STUDY_MESSAGES = 10
MIN_CASE_STUDY_COMPLETED_TASKS = 3
MIN_CASE_STUDY_ACTIVE_USERS = 2
MIN_INVESTOR_MESSAGES = 50
MIN_INVESTOR_COMPLETED_TASKS = 15
MIN_INVESTOR_ACTIVE_USERS = 5
MAX_ADS_FAILURE_RATE = 0.15


def _text(value: Any) -> str:
    return str(value or "").strip()


def _token(value: Any) -> str:
    return _text(value).lower()


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 3)


def _trace_ids(events: List[Dict[str, Any]], *, limit: int = 20) -> List[str]:
    out: List[str] = []
    for event in events:
        trace_id = _text(event.get("trace_id"))
        if trace_id and trace_id not in out:
            out.append(trace_id)
        if len(out) >= limit:
            break
    return out


def _project_row(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = _dict(row.get("payload"))
    metadata = _dict(row.get("metadata"))
    response_time_seconds: Optional[float] = None
    for source in (metadata, payload):
        for key in ("response_time_seconds", "runtime_duration_seconds", "duration_seconds", "elapsed_seconds"):
            try:
                value = float(source.get(key))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                response_time_seconds = value
                break
        if response_time_seconds is not None:
            break
    return {
        "id": _text(row.get("id")) or None,
        "created_at": _text(row.get("created_at")) or None,
        "event_class": _token(row.get("event_class")) or None,
        "action": _token(row.get("action")) or None,
        "status": _token(row.get("status")) or None,
        "channel": _token(row.get("channel")) or _token(metadata.get("channel")) or _token(payload.get("channel")) or None,
        "trace_id": _text(row.get("trace_id")) or None,
        "workflow": _token(metadata.get("workflow")) or _token(payload.get("workflow")) or _token(row.get("action")) or _token(row.get("event_class")) or None,
        "severity": _token(payload.get("severity")) or _token(_dict(payload.get("issue")).get("severity")) or None,
        "response_time_seconds": response_time_seconds,
        "summary": _text(row.get("summary")) or None,
    }


def _is_phase7_pilot_row(row: Dict[str, Any]) -> bool:
    return bool(
        pilot_operations_service._is_pilot_channel(row)
        or _token(row.get("action")).startswith("pilot_")
    )


def _is_open_p0_p1_issue(row: Dict[str, Any]) -> bool:
    if _token(row.get("action")) != "pilot_issue.reported":
        return False
    payload = _dict(row.get("payload"))
    issue = _dict(payload.get("issue"))
    severity = _token(payload.get("severity")) or _token(issue.get("severity"))
    if severity not in {"p0", "p1"}:
        return False
    status = _token(row.get("status"))
    if status != "open":
        return False
    review_required = row.get("review_required")
    if isinstance(review_required, bool):
        return review_required
    return _token(review_required) == "true"


def _top_failure_reason(events: List[Dict[str, Any]]) -> Optional[str]:
    reasons = Counter()
    for event in events:
        reason = _token(event.get("summary")) or _token(event.get("action")) or _token(event.get("event_class"))
        if reason:
            reasons[reason] += 1
    if not reasons:
        return None
    return reasons.most_common(1)[0][0]


async def _load_pilot_rows(
    *,
    tenant_id: str,
    workspace_id: str,
    days: int,
    limit: int,
) -> List[Dict[str, Any]]:
    safe_days = max(1, min(int(days or 30), 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=safe_days)
    rows = await control_plane_repository.list_activity_ledger_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since_created_at=cutoff,
        limit=max(1, min(int(limit or 1000), 2000)),
    )
    return [row for row in rows if isinstance(row, dict) and _is_phase7_pilot_row(row)]


def build_proof_metrics(report: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    metrics = _dict(report.get("metrics"))
    messages = int(metrics.get("messages_handled") or 0)
    completed = int(metrics.get("tasks_completed") or 0)
    failed = int(metrics.get("tasks_failed") or 0)
    approvals = int(metrics.get("approval_count") or 0)
    blocked = int(metrics.get("blocked_action_count") or 0)
    active_users = int(metrics.get("active_users") or 0)
    top_workflows = list(_dict(report.get("daily_summary")).get("top_workflows") or [])
    failure_events = list(report.get("failures") or [])
    evidence_rows = [_project_row(row) for row in rows]

    return {
        "pilot_users": active_users,
        "messages_handled": messages,
        "tasks_completed": completed,
        "failure_rate": _rate(failed, completed + failed),
        "manual_approval_rate": _rate(approvals, messages),
        "blocked_action_rate": _rate(blocked, messages),
        "average_response_time_seconds": metrics.get("average_response_time_seconds"),
        "repeat_usage_rate": _rate(int(metrics.get("repeated_usage_by_user") or 0), active_users),
        "average_usefulness_score": metrics.get("user_reported_usefulness"),
        "top_workflow": _text((top_workflows[0] if top_workflows else {}).get("workflow")) or None,
        "top_failure_reason": _top_failure_reason(failure_events),
        "trace_ids": _trace_ids(evidence_rows),
    }


def evaluate_proof_readiness(
    *,
    proof_metrics: Dict[str, Any],
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    missing: List[str] = []
    available: List[str] = []
    unresolved_p0_p1 = [_project_row(row) for row in rows if _is_open_p0_p1_issue(row)]

    messages = int(proof_metrics.get("messages_handled") or 0)
    completed = int(proof_metrics.get("tasks_completed") or 0)
    users = int(proof_metrics.get("pilot_users") or 0)
    usefulness = proof_metrics.get("average_usefulness_score")
    trace_ids = list(proof_metrics.get("trace_ids") or [])

    if users < MIN_CASE_STUDY_ACTIVE_USERS:
        missing.append(f"Need at least {MIN_CASE_STUDY_ACTIVE_USERS} active pilot users.")
    else:
        available.append("Active pilot users present.")
    if messages < MIN_CASE_STUDY_MESSAGES:
        missing.append(f"Need at least {MIN_CASE_STUDY_MESSAGES} real pilot messages.")
    else:
        available.append("Real pilot messages present.")
    if completed < MIN_CASE_STUDY_COMPLETED_TASKS:
        missing.append(f"Need at least {MIN_CASE_STUDY_COMPLETED_TASKS} completed pilot tasks.")
    else:
        available.append("Completed pilot tasks present.")
    if usefulness is None:
        missing.append("Need at least one user-reported usefulness score.")
    else:
        available.append("User-reported usefulness present.")
    if not trace_ids:
        missing.append("Need trace IDs for proof evidence.")
    else:
        available.append("Trace IDs present.")
    if unresolved_p0_p1:
        missing.append("Resolve open P0/P1 pilot issues before proof is ready.")
    else:
        available.append("No open P0/P1 pilot issue found.")

    status = PROOF_STATUS_INSUFFICIENT
    if not missing:
        status = PROOF_STATUS_CASE_STUDY
    if (
        status == PROOF_STATUS_CASE_STUDY
        and users >= MIN_INVESTOR_ACTIVE_USERS
        and messages >= MIN_INVESTOR_MESSAGES
        and completed >= MIN_INVESTOR_COMPLETED_TASKS
    ):
        status = PROOF_STATUS_INVESTOR_MEMO

    return {
        "proof_status": status,
        "missing_evidence": missing,
        "available_evidence": available,
        "can_prepare_ads": False,
        "unresolved_p0_p1_issues": unresolved_p0_p1,
    }


async def build_pilot_proof_package(
    *,
    tenant_id: str,
    workspace_id: str,
    days: int = 30,
    limit: int = 1000,
) -> Dict[str, Any]:
    report = await pilot_operations_service.build_pilot_operations_report(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        days=days,
        limit=limit,
    )
    rows = await _load_pilot_rows(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        days=days,
        limit=limit,
    )
    metrics = build_proof_metrics(report, rows)
    readiness = evaluate_proof_readiness(proof_metrics=metrics, rows=rows)
    evidence_rows = [_project_row(row) for row in rows]
    return {
        "workflow": report.get("workflow"),
        "proof_readiness": readiness,
        "proof_metrics": metrics,
        "evidence": {
            "trace_ids": metrics.get("trace_ids") or [],
            "sample_events": evidence_rows[:20],
            "failures": list(report.get("failures") or []),
            "blocked_actions": list(report.get("blocked_actions") or []),
            "risky_events": list(report.get("risky_events") or []),
            "user_feedback": list(report.get("user_feedback") or []),
        },
        "source": {
            "phase": "phase_8_proof_ads_investor_readiness",
            "derived_from": "phase_7_activity_ledger_schema",
            "invented_metrics": False,
        },
    }


def build_case_study(package: Dict[str, Any]) -> Dict[str, Any]:
    readiness = _dict(package.get("proof_readiness"))
    metrics = _dict(package.get("proof_metrics"))
    evidence = _dict(package.get("evidence"))
    insufficient = readiness.get("proof_status") == PROOF_STATUS_INSUFFICIENT
    missing_value = "not_enough_data"
    return {
        "status": readiness.get("proof_status"),
        "workflow_tested": pilot_operations_service.PILOT_WORKFLOW_NAME,
        "user_type": "known business/workplace pilot users" if not insufficient else missing_value,
        "problem_before_empyralis": "Customer questions across WhatsApp/Telegram required manual triage." if not insufficient else missing_value,
        "what_empyralis_did": "Handled customer questions, produced safe responses, and surfaced approval/blocked events." if not insufficient else missing_value,
        "measured_result": metrics if not insufficient else missing_value,
        "failures_and_limitations": evidence.get("failures") or readiness.get("missing_evidence") or [],
        "approval_safety_behavior": {
            "manual_approval_rate": metrics.get("manual_approval_rate"),
            "blocked_action_rate": metrics.get("blocked_action_rate"),
            "unresolved_p0_p1_issues": readiness.get("unresolved_p0_p1_issues") or [],
        },
        "evidence_trace_ids": evidence.get("trace_ids") or [],
        "what_is_still_not_proven": readiness.get("missing_evidence") or ["Public acquisition and paid ads are not proven by closed pilot data."],
    }


def build_investor_memo(package: Dict[str, Any]) -> Dict[str, Any]:
    readiness = _dict(package.get("proof_readiness"))
    metrics = _dict(package.get("proof_metrics"))
    enough = readiness.get("proof_status") in {PROOF_STATUS_CASE_STUDY, PROOF_STATUS_INVESTOR_MEMO}
    return {
        "status": readiness.get("proof_status"),
        "product_wedge": "Sage-centered assistant for WhatsApp/Telegram customer question handling." if enough else "not_enough_data",
        "workflow_proven": pilot_operations_service.PILOT_WORKFLOW_NAME if enough else "not_enough_data",
        "pilot_usage": metrics if enough else "not_enough_data",
        "safety_model": "Risky actions are approval-gated or blocked, and proof must include trace IDs." if enough else "not_enough_data",
        "retention_repeat_usage_signal": metrics.get("repeat_usage_rate") if enough else "not_enough_data",
        "failure_modes": _dict(package.get("evidence")).get("failures") or readiness.get("missing_evidence") or [],
        "why_now": "Messaging channels are already where pilot users ask operational business questions." if enough else "not_enough_data",
        "what_is_not_proven_yet": readiness.get("missing_evidence") or ["Public demand, paid acquisition, and larger-scale retention are not proven yet."],
        "next_milestone": "Run the closed pilot until proof reaches investor-memo status, then prepare Phase 8 proof materials without invented metrics.",
        "evidence_trace_ids": _dict(package.get("evidence")).get("trace_ids") or [],
    }


def build_ads_readiness(package: Dict[str, Any]) -> Dict[str, Any]:
    readiness = _dict(package.get("proof_readiness"))
    metrics = _dict(package.get("proof_metrics"))
    reasons: List[str] = []
    required: List[str] = []

    if readiness.get("proof_status") != PROOF_STATUS_INVESTOR_MEMO:
        reasons.append("Investor-memo level pilot proof is not ready.")
        required.append("Reach investor-memo proof status from real pilot data.")
    if float(metrics.get("failure_rate") or 0.0) > MAX_ADS_FAILURE_RATE:
        reasons.append("Pilot failure rate is above ads threshold.")
        required.append(f"Reduce failure rate to {MAX_ADS_FAILURE_RATE} or below.")
    if readiness.get("unresolved_p0_p1_issues"):
        reasons.append("Open P0/P1 pilot issues remain.")
        required.append("Resolve all open P0/P1 pilot issues.")
    if not metrics.get("average_usefulness_score"):
        reasons.append("No user usefulness score exists.")
        required.append("Collect user-reported usefulness feedback.")

    ads_ready = not reasons
    return {
        "ads_ready": ads_ready,
        "reasons": reasons,
        "required_before_ads": required,
        "allowed_actions": ["prepare_case_study", "prepare_investor_memo"] if not ads_ready else ["prepare_case_study", "prepare_investor_memo", "prepare_ad_copy_review"],
        "forbidden_actions": [] if ads_ready else ["launch_campaign", "increase_ad_spend", "publish_public_claims"],
    }

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from server_modules import activity_ledger_service, control_plane_repository


PILOT_WORKFLOW_ID = "customer_question_handling"
PILOT_WORKFLOW_NAME = "WhatsApp/Telegram customer question handling"
PILOT_CHANNELS = ["telegram", "whatsapp", "telegram_personal", "whatsapp_personal"]
PILOT_REQUIRED_ROLES = ["owner_admin", "operator", "normal_user", "external_customer"]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _token(value: Any) -> str:
    return _text(value).lower()


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _created_at(row: Dict[str, Any]) -> Optional[datetime]:
    value = row.get("created_at")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    token = _text(value)
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _duration_seconds(row: Dict[str, Any]) -> Optional[float]:
    metadata = _dict(row.get("metadata"))
    payload = _dict(row.get("payload"))
    for source in (metadata, payload):
        for key in ("response_time_seconds", "runtime_duration_seconds", "duration_seconds", "elapsed_seconds"):
            try:
                value = float(source.get(key))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return value
    return None


def _workflow_key(row: Dict[str, Any]) -> str:
    metadata = _dict(row.get("metadata"))
    payload = _dict(row.get("payload"))
    return (
        _token(metadata.get("workflow"))
        or _token(payload.get("workflow"))
        or _token(row.get("action"))
        or _token(row.get("event_class"))
        or "unknown"
    )


def _is_pilot_channel(row: Dict[str, Any]) -> bool:
    channel = _token(row.get("channel"))
    if channel in PILOT_CHANNELS:
        return True
    metadata = _dict(row.get("metadata"))
    payload = _dict(row.get("payload"))
    return _token(metadata.get("channel")) in PILOT_CHANNELS or _token(payload.get("channel")) in PILOT_CHANNELS


def _is_failure(row: Dict[str, Any]) -> bool:
    status = _token(row.get("status"))
    action = _token(row.get("action"))
    event_class = _token(row.get("event_class"))
    return (
        status in {"failed", "error", "blocked", "denied", "timeout"}
        or event_class == "blocked_action"
        or action.endswith(".failed")
        or action.endswith("_failed")
    )


def _is_success(row: Dict[str, Any]) -> bool:
    if _token(row.get("channel")) == "pilot" or _token(row.get("action")).startswith("pilot_"):
        return False
    status = _token(row.get("status"))
    action = _token(row.get("action"))
    return status in {"success", "completed", "ok", "delivered"} or action.endswith(".completed")


def _project_event(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _text(row.get("id")) or None,
        "created_at": _text(row.get("created_at")) or None,
        "event_class": _token(row.get("event_class")) or None,
        "action": _token(row.get("action")) or None,
        "status": _token(row.get("status")) or None,
        "channel": _token(row.get("channel")) or None,
        "trace_id": _text(row.get("trace_id")) or None,
        "title": _text(row.get("title")) or None,
        "summary": _text(row.get("summary")) or None,
    }


def _average(values: Iterable[float]) -> Optional[float]:
    items = [float(value) for value in values if value is not None]
    if not items:
        return None
    return round(sum(items) / len(items), 3)


def get_pilot_operations_contract() -> Dict[str, Any]:
    return {
        "workflow": {
            "id": PILOT_WORKFLOW_ID,
            "name": PILOT_WORKFLOW_NAME,
            "channels": ["WhatsApp", "Telegram"],
            "proof_target": "Answer customer questions, draft safe responses, escalate risky writes, and leave an audit trail.",
            "excluded": ["public ads", "marketplace", "payment processing", "multi-agent graph", "future runtime providers"],
        },
        "users_roles": {
            "owner_admin": {
                "can": ["configure channel", "approve risky actions", "pause or kill sessions", "view audit logs", "run daily review"],
                "cannot_delegate": ["workspace emergency stop", "privacy boundary changes", "approval policy changes"],
            },
            "operator": {
                "can": ["monitor daily report", "triage failures", "escalate unclear customer requests", "record issue reports"],
                "cannot": ["change runtime policy", "approve high-risk actions unless promoted to admin"],
            },
            "normal_user": {
                "can": ["ask the agent/internal assistant questions", "receive drafts", "report usefulness"],
                "cannot": ["deploy agents", "view customer audit logs", "kill workspace sessions"],
            },
            "external_customer": {
                "can": ["send WhatsApp/Telegram questions", "receive approved safe replies"],
                "cannot": ["trigger tools directly", "see internal memory", "view audit logs"],
            },
        },
        "safety_boundaries": {
            "agent_can_read": ["configured business knowledge", "current conversation", "approved channel metadata", "non-restricted memory"],
            "agent_can_write": ["draft replies", "activity/audit entries", "operator-visible issue notes"],
            "requires_approval": ["external sends that commit the business", "refunds", "discounts", "payment links", "deleting data", "permission changes"],
            "forbidden": ["payment processing", "credential entry", "mass messaging", "medical/legal/financial commitments", "bypassing audit"],
            "kill_sessions": {"owner_admin": ["per-agent", "runtime session", "workspace emergency stop"], "operator": ["escalate to owner_admin"]},
            "audit_log_access": {"owner_admin": "full pilot audit", "operator": "daily report and trace ids", "normal_user": "own feedback only"},
        },
        "metrics_plan": [
            "active_users",
            "messages_handled",
            "tasks_completed",
            "tasks_failed",
            "approval_count",
            "blocked_action_count",
            "average_response_time_seconds",
            "manual_intervention_rate",
            "repeated_usage_by_user",
            "user_reported_usefulness",
        ],
        "daily_operating_procedure": {
            "onboarding_script": [
                "Confirm the pilot is limited to known users.",
                "Explain that WhatsApp/Telegram replies may be drafted or gated for approval.",
                "Show where trace_id appears for support.",
                "Confirm who can pause or kill the agent.",
            ],
            "first_task_script": [
                "Send one common customer question.",
                "Verify response quality and trace_id.",
                "Send one risky request and confirm approval/blocked behavior.",
                "Record usefulness score after the first session.",
            ],
            "failure_escalation_script": [
                "Capture user, channel, trace_id, expected behavior, and actual behavior.",
                "Pause affected agent/session if the issue is safety-related.",
                "Assign severity before fixing.",
                "Review audit trail before re-enabling.",
            ],
            "daily_review_script": [
                "Review daily summary before the next pilot window.",
                "Check failures, blocked actions, risky events, and usefulness feedback.",
                "Pick no more than three fixes for the next day.",
                "Keep the pilot closed until success/failure metrics are stable.",
            ],
        },
        "issue_template": {
            "user": "",
            "workflow": PILOT_WORKFLOW_ID,
            "expected_behavior": "",
            "actual_behavior": "",
            "logs_trace_id": "",
            "severity": "p2",
            "fix_status": "open",
        },
        "acceptance_criteria": [
            "10-20 known users can participate without public signup.",
            "One WhatsApp/Telegram workflow completes end to end.",
            "Risky actions are blocked in the current safety mode.",
            "Owner/admin can find trace_id and audit events for every failure.",
            "Daily report shows metrics, failures, blocked actions, risky events, and feedback.",
        ],
    }


async def build_pilot_operations_report(
    *,
    tenant_id: str,
    workspace_id: str,
    days: int = 1,
    limit: int = 500,
) -> Dict[str, Any]:
    safe_days = max(1, min(int(days or 1), 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=safe_days)
    rows = await control_plane_repository.list_activity_ledger_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        since_created_at=cutoff,
        limit=max(1, min(int(limit or 500), 1000)),
    )
    rows = [row for row in rows if isinstance(row, dict)]
    pilot_rows = [row for row in rows if _is_pilot_channel(row) or _token(row.get("action")).startswith("pilot_")]

    actor_ids = {_text(row.get("actor_id")) for row in pilot_rows if _text(row.get("actor_id"))}
    session_counts: Dict[str, int] = defaultdict(int)
    for row in pilot_rows:
        if _token(row.get("actor_id")) in {"agent", "system", "runtime"}:
            continue
        session_key = _text(row.get("session_key")) or _text(row.get("thread_id")) or _text(row.get("actor_id"))
        if session_key:
            session_counts[session_key] += 1

    approval_rows = [row for row in pilot_rows if _token(row.get("event_class")) == "approval" or bool(row.get("review_required"))]
    blocked_rows = [row for row in pilot_rows if _token(row.get("event_class")) == "blocked_action" or _token(row.get("status")) in {"blocked", "denied"}]
    failure_rows = [row for row in pilot_rows if _is_failure(row)]
    completed_rows = [row for row in pilot_rows if _is_success(row)]
    feedback_rows = [row for row in pilot_rows if _token(row.get("action")) == "pilot_feedback.reported"]
    usefulness_scores = [
        float(_dict(row.get("payload")).get("usefulness_score"))
        for row in feedback_rows
        if isinstance(_dict(row.get("payload")).get("usefulness_score"), (int, float))
    ]
    workflow_counts = Counter(_workflow_key(row) for row in pilot_rows)
    durations = [_duration_seconds(row) for row in pilot_rows]
    messages_handled = len(
        [
            row
            for row in pilot_rows
            if _token(row.get("direction")) in {"inbound", "outbound"}
            or _token(row.get("channel")) in PILOT_CHANNELS
        ]
    )

    manual_intervention_rate = 0.0
    if messages_handled > 0:
        manual_intervention_rate = round(len(approval_rows) / messages_handled, 3)

    return {
        "workflow": get_pilot_operations_contract()["workflow"],
        "window": {
            "days": safe_days,
            "since": cutoff.isoformat().replace("+00:00", "Z"),
            "event_count": len(pilot_rows),
        },
        "metrics": {
            "active_users": len(actor_ids),
            "messages_handled": messages_handled,
            "tasks_completed": len(completed_rows),
            "tasks_failed": len(failure_rows),
            "approval_count": len(approval_rows),
            "blocked_action_count": len(blocked_rows),
            "average_response_time_seconds": _average([value for value in durations if value is not None]),
            "manual_intervention_rate": manual_intervention_rate,
            "repeated_usage_by_user": len([key for key, count in session_counts.items() if count > 1]),
            "user_reported_usefulness": _average(usefulness_scores),
        },
        "daily_summary": {
            "status": "needs_data" if not pilot_rows else "ready",
            "top_workflows": [{"workflow": key, "count": count} for key, count in workflow_counts.most_common(5)],
            "active_channels": sorted({_token(row.get("channel")) for row in pilot_rows if _token(row.get("channel"))}),
        },
        "failures": [_project_event(row) for row in failure_rows[:20]],
        "blocked_actions": [_project_event(row) for row in blocked_rows[:20]],
        "risky_events": [_project_event(row) for row in approval_rows[:20]],
        "user_feedback": [_project_event(row) for row in feedback_rows[:20]],
        "acceptance_criteria": get_pilot_operations_contract()["acceptance_criteria"],
    }


async def record_pilot_feedback(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    usefulness_score: int,
    comment: str = "",
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    score = max(1, min(int(usefulness_score), 5))
    event = await activity_ledger_service.append_activity_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=actor_id or "pilot_user",
        event_class="system_activity",
        detail_level="timeline_detail",
        channel="pilot",
        direction="inbound",
        action="pilot_feedback.reported",
        trace_id=trace_id,
        title="Pilot feedback reported",
        summary=comment or f"Pilot usefulness score: {score}/5",
        status="logged",
        payload={"workflow": PILOT_WORKFLOW_ID, "usefulness_score": score, "comment": comment},
        metadata={"phase": "phase_7_closed_pilot_operations"},
    )
    return {"ok": True, "event": event}


async def record_pilot_issue(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    issue: Dict[str, Any],
) -> Dict[str, Any]:
    severity = _token(issue.get("severity")) or "p2"
    if severity not in {"p0", "p1", "p2"}:
        raise ValueError("severity must be one of p0, p1, or p2.")
    trace_id = _text(issue.get("logs_trace_id")) or None
    event = await activity_ledger_service.append_activity_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=actor_id or "pilot_operator",
        event_class="blocked_action" if severity in {"p0", "p1"} else "system_activity",
        detail_level="timeline_detail",
        channel="pilot",
        direction="inbound",
        action="pilot_issue.reported",
        trace_id=trace_id,
        title=f"Pilot issue reported ({severity.upper()})",
        summary=_text(issue.get("actual_behavior")) or "Pilot issue reported.",
        status="open",
        review_required=severity in {"p0", "p1"},
        payload={"workflow": PILOT_WORKFLOW_ID, "issue": dict(issue), "severity": severity},
        metadata={"phase": "phase_7_closed_pilot_operations"},
    )
    return {"ok": True, "event": event}

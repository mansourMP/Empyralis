from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from server_modules import secret_redaction_service

DAILY_OPERATOR_LOOP_VERSION = "daily_operator_v1"

_RECIPE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "morning_brief": {
        "title": "Morning brief",
        "triggers": (
            "morning brief",
            "daily brief",
            "brief me",
            "today brief",
            "today's brief",
        ),
        "required_tools": {
            "gmail": "google_workspace__fetch_emails",
            "google_calendar": "google_workspace__list_calendar_events",
        },
        "read_tool_calls": [
            {"name": "google_workspace__fetch_emails", "arguments": {"limit": 8}},
            {"name": "google_workspace__list_calendar_events", "arguments": {"top": 10}},
        ],
        "write_actions": [],
    },
    "email_triage": {
        "title": "Email triage",
        "triggers": (
            "email triage",
            "triage my email",
            "triage important email",
            "important email",
            "urgent replies",
        ),
        "required_tools": {
            "gmail": "google_workspace__fetch_emails",
        },
        "read_tool_calls": [
            {"name": "google_workspace__fetch_emails", "arguments": {"limit": 10}},
        ],
        "write_actions": [
            {
                "tool": "google_workspace__draft_email",
                "connector": "google_workspace",
                "action": "draft_email",
                "prompt": "Approve Sage to draft email responses from this triage.",
            }
        ],
    },
    "meeting_prep": {
        "title": "Meeting prep",
        "triggers": (
            "meeting prep",
            "prepare me for my next meeting",
            "prepare me before meetings",
            "next meeting",
            "before my meeting",
        ),
        "required_tools": {
            "google_calendar": "google_workspace__list_calendar_events",
            "google_drive": "google_workspace__list_drive_files",
        },
        "read_tool_calls": [
            {"name": "google_workspace__list_calendar_events", "arguments": {"top": 5}},
            {"name": "google_workspace__list_drive_files", "arguments": {"path": "gdrive:/", "top": 20}},
        ],
        "write_actions": [
            {
                "tool": "google_workspace__create_calendar_event",
                "connector": "google_workspace",
                "action": "create_calendar_event",
                "prompt": "Approve Sage before it creates or changes a calendar event.",
            }
        ],
    },
}

_WRITE_INTENT_TERMS = (
    "draft",
    "send",
    "reply",
    "respond",
    "create",
    "update",
    "schedule",
    "book",
    "reschedule",
    "cancel",
    "invite",
)
_SCHEDULE_INTENT_TERMS = (
    "every day",
    "every weekday",
    "recurring",
    "schedule this",
    "schedule it",
    "run this every",
)


def _compact(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def match_daily_operator_recipe(message: str) -> Optional[Dict[str, Any]]:
    compact = _compact(message)
    if not compact:
        return None
    for recipe_id, recipe in _RECIPE_DEFINITIONS.items():
        if any(term in compact for term in recipe.get("triggers", ())):
            return {"id": recipe_id, **recipe}
    if "triage" in compact and any(term in compact for term in ("email", "gmail", "inbox")):
        return {"id": "email_triage", **_RECIPE_DEFINITIONS["email_triage"]}
    if "brief" in compact and any(term in compact for term in ("today", "morning", "calendar", "email")):
        return {"id": "morning_brief", **_RECIPE_DEFINITIONS["morning_brief"]}
    if "prepare" in compact and "meeting" in compact:
        return {"id": "meeting_prep", **_RECIPE_DEFINITIONS["meeting_prep"]}
    return None


def message_might_need_daily_operator(message: str) -> bool:
    return match_daily_operator_recipe(message) is not None


def _available_tool_names(tools: List[Dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for item in tools:
        if isinstance(item, dict):
            name = _coerce_text(item.get("name"))
            if name:
                out.add(name)
    return out


def _missing_required_tools(recipe: Dict[str, Any], tools: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    available = _available_tool_names(tools)
    missing: List[Dict[str, str]] = []
    for connection_id, tool_name in (recipe.get("required_tools") or {}).items():
        if _coerce_text(tool_name) not in available:
            missing.append(
                {
                    "connection": connection_id,
                    "tool": _coerce_text(tool_name),
                    "reason": "connection_or_read_action_unavailable",
                }
            )
    return missing


def _needs_write_approval(message: str, recipe: Dict[str, Any]) -> bool:
    if not recipe.get("write_actions"):
        return False
    compact = _compact(message)
    return any(term in compact for term in _WRITE_INTENT_TERMS)


def _needs_schedule_approval(message: str) -> bool:
    compact = _compact(message)
    return any(term in compact for term in _SCHEDULE_INTENT_TERMS)


def _summarize_output(value: Any, *, max_chars: int = 1800) -> str:
    text = secret_redaction_service.redact_text(str(value or "").replace("\0", "")).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n...[truncated]"


def _proof_checked_item(*, tool_name: str, status: str, summary: str = "", reason: str = "") -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "tool": _coerce_text(tool_name),
        "label": _coerce_text(tool_name).replace("google_workspace__", "").replace("_", " "),
        "status": _coerce_text(status) or "unknown",
    }
    if summary:
        item["summary"] = _summarize_output(summary, max_chars=900)
    if reason:
        item["reason"] = _summarize_output(reason, max_chars=500)
    return item


def _proof_blocked_items(blocked_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in blocked_tools:
        if not isinstance(item, dict):
            continue
        tool_name = _coerce_text(item.get("name") or item.get("tool"))
        if not tool_name:
            continue
        blocked: Dict[str, Any] = {
            "tool": tool_name,
            "status": _coerce_text(item.get("status")) or "blocked",
            "reason": _summarize_output(item.get("reason"), max_chars=500),
        }
        connection = _coerce_text(item.get("connection"))
        if connection:
            blocked["connection"] = connection
        out.append(blocked)
    return out


def _proof_approval_items(approvals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in approvals:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "status": _coerce_text(item.get("status")) or "waiting",
                "reason": _coerce_text(item.get("prompt")),
                "labels": [
                    _coerce_text(label)
                    for label in (item.get("labels") or [])
                    if _coerce_text(label)
                ],
                "actions": [
                    _coerce_text(action)
                    for action in (item.get("actions") or [])
                    if _coerce_text(action)
                ],
                "scope": _coerce_text(item.get("scope")) or "once",
            }
        )
    return out


def _build_proof_log(
    *,
    recipe: Dict[str, Any],
    status: str,
    checked: List[Dict[str, Any]],
    blocked_tools: List[Dict[str, Any]],
    approvals: List[Dict[str, Any]],
    changes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "version": "daily_operator_proof_v1",
        "recipe_id": recipe.get("id"),
        "title": recipe.get("title"),
        "status": status,
        "checked": checked,
        "changes": changes or [],
        "blocked": _proof_blocked_items(blocked_tools),
        "approvals": _proof_approval_items(approvals),
        "verify_hint": "Read actions are summarized here; external writes require approval before dispatch.",
    }


def _approval_payloads(
    message: str,
    recipe: Dict[str, Any],
    tools: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    approvals: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    tool_calls: List[Dict[str, Any]] = []
    available = _available_tool_names(tools)
    if _needs_write_approval(message, recipe):
        for index, action in enumerate(recipe.get("write_actions") or [], start=1):
            connector = _coerce_text(action.get("connector"))
            action_id = _coerce_text(action.get("action"))
            tool_name = _coerce_text(action.get("tool")) or f"{connector}__{action_id}".strip("_")
            if tool_name not in available:
                blocked.append(
                    {
                        "name": tool_name,
                        "reason": "connection_or_write_action_unavailable",
                        "status": "blocked",
                    }
                )
                continue
            approvals.append(
                {
                    "prompt": _coerce_text(action.get("prompt")) or f"Approve {connector}.{action_id} before Sage continues.",
                    "labels": [f"{connector}.{action_id}".strip(".")],
                    "capabilities": [connector] if connector else [],
                    "actions": [action_id] if action_id else [],
                    "target": {"recipe_id": recipe.get("id"), "request": message},
                    "scope": "once",
                    "reusable": False,
                    "status": "waiting",
                }
            )
            blocked.append({"name": f"{connector}.{action_id}".strip("."), "reason": "approval_required", "status": "blocked"})
            tool_calls.append(
                {
                    "id": f"approval:{recipe.get('id')}:{index}",
                    "name": tool_name,
                    "arguments": {"recipe_id": recipe.get("id")},
                    "status": "approval_required",
                    "iteration": 1,
                    "action_loop_version": DAILY_OPERATOR_LOOP_VERSION,
                }
            )
    if _needs_schedule_approval(message):
        approvals.append(
            {
                "prompt": f"Approve scheduling the {recipe.get('title', 'Sage')} recipe.",
                "labels": ["sage_recipe.schedule"],
                "capabilities": ["sage_recipe"],
                "actions": ["schedule_recipe"],
                "target": {"recipe_id": recipe.get("id"), "request": message},
                "scope": "once",
                "reusable": False,
                "status": "waiting",
            }
        )
        blocked.append({"name": "sage_recipe.schedule_recipe", "reason": "approval_required", "status": "blocked"})
        tool_calls.append(
            {
                "id": f"approval:{recipe.get('id')}:schedule",
                "name": "sage_recipe__schedule_recipe",
                "arguments": {"recipe_id": recipe.get("id")},
                "status": "approval_required",
                "iteration": 1,
                "action_loop_version": DAILY_OPERATOR_LOOP_VERSION,
            }
        )
    return approvals, blocked, tool_calls


def run_daily_operator_recipe(
    *,
    message: str,
    tools: List[Dict[str, Any]],
    tool_capabilities: List[Dict[str, Any]],
    availability: Dict[str, Any],
    route_decision: Dict[str, Any],
    execute_tool_call: Callable[[Dict[str, Any], int], Any],
) -> Optional[Dict[str, Any]]:
    recipe = match_daily_operator_recipe(message)
    if recipe is None:
        return None

    trace_events: List[Dict[str, Any]] = [
        {
            "event_type": "daily_operator.recipe_detected",
            "data": {
                "recipe_id": recipe.get("id"),
                "title": recipe.get("title"),
            },
        }
    ]
    missing = _missing_required_tools(recipe, tools)
    if missing:
        trace_events.append(
            {
                "event_type": "daily_operator.requirements_blocked",
                "data": {"missing": missing},
            }
        )
        required_connections = sorted({str(item.get("connection") or "").strip() for item in missing if item.get("connection")})
        route = {
            **dict(route_decision or {}),
            "mode": "connector_api",
            "user_label": "Connected Assistant",
            "reason": "This Sage recipe needs connected Google Workspace app actions before it can run.",
            "required_connections": required_connections,
            "approval_required": False,
        }
        return {
            "message": (
                f"I cannot run {recipe.get('title', 'this recipe')} yet. "
                "Missing Google Workspace access: "
                + ", ".join(required_connections)
                + "."
            ),
            "tool_calls": [],
            "blocked_tools": [
                {
                    "name": item["tool"],
                    "reason": item["reason"],
                    "status": "blocked",
                    "connection": item["connection"],
                }
                for item in missing
            ],
            "proof_log": _build_proof_log(
                recipe=recipe,
                status="blocked",
                checked=[],
                blocked_tools=[
                    {
                        "name": item["tool"],
                        "reason": item["reason"],
                        "status": "blocked",
                        "connection": item["connection"],
                    }
                    for item in missing
                ],
                approvals=[],
            ),
            "approvals_required": [],
            "action_execution_mode": "daily_operator_blocked",
            "available_tools": tools,
            "route_decision": route,
            "action_loop_version": DAILY_OPERATOR_LOOP_VERSION,
            "trace_events": trace_events,
            "loop_budget": {
                "max_tool_calls": len(recipe.get("read_tool_calls") or []),
                "planned_tool_calls": len(recipe.get("read_tool_calls") or []),
                "executed_tool_calls": 0,
                "blocked_tool_calls": len(missing),
            },
            "daily_operator": {
                "recipe_id": recipe.get("id"),
                "title": recipe.get("title"),
                "status": "blocked",
                "missing": missing,
            },
        }

    tool_calls: List[Dict[str, Any]] = []
    blocked_tools: List[Dict[str, Any]] = []
    outputs: List[str] = []
    proof_checked: List[Dict[str, Any]] = []
    for index, plan in enumerate(recipe.get("read_tool_calls") or [], start=1):
        tool_name = _coerce_text(plan.get("name"))
        arguments = dict(plan.get("arguments") or {})
        call = {
            "id": f"daily:{recipe.get('id')}:{index}",
            "name": tool_name,
            "arguments": arguments,
        }
        trace_events.append(
            {
                "event_type": "daily_operator.tool_started",
                "tool_call_id": call["id"],
                "data": {"tool_name": tool_name, "arguments": arguments},
            }
        )
        try:
            output = execute_tool_call(call, index)
            summary = _summarize_output(output)
            tool_calls.append(
                {
                    **call,
                    "status": "completed",
                    "output": summary,
                    "iteration": 1,
                    "action_loop_version": DAILY_OPERATOR_LOOP_VERSION,
                }
            )
            if summary:
                outputs.append(f"{tool_name}: {summary}")
            proof_checked.append(_proof_checked_item(tool_name=tool_name, status="completed", summary=summary))
            trace_events.append(
                {
                    "event_type": "daily_operator.tool_completed",
                    "tool_call_id": call["id"],
                    "data": {"tool_name": tool_name, "status": "completed"},
                }
            )
        except Exception as exc:
            reason = _summarize_output(str(exc), max_chars=800) or type(exc).__name__
            blocked_tools.append({"name": tool_name, "reason": reason, "status": "blocked"})
            tool_calls.append(
                {
                    **call,
                    "status": "failed",
                    "error": reason,
                    "iteration": 1,
                    "action_loop_version": DAILY_OPERATOR_LOOP_VERSION,
                }
            )
            trace_events.append(
                {
                    "event_type": "daily_operator.tool_failed",
                    "tool_call_id": call["id"],
                    "data": {"tool_name": tool_name, "reason": reason},
                }
            )
            proof_checked.append(_proof_checked_item(tool_name=tool_name, status="failed", reason=reason))

    approvals, approval_blocked, approval_tool_calls = _approval_payloads(message, recipe, tools)
    blocked_tools.extend(approval_blocked)
    tool_calls.extend(approval_tool_calls)
    if approvals:
        trace_events.append(
            {
                "event_type": "daily_operator.approval_required",
                "data": {"recipe_id": recipe.get("id"), "approval_count": len(approvals)},
            }
        )

    checked = [
        _coerce_text(call.get("name")).replace("google_workspace__", "").replace("_", " ")
        for call in tool_calls
        if call.get("status") == "completed"
    ]
    message_lines = [f"{recipe.get('title', 'Daily operator recipe')} is ready."]
    if checked:
        message_lines.append("Checked: " + ", ".join(checked) + ".")
    if outputs:
        message_lines.append("Proof log:")
        for output in outputs[:3]:
            message_lines.append(f"- {_summarize_output(output, max_chars=900)}")
    if approvals:
        message_lines.append("Approval is required before Sage sends, changes, or schedules anything.")
    if blocked_tools and not approvals:
        message_lines.append("Some requested app actions could not run; see blocked tools for the operational reason.")

    route = {
        **dict(route_decision or {}),
        "mode": "connector_api",
        "user_label": "Connected Assistant",
        "reason": "Sage matched this request to a Daily Operator recipe.",
        "required_connections": sorted(recipe.get("required_tools", {}).keys()),
        "approval_required": bool(approvals),
    }
    recipe_status = "approval_required" if approvals else ("partial" if blocked_tools else "completed")
    proof_log = _build_proof_log(
        recipe=recipe,
        status=recipe_status,
        checked=proof_checked,
        blocked_tools=blocked_tools,
        approvals=approvals,
    )
    return {
        "message": "\n".join(message_lines).strip(),
        "tool_calls": tool_calls,
        "blocked_tools": blocked_tools,
        "approvals_required": approvals,
        "proof_log": proof_log,
        "action_execution_mode": "approval_required" if approvals else ("partial_tools_executed" if blocked_tools else "daily_operator_executed"),
        "available_tools": tools,
        "route_decision": route,
        "action_loop_version": DAILY_OPERATOR_LOOP_VERSION,
        "trace_events": trace_events,
        "loop_budget": {
            "max_tool_calls": len(recipe.get("read_tool_calls") or []),
            "planned_tool_calls": len(recipe.get("read_tool_calls") or []),
            "executed_tool_calls": len([call for call in tool_calls if call.get("status") == "completed"]),
            "blocked_tool_calls": len(blocked_tools),
        },
        "daily_operator": {
            "recipe_id": recipe.get("id"),
            "title": recipe.get("title"),
            "status": recipe_status,
            "proof_log_status": proof_log.get("status"),
            "tool_capability_count": len(tool_capabilities or []),
            "availability_state": availability.get("runtime_state") if isinstance(availability, dict) else None,
        },
    }

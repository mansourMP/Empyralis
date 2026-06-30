from __future__ import annotations

import asyncio
import hashlib
import threading
from typing import Any, Dict, Optional

from server_modules import activity_ledger_service
from server_modules import control_plane_repository
from server_modules import credit_ledger_contract
from server_modules import secret_redaction_service


ACTION_DOMAINS = {"tool", "mcp", "skill", "connector", "rag", "runtime"}
ACTION_TYPES = {"read", "write", "execute", "invoke", "retrieve", "meter", "policy"}
ACTION_STATUSES = {"started", "completed", "failed", "blocked", "approval_required", "approved", "denied", "policy_decision"}
BILLING_MODES = {"none", "transparency", "metered", "ai_token_usage_ref"}


def _run_coro_sync(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise

    result: Dict[str, Any] = {}
    failure: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as err:  # pragma: no cover
            failure["error"] = err

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in failure:
        raise failure["error"]
    return result.get("value")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _optional_text(value: Any) -> Optional[str]:
    token = _text(value)
    return token or None


def _bounded(value: Any, *, limit: int = 1000) -> str:
    return secret_redaction_service.redact_text(_text(value))[:limit]


def _coerce_metadata(value: Any) -> Dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    return secret_redaction_service.sanitize_mapping(payload)


def _stable_suffix(*parts: Any) -> str:
    raw = "::".join(_text(part) for part in parts if _text(part))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16] if raw else ""


def build_source_event_id(
    *,
    source_surface: Any,
    run_id: Any = None,
    thread_id: Any = None,
    node_id: Any = None,
    tool_call_id: Any = None,
    action_name: Any = None,
    attempt: Any = None,
) -> Optional[str]:
    parts = [
        _lower(source_surface),
        _text(run_id) or _text(thread_id),
        _text(node_id) or _text(tool_call_id),
        _text(action_name),
        _text(attempt) or "1",
    ]
    if not any(parts):
        return None
    suffix = _stable_suffix(*parts)
    return ":".join(part for part in [parts[0] or "agent_action", suffix] if part)


def _normalize_event(
    *,
    tenant_id: str,
    workspace_id: str,
    surface: Any,
    source_surface: Any,
    action_domain: Any,
    action_type: Any,
    action_name: Any,
    status: Any,
    payer: Any = "platform_credits",
    billing_mode: Any = "none",
    credit_type: Any = None,
    credits_debited: Any = 0,
    platform_cost_usd: Any = 0,
    usage_ref: Any = None,
    metadata: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    resolved_surface = _lower(surface)
    if resolved_surface not in credit_ledger_contract.PRODUCT_SURFACES:
        raise ValueError("surface must be sage, studio, or mini_app.")
    resolved_domain = _lower(action_domain)
    if resolved_domain not in ACTION_DOMAINS:
        raise ValueError("action_domain is not supported.")
    resolved_action_type = _lower(action_type) or "invoke"
    if resolved_action_type not in ACTION_TYPES:
        resolved_action_type = "invoke"
    resolved_status = _lower(status)
    if resolved_status not in ACTION_STATUSES:
        raise ValueError("status is not supported.")
    resolved_payer = _text(payer) or "platform_credits"
    if resolved_payer not in credit_ledger_contract.LEDGER_PAYERS:
        raise ValueError("payer is not supported.")
    resolved_billing_mode = _lower(billing_mode) or "none"
    if resolved_billing_mode not in BILLING_MODES:
        raise ValueError("billing_mode is not supported.")
    resolved_credit_type = _lower(credit_type) or None
    if resolved_credit_type:
        if resolved_credit_type not in credit_ledger_contract.CREDIT_LEDGER_TYPES:
            raise ValueError("credit_type is not supported.")
        if resolved_credit_type == "ai_tokens" and resolved_billing_mode != "ai_token_usage_ref":
            raise ValueError("agent action events must not debit ai_tokens.")
    if resolved_billing_mode == "metered" and not resolved_credit_type:
        raise ValueError("metered agent actions require a non-AI credit_type.")
    event = {
        "surface": resolved_surface,
        "source_surface": _lower(source_surface) or resolved_surface,
        "action_domain": resolved_domain,
        "action_type": resolved_action_type,
        "action_name": _text(action_name) or f"{resolved_domain}.{resolved_action_type}",
        "tool_kind": _lower(kwargs.get("tool_kind")) or None,
        "tool_id": _optional_text(kwargs.get("tool_id")),
        "connector_id": _optional_text(kwargs.get("connector_id")),
        "skill_id": _optional_text(kwargs.get("skill_id")),
        "mcp_server_id": _optional_text(kwargs.get("mcp_server_id")),
        "mcp_tool_id": _optional_text(kwargs.get("mcp_tool_id")),
        "runtime_target": _optional_text(kwargs.get("runtime_target")),
        "capability_id": _optional_text(kwargs.get("capability_id")),
        "risk_level": _lower(kwargs.get("risk_level")) or None,
        "approval_required": bool(kwargs.get("approval_required")),
        "approval_id": _optional_text(kwargs.get("approval_id")),
        "approval_status": _lower(kwargs.get("approval_status")) or None,
        "policy_decision": _lower(kwargs.get("policy_decision")) or None,
        "status": resolved_status,
        "error_code": _optional_text(kwargs.get("error_code")),
        "payer": resolved_payer,
        "billing_mode": resolved_billing_mode,
        "credit_type": resolved_credit_type,
        "credits_debited": max(0.0, float(credits_debited or 0.0)),
        "platform_cost_usd": max(0.0, float(platform_cost_usd or 0.0)),
        "usage_ref": dict(usage_ref or {}) if isinstance(usage_ref, dict) else {},
        "trace_id": _optional_text(kwargs.get("trace_id")),
        "user_id": _optional_text(kwargs.get("user_id")),
        "thread_id": _optional_text(kwargs.get("thread_id")),
        "run_id": _optional_text(kwargs.get("run_id")),
        "agent_id": _optional_text(kwargs.get("agent_id")),
        "app_id": _optional_text(kwargs.get("app_id")),
        "input_summary": _bounded(kwargs.get("input_summary")),
        "output_summary": _bounded(kwargs.get("output_summary")),
        "metadata": _coerce_metadata(metadata),
    }
    secret_redaction_service.assert_secrets_free(event, context="agent_action_event")
    return event


async def append_agent_action_event(
    *,
    tenant_id: str,
    workspace_id: str,
    surface: str,
    source_surface: Optional[str],
    action_domain: str,
    action_type: str,
    action_name: str,
    status: str,
    payer: str = "platform_credits",
    billing_mode: str = "none",
    credit_type: Optional[str] = None,
    credits_debited: float = 0.0,
    platform_cost_usd: float = 0.0,
    usage_ref: Optional[Dict[str, Any]] = None,
    source_table: Optional[str] = None,
    source_event_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    project_activity: bool = True,
    write_credit_ledger: bool = True,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    event = _normalize_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        surface=surface,
        source_surface=source_surface,
        action_domain=action_domain,
        action_type=action_type,
        action_name=action_name,
        status=status,
        payer=payer,
        billing_mode=billing_mode,
        credit_type=credit_type,
        credits_debited=credits_debited,
        platform_cost_usd=platform_cost_usd,
        usage_ref=usage_ref,
        **kwargs,
    )
    if not source_event_id and not idempotency_key:
        source_event_id = build_source_event_id(
            source_surface=event["source_surface"],
            run_id=event.get("run_id"),
            thread_id=event.get("thread_id"),
            node_id=(kwargs.get("node_id") or kwargs.get("tool_call_id")),
            action_name=event.get("action_name"),
            attempt=kwargs.get("attempt"),
        )
    idempotency_key = idempotency_key or source_event_id
    row = await control_plane_repository.record_agent_action_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        event=event,
        source_table=source_table or "agent_action_events",
        source_event_id=source_event_id,
        idempotency_key=idempotency_key,
    )
    event_id = str((row or {}).get("id") or "").strip() or None
    if write_credit_ledger and event["billing_mode"] == "metered" and event.get("credit_type"):
        await control_plane_repository.record_credit_ledger_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_table="agent_action_events",
            source_event_id=event_id or source_event_id,
            event={
                "surface": event["surface"],
                "source_surface": event["source_surface"],
                "payer": event["payer"],
                "credit_type": event["credit_type"],
                "runtime_target": event.get("runtime_target"),
                "workspace_id": workspace_id,
                "user_id": event.get("user_id"),
                "thread_id": event.get("thread_id"),
                "run_id": event.get("run_id"),
                "agent_id": event.get("agent_id"),
                "app_id": event.get("app_id"),
                "provider_usage": {"action_domain": event["action_domain"], "action_name": event["action_name"]},
                "platform_cost_usd": event["platform_cost_usd"],
                "credits_debited": event["credits_debited"],
                "estimation_mode": "agent_action_metered",
                "metadata": {
                    "agent_action_event_id": event_id,
                    "action_type": event["action_type"],
                    "tool_id": event.get("tool_id"),
                    "connector_id": event.get("connector_id"),
                    "skill_id": event.get("skill_id"),
                    "mcp_server_id": event.get("mcp_server_id"),
                    "mcp_tool_id": event.get("mcp_tool_id"),
                },
            },
        )
    if project_activity and event["status"] in {"completed", "failed", "blocked", "approval_required"}:
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="system",
            actor_id="agent_action_meter",
            event_class="blocked_action" if event["status"] == "blocked" else "system_activity",
            detail_level="timeline_detail",
            app_id=event.get("app_id"),
            run_id=event.get("run_id"),
            thread_id=event.get("thread_id"),
            session_key=event.get("thread_id") or event.get("run_id"),
            channel=event["surface"],
            direction="system",
            action=f"agent_action.{event['status']}",
            trace_id=event.get("trace_id"),
            title=f"{event['action_domain'].title()} action {event['status'].replace('_', ' ')}",
            summary=event.get("output_summary") or event.get("input_summary") or event["action_name"],
            status=event["status"],
            review_required=event["status"] == "approval_required",
            payload={
                "agent_action_event_id": event_id,
                "action_domain": event["action_domain"],
                "action_type": event["action_type"],
                "action_name": event["action_name"],
                "billing_mode": event["billing_mode"],
                "credit_type": event.get("credit_type"),
            },
            metadata={"source": "agent_action_metering_service"},
        )
    return row


def append_agent_action_event_sync(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return _run_coro_sync(append_agent_action_event(**kwargs))


async def record_started(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return await append_agent_action_event(status="started", project_activity=False, **kwargs)


def record_started_sync(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return append_agent_action_event_sync(status="started", project_activity=False, **kwargs)


async def record_policy_decision(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return await append_agent_action_event(status="policy_decision", project_activity=False, **kwargs)


def record_policy_decision_sync(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return append_agent_action_event_sync(status="policy_decision", project_activity=False, **kwargs)


async def record_completed(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return await append_agent_action_event(status="completed", **kwargs)


def record_completed_sync(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return append_agent_action_event_sync(status="completed", **kwargs)


async def record_failed(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return await append_agent_action_event(status="failed", **kwargs)


def record_failed_sync(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return append_agent_action_event_sync(status="failed", **kwargs)


async def record_blocked(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return await append_agent_action_event(status="blocked", **kwargs)


def record_blocked_sync(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return append_agent_action_event_sync(status="blocked", **kwargs)


async def record_approval_required(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return await append_agent_action_event(
        status="approval_required",
        approval_required=True,
        approval_status=kwargs.pop("approval_status", "pending"),
        **kwargs,
    )


def record_approval_required_sync(**kwargs: Any) -> Optional[Dict[str, Any]]:
    return append_agent_action_event_sync(
        status="approval_required",
        approval_required=True,
        approval_status=kwargs.pop("approval_status", "pending"),
        **kwargs,
    )

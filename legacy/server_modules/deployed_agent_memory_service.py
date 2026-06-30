from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from server_modules import activity_ledger_service
from server_modules import control_plane_repository, session_transcript_store
from server_modules import deployed_agent_business_insights_service
from server_modules import deployed_agent_config_schema
from server_modules import failure_policy_service
from server_modules import memory_summary_service
from server_modules import provider_profiles
from server_modules import workspace_context_memory_adapter
from server_modules.conversation_memory_policy import (
    EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
    MemoryPolicyProfile,
    build_external_channel_memory_profile,
    get_memory_policy_profile,
)
from server_modules.conversation_compaction import (
    compact_conversation_history,
    estimate_conversation_tokens,
)
from server_modules.error_contracts import SEVERITY_WARNING, STORAGE_WRITE_FAILURE


_CHANNEL_MEMORY_POLICY = get_memory_policy_profile(EXTERNAL_CHANNEL_CUSTOMER_PROFILE)
DEFAULT_MEMORY_FETCH_LIMIT = 40
DEFAULT_MEMORY_FETCH_LIMIT_WITH_SUMMARY = 18
DEFAULT_MEMORY_TOKEN_LIMIT = _CHANNEL_MEMORY_POLICY.max_prompt_tokens
DEFAULT_MEMORY_SUMMARY_TRIGGER_MESSAGES = _CHANNEL_MEMORY_POLICY.summary_trigger_messages
DEFAULT_MEMORY_SUMMARY_TRIGGER_TOKENS = _CHANNEL_MEMORY_POLICY.summary_trigger_tokens
DEFAULT_MEMORY_PRESERVE_LAST_MESSAGES = _CHANNEL_MEMORY_POLICY.preserve_last_messages
DEFAULT_MEMORY_BUSINESS_PLAN_MAX_CHARS = _CHANNEL_MEMORY_POLICY.max_business_plan_chars
logger = logging.getLogger(__name__)


def _metadata(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _compact_text(value: Any, *, limit: int = 320) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _external_safe_memory_text(value: Any, *, limit: int = 320) -> str:
    sanitized = workspace_context_memory_adapter.strip_red_facts_from_external_context(str(value or ""))
    if not sanitized:
        return ""
    return _compact_text(sanitized, limit=limit)


async def _append_memory_audit_event(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Dict[str, Any],
    channel_key: str,
    external_user_id: str,
    session_key: str,
    thread_id: str,
    summary_present: bool,
    summary_length: int,
    source_message_count: int,
    recent_message_count: int,
) -> None:
    deployed_agent_id = str((deployed_agent or {}).get("id") or "").strip()
    if not deployed_agent_id:
        return
    try:
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="deployed_agent",
            actor_id=deployed_agent_id,
            install_id=str((deployed_agent or {}).get("backing_install_id") or "").strip() or None,
            event_class="memory_update",
            detail_level="audit_reference",
            action="deployed_agent_memory_snapshot_written",
            title="Deployed-agent memory updated",
            summary=f"Memory snapshot updated for {channel_key} channel.",
            payload={
                "deployed_agent_id": deployed_agent_id,
                "channel_key": str(channel_key or "").strip().lower() or None,
                "external_user_id": str(external_user_id or "").strip() or None,
                "session_key": str(session_key or "").strip() or None,
                "thread_id": str(thread_id or "").strip() or None,
                "summary_present": bool(summary_present),
                "summary_length": int(summary_length or 0),
                "source_message_count": int(source_message_count or 0),
                "recent_message_count": int(recent_message_count or 0),
            },
            metadata={
                "deployed_agent_id": deployed_agent_id,
                "workspace_id": str(workspace_id or "").strip() or None,
                "memory_policy": {
                    "enabled": True,
                },
            },
        )
    except Exception:
        logger.exception(
            "Failed to append deployed-agent memory activity event",
            extra={
                "workspace_id": workspace_id,
                "deployed_agent_id": deployed_agent_id,
                "channel_key": channel_key,
            },
        )


def deployed_agent_memory_enabled(deployed_agent: Optional[Dict[str, Any]]) -> bool:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    return bool(config.memory_policy.memory_enabled)


def _memory_policy_for_deployed_agent(
    deployed_agent: Optional[Dict[str, Any]],
) -> MemoryPolicyProfile:
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    context_window_tokens = provider_profiles.context_window_for_model(config.provider, config.model)
    return build_external_channel_memory_profile(
        context_budget_preset=config.memory_policy.context_budget_preset,
        retention_preset=config.memory_policy.retention_preset,
        context_window_tokens=context_window_tokens,
    )


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    token = str(value or "").strip()
    if not token:
        return None
    normalized = token.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _retention_cutoff(retention_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days or 1)))


def _within_retention_window(value: Any, *, retention_days: int) -> bool:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return True
    return parsed >= _retention_cutoff(retention_days)


def _filter_events_for_retention(
    events: List[Dict[str, Any]],
    *,
    retention_days: int,
) -> List[Dict[str, Any]]:
    return [
        dict(item)
        for item in list(events or [])
        if isinstance(item, dict) and _within_retention_window(item.get("created_at"), retention_days=retention_days)
    ]


def _conversation_key(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
) -> Dict[str, str]:
    return {
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "deployed_agent_id": str(deployed_agent_id or "").strip(),
        "channel_key": str(channel_key or "").strip().lower(),
        "external_user_id": str(external_user_id or "").strip(),
    }


def _event_role(event: Dict[str, Any]) -> Optional[str]:
    direction = str(event.get("direction") or "").strip().lower()
    if direction == "inbound":
        return "user"
    if direction == "outbound":
        return "assistant"
    return None


def _normalize_history_messages(
    *,
    events: List[Dict[str, Any]],
    exclude_event_id: Optional[str] = None,
) -> List[Dict[str, str]]:
    resolved_exclude_event_id = str(exclude_event_id or "").strip()
    chronological = list(reversed([dict(item) for item in events if isinstance(item, dict)]))
    normalized: List[Dict[str, str]] = []
    for event in chronological:
        if resolved_exclude_event_id and str(event.get("id") or "").strip() == resolved_exclude_event_id:
            continue
        role = _event_role(event)
        content = _external_safe_memory_text(event.get("text"), limit=900)
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def _render_memory_business_plan(
    prior_messages: List[Dict[str, str]],
    *,
    max_chars: int,
) -> str:
    if not prior_messages:
        return ""
    lines: List[str] = [
        "Conversation memory for this returning channel user. Use it only when relevant to the current request.",
    ]
    for item in prior_messages:
        role = str(item.get("role") or "").strip().lower()
        content = _external_safe_memory_text(item.get("content"), limit=900)
        if not content:
            continue
        if role == "assistant" and content.lower().startswith("persistent customer memory:"):
            lines.extend(["", content])
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    rendered = "\n".join(lines).strip()
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 1].rstrip() + "…"


async def _list_memory_events(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    backing_install_id: Optional[str],
    channel_key: str,
    session_key: str,
    responder_install_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    resolved_backing_install_id = str(backing_install_id or "").strip()
    resolved_session_key = str(session_key or "").strip()
    if resolved_backing_install_id and resolved_session_key:
        events = await control_plane_repository.list_deployed_agent_conversation_events(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            backing_install_id=resolved_backing_install_id,
            session_id=resolved_session_key,
            limit=limit,
        )
        return list(reversed(events))
    return await control_plane_repository.list_agent_channel_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        channel_key=channel_key,
        session_key=resolved_session_key,
        responder_install_id=str(responder_install_id or "").strip() or None,
        limit=limit,
    )


async def load_deployed_agent_memory_context(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Optional[Dict[str, Any]],
    channel_key: str,
    external_user_id: str,
    session_key: str,
    responder_install_id: Optional[str],
    exclude_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    deployed_agent_id = str((deployed_agent or {}).get("id") or "").strip()
    if not deployed_agent_id or not deployed_agent_memory_enabled(deployed_agent):
        return {
            "applied": False,
            "enabled": False,
            "prior_messages": [],
            "business_plan": "",
        }
    policy = _memory_policy_for_deployed_agent(deployed_agent)
    key = _conversation_key(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
        channel_key=channel_key,
        external_user_id=external_user_id,
    )
    backing_install_id = str((deployed_agent or {}).get("backing_install_id") or "").strip() or None
    memory_row = await control_plane_repository.get_deployed_agent_conversation_memory(**key)
    existing_summary = (
        _external_safe_memory_text(
            (memory_row or {}).get("summary_text"),
            limit=DEFAULT_MEMORY_BUSINESS_PLAN_MAX_CHARS,
        )
        if _within_retention_window(
            (memory_row or {}).get("updated_at") or (memory_row or {}).get("created_at"),
            retention_days=policy.retention.summary_days,
        )
        else ""
    )
    fetch_limit = (
        DEFAULT_MEMORY_FETCH_LIMIT_WITH_SUMMARY
        if existing_summary
        else DEFAULT_MEMORY_FETCH_LIMIT
    )
    events = await _list_memory_events(
        tenant_id=key["tenant_id"],
        workspace_id=key["workspace_id"],
        deployed_agent_id=deployed_agent_id,
        backing_install_id=backing_install_id,
        channel_key=key["channel_key"],
        session_key=str(session_key or "").strip(),
        responder_install_id=str(responder_install_id or "").strip() or None,
        limit=fetch_limit,
    )
    events = _filter_events_for_retention(
        events,
        retention_days=policy.retention.event_memory_days,
    )
    recent_messages = _normalize_history_messages(events=events, exclude_event_id=exclude_event_id)
    history_messages: List[Dict[str, str]] = []
    if existing_summary:
        history_messages.append({"role": "assistant", "content": existing_summary})
    history_messages.extend(recent_messages)
    if not history_messages:
        return {
            "applied": False,
            "enabled": True,
            "memory_key": key,
            "prior_messages": [],
            "business_plan": "",
            "summary_present": False,
            "message_count": 0,
        }
    compaction = compact_conversation_history(
        history_messages,
        max_tokens=policy.max_prompt_tokens,
        preserve_last_messages=policy.preserve_last_messages,
    )
    prior_messages = [
        {"role": str(item.get("role") or "").strip().lower(), "content": str(item.get("content") or "").strip()}
        for item in list(compaction.get("messages") or [])
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]
    return {
        "applied": bool(prior_messages),
        "enabled": True,
        "memory_key": key,
        "prior_messages": prior_messages,
        "business_plan": _render_memory_business_plan(
            prior_messages,
            max_chars=policy.max_business_plan_chars,
        ),
        "summary_present": bool(existing_summary),
        "message_count": len(recent_messages),
        "compacted": bool(compaction.get("compacted")),
        "summary_text": existing_summary or None,
        "context_budget_preset": deployed_agent_config_schema.deployed_agent_config_from_record(
            deployed_agent
        ).memory_policy.context_budget_preset,
        "retention_preset": deployed_agent_config_schema.deployed_agent_config_from_record(
            deployed_agent
        ).memory_policy.retention_preset,
    }


async def persist_deployed_agent_memory_snapshot(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Optional[Dict[str, Any]],
    channel_key: str,
    external_user_id: str,
    session_key: str,
    thread_id: str,
    responder_install_id: Optional[str],
    user_message: str,
    assistant_reply: str,
) -> Optional[Dict[str, Any]]:
    deployed_agent_id = str((deployed_agent or {}).get("id") or "").strip()
    if not deployed_agent_id or not deployed_agent_memory_enabled(deployed_agent):
        return None
    config = deployed_agent_config_schema.deployed_agent_config_from_record(deployed_agent)
    policy = _memory_policy_for_deployed_agent(deployed_agent)
    key = _conversation_key(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
        channel_key=channel_key,
        external_user_id=external_user_id,
    )
    backing_install_id = str((deployed_agent or {}).get("backing_install_id") or "").strip() or None
    existing_row = await control_plane_repository.get_deployed_agent_conversation_memory(**key)
    existing_summary = (
        str((existing_row or {}).get("summary_text") or "").strip()
        if _within_retention_window(
            (existing_row or {}).get("updated_at") or (existing_row or {}).get("created_at"),
            retention_days=policy.retention.summary_days,
        )
        else ""
    )
    events = await _list_memory_events(
        tenant_id=key["tenant_id"],
        workspace_id=key["workspace_id"],
        deployed_agent_id=deployed_agent_id,
        backing_install_id=backing_install_id,
        channel_key=key["channel_key"],
        session_key=str(session_key or "").strip(),
        responder_install_id=str(responder_install_id or "").strip() or None,
        limit=DEFAULT_MEMORY_FETCH_LIMIT,
    )
    events = _filter_events_for_retention(
        events,
        retention_days=policy.retention.event_memory_days,
    )
    messages = _normalize_history_messages(events=events)
    if not messages:
        return None
    source_messages = list(messages)
    prior_messages: List[Dict[str, str]] = []
    if existing_summary:
        prior_messages.append({"role": "assistant", "content": existing_summary})
    prior_messages.extend(messages)
    total_tokens = estimate_conversation_tokens(prior_messages)
    recent_message_count = min(len(source_messages), policy.preserve_last_messages)
    summary_text = existing_summary
    should_roll_summary = (
        bool(existing_summary)
        or len(source_messages) >= policy.summary_trigger_messages
        or total_tokens > policy.summary_trigger_tokens
    )
    if should_roll_summary:
        preserved_tail = source_messages[-recent_message_count:] if recent_message_count else []
        older_messages = source_messages[:-recent_message_count] if recent_message_count else source_messages
        summary_text = memory_summary_service.build_deployed_agent_memory_summary(
            messages=older_messages,
            prior_summary=existing_summary or None,
        )
        if not summary_text and existing_summary:
            summary_text = existing_summary
        if not summary_text and preserved_tail:
            summary_text = memory_summary_service.build_deployed_agent_memory_summary(
                messages=preserved_tail[:-1],
                prior_summary=None,
            )
    transcript_messages = prior_messages[-max(2, min(len(prior_messages), policy.max_transcript_items)) :]
    try:
        session_transcript_store.save_session_transcript(
            workspace_id=workspace_id,
            agent_install_id=str((deployed_agent or {}).get("backing_install_id") or "").strip() or None,
            thread_id=thread_id,
            provider=None,
            model=None,
            messages=transcript_messages,
            user_message=str(user_message or "").strip(),
            assistant_reply=str(assistant_reply or "").strip(),
        )
    except Exception as exc:
        failure_policy_service.log_degraded_operation(
            logger=logger,
            code="deployed_agent_transcript_persist_failed",
            message="Failed to persist deployed-agent transcript snapshot.",
            error_class=STORAGE_WRITE_FAILURE,
            degraded_component="deployed_agent_memory_transcript",
            severity=SEVERITY_WARNING,
            retryable=False,
            status_code=500,
            metadata={
                "workspace_id": workspace_id,
                "deployed_agent_id": deployed_agent_id,
                "thread_id": str(thread_id or "").strip() or None,
                "session_key": str(session_key or "").strip() or None,
            },
            exc=exc,
        )
    memory_payload = await control_plane_repository.upsert_deployed_agent_conversation_memory(
        **key,
        session_key=session_key,
        summary_text=summary_text or "",
        recent_message_count=recent_message_count,
        source_message_count=len(source_messages),
        metadata={
            "last_thread_id": str(thread_id or "").strip() or None,
            "last_session_key": str(session_key or "").strip() or None,
            "summary_present": bool(summary_text),
            "last_user_message": _compact_text(user_message, limit=280),
            "last_assistant_reply": _compact_text(assistant_reply, limit=420),
            "context_budget_preset": config.memory_policy.context_budget_preset,
            "retention_preset": config.memory_policy.retention_preset,
            "retention_days": policy.retention.event_memory_days,
        },
    )
    await _append_memory_audit_event(
        tenant_id=key["tenant_id"],
        workspace_id=key["workspace_id"],
        deployed_agent=dict(deployed_agent or {}),
        channel_key=key["channel_key"],
        external_user_id=key["external_user_id"],
        session_key=str(session_key or "").strip(),
        thread_id=str(thread_id or "").strip(),
        summary_present=bool(summary_text),
        summary_length=len(str(summary_text or "").strip()),
        source_message_count=len(source_messages),
        recent_message_count=recent_message_count,
    )
    try:
        await deployed_agent_business_insights_service.record_deployed_agent_turn_insights(
            tenant_id=key["tenant_id"],
            workspace_id=key["workspace_id"],
            deployed_agent_id=deployed_agent_id,
            channel_key=key["channel_key"],
            user_message=user_message,
            assistant_reply=assistant_reply,
        )
    except Exception as exc:
        failure_policy_service.log_degraded_operation(
            logger=logger,
            code="deployed_agent_business_insights_persist_failed",
            message="Failed to persist deployed-agent business insight candidate.",
            error_class=STORAGE_WRITE_FAILURE,
            degraded_component="deployed_agent_business_insights",
            severity=SEVERITY_WARNING,
            retryable=False,
            status_code=500,
            metadata={
                "workspace_id": workspace_id,
                "deployed_agent_id": deployed_agent_id,
                "thread_id": str(thread_id or "").strip() or None,
                "session_key": str(session_key or "").strip() or None,
            },
            exc=exc,
        )
    return memory_payload

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository


SUPPORTED_PERSONAL_CONTEXT_EVENT_TYPES = frozenset(
    {
        "message_awaiting_reply",
        "reminder_ignored",
        "study_session_missed",
        "study_session_completed",
        "flashcards_created",
        "language_session_logged",
        "nutrition_entry_logged",
        "saved_preference_updated",
        "screen_time_threshold_crossed",
        "calendar_conflict",
    }
)

DEFAULT_PRIORITY_BY_EVENT_TYPE: dict[str, int] = {
    "message_awaiting_reply": 75,
    "reminder_ignored": 60,
    "study_session_missed": 68,
    "study_session_completed": 32,
    "flashcards_created": 28,
    "language_session_logged": 30,
    "nutrition_entry_logged": 26,
    "saved_preference_updated": 18,
    "screen_time_threshold_crossed": 58,
    "calendar_conflict": 82,
}


class PersonalContextValidationError(Exception):
    pass


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _normalize_event_type(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in SUPPORTED_PERSONAL_CONTEXT_EVENT_TYPES:
        raise PersonalContextValidationError("Unsupported personal context event_type.")
    return token


def _normalize_source_app(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        raise PersonalContextValidationError("source_app is required.")
    return token


def _normalize_entity_id(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise PersonalContextValidationError("entity_id is required.")
    return token


def _normalize_priority(value: Any, *, event_type: str) -> int:
    if value is None:
        return DEFAULT_PRIORITY_BY_EVENT_TYPE.get(event_type, 50)
    try:
        priority = int(value)
    except (TypeError, ValueError) as exc:
        raise PersonalContextValidationError("priority must be an integer.") from exc
    return max(0, min(100, priority))


def _normalize_scope(scope: Any) -> Dict[str, Any]:
    raw = _coerce_dict(scope)
    audience = [
        str(item or "").strip().lower()
        for item in list(raw.get("audience") or [])
        if str(item or "").strip()
    ]
    if not audience:
        audience = ["sage"]
    deduped_audience: list[str] = []
    seen = set()
    for item in audience:
        if item in seen:
            continue
        seen.add(item)
        deduped_audience.append(item)
    install_ids = [
        str(item or "").strip()
        for item in list(raw.get("agent_install_ids") or [])
        if str(item or "").strip()
    ]
    deduped_install_ids: list[str] = []
    seen_install_ids = set()
    for item in install_ids:
        if item in seen_install_ids:
            continue
        seen_install_ids.add(item)
        deduped_install_ids.append(item)
    return {
        "audience": deduped_audience,
        "agent_install_ids": deduped_install_ids,
        "visibility": str(raw.get("visibility") or "structured_event").strip().lower() or "structured_event",
    }


def _default_summary(*, event_type: str, payload: Dict[str, Any], entity_id: str) -> str:
    if event_type == "message_awaiting_reply":
        who = str(payload.get("contact_name") or payload.get("sender") or payload.get("thread_label") or entity_id).strip()
        return f"Reply is still pending for {who}."
    if event_type == "reminder_ignored":
        title = str(payload.get("title") or payload.get("label") or entity_id).strip()
        return f"Reminder '{title}' was ignored."
    if event_type == "study_session_missed":
        label = str(payload.get("subject") or payload.get("title") or entity_id).strip()
        return f"Study session for {label} was missed."
    if event_type == "study_session_completed":
        label = str(payload.get("subject") or payload.get("title") or entity_id).strip()
        return f"Study session for {label} was completed."
    if event_type == "flashcards_created":
        count = int(payload.get("count") or 0)
        deck = str(payload.get("deck") or entity_id).strip()
        if count > 0:
            return f"{count} flashcards were created for {deck}."
        return f"New flashcards were created for {deck}."
    if event_type == "language_session_logged":
        language = str(payload.get("language") or payload.get("target_language") or entity_id).strip()
        phrase = str(payload.get("phrase") or "").strip()
        if phrase:
            return f"Language practice captured for {language}: {phrase}."
        return f"Language practice was logged for {language}."
    if event_type == "nutrition_entry_logged":
        meal = str(payload.get("meal") or entity_id).strip()
        calories = int(payload.get("calories") or 0)
        if calories > 0:
            return f"Nutrition entry logged for {meal} at {calories} kcal."
        return f"Nutrition entry logged for {meal}."
    if event_type == "saved_preference_updated":
        service_label = str(payload.get("service") or payload.get("service_id") or entity_id).strip()
        return f"Saved preferences were updated for {service_label}."
    if event_type == "screen_time_threshold_crossed":
        app_name = str(payload.get("app_name") or payload.get("app") or entity_id).strip()
        minutes = int(payload.get("minutes") or 0)
        if minutes > 0:
            return f"Screen time crossed the threshold in {app_name} after {minutes} minutes."
        return f"Screen time crossed the threshold in {app_name}."
    if event_type == "calendar_conflict":
        label = str(payload.get("title") or payload.get("calendar_label") or entity_id).strip()
        return f"Calendar conflict detected for {label}."
    return str(entity_id or event_type).strip()


def _visible_to_sage(scope: Dict[str, Any]) -> bool:
    audience = [str(item or "").strip().lower() for item in list(scope.get("audience") or [])]
    if not audience:
        return True
    return any(item in {"sage", "workspace", "all"} for item in audience)


def _visible_to_install(scope: Dict[str, Any], *, agent_install_id: Optional[str]) -> bool:
    install_id = str(agent_install_id or "").strip()
    if not install_id:
        return False
    allowed_install_ids = {
        str(item or "").strip()
        for item in list(scope.get("agent_install_ids") or [])
        if str(item or "").strip()
    }
    if install_id in allowed_install_ids:
        return True
    audience = {str(item or "").strip().lower() for item in list(scope.get("audience") or []) if str(item or "").strip()}
    return "all_specialists" in audience or f"install:{install_id.lower()}" in audience


def _project_event(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(record.get("id") or "").strip(),
        "source_app": str(record.get("source_app") or "").strip(),
        "event_type": str(record.get("event_type") or "").strip(),
        "entity_id": str(record.get("entity_id") or "").strip(),
        "summary": str(record.get("summary") or "").strip(),
        "payload": _coerce_dict(record.get("payload")),
        "priority": int(record.get("priority") or 0),
        "scope": _coerce_dict(record.get("scope")),
        "seen_by_sage_at": str(record.get("seen_by_sage_at") or "").strip() or None,
        "created_at": str(record.get("created_at") or "").strip() or None,
        "status": str(record.get("status") or "").strip() or "active",
        "metadata": _coerce_dict(record.get("metadata")),
    }


def summarize_context_feed(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    projected = [_project_event(item) for item in events if isinstance(item, dict)]
    by_type = Counter(item["event_type"] for item in projected if item["event_type"])
    by_source_app = Counter(item["source_app"] for item in projected if item["source_app"])
    unseen_count = sum(1 for item in projected if not item.get("seen_by_sage_at"))
    high_priority_count = sum(1 for item in projected if int(item.get("priority") or 0) >= 70)
    return {
        "count": len(projected),
        "unseen_count": unseen_count,
        "high_priority_count": high_priority_count,
        "by_type": dict(by_type),
        "by_source_app": dict(by_source_app),
    }


async def publish_event(
    *,
    tenant_id: str,
    workspace_id: str,
    source_app: str,
    event_type: str,
    entity_id: str,
    summary: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    priority: Optional[int] = None,
    scope: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_event_type = _normalize_event_type(event_type)
    resolved_source_app = _normalize_source_app(source_app)
    resolved_entity_id = _normalize_entity_id(entity_id)
    normalized_payload = _coerce_dict(payload)
    normalized_scope = _normalize_scope(scope)
    resolved_summary = str(summary or "").strip() or _default_summary(
        event_type=resolved_event_type,
        payload=normalized_payload,
        entity_id=resolved_entity_id,
    )
    if not resolved_summary:
        raise PersonalContextValidationError("summary is required.")
    record = await control_plane_repository.append_personal_context_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_app=resolved_source_app,
        event_type=resolved_event_type,
        entity_id=resolved_entity_id,
        summary=resolved_summary,
        payload=normalized_payload,
        priority=_normalize_priority(priority, event_type=resolved_event_type),
        scope=normalized_scope,
        metadata=_coerce_dict(metadata),
        status="active",
    )
    if not isinstance(record, dict):
        raise PersonalContextValidationError("Failed to persist personal context event.")
    projected = _project_event(record)
    try:
        from server_modules import activity_ledger_service

        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="application",
            actor_id=resolved_source_app,
            app_id=resolved_source_app,
            event_class="memory_update",
            detail_level="timeline_detail",
            action=resolved_event_type,
            title=f"{resolved_source_app.replace('_', ' ').title()} memory update",
            summary=resolved_summary,
            status="recorded",
            payload={
                "entity_id": resolved_entity_id,
                "event_type": resolved_event_type,
                "priority": _normalize_priority(priority, event_type=resolved_event_type),
            },
            metadata={
                "source_app": resolved_source_app,
                "scope": normalized_scope,
                "personal_context_event_id": str(record.get("id") or "").strip() or None,
            },
        )
    except Exception:
        pass
    try:
        from server_modules import bounded_scheduler_service

        wake_request = await bounded_scheduler_service.maybe_schedule_event_trigger(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            event=projected,
        )
        if isinstance(wake_request, dict):
            projected["wake_request"] = {
                "id": str(wake_request.get("id") or "").strip() or None,
                "trigger_kind": str(wake_request.get("trigger_kind") or "").strip() or None,
                "status": str(wake_request.get("status") or "").strip() or None,
                "due_at": str(wake_request.get("due_at") or "").strip() or None,
            }
    except Exception:
        pass
    return projected


async def list_events(
    *,
    tenant_id: str,
    workspace_id: str,
    audience: str = "sage",
    agent_install_id: Optional[str] = None,
    limit: int = 40,
    unseen_only: bool = False,
    source_app: Optional[str] = None,
    event_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    resolved_audience = str(audience or "sage").strip().lower() or "sage"
    if resolved_audience not in {"sage", "specialist"}:
        raise PersonalContextValidationError("audience must be 'sage' or 'specialist'.")
    if resolved_audience == "specialist" and not str(agent_install_id or "").strip():
        raise PersonalContextValidationError("agent_install_id is required for specialist personal context queries.")
    fetch_limit = max(1, min(max(int(limit or 40), 1) * 5, 500))
    rows = await control_plane_repository.list_personal_context_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_app=str(source_app or "").strip().lower() or None,
        event_type=str(event_type or "").strip().lower() or None,
        unseen_only=bool(unseen_only),
        limit=fetch_limit,
    )
    visible: list[dict[str, Any]] = []
    for row in rows:
        scope = _normalize_scope(row.get("scope"))
        if resolved_audience == "sage":
            if not _visible_to_sage(scope):
                continue
        else:
            if not _visible_to_install(scope, agent_install_id=agent_install_id):
                continue
        visible.append(_project_event(row))
        if len(visible) >= max(1, int(limit or 40)):
            break
    return visible


async def mark_seen_by_sage(
    *,
    tenant_id: str,
    workspace_id: str,
    event_ids: Optional[List[str]] = None,
    mark_all: bool = False,
) -> Dict[str, Any]:
    return await control_plane_repository.mark_personal_context_events_seen_by_sage(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        event_ids=event_ids,
        mark_all=mark_all,
    )


async def build_sage_context_payload(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 20,
) -> Dict[str, Any]:
    events = await list_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        audience="sage",
        limit=limit,
        unseen_only=False,
    )
    return {
        "recent_changes": events,
        "summary": summarize_context_feed(events),
    }

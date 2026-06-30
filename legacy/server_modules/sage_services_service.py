from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import personal_context_engine, rust_runtime_kernel_client, workspace_context


SAGE_SERVICE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "flashcards": {
        "id": "flashcards",
        "name": "Flashcards",
        "description": "Store cards Sage can expand into study sessions, recall drills, and spaced review prompts.",
        "entry_noun": "card",
        "profile_fields": ("focus_topic", "active_deck"),
        "entry_fields": ("deck", "topic", "front", "back"),
    },
    "language_coach": {
        "id": "language_coach",
        "name": "Language Coach",
        "description": "Track what language you are practicing, recent phrases, and the next area Sage should coach.",
        "entry_noun": "practice item",
        "profile_fields": ("target_language", "current_level", "focus_area"),
        "entry_fields": ("language", "phrase", "translation", "notes", "confidence"),
    },
    "nutrition_log": {
        "id": "nutrition_log",
        "name": "Nutrition Log",
        "description": "Capture meals, calories, protein, and diet goals so Sage can keep a running daily picture.",
        "entry_noun": "meal",
        "profile_fields": ("daily_calorie_target", "daily_protein_target", "dietary_pattern"),
        "entry_fields": ("meal", "calories", "protein_grams", "occurred_on", "notes"),
    },
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_file(workspace_id: str) -> Path:
    return workspace_context.workspace_scope_dir(workspace_id) / "sage_services.json"


def _default_service_state(service_id: str) -> Dict[str, Any]:
    if service_id == "flashcards":
        profile = {"focus_topic": "", "active_deck": ""}
    elif service_id == "language_coach":
        profile = {"target_language": "", "current_level": "", "focus_area": ""}
    elif service_id == "nutrition_log":
        profile = {
            "daily_calorie_target": None,
            "daily_protein_target": None,
            "dietary_pattern": "",
        }
    else:
        profile = {}
    return {
        "profile": profile,
        "entries": [],
        "activity": [],
        "updated_at": None,
    }


def _default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _utc_now_iso(),
        "services": {
            service_id: _default_service_state(service_id)
            for service_id in SAGE_SERVICE_DEFINITIONS
        },
    }


def _safe_read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("services"), dict):
        raw["services"] = {}
    for service_id in SAGE_SERVICE_DEFINITIONS:
        service_state = raw["services"].get(service_id)
        if not isinstance(service_state, dict):
            raw["services"][service_id] = _default_service_state(service_id)
            continue
        raw["services"][service_id] = {
            **_default_service_state(service_id),
            **service_state,
            "profile": dict(service_state.get("profile") or {}) if isinstance(service_state.get("profile"), dict) else dict(_default_service_state(service_id)["profile"]),
            "entries": list(service_state.get("entries") or []) if isinstance(service_state.get("entries"), list) else [],
            "activity": list(service_state.get("activity") or []) if isinstance(service_state.get("activity"), list) else [],
        }
    return raw


def _save_state(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _state_file(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _utc_now_iso()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


class SageServicesRustGateError(RuntimeError):
    pass


def _enforce_sage_service_state_decision(
    *,
    operation: str,
    workspace_id: str,
    service_id: str,
    payload: Dict[str, Any],
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
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
            operation=operation,
            state_class="sage_services",
            workspace_id=_coerce_text(workspace_id),
            actor_id=_coerce_text(actor_user_id),
            status="active",
            payload={
                "service_id": _coerce_text(service_id),
                "state": normalized_payload,
            },
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = _coerce_text(decision.get("next_action"))
        if next_action != _coerce_text(operation):
            raise SageServicesRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise SageServicesRustGateError(exc.reason) from exc


def _require_service(service_id: str) -> dict[str, Any]:
    service = SAGE_SERVICE_DEFINITIONS.get(str(service_id or "").strip())
    if not service:
        raise HTTPException(status_code=404, detail="Sage service not found.")
    return service


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_optional_int(value: Any) -> Optional[int]:
    token = _coerce_text(value)
    if not token:
        return None
    try:
        return int(token)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Numeric fields must be integers.") from exc


def _summarize_entry(service_id: str, entry: Dict[str, Any]) -> str:
    if service_id == "flashcards":
        deck = _coerce_text(entry.get("deck")) or "General"
        front = _coerce_text(entry.get("front")) or "Card"
        back = _coerce_text(entry.get("back")) or "Answer"
        return f"{deck}: {front} -> {back}"
    if service_id == "language_coach":
        language = _coerce_text(entry.get("language")) or "Practice"
        phrase = _coerce_text(entry.get("phrase")) or "Phrase"
        translation = _coerce_text(entry.get("translation"))
        return f"{language}: {phrase}" + (f" -> {translation}" if translation else "")
    if service_id == "nutrition_log":
        meal = _coerce_text(entry.get("meal")) or "Meal"
        calories = _coerce_optional_int(entry.get("calories"))
        protein = _coerce_optional_int(entry.get("protein_grams"))
        parts = [meal]
        if calories is not None:
            parts.append(f"{calories} kcal")
        if protein is not None:
            parts.append(f"{protein}g protein")
        return " · ".join(parts)
    return _coerce_text(entry.get("summary")) or "Entry"


def _normalize_profile(service_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    _require_service(service_id)
    raw = dict(profile or {})
    if service_id == "flashcards":
        return {
            "focus_topic": _coerce_text(raw.get("focus_topic")),
            "active_deck": _coerce_text(raw.get("active_deck")),
        }
    if service_id == "language_coach":
        return {
            "target_language": _coerce_text(raw.get("target_language")),
            "current_level": _coerce_text(raw.get("current_level")),
            "focus_area": _coerce_text(raw.get("focus_area")),
        }
    if service_id == "nutrition_log":
        return {
            "daily_calorie_target": _coerce_optional_int(raw.get("daily_calorie_target")),
            "daily_protein_target": _coerce_optional_int(raw.get("daily_protein_target")),
            "dietary_pattern": _coerce_text(raw.get("dietary_pattern")),
        }
    return {}


def _normalize_entry_payload(
    service_id: str,
    entry: Dict[str, Any],
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw = dict(existing or {})
    raw.update(dict(entry or {}))
    if service_id == "flashcards":
        front = _coerce_text(raw.get("front"))
        back = _coerce_text(raw.get("back"))
        if not front or not back:
            raise HTTPException(status_code=400, detail="Flashcards require front and back text.")
        normalized = {
            "deck": _coerce_text(raw.get("deck")) or "General",
            "topic": _coerce_text(raw.get("topic")),
            "front": front,
            "back": back,
        }
    elif service_id == "language_coach":
        phrase = _coerce_text(raw.get("phrase"))
        if not phrase:
            raise HTTPException(status_code=400, detail="Language Coach entries require a phrase.")
        confidence = _coerce_optional_int(raw.get("confidence"))
        if confidence is not None:
            confidence = max(1, min(confidence, 5))
        normalized = {
            "language": _coerce_text(raw.get("language")) or _coerce_text(raw.get("target_language")) or "Target language",
            "phrase": phrase,
            "translation": _coerce_text(raw.get("translation")),
            "notes": _coerce_text(raw.get("notes")),
            "confidence": confidence,
        }
    elif service_id == "nutrition_log":
        meal = _coerce_text(raw.get("meal"))
        if not meal:
            raise HTTPException(status_code=400, detail="Nutrition Log entries require a meal label.")
        occurred_on = _coerce_text(raw.get("occurred_on")) or date.today().isoformat()
        normalized = {
            "meal": meal,
            "calories": _coerce_optional_int(raw.get("calories")),
            "protein_grams": _coerce_optional_int(raw.get("protein_grams")),
            "occurred_on": occurred_on,
            "notes": _coerce_text(raw.get("notes")),
        }
    else:
        normalized = dict(raw)
    normalized["summary"] = _summarize_entry(service_id, normalized)
    return normalized


def _append_activity(
    service_state: Dict[str, Any],
    *,
    action: str,
    summary: str,
    actor_user_id: Optional[str],
    entry_id: Optional[str] = None,
    policy_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    activity = list(service_state.get("activity") or [])
    activity.insert(0, {
        "id": f"svc-activity-{uuid.uuid4().hex}",
        "action": action,
        "summary": summary,
        "entry_id": entry_id,
        "actor_user_id": _coerce_text(actor_user_id) or None,
        "policy": dict(policy_metadata or {}),
        "created_at": _utc_now_iso(),
    })
    service_state["activity"] = activity[:20]
    service_state["updated_at"] = _utc_now_iso()


def _resolve_sage_write_policy(
    *,
    actor_user_id: Optional[str],
    write_authorization: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    actor = _coerce_text(actor_user_id)
    raw = dict(write_authorization or {}) if isinstance(write_authorization, dict) else {}
    explicit_user_intent = bool(raw.get("explicit_user_intent") or raw.get("confirm_write") or raw.get("user_intent"))
    approval_granted = bool(raw.get("approval_granted") or raw.get("approved"))
    approval_id = _coerce_text(raw.get("approval_id")) or None
    approval_source = _coerce_text(raw.get("approval_source")) or None
    if actor:
        return {
            "enforced": True,
            "allowed": True,
            "mode": "actor_user",
            "explicit_user_intent": True,
            "approval_granted": bool(approval_granted or approval_id),
            "approval_id": approval_id,
            "approval_source": approval_source or "workspace_member_api",
        }
    if explicit_user_intent:
        return {
            "enforced": True,
            "allowed": True,
            "mode": "explicit_intent",
            "explicit_user_intent": True,
            "approval_granted": bool(approval_granted or approval_id),
            "approval_id": approval_id,
            "approval_source": approval_source,
        }
    if approval_granted or approval_id:
        return {
            "enforced": True,
            "allowed": True,
            "mode": "approval",
            "explicit_user_intent": False,
            "approval_granted": True,
            "approval_id": approval_id,
            "approval_source": approval_source or "runtime_approval",
        }
    raise HTTPException(
        status_code=403,
        detail=(
            "Sage service write blocked. "
            "Provide explicit_user_intent=true or an approval grant before writing."
        ),
    )


def _entry_preview(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _coerce_text(entry.get("id")),
        "summary": _coerce_text(entry.get("summary")),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
        "pinned": bool(entry.get("pinned")),
    }


def _memory_snapshot(service_id: str, service_state: Dict[str, Any]) -> Dict[str, Any]:
    profile = dict(service_state.get("profile") or {})
    entries = [dict(item) for item in list(service_state.get("entries") or []) if isinstance(item, dict)]
    pinned_entries = [item for item in entries if bool(item.get("pinned"))]
    recent_entries = entries[:3]
    preferences: List[str] = []
    active_context: List[str] = []
    app_state: List[str] = []

    if service_id == "flashcards":
        if _coerce_text(profile.get("focus_topic")):
            preferences.append(f"Focus topic: {_coerce_text(profile.get('focus_topic'))}")
        if _coerce_text(profile.get("active_deck")):
            active_context.append(f"Active deck: {_coerce_text(profile.get('active_deck'))}")
        app_state.append(f"{len(entries)} cards stored")
    elif service_id == "language_coach":
        if _coerce_text(profile.get("target_language")):
            preferences.append(f"Target language: {_coerce_text(profile.get('target_language'))}")
        if _coerce_text(profile.get("current_level")):
            preferences.append(f"Level: {_coerce_text(profile.get('current_level'))}")
        if _coerce_text(profile.get("focus_area")):
            active_context.append(f"Focus area: {_coerce_text(profile.get('focus_area'))}")
        app_state.append(f"{len(entries)} practice items logged")
    elif service_id == "nutrition_log":
        calorie_target = _coerce_optional_int(profile.get("daily_calorie_target"))
        protein_target = _coerce_optional_int(profile.get("daily_protein_target"))
        if calorie_target is not None:
            preferences.append(f"Daily calorie target: {calorie_target} kcal")
        if protein_target is not None:
            preferences.append(f"Daily protein target: {protein_target}g")
        if _coerce_text(profile.get("dietary_pattern")):
            preferences.append(f"Dietary pattern: {_coerce_text(profile.get('dietary_pattern'))}")
        today = date.today().isoformat()
        todays_entries = [item for item in entries if _coerce_text(item.get("occurred_on")) == today]
        total_calories = sum(int(item.get("calories") or 0) for item in todays_entries)
        total_protein = sum(int(item.get("protein_grams") or 0) for item in todays_entries)
        active_context.append(f"Today: {len(todays_entries)} meal entries")
        app_state.append(f"Today totals: {total_calories} kcal, {total_protein}g protein")

    if pinned_entries:
        active_context.extend(f"Pinned: {_coerce_text(item.get('summary'))}" for item in pinned_entries[:2])
    if recent_entries:
        app_state.extend(f"Recent: {_coerce_text(item.get('summary'))}" for item in recent_entries[:2])

    return {
        "preferences": preferences,
        "active_context": active_context,
        "app_state": app_state,
    }


def _service_payload(service_id: str, service_state: Dict[str, Any]) -> Dict[str, Any]:
    definition = _require_service(service_id)
    entries = [dict(item) for item in list(service_state.get("entries") or []) if isinstance(item, dict)]
    activity = [dict(item) for item in list(service_state.get("activity") or []) if isinstance(item, dict)]
    snapshot = _memory_snapshot(service_id, service_state)
    return {
        "id": service_id,
        "name": definition["name"],
        "description": definition["description"],
        "entry_noun": definition["entry_noun"],
        "profile": dict(service_state.get("profile") or {}),
        "entries": entries,
        "recent_entries": [_entry_preview(item) for item in entries[:5]],
        "recent_activity": activity[:6],
        "memory_snapshot": snapshot,
        "summary": {
            "entry_count": len(entries),
            "pinned_count": sum(1 for item in entries if bool(item.get("pinned"))),
            "updated_at": service_state.get("updated_at"),
        },
        "suggested_prompt": _suggested_prompt(service_id, service_state),
        "status": "active",
    }


def _suggested_prompt(service_id: str, service_state: Dict[str, Any]) -> str:
    profile = dict(service_state.get("profile") or {})
    if service_id == "flashcards":
        deck = _coerce_text(profile.get("active_deck")) or "my current deck"
        return f"Use my flashcards service and help me review {deck}."
    if service_id == "language_coach":
        language = _coerce_text(profile.get("target_language")) or "my target language"
        return f"Use my language coach state and give me a short practice session for {language}."
    if service_id == "nutrition_log":
        return "Use my nutrition log and summarize today’s meals against my targets."
    return "Use this Sage service in the next turn."


def list_sage_services(*, workspace_id: str) -> Dict[str, Any]:
    state = _safe_read_json(_state_file(workspace_id))
    items = [
        _service_payload(service_id, dict(state.get("services", {}).get(service_id) or {}))
        for service_id in SAGE_SERVICE_DEFINITIONS
    ]
    return {
        "items": items,
        "updated_at": state.get("updated_at"),
        "memory_context": build_sage_services_memory_block(workspace_id=workspace_id),
    }


def get_sage_service(*, workspace_id: str, service_id: str) -> Dict[str, Any]:
    state = _safe_read_json(_state_file(workspace_id))
    service_state = dict(state.get("services", {}).get(service_id) or {})
    return {"service": _service_payload(service_id, service_state), "updated_at": state.get("updated_at")}


def build_sage_services_memory_block(*, workspace_id: str) -> str:
    state = _safe_read_json(_state_file(workspace_id))
    items = [
        _service_payload(service_id, dict(state.get("services", {}).get(service_id) or {}))
        for service_id in SAGE_SERVICE_DEFINITIONS
    ]
    lines: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(str(item.get("name") or "Service"))
        snapshot = dict(item.get("memory_snapshot") or {})
        for label, values in (
            ("Preferences", snapshot.get("preferences")),
            ("Active context", snapshot.get("active_context")),
            ("App state", snapshot.get("app_state")),
        ):
            normalized_values = [str(value or "").strip() for value in list(values or []) if str(value or "").strip()]
            if normalized_values:
                lines.append(f"{label}: " + " | ".join(normalized_values[:3]))
    if not lines:
        return ""
    return "Sage services state\n" + "\n".join(f"- {line}" for line in lines)


async def update_service_profile(
    *,
    tenant_id: str,
    workspace_id: str,
    service_id: str,
    profile: Dict[str, Any],
    actor_user_id: Optional[str],
    write_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    write_policy = _resolve_sage_write_policy(
        actor_user_id=actor_user_id,
        write_authorization=write_authorization,
    )
    state = _safe_read_json(_state_file(workspace_id))
    service_state = dict(state.get("services", {}).get(service_id) or _default_service_state(service_id))
    service_state["profile"] = _normalize_profile(service_id, profile)
    _append_activity(
        service_state,
        action="profile_updated",
        summary="Saved service preferences for Sage memory.",
        actor_user_id=actor_user_id,
        policy_metadata=write_policy,
    )
    state["services"][service_id] = service_state
    _enforce_sage_service_state_decision(
        operation="update_sage_service_profile",
        workspace_id=workspace_id,
        service_id=service_id,
        payload=service_state,
        actor_user_id=actor_user_id,
    )
    _save_state(workspace_id, state)
    await _publish_service_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        service_id=service_id,
        actor_user_id=actor_user_id,
        action="profile_updated",
        summary="Service preferences updated.",
        payload={"profile": service_state["profile"]},
        policy_metadata=write_policy,
    )
    return get_sage_service(workspace_id=workspace_id, service_id=service_id)


async def create_service_entry(
    *,
    tenant_id: str,
    workspace_id: str,
    service_id: str,
    entry: Dict[str, Any],
    actor_user_id: Optional[str],
    write_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    write_policy = _resolve_sage_write_policy(
        actor_user_id=actor_user_id,
        write_authorization=write_authorization,
    )
    state = _safe_read_json(_state_file(workspace_id))
    service_state = dict(state.get("services", {}).get(service_id) or _default_service_state(service_id))
    normalized = _normalize_entry_payload(service_id, entry)
    normalized["id"] = f"{service_id}-{uuid.uuid4().hex[:12]}"
    normalized["created_at"] = _utc_now_iso()
    normalized["updated_at"] = normalized["created_at"]
    normalized["pinned"] = bool(entry.get("pinned"))
    entries = [normalized] + [dict(item) for item in list(service_state.get("entries") or []) if isinstance(item, dict)]
    service_state["entries"] = entries[:100]
    _append_activity(
        service_state,
        action="entry_created",
        summary=normalized["summary"],
        actor_user_id=actor_user_id,
        entry_id=normalized["id"],
        policy_metadata=write_policy,
    )
    state["services"][service_id] = service_state
    _enforce_sage_service_state_decision(
        operation="create_sage_service_entry",
        workspace_id=workspace_id,
        service_id=service_id,
        payload=normalized,
        actor_user_id=actor_user_id,
    )
    _save_state(workspace_id, state)
    await _publish_service_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        service_id=service_id,
        actor_user_id=actor_user_id,
        action="entry_created",
        summary=normalized["summary"],
        payload=normalized,
        policy_metadata=write_policy,
    )
    return get_sage_service(workspace_id=workspace_id, service_id=service_id)


async def update_service_entry(
    *,
    tenant_id: str,
    workspace_id: str,
    service_id: str,
    entry_id: str,
    entry: Dict[str, Any],
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    state = _safe_read_json(_state_file(workspace_id))
    service_state = dict(state.get("services", {}).get(service_id) or _default_service_state(service_id))
    entries = [dict(item) for item in list(service_state.get("entries") or []) if isinstance(item, dict)]
    match = next((item for item in entries if _coerce_text(item.get("id")) == _coerce_text(entry_id)), None)
    if not match:
        raise HTTPException(status_code=404, detail="Service entry not found.")
    normalized = _normalize_entry_payload(service_id, entry, existing=match)
    match.update(normalized)
    match["updated_at"] = _utc_now_iso()
    _append_activity(
        service_state,
        action="entry_updated",
        summary=match["summary"],
        actor_user_id=actor_user_id,
        entry_id=_coerce_text(match.get("id")),
    )
    service_state["entries"] = entries
    state["services"][service_id] = service_state
    _enforce_sage_service_state_decision(
        operation="update_sage_service_entry",
        workspace_id=workspace_id,
        service_id=service_id,
        payload=match,
        actor_user_id=actor_user_id,
    )
    _save_state(workspace_id, state)
    await _publish_service_event(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        service_id=service_id,
        actor_user_id=actor_user_id,
        action="entry_updated",
        summary=match["summary"],
        payload=match,
    )
    return get_sage_service(workspace_id=workspace_id, service_id=service_id)


async def delete_service_entry(
    *,
    tenant_id: str,
    workspace_id: str,
    service_id: str,
    entry_id: str,
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    state = _safe_read_json(_state_file(workspace_id))
    service_state = dict(state.get("services", {}).get(service_id) or _default_service_state(service_id))
    entries = [dict(item) for item in list(service_state.get("entries") or []) if isinstance(item, dict)]
    match = next((item for item in entries if _coerce_text(item.get("id")) == _coerce_text(entry_id)), None)
    if not match:
        raise HTTPException(status_code=404, detail="Service entry not found.")
    service_state["entries"] = [item for item in entries if _coerce_text(item.get("id")) != _coerce_text(entry_id)]
    _append_activity(
        service_state,
        action="entry_deleted",
        summary=f"Removed {_coerce_text(match.get('summary')) or 'service entry'}.",
        actor_user_id=actor_user_id,
        entry_id=_coerce_text(entry_id),
    )
    state["services"][service_id] = service_state
    _enforce_sage_service_state_decision(
        operation="delete_sage_service_entry",
        workspace_id=workspace_id,
        service_id=service_id,
        payload=match,
        actor_user_id=actor_user_id,
    )
    _save_state(workspace_id, state)
    return get_sage_service(workspace_id=workspace_id, service_id=service_id)


async def set_service_entry_pinned(
    *,
    tenant_id: str,
    workspace_id: str,
    service_id: str,
    entry_id: str,
    pinned: bool,
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    state = _safe_read_json(_state_file(workspace_id))
    service_state = dict(state.get("services", {}).get(service_id) or _default_service_state(service_id))
    entries = [dict(item) for item in list(service_state.get("entries") or []) if isinstance(item, dict)]
    match = next((item for item in entries if _coerce_text(item.get("id")) == _coerce_text(entry_id)), None)
    if not match:
        raise HTTPException(status_code=404, detail="Service entry not found.")
    match["pinned"] = bool(pinned)
    match["updated_at"] = _utc_now_iso()
    _append_activity(
        service_state,
        action="entry_pinned" if pinned else "entry_unpinned",
        summary=f"{'Pinned' if pinned else 'Unpinned'} {_coerce_text(match.get('summary')) or 'service entry'}.",
        actor_user_id=actor_user_id,
        entry_id=_coerce_text(entry_id),
    )
    service_state["entries"] = entries
    state["services"][service_id] = service_state
    _enforce_sage_service_state_decision(
        operation="set_sage_service_entry_pinned",
        workspace_id=workspace_id,
        service_id=service_id,
        payload=match,
        actor_user_id=actor_user_id,
    )
    _save_state(workspace_id, state)
    return get_sage_service(workspace_id=workspace_id, service_id=service_id)


async def _publish_service_event(
    *,
    tenant_id: str,
    workspace_id: str,
    service_id: str,
    actor_user_id: Optional[str],
    action: str,
    summary: str,
    payload: Dict[str, Any],
    policy_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if action == "profile_updated":
        event_type = "saved_preference_updated"
    else:
        event_type = {
            "flashcards": "flashcards_created",
            "language_coach": "language_session_logged",
            "nutrition_log": "nutrition_entry_logged",
        }.get(service_id, "flashcards_created")
    metadata = {
        "service_id": service_id,
        "action": action,
        "actor_user_id": _coerce_text(actor_user_id) or None,
        "write_policy": dict(policy_metadata or {}),
    }
    try:
        await personal_context_engine.publish_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_app=service_id,
            event_type=event_type,
            entity_id=_coerce_text(payload.get("id")) or service_id,
            summary=summary,
            payload=payload,
            metadata=metadata,
        )
    except Exception:
        return

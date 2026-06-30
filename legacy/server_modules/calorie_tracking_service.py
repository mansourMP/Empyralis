from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from server_modules import mini_apps_service


CALORIE_APP_ID = "calorie_tracking"
CALORIE_APP_LABEL = "Calorie Tracking"
CALORIE_APP_DESCRIPTION = (
    "Structured calorie tracker with meal events, goal tracking, and compact daily/weekly summaries."
)
MAX_CALORIE_RECORDS = 1200


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _parse_isoish(value: Any) -> Optional[datetime]:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        normalized = token.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_date(value: Any) -> Optional[date]:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return date.fromisoformat(token)
    except Exception:
        parsed = _parse_isoish(token)
        return parsed.date() if parsed is not None else None


def _as_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed < 0:
        return None
    return parsed


def _round_metric(value: Any) -> Optional[float]:
    number = _as_number(value)
    if number is None:
        return None
    return round(number, 2)


def _to_iso_timestamp(value: Any) -> str:
    parsed = _parse_isoish(value)
    if parsed is None:
        return _utc_now_iso()
    return parsed.isoformat().replace("+00:00", "Z")


def _event_date_key(record: Dict[str, Any]) -> str:
    token = str(record.get("date") or "").strip()
    if token:
        return token
    parsed = _parse_isoish(record.get("timestamp") or record.get("created_at"))
    if parsed is not None:
        return parsed.date().isoformat()
    return _utc_now().date().isoformat()


def _record_sort_key(record: Dict[str, Any]) -> datetime:
    parsed = _parse_isoish(record.get("timestamp") or record.get("created_at"))
    if parsed is not None:
        return parsed
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _compact_label(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:96]


def _normalize_goals(goal_payload: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    goals = dict(base or {})
    goal_key_map = {
        "calories": "calories",
        "calorie_goal": "calories",
        "calorie_goal_kcal": "calories",
        "protein_g": "protein_g",
        "protein_goal_g": "protein_g",
        "carbs_g": "carbs_g",
        "carbs_goal_g": "carbs_g",
        "fat_g": "fat_g",
        "fat_goal_g": "fat_g",
    }
    for raw_key, normalized_key in goal_key_map.items():
        if raw_key not in goal_payload:
            continue
        value = goal_payload.get(raw_key)
        if value is None:
            goals.pop(normalized_key, None)
            continue
        parsed = _round_metric(value)
        if parsed is None:
            continue
        goals[normalized_key] = parsed
    return goals


def _load_existing_goals(workspace_id: str) -> Dict[str, float]:
    try:
        contract = mini_apps_service.get_mini_app_contract(workspace_id, CALORIE_APP_ID)
    except KeyError:
        return {}
    state = contract.get("current_state")
    if not isinstance(state, dict):
        return {}
    goals = state.get("goals")
    if not isinstance(goals, dict):
        return {}
    return _normalize_goals(goals)


def _normalize_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = _to_iso_timestamp(payload.get("timestamp") or payload.get("occurred_at") or payload.get("created_at"))
    parsed = _parse_isoish(timestamp) or _utc_now()
    date_key = parsed.date().isoformat()
    meal_label = _compact_label(payload.get("meal_label") or payload.get("meal") or payload.get("label") or "Meal")
    calories = _round_metric(payload.get("calories"))
    protein_g = _round_metric(payload.get("protein_g"))
    carbs_g = _round_metric(payload.get("carbs_g"))
    fat_g = _round_metric(payload.get("fat_g"))
    notes = _compact_label(payload.get("notes") or payload.get("summary") or "")
    event_id = str(payload.get("id") or "").strip() or f"meal:{date_key}:{meal_label.lower().replace(' ', '_')}"
    summary_parts = [meal_label]
    if calories is not None:
        summary_parts.append(f"{int(calories) if calories.is_integer() else calories} kcal")
    if notes:
        summary_parts.append(notes)
    tags = payload.get("tags")
    normalized_tags = [
        str(item).strip().lower()
        for item in (tags if isinstance(tags, list) else [tags])
        if str(item or "").strip()
    ]
    return {
        "id": event_id,
        "kind": "meal_event",
        "type": "meal_event",
        "timestamp": timestamp,
        "created_at": timestamp,
        "date": date_key,
        "meal_label": meal_label,
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "notes": notes or None,
        "summary": " • ".join(part for part in summary_parts if part),
        "tags": sorted(set(normalized_tags)),
    }


def _zero_totals() -> Dict[str, float]:
    return {
        "calories": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
    }


def _add_record_totals(target: Dict[str, float], record: Dict[str, Any]) -> None:
    for key in ("calories", "protein_g", "carbs_g", "fat_g"):
        value = _as_number(record.get(key))
        if value is not None:
            target[key] += value


def _normalize_totals(values: Dict[str, float]) -> Dict[str, float]:
    return {key: round(max(0.0, float(values.get(key) or 0.0)), 2) for key in ("calories", "protein_g", "carbs_g", "fat_g")}


def _compute_calorie_payload(
    *,
    records: List[Dict[str, Any]],
    goals: Dict[str, float],
    target_date: Optional[date] = None,
) -> Dict[str, Any]:
    anchor_date = target_date or _utc_now().date()
    by_date: Dict[str, Dict[str, float]] = {}
    for record in records:
        key = _event_date_key(record)
        bucket = by_date.setdefault(key, _zero_totals())
        _add_record_totals(bucket, record)

    today_key = anchor_date.isoformat()
    today_totals = _normalize_totals(by_date.get(today_key, _zero_totals()))

    week_start = anchor_date - timedelta(days=6)
    weekly_totals = _zero_totals()
    events_logged = 0
    days_over_goal = 0
    for offset in range(7):
        day_key = (week_start + timedelta(days=offset)).isoformat()
        bucket = by_date.get(day_key)
        if not bucket:
            continue
        normalized_bucket = _normalize_totals(bucket)
        if normalized_bucket["calories"] > 0:
            events_logged += 1
        for metric in weekly_totals:
            weekly_totals[metric] += normalized_bucket[metric]
        if goals.get("calories") is not None and normalized_bucket["calories"] > goals["calories"]:
            days_over_goal += 1
    weekly_totals = _normalize_totals(weekly_totals)
    weekly_average = _normalize_totals({key: value / 7.0 for key, value in weekly_totals.items()})

    calorie_goal = goals.get("calories")
    if calorie_goal is None:
        daily_status = "goal_not_set"
    elif today_totals["calories"] <= calorie_goal:
        daily_status = "on_target"
    else:
        daily_status = "over_goal"

    recent_events: List[Dict[str, Any]] = []
    for item in records[:8]:
        recent_events.append(
            {
                "id": item.get("id"),
                "kind": "meal_event",
                "summary": item.get("summary") or item.get("meal_label") or "Meal logged",
                "created_at": item.get("timestamp") or item.get("created_at"),
                "calories": item.get("calories"),
                "meal_label": item.get("meal_label"),
            }
        )

    long_term_facts: List[Dict[str, str]] = []
    if goals.get("calories") is not None:
        long_term_facts.append(
            {
                "kind": "goal",
                "text": f"Daily calorie goal is {int(goals['calories']) if goals['calories'].is_integer() else goals['calories']} kcal.",
            }
        )
    for metric in ("protein_g", "carbs_g", "fat_g"):
        if goals.get(metric) is not None:
            metric_label = metric.replace("_g", "").replace("_", " ")
            amount = goals[metric]
            long_term_facts.append(
                {
                    "kind": "goal",
                    "text": f"Daily {metric_label} goal is {int(amount) if amount.is_integer() else amount} g.",
                }
            )
    if records:
        long_term_facts.append(
            {
                "kind": "pattern",
                "text": f"Tracking has {len(records)} logged meal events.",
            }
        )
    if calorie_goal is not None and events_logged > 0:
        weekly_average_calories = weekly_average["calories"]
        direction = "below" if weekly_average_calories <= calorie_goal else "above"
        long_term_facts.append(
            {
                "kind": "pattern",
                "text": (
                    f"7-day average calories are {int(weekly_average_calories) if weekly_average_calories.is_integer() else weekly_average_calories} "
                    f"kcal, {direction} goal."
                ),
            }
        )

    current_state = {
        "goals": goals,
        "today_date": today_key,
        "today_totals": today_totals,
        "weekly_totals": weekly_totals,
        "weekly_average": weekly_average,
        "event_count": len(records),
        "latest_event_at": records[0].get("timestamp") if records else None,
    }
    daily_summary = {
        "date": today_key,
        "totals": today_totals,
        "goals": goals,
        "status": daily_status,
        "calorie_delta": (
            round(today_totals["calories"] - calorie_goal, 2)
            if calorie_goal is not None
            else None
        ),
    }
    weekly_summary = {
        "window_start": week_start.isoformat(),
        "window_end": anchor_date.isoformat(),
        "totals": weekly_totals,
        "daily_average": weekly_average,
        "days_logged": events_logged,
        "days_over_calorie_goal": days_over_goal if calorie_goal is not None else None,
    }
    return {
        "current_state": current_state,
        "recent_events": recent_events,
        "daily_summary": daily_summary,
        "weekly_summary": weekly_summary,
        "long_term_facts": long_term_facts[:6],
    }


def _write_calorie_contract(
    workspace_id: str,
    *,
    records: List[Dict[str, Any]],
    goals: Dict[str, float],
) -> Dict[str, Any]:
    computed = _compute_calorie_payload(records=records, goals=goals)
    return mini_apps_service.upsert_mini_app_contract(
        workspace_id,
        CALORIE_APP_ID,
        label=CALORIE_APP_LABEL,
        description=CALORIE_APP_DESCRIPTION,
        current_state=computed["current_state"],
        recent_events=computed["recent_events"],
        daily_summary=computed["daily_summary"],
        weekly_summary=computed["weekly_summary"],
        long_term_facts=computed["long_term_facts"],
        records=records,
    )


def _load_records(workspace_id: str) -> List[Dict[str, Any]]:
    records = mini_apps_service.list_mini_app_records(workspace_id, CALORIE_APP_ID)
    records.sort(key=_record_sort_key, reverse=True)
    return records


def _require_explicit_write_intent(write_authorization: Optional[Dict[str, Any]]) -> None:
    payload = dict(write_authorization or {}) if isinstance(write_authorization, dict) else {}
    explicit_intent = bool(
        payload.get("explicit_user_intent")
        or payload.get("confirm_write")
        or payload.get("user_intent")
    )
    if explicit_intent:
        return
    raise PermissionError(
        "Mini app write blocked. Provide explicit_user_intent=true for calorie tracking writes."
    )


def log_calorie_event(
    workspace_id: str,
    payload: Dict[str, Any],
    *,
    write_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _require_explicit_write_intent(write_authorization)
    record = _normalize_event(payload)
    existing = [item for item in _load_records(workspace_id) if str(item.get("id") or "").strip() != record["id"]]
    existing.insert(0, record)
    existing.sort(key=_record_sort_key, reverse=True)
    records = existing[:MAX_CALORIE_RECORDS]
    goals = _load_existing_goals(workspace_id)
    contract = _write_calorie_contract(workspace_id, records=records, goals=goals)
    return {
        "workspace_id": workspace_id,
        "app_id": CALORIE_APP_ID,
        "record": record,
        "contract": contract,
    }


def update_calorie_goals(
    workspace_id: str,
    payload: Dict[str, Any],
    *,
    write_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _require_explicit_write_intent(write_authorization)
    records = _load_records(workspace_id)
    existing_goals = _load_existing_goals(workspace_id)
    goals = _normalize_goals(payload, base=existing_goals)
    contract = _write_calorie_contract(workspace_id, records=records, goals=goals)
    return {
        "workspace_id": workspace_id,
        "app_id": CALORIE_APP_ID,
        "goals": goals,
        "contract": contract,
    }


def calorie_overview(
    workspace_id: str,
    *,
    date_filter: Optional[str] = None,
) -> Dict[str, Any]:
    records = _load_records(workspace_id)
    goals = _load_existing_goals(workspace_id)
    contract = _write_calorie_contract(workspace_id, records=records, goals=goals)

    requested_date = _parse_date(date_filter)
    summary_payload = _compute_calorie_payload(records=records, goals=goals, target_date=requested_date)
    if requested_date is None:
        preview = records[:20]
    else:
        day_key = requested_date.isoformat()
        preview = [item for item in records if _event_date_key(item) == day_key][:50]

    return {
        "workspace_id": workspace_id,
        "app_id": CALORIE_APP_ID,
        "date": requested_date.isoformat() if requested_date is not None else summary_payload["current_state"]["today_date"],
        "contract": contract,
        "current_state": summary_payload["current_state"],
        "daily_summary": summary_payload["daily_summary"],
        "weekly_summary": summary_payload["weekly_summary"],
        "long_term_facts": summary_payload["long_term_facts"],
        "recent_events": summary_payload["recent_events"],
        "records_preview": preview,
        "records_preview_count": len(preview),
        "retrieve_records_hint": contract.get("retrieve_records"),
    }

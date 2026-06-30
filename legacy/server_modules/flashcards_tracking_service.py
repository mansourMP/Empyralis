from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Dict, List, Optional

from server_modules import mini_app_invoke_service
from server_modules import mini_apps_service
from server_modules.direct_chat_runtime_exports import generate_chat_reply_with_provider_fallback


FLASHCARDS_APP_ID = "flashcards"
FLASHCARDS_APP_LABEL = "Flashcards"
FLASHCARDS_APP_DESCRIPTION = (
    "Structured flashcards tracker with deck metadata, review outcomes, weak-topic summaries, and progress signals."
)
MAX_FLASHCARD_RECORDS = 3000
DEFAULT_GENERATED_CARD_COUNT = 8
MAX_GENERATED_CARD_COUNT = 12


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


def _slug(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    output: List[str] = []
    last_dash = False
    for char in token:
        if char.isalnum():
            output.append(char)
            last_dash = False
            continue
        if not last_dash:
            output.append("-")
            last_dash = True
    return "".join(output).strip("-")


def _compact(value: Any, *, limit: int = 180) -> str:
    token = " ".join(str(value or "").split()).strip()
    if len(token) <= limit:
        return token
    return f"{token[: max(0, limit - 1)].rstrip()}…"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "correct"}:
            return True
        if normalized in {"false", "0", "no", "n", "incorrect"}:
            return False
    return None


def _record_sort_key(record: Dict[str, Any]) -> datetime:
    parsed = _parse_isoish(record.get("timestamp") or record.get("created_at"))
    if parsed is not None:
        return parsed
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _event_date(record: Dict[str, Any]) -> str:
    token = str(record.get("date") or "").strip()
    if token:
        return token
    parsed = _parse_isoish(record.get("timestamp") or record.get("created_at"))
    if parsed is not None:
        return parsed.date().isoformat()
    return _utc_now().date().isoformat()


def _extract_json_payload(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("No generation payload returned.")
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _event_tags(
    *,
    deck: str,
    topic: str,
    user_tags: Optional[List[Any]],
) -> List[str]:
    tags = set()
    if deck:
        tags.add(f"deck:{_slug(deck)}")
    if topic:
        tags.add(f"topic:{_slug(topic)}")
    for item in user_tags or []:
        token = _slug(item)
        if token:
            tags.add(token)
    return sorted(tags)


def _normalize_generated_cards(payload: Any, *, default_topic: str = "", count: int) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("cards")
    else:
        items = payload
    if not isinstance(items, list):
        raise ValueError("Generated flashcards payload is not a list.")

    cards: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        front = _compact(item.get("front") or item.get("question") or "", limit=480)
        back = _compact(item.get("back") or item.get("answer") or "", limit=480)
        if not front or not back:
            continue
        topic = _compact(item.get("topic") or default_topic or "", limit=80)
        difficulty = _compact(item.get("difficulty") or "normal", limit=24).lower()
        cards.append(
            {
                "front": front,
                "back": back,
                "topic": topic or None,
                "difficulty": difficulty or "normal",
            }
        )
        if len(cards) >= count:
            break
    if not cards:
        raise ValueError("No valid flashcards were generated.")
    return cards


def _load_records(workspace_id: str) -> List[Dict[str, Any]]:
    records = mini_apps_service.list_mini_app_records(workspace_id, FLASHCARDS_APP_ID)
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
        "Mini app write blocked. Provide explicit_user_intent=true for flashcards writes."
    )


def _load_deck_metadata(workspace_id: str) -> Dict[str, Dict[str, Any]]:
    try:
        contract = mini_apps_service.get_mini_app_contract(workspace_id, FLASHCARDS_APP_ID)
    except KeyError:
        return {}
    current_state = contract.get("current_state")
    if not isinstance(current_state, dict):
        return {}
    decks = current_state.get("decks")
    if not isinstance(decks, dict):
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    for key, value in decks.items():
        if isinstance(value, dict):
            output[str(key)] = dict(value)
    return output


def _normalize_create_card(payload: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = (_parse_isoish(payload.get("timestamp")) or _utc_now()).isoformat().replace("+00:00", "Z")
    deck = _compact(payload.get("deck") or payload.get("deck_name") or "Default", limit=80)
    topic = _compact(payload.get("topic") or "", limit=80)
    front = _compact(payload.get("front") or payload.get("question") or "", limit=480)
    back = _compact(payload.get("back") or payload.get("answer") or "", limit=480)
    difficulty = _compact(payload.get("difficulty") or "normal", limit=24).lower()
    card_id = _compact(payload.get("card_id") or payload.get("id") or "", limit=120)
    if not card_id:
        card_id = f"card:{_slug(deck) or 'default'}:{_slug(front)[:40] or _slug(timestamp)}"
    summary = f"Card added to {deck}"
    if topic:
        summary = f"{summary} ({topic})"
    return {
        "id": f"card_create:{card_id}:{timestamp}",
        "kind": "card_create",
        "type": "card_create",
        "timestamp": timestamp,
        "created_at": timestamp,
        "date": timestamp[:10],
        "card_id": card_id,
        "deck": deck,
        "topic": topic or None,
        "front": front,
        "back": back,
        "difficulty": difficulty,
        "summary": summary,
        "tags": _event_tags(deck=deck, topic=topic, user_tags=payload.get("tags") if isinstance(payload.get("tags"), list) else None),
    }


def _normalize_review_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = (_parse_isoish(payload.get("timestamp")) or _utc_now()).isoformat().replace("+00:00", "Z")
    deck = _compact(payload.get("deck") or payload.get("deck_name") or "Default", limit=80)
    topic = _compact(payload.get("topic") or "", limit=80)
    card_id = _compact(payload.get("card_id") or payload.get("id") or "", limit=120)
    correct = _as_bool(payload.get("correct"))
    quality = _as_int(payload.get("quality"), default=0)
    if quality < 0:
        quality = 0
    if quality > 5:
        quality = 5
    response_ms = _as_int(payload.get("response_ms"), default=0)
    summary = f"Review in {deck}"
    if topic:
        summary = f"{summary} ({topic})"
    if correct is True:
        summary = f"{summary}: correct"
    elif correct is False:
        summary = f"{summary}: incorrect"
    return {
        "id": f"review_result:{card_id or _slug(deck)}:{timestamp}",
        "kind": "review_result",
        "type": "review_result",
        "timestamp": timestamp,
        "created_at": timestamp,
        "date": timestamp[:10],
        "card_id": card_id or None,
        "deck": deck,
        "topic": topic or None,
        "correct": correct,
        "quality": quality,
        "response_ms": response_ms if response_ms > 0 else None,
        "summary": summary,
        "tags": _event_tags(deck=deck, topic=topic, user_tags=payload.get("tags") if isinstance(payload.get("tags"), list) else None),
    }


def _normalize_deck_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = (_parse_isoish(payload.get("timestamp")) or _utc_now()).isoformat().replace("+00:00", "Z")
    deck = _compact(payload.get("deck") or payload.get("deck_name") or "Default", limit=80)
    topic_focus = _compact(payload.get("topic_focus") or payload.get("topic") or "", limit=80)
    language = _compact(payload.get("language") or "", limit=40)
    target_new = _as_int(payload.get("target_new_per_day"), default=0)
    target_reviews = _as_int(payload.get("target_reviews_per_day"), default=0)
    return {
        "id": f"deck_metadata:{_slug(deck) or 'default'}:{timestamp}",
        "kind": "deck_metadata",
        "type": "deck_metadata",
        "timestamp": timestamp,
        "created_at": timestamp,
        "date": timestamp[:10],
        "deck": deck,
        "topic_focus": topic_focus or None,
        "language": language or None,
        "target_new_per_day": target_new if target_new > 0 else None,
        "target_reviews_per_day": target_reviews if target_reviews > 0 else None,
        "summary": f"Deck metadata updated for {deck}",
        "tags": _event_tags(deck=deck, topic=topic_focus, user_tags=payload.get("tags") if isinstance(payload.get("tags"), list) else None),
    }


def _compute_payload(
    *,
    records: List[Dict[str, Any]],
    deck_metadata: Dict[str, Dict[str, Any]],
    target_date: Optional[date] = None,
) -> Dict[str, Any]:
    anchor = target_date or _utc_now().date()
    weekly_start = anchor - timedelta(days=6)
    today_key = anchor.isoformat()

    deck_stats: Dict[str, Dict[str, Any]] = {}
    weak_topic_counts: Dict[str, int] = defaultdict(int)
    weekly_reviews = 0
    weekly_correct = 0
    today_reviews = 0
    today_correct = 0
    total_cards_created = 0
    daily_reviews: Dict[str, int] = defaultdict(int)

    for record in records:
        kind = str(record.get("kind") or record.get("type") or "").strip().lower()
        deck = _compact(record.get("deck") or "Default", limit=80) or "Default"
        topic = _compact(record.get("topic") or "", limit=80)
        date_key = _event_date(record)
        ts = _parse_isoish(record.get("timestamp") or record.get("created_at"))
        if deck not in deck_stats:
            deck_stats[deck] = {
                "cards_created": 0,
                "reviews": 0,
                "correct_reviews": 0,
                "accuracy": 0.0,
                "last_activity_at": None,
                "topics": {},
            }
        if ts is not None:
            current_last = _parse_isoish(deck_stats[deck].get("last_activity_at"))
            if current_last is None or ts > current_last:
                deck_stats[deck]["last_activity_at"] = ts.isoformat().replace("+00:00", "Z")

        if kind == "card_create":
            total_cards_created += 1
            deck_stats[deck]["cards_created"] += 1
            continue

        if kind != "review_result":
            continue

        correct = _as_bool(record.get("correct"))
        deck_stats[deck]["reviews"] += 1
        if correct is True:
            deck_stats[deck]["correct_reviews"] += 1
        if topic:
            topic_bucket = deck_stats[deck]["topics"].setdefault(topic, {"reviews": 0, "incorrect": 0})
            topic_bucket["reviews"] += 1
            if correct is False:
                topic_bucket["incorrect"] += 1
                weak_topic_counts[f"{deck}::{topic}"] += 1
        if ts is not None and weekly_start <= ts.date() <= anchor:
            weekly_reviews += 1
            if correct is True:
                weekly_correct += 1
        if date_key == today_key:
            today_reviews += 1
            if correct is True:
                today_correct += 1
        if ts is not None and weekly_start <= ts.date() <= anchor:
            daily_reviews[ts.date().isoformat()] += 1

    for deck_name, stats in deck_stats.items():
        reviews = _as_int(stats.get("reviews"))
        correct_reviews = _as_int(stats.get("correct_reviews"))
        stats["accuracy"] = round((correct_reviews / reviews) * 100.0, 2) if reviews > 0 else 0.0
        metadata = deck_metadata.get(deck_name) or {}
        if metadata:
            stats["metadata"] = metadata
        topics = stats.get("topics")
        if isinstance(topics, dict):
            weak_topics = sorted(
                (
                    {
                        "topic": topic_name,
                        "reviews": _as_int(topic_data.get("reviews")),
                        "incorrect": _as_int(topic_data.get("incorrect")),
                    }
                    for topic_name, topic_data in topics.items()
                ),
                key=lambda item: (item["incorrect"], item["reviews"]),
                reverse=True,
            )[:3]
            stats["weak_topics"] = [item for item in weak_topics if item["incorrect"] > 0]

    weak_topics_summary = []
    for key, count in sorted(weak_topic_counts.items(), key=lambda item: item[1], reverse=True)[:6]:
        deck, topic = key.split("::", 1)
        weak_topics_summary.append({"deck": deck, "topic": topic, "incorrect": count})

    weekly_accuracy = round((weekly_correct / weekly_reviews) * 100.0, 2) if weekly_reviews > 0 else 0.0
    today_accuracy = round((today_correct / today_reviews) * 100.0, 2) if today_reviews > 0 else 0.0
    active_decks = sorted(
        (
            {"deck": deck_name, "reviews": stats["reviews"], "cards_created": stats["cards_created"], "accuracy": stats["accuracy"]}
            for deck_name, stats in deck_stats.items()
        ),
        key=lambda item: (item["reviews"], item["cards_created"]),
        reverse=True,
    )

    recent_events: List[Dict[str, Any]] = []
    for item in records[:10]:
        recent_events.append(
            {
                "id": item.get("id"),
                "kind": item.get("kind") or item.get("type"),
                "summary": item.get("summary") or "Flashcards event",
                "deck": item.get("deck"),
                "topic": item.get("topic"),
                "created_at": item.get("timestamp") or item.get("created_at"),
            }
        )

    long_term_facts: List[Dict[str, str]] = []
    long_term_facts.append({"kind": "progress", "text": f"Total flashcards created: {total_cards_created}."})
    long_term_facts.append({"kind": "progress", "text": f"7-day review accuracy: {weekly_accuracy}%."})
    if active_decks:
        top_deck = active_decks[0]
        long_term_facts.append(
            {
                "kind": "pattern",
                "text": (
                    f"Most active deck is {top_deck['deck']} with {top_deck['reviews']} reviews "
                    f"and {top_deck['accuracy']}% accuracy."
                ),
            }
        )
    for item in weak_topics_summary[:3]:
        long_term_facts.append(
            {
                "kind": "weak_topic",
                "text": f"Weak topic in {item['deck']}: {item['topic']} ({item['incorrect']} incorrect reviews).",
            }
        )

    current_state = {
        "today_date": today_key,
        "total_cards_created": total_cards_created,
        "deck_count": len(deck_stats),
        "decks": deck_stats,
        "weak_topics": weak_topics_summary,
        "latest_event_at": records[0].get("timestamp") if records else None,
    }
    daily_summary = {
        "date": today_key,
        "reviews": today_reviews,
        "correct_reviews": today_correct,
        "accuracy": today_accuracy,
        "cards_created_total": total_cards_created,
    }
    weekly_summary = {
        "window_start": weekly_start.isoformat(),
        "window_end": anchor.isoformat(),
        "reviews": weekly_reviews,
        "correct_reviews": weekly_correct,
        "accuracy": weekly_accuracy,
        "daily_reviews": dict(sorted(daily_reviews.items())),
        "weak_topics": weak_topics_summary,
        "active_decks": active_decks[:5],
    }
    return {
        "current_state": current_state,
        "recent_events": recent_events,
        "daily_summary": daily_summary,
        "weekly_summary": weekly_summary,
        "long_term_facts": long_term_facts[:8],
    }


def _write_flashcards_contract(
    workspace_id: str,
    *,
    records: List[Dict[str, Any]],
    deck_metadata: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    computed = _compute_payload(records=records, deck_metadata=deck_metadata)
    return mini_apps_service.upsert_mini_app_contract(
        workspace_id,
        FLASHCARDS_APP_ID,
        label=FLASHCARDS_APP_LABEL,
        description=FLASHCARDS_APP_DESCRIPTION,
        current_state=computed["current_state"],
        recent_events=computed["recent_events"],
        daily_summary=computed["daily_summary"],
        weekly_summary=computed["weekly_summary"],
        long_term_facts=computed["long_term_facts"],
        records=records,
    )


def create_flashcard(
    workspace_id: str,
    payload: Dict[str, Any],
    *,
    write_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _require_explicit_write_intent(write_authorization)
    record = _normalize_create_card(payload)
    records = [item for item in _load_records(workspace_id) if str(item.get("id") or "").strip() != record["id"]]
    records.insert(0, record)
    records.sort(key=_record_sort_key, reverse=True)
    records = records[:MAX_FLASHCARD_RECORDS]
    deck_metadata = _load_deck_metadata(workspace_id)
    contract = _write_flashcards_contract(workspace_id, records=records, deck_metadata=deck_metadata)
    return {"workspace_id": workspace_id, "app_id": FLASHCARDS_APP_ID, "record": record, "contract": contract}


def update_deck_metadata(
    workspace_id: str,
    payload: Dict[str, Any],
    *,
    write_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _require_explicit_write_intent(write_authorization)
    record = _normalize_deck_metadata(payload)
    deck = str(record.get("deck") or "").strip() or "Default"
    records = [item for item in _load_records(workspace_id) if str(item.get("id") or "").strip() != record["id"]]
    records.insert(0, record)
    records.sort(key=_record_sort_key, reverse=True)
    records = records[:MAX_FLASHCARD_RECORDS]
    deck_metadata = _load_deck_metadata(workspace_id)
    deck_metadata[deck] = {
        "deck": deck,
        "topic_focus": record.get("topic_focus"),
        "language": record.get("language"),
        "target_new_per_day": record.get("target_new_per_day"),
        "target_reviews_per_day": record.get("target_reviews_per_day"),
        "updated_at": record.get("timestamp"),
    }
    contract = _write_flashcards_contract(workspace_id, records=records, deck_metadata=deck_metadata)
    return {
        "workspace_id": workspace_id,
        "app_id": FLASHCARDS_APP_ID,
        "deck": deck,
        "metadata": deck_metadata[deck],
        "contract": contract,
    }


def log_review_result(
    workspace_id: str,
    payload: Dict[str, Any],
    *,
    write_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _require_explicit_write_intent(write_authorization)
    record = _normalize_review_result(payload)
    records = [item for item in _load_records(workspace_id) if str(item.get("id") or "").strip() != record["id"]]
    records.insert(0, record)
    records.sort(key=_record_sort_key, reverse=True)
    records = records[:MAX_FLASHCARD_RECORDS]
    deck_metadata = _load_deck_metadata(workspace_id)
    contract = _write_flashcards_contract(workspace_id, records=records, deck_metadata=deck_metadata)
    return {"workspace_id": workspace_id, "app_id": FLASHCARDS_APP_ID, "record": record, "contract": contract}


def flashcards_overview(
    workspace_id: str,
    *,
    date_filter: Optional[str] = None,
) -> Dict[str, Any]:
    records = _load_records(workspace_id)
    deck_metadata = _load_deck_metadata(workspace_id)
    contract = _write_flashcards_contract(workspace_id, records=records, deck_metadata=deck_metadata)
    requested_date = _parse_date(date_filter)
    summary_payload = _compute_payload(records=records, deck_metadata=deck_metadata, target_date=requested_date)
    if requested_date is None:
        preview = records[:40]
    else:
        day_key = requested_date.isoformat()
        preview = [item for item in records if _event_date(item) == day_key][:100]
    return {
        "workspace_id": workspace_id,
        "app_id": FLASHCARDS_APP_ID,
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


def retrieve_flashcard_records(
    workspace_id: str,
    *,
    deck: Optional[str] = None,
    topic: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    kind: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    tags: List[str] = []
    if deck:
        tags.append(f"deck:{_slug(deck)}")
    if topic:
        tags.append(f"topic:{_slug(topic)}")
    filters: Dict[str, Any] = {}
    if kind:
        filters["kind"] = str(kind).strip().lower()
    if since:
        filters["since"] = str(since).strip()
    if until:
        filters["until"] = str(until).strip()
    if tags:
        filters["tags"] = tags
    if limit is not None:
        filters["limit"] = max(1, min(mini_apps_service.MAX_RETRIEVE_LIMIT, int(limit)))
    result = mini_apps_service.retrieve_mini_app_records(
        workspace_id,
        FLASHCARDS_APP_ID,
        filters=filters,
    )
    result["deck"] = deck or None
    result["topic"] = topic or None
    return result


def generate_flashcards(
    workspace_id: str,
    *,
    deck: str,
    source_text: str,
    topic: Optional[str] = None,
    language: Optional[str] = None,
    count: int = DEFAULT_GENERATED_CARD_COUNT,
    requested_provider: str = "",
    requested_model: str = "",
    write_authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _require_explicit_write_intent(write_authorization)
    clean_deck = _compact(deck or "Default", limit=80) or "Default"
    clean_source = " ".join(str(source_text or "").split()).strip()
    clean_topic = _compact(topic or "", limit=80)
    clean_language = _compact(language or "", limit=40)
    if not clean_source:
        raise ValueError("source_text is required")

    target_count = max(1, min(int(count or DEFAULT_GENERATED_CARD_COUNT), MAX_GENERATED_CARD_COUNT))
    provider, credentials = mini_app_invoke_service.resolve_cloud_provider_for_workspace(workspace_id, requested_provider)
    context: Dict[str, Any] = {
        "workspace_id": str(workspace_id or "").strip(),
        "provider": provider,
        "source": "flashcards_generate",
        "disable_provider_fallback": True,
    }
    metadata: Dict[str, Any] = {
        "workspace_id": str(workspace_id or "").strip(),
        "provider": provider,
        "source": "flashcards_generate",
        "disable_provider_fallback": True,
        "credentials": credentials,
    }
    if requested_model:
        context["model"] = requested_model
        metadata["model"] = requested_model

    user_prompt = (
        f"Deck: {clean_deck}\n"
        f"Topic: {clean_topic or 'General'}\n"
        f"Language: {clean_language or 'Same as source'}\n"
        f"Generate {target_count} concise study flashcards from the source below.\n"
        "Return strict JSON only in the shape "
        '{"cards":[{"front":"...","back":"...","topic":"...","difficulty":"easy|normal|hard"}]}. '
        "No markdown, no explanation.\n\n"
        f"Source:\n{clean_source}"
    )
    system_prompt = (
        "You create high-quality study flashcards. "
        "Return JSON only. Keep fronts concise and backs crisp. "
        "Do not reference memory, hidden state, or act like a personal agent."
    )
    reply, usage_masked, attempted_providers, last_error = generate_chat_reply_with_provider_fallback(
        context,
        metadata,
        user_prompt,
        system_prompt,
        prior_messages=None,
    )
    if not reply:
        raise RuntimeError(last_error or "Flashcard generation failed.")

    payload = _extract_json_payload(reply)
    generated_cards = _normalize_generated_cards(payload, default_topic=clean_topic, count=target_count)
    created_at = _utc_now_iso()
    existing_records = _load_records(workspace_id)
    deck_metadata = _load_deck_metadata(workspace_id)
    records = list(existing_records)
    created_records: List[Dict[str, Any]] = []
    for index, card in enumerate(generated_cards, start=1):
        record = _normalize_create_card(
            {
                "id": f"generated:{_slug(clean_deck) or 'default'}:{index}:{created_at}",
                "deck": clean_deck,
                "topic": card.get("topic"),
                "front": card["front"],
                "back": card["back"],
                "difficulty": card.get("difficulty") or "normal",
                "timestamp": created_at,
                "tags": ["generated", "ai"],
            }
        )
        records = [item for item in records if str(item.get("id") or "").strip() != record["id"]]
        records.insert(0, record)
        created_records.append(record)

    records.sort(key=_record_sort_key, reverse=True)
    records = records[:MAX_FLASHCARD_RECORDS]
    if clean_language or clean_topic:
        existing_metadata = deck_metadata.get(clean_deck) or {}
        deck_metadata[clean_deck] = {
            "deck": clean_deck,
            "topic_focus": clean_topic or existing_metadata.get("topic_focus"),
            "language": clean_language or existing_metadata.get("language"),
            "target_new_per_day": existing_metadata.get("target_new_per_day"),
            "target_reviews_per_day": existing_metadata.get("target_reviews_per_day"),
            "updated_at": created_at,
        }
    contract = _write_flashcards_contract(workspace_id, records=records, deck_metadata=deck_metadata)
    attempted = [item.strip() for item in str(attempted_providers or "").split(",") if item.strip()]
    effective_provider = attempted[-1] if attempted else provider
    effective_model = str((usage_masked or {}).get("model") or requested_model or "").strip() or None
    return {
        "workspace_id": workspace_id,
        "app_id": FLASHCARDS_APP_ID,
        "deck": clean_deck,
        "cards": created_records,
        "count": len(created_records),
        "provider": effective_provider,
        "model": effective_model,
        "attempted_providers": attempted,
        "usage": usage_masked or None,
        "contract": contract,
    }

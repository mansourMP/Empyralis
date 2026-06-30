from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from server_modules import deployed_agent_config_schema


HEALTHGUIDE_SAFETY_POLICY_VERSION = 1
HEALTHGUIDE_SAFETY_MARKER = "[healthguide_safety_policy_v1]"
DEFAULT_ASSISTANT_NAME = "HealthGuide"
NO_CITATION_DISCLOSURE = (
    "Citation note: This answer is general informational guidance without verified citations."
)

_DISCLAIMER_TOKENS = (
    "not a doctor",
    "not medical advice",
    "general health information only",
)
_RED_FLAG_RULES = (
    (
        "chest_pain",
        re.compile(r"\b(chest pain|chest pressure|pressure in (my|the) chest)\b", re.IGNORECASE),
    ),
    (
        "breathing_distress",
        re.compile(r"\b(shortness of breath|trouble breathing|can't breathe|difficulty breathing)\b", re.IGNORECASE),
    ),
    (
        "stroke_signs",
        re.compile(r"\b(face droop|slurred speech|sudden weakness|stroke symptoms?)\b", re.IGNORECASE),
    ),
    (
        "severe_bleeding",
        re.compile(r"\b(severe bleeding|won't stop bleeding|bleeding heavily)\b", re.IGNORECASE),
    ),
    (
        "seizure_or_unconscious",
        re.compile(r"\b(seizure|passed out|unconscious|not waking up|fainted and (?:still )?out)\b", re.IGNORECASE),
    ),
    (
        "anaphylaxis",
        re.compile(r"\b(anaphylaxis|severe allergic reaction|swelling of (?:the )?(?:tongue|throat|lips))\b", re.IGNORECASE),
    ),
    (
        "self_harm_risk",
        re.compile(r"\b(suicidal|want to die|kill myself|self harm|overdose)\b", re.IGNORECASE),
    ),
)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _normalize_text(value: Any, *, default: str = "") -> str:
    token = " ".join(str(value or "").strip().split())
    return token or default


def _normalize_optional_text(value: Any) -> Optional[str]:
    token = _normalize_text(value)
    return token or None


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    return token in {"1", "true", "yes", "on", "enabled"}


def _extract_turn_request_metadata(turn_request: Any) -> Dict[str, Any]:
    if isinstance(turn_request, dict):
        context_hints = turn_request.get("context_hints") if isinstance(turn_request.get("context_hints"), dict) else {}
        metadata = context_hints.get("metadata") if isinstance(context_hints.get("metadata"), dict) else {}
        return dict(metadata or {})
    context_hints = getattr(turn_request, "context_hints", None)
    if isinstance(context_hints, dict) and isinstance(context_hints.get("metadata"), dict):
        return dict(context_hints.get("metadata") or {})
    return {}


def runtime_policy_snapshot() -> Dict[str, Any]:
    return {
        "policy_version": HEALTHGUIDE_SAFETY_POLICY_VERSION,
        "red_flag_rule_count": len(_RED_FLAG_RULES),
        "citation_disclosure_enabled": True,
        "default_assistant_name": DEFAULT_ASSISTANT_NAME,
    }


def resolve_health_safety_context(
    *,
    deployed_agent: Optional[Dict[str, Any]] = None,
    turn_request: Any = None,
    session_ctx: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_metadata = _coerce_dict(metadata)
    deployed_agent_payload = _coerce_dict(deployed_agent)
    deployed_agent_metadata = _coerce_dict(deployed_agent_payload.get("metadata"))
    deployed_agent_config = deployed_agent_config_schema.deployed_agent_config_from_record(
        deployed_agent_payload
    )
    turn_request_metadata = _extract_turn_request_metadata(turn_request)
    session_request = _coerce_dict(_coerce_dict(session_ctx).get("agent_turn_request"))
    session_metadata = _extract_turn_request_metadata(session_request)

    merged_metadata = {
        **deployed_agent_metadata,
        "health_safety_enabled": bool(
            deployed_agent_config.safety_policy.health_safety_enabled
        ),
        **session_metadata,
        **turn_request_metadata,
        **resolved_metadata,
    }
    assistant_name = (
        _normalize_optional_text(merged_metadata.get("health_safety_assistant_name"))
        or _normalize_optional_text(merged_metadata.get("deployed_agent_name"))
        or _normalize_optional_text(deployed_agent_payload.get("name"))
        or DEFAULT_ASSISTANT_NAME
    )
    enabled = bool(deployed_agent_config.safety_policy.health_safety_enabled) or _normalize_bool(
        merged_metadata.get("health_safety_enabled")
    )
    return {
        "enabled": enabled,
        "assistant_name": assistant_name,
        "policy_version": HEALTHGUIDE_SAFETY_POLICY_VERSION,
        "deployed_agent_id": _normalize_optional_text(merged_metadata.get("deployed_agent_id"))
        or _normalize_optional_text(deployed_agent_payload.get("id")),
        "metadata": merged_metadata,
    }


def merge_health_safety_business_plan(existing_plan: Optional[str], *, assistant_name: str) -> str:
    normalized_existing = _normalize_text(existing_plan)
    if HEALTHGUIDE_SAFETY_MARKER in normalized_existing:
        return normalized_existing
    policy_block = "\n".join(
        [
            HEALTHGUIDE_SAFETY_MARKER,
            f"{assistant_name} is operating in health safety mode.",
            "Provide general health information only.",
            "State clearly that the assistant is not a doctor and cannot diagnose or treat.",
            "If the user describes red-flag or emergency symptoms, stop the normal answer and advise urgent in-person care or local emergency services.",
            "If verified citations are available in the response pipeline, include them. Otherwise explicitly disclose that the guidance does not include verified citations.",
        ]
    )
    if normalized_existing:
        return f"{normalized_existing}\n\n{policy_block}"
    return policy_block


def apply_health_safety_to_turn_request(turn_request: Any) -> Any:
    metadata = _extract_turn_request_metadata(turn_request)
    if not _normalize_bool(metadata.get("health_safety_enabled")):
        return turn_request
    assistant_name = (
        _normalize_optional_text(metadata.get("health_safety_assistant_name"))
        or _normalize_optional_text(metadata.get("deployed_agent_name"))
        or DEFAULT_ASSISTANT_NAME
    )
    next_metadata = {
        **metadata,
        "health_safety_enabled": True,
        "health_safety_mode": "healthguide",
        "health_safety_policy_version": HEALTHGUIDE_SAFETY_POLICY_VERSION,
        "health_safety_assistant_name": assistant_name,
    }
    context_hints = dict(getattr(turn_request, "context_hints", {}) or {})
    context_hints["metadata"] = next_metadata
    context_hints["business_plan"] = merge_health_safety_business_plan(
        context_hints.get("business_plan"),
        assistant_name=assistant_name,
    )
    setattr(turn_request, "context_hints", context_hints)
    return turn_request


def detect_red_flag_symptoms(user_message: Any) -> Dict[str, Any]:
    normalized_message = _normalize_text(user_message)
    matched_rules = [rule_id for rule_id, pattern in _RED_FLAG_RULES if pattern.search(normalized_message)]
    return {
        "matched": bool(matched_rules),
        "matches": matched_rules,
    }


def _render_citation_entry(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        title = (
            _normalize_optional_text(value.get("title"))
            or _normalize_optional_text(value.get("label"))
            or _normalize_optional_text(value.get("name"))
        )
        url = (
            _normalize_optional_text(value.get("url"))
            or _normalize_optional_text(value.get("uri"))
            or _normalize_optional_text(value.get("href"))
        )
        if title and url:
            return f"{title} ({url})"
        return title or url
    token = _normalize_optional_text(value)
    return token


def extract_citation_refs(*payloads: Any) -> List[str]:
    refs: List[str] = []
    seen: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("citation_refs", "citations", "source_refs", "sources"):
            candidate = payload.get(key)
            if not isinstance(candidate, list):
                continue
            for item in candidate:
                rendered = _render_citation_entry(item)
                if not rendered or rendered in seen:
                    continue
                seen.add(rendered)
                refs.append(rendered)
    return refs


def _health_disclaimer(assistant_name: str) -> str:
    return (
        f"{assistant_name} provides general health information only and is not a doctor. "
        "It cannot diagnose, treat, or replace a licensed clinician."
    )


def _has_disclaimer(reply: str) -> bool:
    lowered = str(reply or "").strip().lower()
    return any(token in lowered for token in _DISCLAIMER_TOKENS)


def _has_no_citation_disclosure(reply: str) -> bool:
    lowered = str(reply or "").strip().lower()
    return "without verified citations" in lowered


def _render_citations(citation_refs: Iterable[str]) -> str:
    lines = [f"- {item}" for item in citation_refs if _normalize_text(item)]
    if not lines:
        return ""
    return "Sources:\n" + "\n".join(lines)


def _red_flag_reply(*, assistant_name: str) -> str:
    return "\n\n".join(
        [
            (
                "This could need urgent medical attention. Seek urgent in-person medical care now or contact "
                "local emergency services immediately if symptoms are severe, worsening, or involve breathing "
                "trouble, chest pain, stroke signs, severe bleeding, seizures, fainting, or self-harm risk."
            ),
            _health_disclaimer(assistant_name),
        ]
    )


def apply_health_safety_to_reply(
    *,
    reply: Any,
    user_message: Any,
    assistant_name: Optional[str] = None,
    response_payload: Optional[Dict[str, Any]] = None,
    citation_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_assistant_name = _normalize_text(assistant_name, default=DEFAULT_ASSISTANT_NAME)
    normalized_reply = _normalize_text(reply)
    red_flag_state = detect_red_flag_symptoms(user_message)
    citation_refs = extract_citation_refs(_coerce_dict(response_payload), _coerce_dict(citation_payload))

    rendered_reply = _red_flag_reply(assistant_name=resolved_assistant_name) if red_flag_state["matched"] else normalized_reply
    parts = [rendered_reply] if rendered_reply else []
    if not _has_disclaimer(rendered_reply):
        parts.append(_health_disclaimer(resolved_assistant_name))
    if citation_refs:
        parts.append(_render_citations(citation_refs))
        citation_mode = "verified"
    else:
        if not _has_no_citation_disclosure(rendered_reply):
            parts.append(NO_CITATION_DISCLOSURE)
        citation_mode = "disclosed_unverified"

    final_reply = "\n\n".join(part for part in parts if _normalize_text(part))
    return {
        "reply": final_reply,
        "assistant_name": resolved_assistant_name,
        "policy_version": HEALTHGUIDE_SAFETY_POLICY_VERSION,
        "red_flag_triggered": bool(red_flag_state["matched"]),
        "red_flag_matches": list(red_flag_state["matches"]),
        "citation_refs": citation_refs,
        "citation_mode": citation_mode,
    }

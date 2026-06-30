from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional


APPLE_MESSAGES_BUSINESS = "apple_messages_business"
APPLE_MESSAGES_BUSINESS_PROVIDER = "apple_messages_business_msp"
STAGE_PLANNED = "planned"
RUNTIME_LANE = "studio_business_connector"

_APPLE_REVIEW_REQUIREMENTS: tuple[Dict[str, str], ...] = (
    {
        "id": "apple_business_identity",
        "label": "Apple Business Register identity",
        "description": "The company identity must be prepared before Apple Messages for Business review.",
    },
    {
        "id": "approved_msp",
        "label": "Approved messaging service provider",
        "description": "Outbound and inbound transport should run through an approved MSP until direct integration is certified.",
    },
    {
        "id": "ai_disclosure",
        "label": "AI disclosure",
        "description": "The channel must clearly tell users they are chatting with Sage, an AI assistant.",
    },
    {
        "id": "human_handoff",
        "label": "Human handoff",
        "description": "A user must be able to ask for a human and reach an operator/support queue.",
    },
    {
        "id": "user_initiated_conversation",
        "label": "User-initiated conversation",
        "description": "The channel must not behave like an unsolicited outbound spam channel.",
    },
    {
        "id": "privacy_and_retention",
        "label": "Privacy policy and retention",
        "description": "Privacy, retention, transcript, and escalation behavior must be documented for review.",
    },
)

_ADAPTERS: Dict[str, Dict[str, Any]] = {
    APPLE_MESSAGES_BUSINESS: {
        "id": APPLE_MESSAGES_BUSINESS,
        "display_name": "Apple Messages for Business",
        "provider": APPLE_MESSAGES_BUSINESS_PROVIDER,
        "runtime_lane": RUNTIME_LANE,
        "stage": STAGE_PLANNED,
        "launch_allowed": False,
        "setup_available": False,
        "requires_agent_computer": False,
        "official_business_channel": True,
        "personal_channel": False,
        "requires_msp": True,
        "requires_ai_disclosure": True,
        "requires_human_handoff": True,
        "requires_user_initiated_conversation": True,
        "supports_inbound": True,
        "supports_outbound": True,
        "media_support": {"text": True, "images": False, "files": False, "voice": False},
        "review_requirements": list(_APPLE_REVIEW_REQUIREMENTS),
        "proof_log_contract": {
            "fields": [
                "provider",
                "external_thread_id",
                "external_user_id",
                "inbound_event_id",
                "tool_calls",
                "approvals_required",
                "human_handoff_state",
                "outbound_message_id",
            ],
        },
    }
}


class BusinessMessagingAdapterError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _adapter_key(provider: str) -> str:
    token = _text(provider).lower()
    aliases = {
        APPLE_MESSAGES_BUSINESS_PROVIDER: APPLE_MESSAGES_BUSINESS,
        "apple_business_chat": APPLE_MESSAGES_BUSINESS,
        "apple_messages": APPLE_MESSAGES_BUSINESS,
    }
    return aliases.get(token, token)


def adapter_catalog() -> list[Dict[str, Any]]:
    return [deepcopy(item) for item in _ADAPTERS.values()]


def get_adapter(provider: str) -> Dict[str, Any]:
    key = _adapter_key(provider)
    adapter = _ADAPTERS.get(key)
    if not adapter:
        raise BusinessMessagingAdapterError(f"Unsupported business messaging provider: {_text(provider) or 'unknown'}.")
    return deepcopy(adapter)


def build_review_packet(provider: str, *, support_url: Optional[str] = None, privacy_url: Optional[str] = None) -> Dict[str, Any]:
    adapter = get_adapter(provider)
    checklist = []
    for requirement in adapter.get("review_requirements", []):
        item = dict(requirement)
        requirement_id = _text(item.get("id"))
        if requirement_id == "human_handoff":
            item["ready"] = bool(_text(support_url))
            item["evidence"] = _text(support_url) or None
        elif requirement_id == "privacy_and_retention":
            item["ready"] = bool(_text(privacy_url))
            item["evidence"] = _text(privacy_url) or None
        else:
            item["ready"] = False
            item["evidence"] = None
        checklist.append(item)
    return {
        "provider": adapter["id"],
        "display_name": adapter["display_name"],
        "stage": adapter["stage"],
        "launch_allowed": adapter["launch_allowed"],
        "checklist": checklist,
    }


def normalize_inbound_event(provider: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    adapter = get_adapter(provider)
    if not isinstance(payload, dict):
        raise BusinessMessagingAdapterError("Inbound business message payload must be an object.")
    external_user_id = _text(
        payload.get("external_user_id")
        or payload.get("apple_user_id")
        or payload.get("customer_id")
        or payload.get("user_id")
    )
    external_thread_id = _text(
        payload.get("external_thread_id")
        or payload.get("conversation_id")
        or payload.get("thread_id")
        or external_user_id
    )
    text = _text(payload.get("text") or payload.get("message") or payload.get("body"))
    inbound_event_id = _text(payload.get("event_id") or payload.get("message_id") or payload.get("id"))
    if not external_user_id:
        raise BusinessMessagingAdapterError("Inbound business message is missing external_user_id.")
    if not inbound_event_id:
        raise BusinessMessagingAdapterError("Inbound business message is missing event_id/message_id.")
    if not text:
        raise BusinessMessagingAdapterError("Inbound business message is missing text.")
    return {
        "provider": adapter["id"],
        "runtime_lane": adapter["runtime_lane"],
        "external_user_id": external_user_id,
        "external_thread_id": external_thread_id,
        "inbound_event_id": inbound_event_id,
        "text": text,
        "requires_ai_disclosure": bool(adapter.get("requires_ai_disclosure")),
        "requires_human_handoff": bool(adapter.get("requires_human_handoff")),
        "user_initiated": bool(payload.get("user_initiated", True)),
        "raw_metadata": {
            "msp_provider": _text(payload.get("msp_provider")) or None,
            "locale": _text(payload.get("locale")) or None,
        },
    }


def build_outbound_envelope(
    provider: str,
    *,
    external_thread_id: str,
    text: str,
    human_handoff_available: bool,
    ai_disclosure_sent: bool,
    proof_log_id: Optional[str] = None,
) -> Dict[str, Any]:
    adapter = get_adapter(provider)
    if adapter.get("requires_human_handoff") and not human_handoff_available:
        raise BusinessMessagingAdapterError("Business messaging outbound requires human handoff availability.")
    if adapter.get("requires_ai_disclosure") and not ai_disclosure_sent:
        raise BusinessMessagingAdapterError("Business messaging outbound requires AI disclosure before reply.")
    thread_id = _text(external_thread_id)
    body = _text(text)
    if not thread_id:
        raise BusinessMessagingAdapterError("Business messaging outbound requires external_thread_id.")
    if not body:
        raise BusinessMessagingAdapterError("Business messaging outbound text is required.")
    return {
        "provider": adapter["id"],
        "runtime_provider": adapter["provider"],
        "runtime_lane": adapter["runtime_lane"],
        "external_thread_id": thread_id,
        "text": body,
        "human_handoff_available": True,
        "ai_disclosure_sent": True,
        "proof_log_id": _text(proof_log_id) or None,
        "dispatch_allowed": bool(adapter.get("launch_allowed")),
        "dispatch_blocker": None if adapter.get("launch_allowed") else "adapter_not_launch_ready",
    }

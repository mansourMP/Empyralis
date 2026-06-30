from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List, Optional

from server_modules import credit_ledger_contract
from server_modules import product_catalog_live_data_service
from server_modules import studio_proof_agent_seed_service


ELEVATED_ACTION_PATTERNS = {
    "discount": re.compile(r"\b(discount|price match|price-match|cheaper|coupon|deal)\b", re.IGNORECASE),
    "refund": re.compile(r"\b(refund|return money|money back|chargeback)\b", re.IGNORECASE),
    "payment_link": re.compile(r"\b(payment link|pay now|checkout link|invoice link)\b", re.IGNORECASE),
    "mass_message": re.compile(r"\b(mass message|broadcast|send to everyone|all customers|bulk message)\b", re.IGNORECASE),
}
CATALOG_QUERY_PATTERN = re.compile(
    r"\b(do you have|in stock|available|stock|sku|price|how much|size|catalog|inventory)\b",
    re.IGNORECASE,
)
ORDER_STATUS_PATTERN = re.compile(r"\b(order|tracking|shipment|delivery).{0,24}\b(status|pending|where|check)\b", re.IGNORECASE)
SHIPPING_PATTERN = re.compile(r"\b(shipping|ship|delivery|arrive|eta)\b", re.IGNORECASE)
RETURN_POLICY_PATTERN = re.compile(r"\b(return|worn|exchange|policy)\b", re.IGNORECASE)
PURCHASE_INTENT_PATTERN = re.compile(r"\b(buy|purchase|order|pay|checkout|reserve)\b", re.IGNORECASE)


def _normalize_text(value: Any, *, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _truncate_summary(value: Any, *, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", _normalize_text(value))
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _elevated_action_for_message(message: str) -> Optional[str]:
    for action, pattern in ELEVATED_ACTION_PATTERNS.items():
        if pattern.search(message):
            return action
    return None


def _faq_answer(message: str, faq_items: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if SHIPPING_PATTERN.search(message):
        target = "shipping"
    elif RETURN_POLICY_PATTERN.search(message):
        target = "return"
    else:
        return None
    for item in faq_items:
        question = _normalize_text(item.get("question")).lower()
        if target in question:
            return {
                "question": _normalize_text(item.get("question")),
                "answer": _normalize_text(item.get("answer")),
            }
    return None


def _activity_event(
    *,
    event: str,
    deployed_agent_id: str,
    customer_message: str,
    status: str = "logged",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "event": event,
        "actor_type": "deployed_agent",
        "actor_id": deployed_agent_id,
        "status": status,
        "customer_message_summary": _truncate_summary(customer_message),
        "metadata": dict(metadata or {}),
    }


def _public_tier_label(public_tier: Any) -> str:
    token = _normalize_text(public_tier, default="pro").replace("_", " ")
    return token.title()


def _line_item_public_projection(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "credit_item_type": _normalize_text(item.get("credit_item_type")),
        "quantity": item.get("quantity"),
        "quantity_unit": _normalize_text(item.get("quantity_unit")),
        "billing_source": _normalize_text(item.get("billing_source")),
        "public_tier": item.get("public_tier"),
        "credit_multiplier": item.get("credit_multiplier"),
    }


def _line_item_admin_projection(item: Dict[str, Any]) -> Dict[str, Any]:
    return dict(item)


def build_shop_assistant_backend_package() -> Dict[str, Any]:
    contract = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")
    if not isinstance(contract, dict):
        raise ValueError("Shop Assistant proof-agent contract is unavailable.")
    revenue_proof = _coerce_dict(contract.get("revenue_proof"))
    return {
        "package_id": "shop_assistant_revenue_agent",
        "package_version": "1.0",
        "vertical": "shop_assistant",
        "template_slug": "shop-assistant",
        "catalog_schema": deepcopy(revenue_proof.get("product_catalog_schema") or {}),
        "demo_catalog_rows": deepcopy(revenue_proof.get("demo_catalog_rows") or []),
        "faq_seed": deepcopy(revenue_proof.get("faq_and_policy_seed") or []),
        "return_policy": {
            "summary": "Returns are accepted only for unworn items within 14 days.",
            "owner_exception_required": True,
        },
        "elevated_actions": {
            "discount": "owner_required",
            "refund": "owner_required",
            "payment_link": "owner_required",
            "mass_message": "owner_required",
        },
        "activity_event_contract": [
            "studio.proof.shop_assistant.conversation_handled",
            "studio.proof.shop_assistant.inventory_hit",
            "studio.proof.shop_assistant.intent_captured",
            "studio.proof.shop_assistant.elevated_action_triggered",
            "studio.proof.shop_assistant.faq_answered",
            "studio.proof.shop_assistant.order_status_requested",
        ],
        "roi_metric_contract": [
            "questions_handled",
            "escalations",
            "inventory_hits",
            "estimated_saved_minutes",
        ],
        "golden_conversations": deepcopy(revenue_proof.get("golden_conversations") or []),
    }


def build_shop_assistant_activity_billing_evidence(
    evaluation_result: Dict[str, Any],
    *,
    public_tier: str = "pro",
    total_tokens: int = 900,
    provider_metadata: Optional[Dict[str, Any]] = None,
    runtime_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    provider_payload = dict(provider_metadata or {})
    public_tier_token = _normalize_text(public_tier, default="pro").lower().replace("-", "_")
    ai_line_item = credit_ledger_contract.build_credit_ledger_line_item(
        metadata={
            "public_tier": public_tier_token,
            "billing_source": "empyralis_credits",
            **provider_payload,
        },
        public_tier=public_tier_token,
        billing_source="empyralis_credits",
        total_tokens=total_tokens,
    )
    credit_line_items = [ai_line_item]
    connector_source = _coerce_dict(_coerce_dict(evaluation_result.get("catalog")).get("source"))
    if connector_source:
        credit_line_items.append(
            credit_ledger_contract.build_credit_ledger_line_item(
                metadata={
                    "credit_item_type": "connector_read",
                    "connector_kind": connector_source.get("connector_type") or "spreadsheet",
                    "connector_id": connector_source.get("connector_id"),
                    "read_count": 1,
                },
                billing_source="empyralis_credits",
            )
        )

    used_credits = round(
        sum(
            float(item.get("quantity") or 0.0) * float(item.get("credit_multiplier") or 0.0)
            for item in credit_line_items
        ),
        3,
    )
    activity_events = list(evaluation_result.get("activity_events") or [])
    approval = _coerce_dict(evaluation_result.get("approval"))
    answered_product_question = bool(evaluation_result.get("intent") == "catalog_lookup")
    visible_activity = {
        "title": (
            "Shop Assistant answered product question."
            if answered_product_question
            else "Shop Assistant handled customer question."
        ),
        "summary": _normalize_text(evaluation_result.get("answer")),
        "status": _normalize_text(evaluation_result.get("status"), default="logged"),
        "public_tier": public_tier_token,
        "tier_label": _public_tier_label(public_tier_token),
        "used_credits": used_credits,
        "credit_line_items": [_line_item_public_projection(item) for item in credit_line_items],
        "elevated_action": bool(approval.get("required")),
        "elevated_action_type": approval.get("action"),
        "roi_metrics": _coerce_dict(evaluation_result.get("roi_metrics")),
    }
    admin_audit = {
        "raw_provider": provider_payload.get("effective_provider") or provider_payload.get("internal_provider"),
        "raw_model": provider_payload.get("effective_model") or provider_payload.get("internal_model"),
        "fallback_provider": provider_payload.get("fallback_provider"),
        "fallback_model": provider_payload.get("fallback_model"),
        "runtime": dict(runtime_metadata or {"runtime_placement": "managed_cloud", "runtime_supply": "empyralis"}),
        "token_usage": {"total_tokens": int(total_tokens or 0)},
        "credit_line_items": [_line_item_admin_projection(item) for item in credit_line_items],
        "activity_events": activity_events,
        "safety_decision": {
            "elevated_action": bool(approval.get("required")),
            "elevated_action_type": approval.get("action"),
            "risk_class": approval.get("risk_class"),
        },
    }
    return {
        "visible_activity": visible_activity,
        "admin_audit": admin_audit,
        "credit_line_items": credit_line_items,
    }


def evaluate_shop_assistant_customer_question(
    deployed_agent: Dict[str, Any],
    *,
    workspace_id: str,
    customer_message: str,
    connector_id: Optional[str] = None,
) -> Dict[str, Any]:
    message = _normalize_text(customer_message)
    if not message:
        raise ValueError("customer_message is required.")

    deployed_agent_id = _normalize_text(deployed_agent.get("id"), default="deployed_agent")
    package = build_shop_assistant_backend_package()
    events: List[Dict[str, Any]] = []
    elevated_action = _elevated_action_for_message(message)
    purchase_intent = bool(PURCHASE_INTENT_PATTERN.search(message))

    if elevated_action:
        events.append(
            _activity_event(
                event="studio.proof.shop_assistant.elevated_action_triggered",
                deployed_agent_id=deployed_agent_id,
                customer_message=message,
                status="elevated_action",
                metadata={"elevated_action": elevated_action},
            )
        )
        if purchase_intent:
            events.append(
                _activity_event(
                    event="studio.proof.shop_assistant.intent_captured",
                    deployed_agent_id=deployed_agent_id,
                    customer_message=message,
                    metadata={"intent": "purchase"},
                )
            )
        return {
            "ok": True,
            "status": "elevated_action",
            "intent": elevated_action,
            "answer": _elevated_action_response(elevated_action),
            "approval": {
                "required": True,
                "action": elevated_action,
                "risk_class": "ORANGE",
                "reason": "Owner notification required before this customer-visible business action.",
            },
            "activity_events": events,
            "roi_metrics": compute_shop_assistant_roi_metrics(events),
        }

    catalog_result: Optional[Dict[str, Any]] = None
    if CATALOG_QUERY_PATTERN.search(message):
        try:
            catalog_result = product_catalog_live_data_service.lookup_product_catalog(
                deployed_agent,
                workspace_id=workspace_id,
                query=message,
                connector_id=connector_id,
            )
        except ValueError:
            catalog_result = None
        if catalog_result and catalog_result.get("matches"):
            events.append(
                _activity_event(
                    event="studio.proof.shop_assistant.inventory_hit",
                    deployed_agent_id=deployed_agent_id,
                    customer_message=message,
                    metadata={
                        "match_count": len(catalog_result.get("matches") or []),
                        "top_sku": (catalog_result.get("matches") or [{}])[0].get("sku"),
                    },
                )
            )
            events.append(
                _activity_event(
                    event="studio.proof.shop_assistant.conversation_handled",
                    deployed_agent_id=deployed_agent_id,
                    customer_message=message,
                    metadata={"resolution": "catalog_answer"},
                )
            )
            return {
                "ok": True,
                "status": "answered",
                "intent": "catalog_lookup",
                "answer": catalog_result["answer"],
                "catalog": catalog_result,
                "approval": {"required": False},
                "activity_events": events,
                "roi_metrics": compute_shop_assistant_roi_metrics(events),
            }

    faq = _faq_answer(message, _coerce_dict(package).get("faq_seed") or [])
    if faq:
        events.append(
            _activity_event(
                event="studio.proof.shop_assistant.faq_answered",
                deployed_agent_id=deployed_agent_id,
                customer_message=message,
                metadata={"faq_question": faq["question"]},
            )
        )
        events.append(
            _activity_event(
                event="studio.proof.shop_assistant.conversation_handled",
                deployed_agent_id=deployed_agent_id,
                customer_message=message,
                metadata={"resolution": "faq_answer"},
            )
        )
        return {
            "ok": True,
            "status": "answered",
            "intent": "faq",
            "answer": faq["answer"],
            "approval": {"required": False},
            "activity_events": events,
            "roi_metrics": compute_shop_assistant_roi_metrics(events),
        }

    if ORDER_STATUS_PATTERN.search(message):
        events.append(
            _activity_event(
                event="studio.proof.shop_assistant.order_status_requested",
                deployed_agent_id=deployed_agent_id,
                customer_message=message,
                status="needs_connector",
                metadata={"connector": "order_status"},
            )
        )
        return {
            "ok": True,
            "status": "needs_connector",
            "intent": "order_status",
            "answer": "I can check order status when the order source is connected. I will escalate this if the source is unavailable or inconsistent.",
            "approval": {"required": False},
            "activity_events": events,
            "roi_metrics": compute_shop_assistant_roi_metrics(events),
        }

    events.append(
        _activity_event(
            event="studio.proof.shop_assistant.conversation_handled",
            deployed_agent_id=deployed_agent_id,
            customer_message=message,
            metadata={"resolution": "general_shop_answer"},
        )
    )
    return {
        "ok": True,
        "status": "answered",
        "intent": "general",
        "answer": "I can help with product availability, pricing, shipping, returns, and order-status questions.",
        "approval": {"required": False},
        "activity_events": events,
        "roi_metrics": compute_shop_assistant_roi_metrics(events),
    }


def _elevated_action_response(elevated_action: str) -> str:
    if elevated_action == "discount":
        return "I can note the discount request, but an owner needs to review before applying or promising a discount."
    if elevated_action == "refund":
        return "I can collect the refund details, but an owner needs to review before processing a refund."
    if elevated_action == "payment_link":
        return "I can capture purchase intent, but an owner needs to review before sending a payment link."
    if elevated_action == "mass_message":
        return "Mass customer messages need an owner to review before anything is sent."
    return "An owner needs to review before this action can continue."


def compute_shop_assistant_roi_metrics(activity_events: List[Dict[str, Any]]) -> Dict[str, Any]:
    questions_handled = sum(1 for event in activity_events if event.get("event") == "studio.proof.shop_assistant.conversation_handled")
    escalations = sum(1 for event in activity_events if bool(event.get("owner_notification_required")))
    inventory_hits = sum(1 for event in activity_events if event.get("event") == "studio.proof.shop_assistant.inventory_hit")
    intent_captures = sum(1 for event in activity_events if event.get("event") == "studio.proof.shop_assistant.intent_captured")
    estimated_saved_minutes = round((questions_handled * 3.0) + (inventory_hits * 2.0) + (intent_captures * 1.5), 2)
    return {
        "questions_handled": questions_handled,
        "escalations": escalations,
        "inventory_hits": inventory_hits,
        "purchase_intent_captures": intent_captures,
        "estimated_saved_minutes": estimated_saved_minutes,
        "estimated_saved_time_label": f"{estimated_saved_minutes:g} minutes",
    }

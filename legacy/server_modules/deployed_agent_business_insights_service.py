from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import auth as auth_module
from server_modules import control_plane_repository
from server_modules import deployed_agent_service
from server_modules import entitlements_service
from server_modules import secret_redaction_service


BUSINESS_INSIGHT_STATUSES = {"candidate", "approved", "dismissed", "archived", "applied"}

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_LONG_DIGIT_RE = re.compile(r"\b\d{5,}\b")
_NAME_PATTERN_RE = re.compile(r"\b(my name is|i am|i'm|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b")
_WHITESPACE_RE = re.compile(r"\s+")
_FORBIDDEN_INSIGHT_METADATA_KEYS = {
    "external_user_id",
    "session_id",
    "session_key",
    "phone",
    "phone_number",
    "name",
    "customer_name",
    "email",
    "authorization",
    "token",
    "access_token",
}


def _normalize_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "").strip())


def _lower_text(value: Any) -> str:
    return _normalize_text(value).lower()


def _redact_example(value: Any, *, limit: int = 180) -> str:
    text = _normalize_text(value)
    text = secret_redaction_service.redact_text(text)
    text = _EMAIL_RE.sub("[email]", text)
    text = _PHONE_RE.sub("[phone]", text)
    text = _LONG_DIGIT_RE.sub("[number]", text)
    text = _NAME_PATTERN_RE.sub(lambda match: f"{match.group(1)} [name]", text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _confidence_for_count(count: int, *, base: float = 0.45) -> float:
    return min(0.95, round(base + (max(1, int(count or 1)) * 0.08), 2))


def detect_business_insight_candidates(
    *,
    user_message: str,
    assistant_reply: str = "",
    channel_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    text = _lower_text(f"{user_message} {assistant_reply}")
    example = _redact_example(user_message)
    if not example:
        return []
    candidates: List[Dict[str, Any]] = []
    channel = str(channel_key or "").strip().lower()

    if re.search(r"\b(price\s*match|match\s+.*price|cheaper|competitor|discount|coupon|lowest price|best price)\b", text):
        candidates.append({
            "pattern_key": "pricing.price_match_or_discount_pressure",
            "insight_type": "pricing_intelligence",
            "title": "Price-match or discount pressure detected",
            "summary": "Multiple customer turns are asking for discounts, price matching, or cheaper alternatives.",
            "recommendation": "Review whether the agent should explain the price policy more clearly. Do not change prices automatically.",
            "sensitivity": "orange",
            "confidence": _confidence_for_count(1, base=0.52),
            "redacted_examples": [example],
            "metadata": {"channel_key": channel, "pricing_action_allowed": False},
        })

    if re.search(r"\b(urgent|asap|right now|immediately|tonight|outside|waiting|need it now|need .{0,40} today|no alternative)\b", text):
        candidates.append({
            "pattern_key": "pricing.urgent_price_insensitive_signal",
            "insight_type": "pricing_intelligence",
            "title": "Urgent purchase context detected",
            "summary": "Customer language suggests urgency or low ability to wait, which may affect price sensitivity.",
            "recommendation": "Keep pricing decisions with the owner. The agent may surface urgency, but must not alter price, discount, or payment terms.",
            "sensitivity": "orange",
            "confidence": _confidence_for_count(1, base=0.5),
            "redacted_examples": [example],
            "metadata": {"channel_key": channel, "pricing_action_allowed": False},
        })

    if re.search(r"\b(in stock|available|availability|size|inventory|have any|left|sold out)\b", text):
        candidates.append({
            "pattern_key": "operations.inventory_availability_questions",
            "insight_type": "business_pattern",
            "title": "Inventory availability questions are recurring",
            "summary": "Customers are asking about stock, size, or availability details.",
            "recommendation": "Connect or refresh live inventory data so the agent can answer with evidence instead of handoff.",
            "sensitivity": "yellow",
            "confidence": _confidence_for_count(1),
            "redacted_examples": [example],
            "metadata": {"channel_key": channel},
        })

    if re.search(r"\b(buy|order|reserve|checkout|book|appointment|schedule|pay|payment link)\b", text):
        candidates.append({
            "pattern_key": "revenue.purchase_or_booking_intent",
            "insight_type": "business_pattern",
            "title": "Purchase or booking intent detected",
            "summary": "Customers are reaching the point where they want to order, book, reserve, or pay.",
            "recommendation": "Review whether this agent needs a governed checkout, booking, or owner-handoff workflow.",
            "sensitivity": "yellow",
            "confidence": _confidence_for_count(1),
            "redacted_examples": [example],
            "metadata": {"channel_key": channel},
        })

    return candidates


def _workspace_business_insights_enabled(workspace_id: str) -> bool:
    try:
        payload = entitlements_service.workspace_entitlement_payload_for_workspace_id(workspace_id=workspace_id)
    except Exception:
        return False
    capabilities = payload.get("capabilities") if isinstance(payload, dict) else {}
    if isinstance(capabilities, dict):
        return bool(
            capabilities.get("business_insights_enabled")
            or capabilities.get("advanced_features_enabled")
            or capabilities.get("premium_tools_enabled")
        )
    entitlements = payload.get("entitlements") if isinstance(payload, dict) else {}
    return bool(isinstance(entitlements, dict) and entitlements.get("advanced_features_enabled"))


async def record_deployed_agent_turn_insights(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    user_message: str,
    assistant_reply: str = "",
) -> List[Dict[str, Any]]:
    if not deployed_agent_id or not workspace_id or not tenant_id:
        return []
    if not _workspace_business_insights_enabled(workspace_id):
        return []
    candidates = detect_business_insight_candidates(
        user_message=user_message,
        assistant_reply=assistant_reply,
        channel_key=channel_key,
    )
    recorded: List[Dict[str, Any]] = []
    for candidate in candidates:
        sanitized_metadata = secret_redaction_service.sanitize_mapping(candidate.get("metadata"))
        sanitized_metadata = {
            key: value
            for key, value in dict(sanitized_metadata or {}).items()
            if str(key or "").strip().lower() not in _FORBIDDEN_INSIGHT_METADATA_KEYS
        }
        sanitized_examples = [
            _redact_example(item)
            for item in list(candidate.get("redacted_examples") or [])
            if _normalize_text(item)
        ][:5]
        payload = await control_plane_repository.upsert_deployed_agent_business_insight_candidate(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            pattern_key=str(candidate.get("pattern_key") or ""),
            insight_type=str(candidate.get("insight_type") or "business_pattern"),
            title=str(candidate.get("title") or ""),
            summary=str(candidate.get("summary") or ""),
            recommendation=str(candidate.get("recommendation") or ""),
            sensitivity=str(candidate.get("sensitivity") or "yellow"),
            channel_key=channel_key,
            confidence=float(candidate.get("confidence") or 0.0),
            redacted_examples=sanitized_examples,
            metadata=sanitized_metadata,
        )
        if isinstance(payload, dict):
            recorded.append(payload)
    return recorded


def _require_real_workspace_owner(current_user: Any, workspace_id: str) -> str:
    if not isinstance(current_user, dict) or not str(current_user.get("user_id") or "").strip():
        raise HTTPException(status_code=401, detail="Authenticated owner user required.")
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type and auth_type != "bearer":
        raise HTTPException(status_code=403, detail="Real authenticated owner session required.")
    return auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")


async def _resolve_owner_deployed_agent_scope(
    *,
    current_user: Any,
    workspace_id: str,
    deployed_agent_id: str,
) -> Dict[str, Any]:
    resolved_workspace_id = _require_real_workspace_owner(current_user, workspace_id)
    tenant_id = auth_module.workspace_tenant_id(current_user, resolved_workspace_id)
    deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    if not isinstance(deployed_agent, dict):
        raise HTTPException(status_code=404, detail="Deployed agent not found in this workspace.")
    workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    try:
        entitlements_service.enforce_advanced_features_access(
            workspace=workspace if isinstance(workspace, dict) else {"workspace_id": resolved_workspace_id},
        )
    except entitlements_service.EntitlementError as error:
        raise HTTPException(
            status_code=402,
            detail={
                "reason": error.reason,
                "message": error.message,
                "entitlement_state": error.entitlement_state,
            },
        ) from error
    return {
        "tenant_id": tenant_id,
        "workspace_id": resolved_workspace_id,
        "deployed_agent": deployed_agent,
    }


async def list_owner_business_insights(
    *,
    current_user: Any,
    workspace_id: str,
    deployed_agent_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    scope = await _resolve_owner_deployed_agent_scope(
        current_user=current_user,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
    )
    items = await control_plane_repository.list_deployed_agent_business_insights(
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        deployed_agent_id=str(scope["deployed_agent"].get("id") or ""),
        status=status,
        limit=limit,
        offset=offset,
    )
    return {
        "workspace_id": scope["workspace_id"],
        "deployed_agent_id": str(scope["deployed_agent"].get("id") or ""),
        "count": len(items),
        "items": items,
    }


async def review_owner_business_insight(
    *,
    current_user: Any,
    workspace_id: str,
    deployed_agent_id: str,
    insight_id: str,
    status: str,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_status = str(status or "").strip().lower()
    if resolved_status not in {"approved", "dismissed", "archived"}:
        raise HTTPException(status_code=400, detail="Unsupported review action.")
    scope = await _resolve_owner_deployed_agent_scope(
        current_user=current_user,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
    )
    deployed_agent_service._enforce_deployed_agent_service_decision(
        "business_insight_review",
        deployed_agent=scope["deployed_agent"],
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        current_user=current_user,
        insight_id=insight_id,
    )
    insight = await control_plane_repository.update_deployed_agent_business_insight_status(
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        deployed_agent_id=str(scope["deployed_agent"].get("id") or ""),
        insight_id=insight_id,
        status=resolved_status,
        actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        metadata={"owner_review_note": _redact_example(note or "", limit=280)} if note else {},
    )
    if not isinstance(insight, dict):
        raise HTTPException(status_code=404, detail="Business insight not found.")
    return {"insight": insight}


async def apply_owner_business_insight(
    *,
    current_user: Any,
    workspace_id: str,
    deployed_agent_id: str,
    insight_id: str,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    scope = await _resolve_owner_deployed_agent_scope(
        current_user=current_user,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
    )
    deployed_agent_service._enforce_deployed_agent_service_decision(
        "business_insight_apply",
        deployed_agent=scope["deployed_agent"],
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        current_user=current_user,
        insight_id=insight_id,
    )
    existing = await control_plane_repository.get_deployed_agent_business_insight(
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        deployed_agent_id=str(scope["deployed_agent"].get("id") or ""),
        insight_id=insight_id,
    )
    if not isinstance(existing, dict):
        raise HTTPException(status_code=404, detail="Business insight not found.")
    if str(existing.get("status") or "").strip().lower() != "approved":
        raise HTTPException(status_code=409, detail="Only approved insights can be applied.")
    insight = await control_plane_repository.mark_deployed_agent_business_insight_applied(
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        deployed_agent_id=str(scope["deployed_agent"].get("id") or ""),
        insight_id=insight_id,
        actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        metadata={
            "application_mode": "owner_confirmed",
            "policy_or_knowledge_change_auto_applied": False,
            **({"owner_apply_note": _redact_example(note or "", limit=280)} if note else {}),
        },
    )
    if not isinstance(insight, dict):
        raise HTTPException(status_code=409, detail="Insight must be approved before it can be applied.")
    return {
        "insight": insight,
        "applied": True,
        "policy_or_knowledge_change_auto_applied": False,
    }

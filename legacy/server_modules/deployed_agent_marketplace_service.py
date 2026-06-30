from __future__ import annotations

import hashlib
from typing import Any, Optional

from server_modules import channel_user_acquisition_service
from server_modules import control_plane_repository
from server_modules import rust_runtime_kernel_client
from server_modules.schemas import (
    MarketplaceAgentCard,
    MarketplaceAgentListResponse,
    MarketplaceUpgradeClickResponse,
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _enforce_upgrade_click_decision(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    acquisition_touch_id: Optional[str],
    attribution_token_hash: str,
    source: str,
) -> None:
    payload = {
        "operation": "deployed_agent_upgrade_click_write",
        "tenant_id": _normalize_text(tenant_id),
        "workspace_id": _normalize_text(workspace_id),
        "actor_id": _normalize_text(acquisition_touch_id) or "marketplace",
        "actor_role": "system",
        "record_type": "deployed_agent_upgrade_click",
        "agent_id": _normalize_text(deployed_agent_id),
        "idempotency_key": _normalize_text(attribution_token_hash),
        "source": _normalize_text(source).lower() or "marketplace",
        "workspace_access": True,
        "owner_access": True,
        "admin_access": True,
        "billing_entitled": True,
        "quota_ok": True,
        "status_transition_valid": True,
        "approval_provided": True,
        "owner_approval_provided": True,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "control-plane-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _normalize_text(exc.reason) or "upgrade_click_denied"
        raise RuntimeError(f"Rust marketplace upgrade-click gate blocked write: {reason}") from exc
    next_action = _normalize_text(
        (decision.get("mutation_plan") if isinstance(decision.get("mutation_plan"), dict) else {}).get("next_action")
        or decision.get("next_action")
    )
    if next_action != "apply_control_plane_write":
        raise RuntimeError(
            "Rust marketplace upgrade-click gate returned unexpected next_action: "
            f"{next_action or 'missing'}"
        )


class DeployedAgentMarketplaceService:
    async def list_public_agents(
        self,
        *,
        category: Optional[str] = None,
        cost_tier: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> MarketplaceAgentListResponse:
        safe_limit = max(1, min(int(limit or 100), 100))
        safe_offset = max(0, int(offset or 0))
        rows = await control_plane_repository.list_public_deployed_agents(
            category=category,
            cost_tier=cost_tier,
            limit=safe_limit + 1,
            offset=safe_offset,
        )
        has_more = len(rows) > safe_limit
        items = [
            MarketplaceAgentCard.model_validate(item)
            for item in rows[:safe_limit]
            if isinstance(item, dict)
        ]
        return MarketplaceAgentListResponse(
            items=items,
            limit=safe_limit,
            offset=safe_offset,
            has_more=has_more,
        )

    async def record_upgrade_click(
        self,
        *,
        channel_attribution: str,
        source: str,
        agent_id: Optional[str] = None,
    ) -> MarketplaceUpgradeClickResponse:
        acquisition_service = channel_user_acquisition_service.get_channel_user_acquisition_service()
        try:
            token_payload = acquisition_service.decode_attribution_token_payload(channel_attribution)
        except Exception:
            return MarketplaceUpgradeClickResponse(ok=False, recorded=False, duplicate=False)
        token_agent_id = _normalize_text(token_payload.get("deployed_agent_id"))
        if agent_id and token_agent_id and _normalize_text(agent_id) != token_agent_id:
            return MarketplaceUpgradeClickResponse(
                ok=False,
                recorded=False,
                duplicate=False,
                deployed_agent_id=token_agent_id or None,
            )
        try:
            touch = acquisition_service.resolve_or_create_touch_from_attribution_token(channel_attribution)
        except Exception:
            return MarketplaceUpgradeClickResponse(
                ok=False,
                recorded=False,
                duplicate=False,
                deployed_agent_id=token_agent_id or None,
            )
        if not isinstance(touch, dict):
            return MarketplaceUpgradeClickResponse(
                ok=False,
                recorded=False,
                duplicate=False,
                deployed_agent_id=token_agent_id or None,
            )
        token_hash = hashlib.sha256(str(channel_attribution).encode("utf-8")).hexdigest()
        resolved_tenant_id = _normalize_text(touch.get("tenant_id"))
        resolved_workspace_id = _normalize_text(touch.get("workspace_id"))
        resolved_deployed_agent_id = _normalize_text(touch.get("deployed_agent_id") or token_agent_id)
        resolved_touch_id = _normalize_text(touch.get("id")) or None
        resolved_source = _normalize_text(source or token_payload.get("source")) or "telegram_limit_hit"
        _enforce_upgrade_click_decision(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent_id=resolved_deployed_agent_id,
            acquisition_touch_id=resolved_touch_id,
            attribution_token_hash=token_hash,
            source=resolved_source,
        )
        result = await control_plane_repository.record_deployed_agent_upgrade_click(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent_id=resolved_deployed_agent_id,
            acquisition_touch_id=resolved_touch_id,
            channel_key=_normalize_text(touch.get("channel_key") or token_payload.get("channel_key")) or "telegram",
            external_user_id=_normalize_text(touch.get("external_user_id") or token_payload.get("external_user_id")),
            source=resolved_source,
            attribution_token_hash=token_hash,
            metadata={
                "touch_id": _normalize_text(touch.get("id")) or None,
                "campaign_token": _normalize_text(touch.get("campaign_token")) or None,
                "request_agent_id": _normalize_text(agent_id) or None,
            },
        )
        return MarketplaceUpgradeClickResponse(
            ok=bool(result.get("recorded")),
            recorded=bool(result.get("recorded")),
            duplicate=bool(result.get("duplicate")),
            deployed_agent_id=_normalize_text(result.get("deployed_agent_id") or token_agent_id) or None,
            clicked_at=_normalize_text(result.get("clicked_at")) or None,
        )


_SERVICE = DeployedAgentMarketplaceService()


def get_deployed_agent_marketplace_service() -> DeployedAgentMarketplaceService:
    return _SERVICE

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server_modules import auth as auth_module
from server_modules.deployed_agent_marketplace_service import get_deployed_agent_marketplace_service
from server_modules import marketplace_distribution_service
from server_modules import studio_proof_agent_seed_service
from server_modules.schemas import (
    MarketplaceAgentListResponse,
    MarketplaceUpgradeClickRequest,
    MarketplaceUpgradeClickResponse,
)


router = APIRouter()
get_current_user = auth_module.get_current_user


class MarketplaceDistributionPackageRequest(BaseModel):
    package_id: Optional[str] = None
    kind: Optional[str] = Field(default=None, pattern="^(agent_template|app|connector|mini_app|provider|skill)$")
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: Optional[str] = None
    publisher: Dict[str, Any] = Field(default_factory=dict)
    onboarding: Dict[str, Any] = Field(default_factory=dict)
    verification_status: Optional[str] = None
    review_state: Optional[str] = None
    health_state: Optional[str] = None
    policy_posture: Optional[str] = None
    approval_required: bool = False
    billing: Dict[str, Any] = Field(default_factory=dict)
    agent_template: Optional[Dict[str, Any]] = None
    provider: Optional[Dict[str, Any]] = None
    app: Optional[Dict[str, Any]] = None
    connector: Optional[Dict[str, Any]] = None
    mini_app: Optional[Dict[str, Any]] = None
    skill: Optional[Dict[str, Any]] = None


class MarketplaceDistributionRuntimeEventRequest(BaseModel):
    event_type: str = Field(min_length=1)
    health_state: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MarketplaceAppReviewRequest(BaseModel):
    approved: bool
    reason: Optional[str] = Field(default=None, max_length=500)
    verification_status: Optional[str] = Field(default=None, pattern="^(unverified|partner|verified)$")


@router.get("/marketplace/agents", response_model=MarketplaceAgentListResponse)
async def list_marketplace_agents(
    category: Optional[str] = None,
    cost_tier: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    return await get_deployed_agent_marketplace_service().list_public_agents(
        category=category,
        cost_tier=cost_tier,
        limit=limit,
        offset=offset,
    )


@router.post("/marketplace/upgrade-click", response_model=MarketplaceUpgradeClickResponse)
async def record_marketplace_upgrade_click(
    body: MarketplaceUpgradeClickRequest,
):
    return await get_deployed_agent_marketplace_service().record_upgrade_click(
        channel_attribution=body.channel_attribution,
        source=body.source,
        agent_id=body.agent_id,
    )


@router.get("/workspaces/{workspace_id}/marketplace/packages")
async def list_workspace_marketplace_packages(
    workspace_id: str,
    kind: Optional[str] = Query(default=None),
    runtime_type: Optional[str] = Query(default=None),
    include_review_queue: bool = Query(default=False),
    current_user=Depends(get_current_user),
):
    minimum_role = "owner" if include_review_queue else "viewer"
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role=minimum_role)
    return marketplace_distribution_service.list_marketplace_packages(
        resolved_workspace_id,
        kind=kind,
        runtime_type=runtime_type,
        include_review_queue=include_review_queue,
    )


@router.get("/workspaces/{workspace_id}/marketplace/app-submissions")
async def list_workspace_marketplace_app_submissions(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    return marketplace_distribution_service.list_marketplace_app_submissions(resolved_workspace_id)


@router.get("/workspaces/{workspace_id}/studio/templates")
async def list_workspace_studio_templates(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    items = studio_proof_agent_seed_service.list_studio_proof_agent_seed_contracts()
    return {
        "version": studio_proof_agent_seed_service.STUDIO_PROOF_AGENT_SEED_VERSION,
        "count": len(items),
        "items": items,
    }


@router.get("/workspaces/{workspace_id}/studio/templates/shop-assistant/revenue-proof")
async def get_shop_assistant_revenue_proof(
    workspace_id: str,
    conversations_handled: int = Query(default=0, ge=0),
    qualified_purchase_intent: int = Query(default=0, ge=0),
    owner_handoffs: int = Query(default=0, ge=0),
    avg_order_value_usd: Optional[float] = Query(default=None, gt=0),
    close_rate_from_intent: Optional[float] = Query(default=None, gt=0),
    gross_margin_rate: Optional[float] = Query(default=None, gt=0),
    monthly_subscription_cost_usd: Optional[float] = Query(default=None, gt=0),
    current_user=Depends(get_current_user),
):
    auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    contract = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")
    if not isinstance(contract, dict):
        raise HTTPException(status_code=404, detail="Shop Assistant template is unavailable.")
    event_counts = {
        "conversations_handled": conversations_handled,
        "qualified_purchase_intent": qualified_purchase_intent,
        "owner_handoffs": owner_handoffs,
    }
    assumptions = {
        key: value
        for key, value in {
            "avg_order_value_usd": avg_order_value_usd,
            "close_rate_from_intent": close_rate_from_intent,
            "gross_margin_rate": gross_margin_rate,
            "monthly_subscription_cost_usd": monthly_subscription_cost_usd,
        }.items()
        if value is not None
    }
    roi_snapshot = studio_proof_agent_seed_service.build_shop_assistant_roi_snapshot(
        event_counts=event_counts,
        assumptions=assumptions,
    )
    return {
        "workspace_id": workspace_id,
        "template_slug": "shop-assistant",
        "revenue_proof": contract.get("revenue_proof"),
        "roi_snapshot": roi_snapshot,
    }


@router.post("/workspaces/{workspace_id}/marketplace/providers")
async def register_workspace_marketplace_provider(
    workspace_id: str,
    body: MarketplaceDistributionPackageRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return marketplace_distribution_service.register_marketplace_package(
            resolved_workspace_id,
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
            payload={**body.model_dump(exclude_none=True), "kind": "provider"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/marketplace/apps")
async def register_workspace_marketplace_app(
    workspace_id: str,
    body: MarketplaceDistributionPackageRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return marketplace_distribution_service.submit_community_app(
            resolved_workspace_id,
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
            payload=body.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/marketplace/packages/{package_id}/review")
async def review_workspace_marketplace_app_submission(
    workspace_id: str,
    package_id: str,
    body: MarketplaceAppReviewRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    try:
        return marketplace_distribution_service.review_marketplace_app_submission(
            resolved_workspace_id,
            package_id=package_id,
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
            approved=body.approved,
            reason=body.reason,
            verification_status=body.verification_status,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/marketplace/packages/{package_id}/install")
async def install_workspace_marketplace_package(
    workspace_id: str,
    package_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return marketplace_distribution_service.install_marketplace_package(
            resolved_workspace_id,
            package_id=package_id,
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces/{workspace_id}/marketplace/packages/{package_id}/runtime-events")
async def record_workspace_marketplace_runtime_event(
    workspace_id: str,
    package_id: str,
    body: MarketplaceDistributionRuntimeEventRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return marketplace_distribution_service.record_marketplace_runtime_event(
            resolved_workspace_id,
            package_id=package_id,
            event_type=body.event_type,
            health_state=body.health_state,
            metadata=body.metadata,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

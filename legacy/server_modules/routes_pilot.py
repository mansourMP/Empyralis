"""Pilot program API routes — invite management and validation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server_modules import auth, pilot_invite_service, pilot_operations_service, pilot_proof_service

router = APIRouter(prefix="/pilot", tags=["pilot"])


class PilotInviteCreateRequest(BaseModel):
    email: Optional[str] = None
    role: str = "owner"
    plan_id: str = "pilot"
    max_uses: int = 1
    expires_hours: Optional[int] = None


class PilotInviteValidateRequest(BaseModel):
    code: str


class PilotInviteClaimRequest(BaseModel):
    code: str


class PilotFeedbackRequest(BaseModel):
    workspace_id: str
    usefulness_score: int = Field(ge=1, le=5)
    comment: Optional[str] = None
    trace_id: Optional[str] = None


class PilotIssueRequest(BaseModel):
    workspace_id: str
    user: str = ""
    workflow: str = "customer_question_handling"
    expected_behavior: str = ""
    actual_behavior: str = ""
    logs_trace_id: str = ""
    severity: str = "p2"
    fix_status: str = "open"


@router.get("/operations/contract")
async def get_pilot_operations_contract_route(
    workspace_id: Optional[str] = None,
    current_user=Depends(auth.get_current_user),
) -> Dict[str, Any]:
    if workspace_id:
        auth.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    return {"ok": True, **pilot_operations_service.get_pilot_operations_contract()}


@router.get("/operations/report")
async def get_pilot_operations_report_route(
    workspace_id: str,
    days: int = 1,
    limit: int = 500,
    current_user=Depends(auth.get_current_user),
) -> Dict[str, Any]:
    resolved_workspace_id = auth.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = auth.workspace_tenant_id(current_user, resolved_workspace_id)
    report = await pilot_operations_service.build_pilot_operations_report(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        days=days,
        limit=limit,
    )
    return {"ok": True, **report}


@router.post("/operations/feedback")
async def record_pilot_feedback_route(
    body: PilotFeedbackRequest,
    request: Request,
    current_user=Depends(auth.get_current_user),
) -> Dict[str, Any]:
    auth.validate_csrf(request)
    resolved_workspace_id = auth.enforce_workspace_access(current_user, body.workspace_id, minimum_role="viewer")
    tenant_id = auth.workspace_tenant_id(current_user, resolved_workspace_id)
    actor_id = str((current_user or {}).get("user_id") or (current_user or {}).get("sub") or "pilot_user").strip()
    return await pilot_operations_service.record_pilot_feedback(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=actor_id,
        usefulness_score=body.usefulness_score,
        comment=body.comment or "",
        trace_id=body.trace_id,
    )


@router.post("/operations/issues")
async def record_pilot_issue_route(
    body: PilotIssueRequest,
    request: Request,
    current_user=Depends(auth.get_current_user),
) -> Dict[str, Any]:
    auth.validate_csrf(request)
    resolved_workspace_id = auth.enforce_workspace_access(current_user, body.workspace_id, minimum_role="member")
    tenant_id = auth.workspace_tenant_id(current_user, resolved_workspace_id)
    actor_id = str((current_user or {}).get("user_id") or (current_user or {}).get("sub") or "pilot_operator").strip()
    try:
        issue_payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        return await pilot_operations_service.record_pilot_issue(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            actor_id=actor_id,
            issue=issue_payload,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


async def _pilot_proof_package_for_request(
    *,
    current_user: Any,
    workspace_id: str,
    days: int,
    limit: int,
) -> Dict[str, Any]:
    resolved_workspace_id = auth.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = auth.workspace_tenant_id(current_user, resolved_workspace_id)
    return await pilot_proof_service.build_pilot_proof_package(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        days=days,
        limit=limit,
    )


@router.get("/proof/readiness")
async def get_pilot_proof_readiness_route(
    workspace_id: str,
    days: int = 30,
    limit: int = 1000,
    current_user=Depends(auth.get_current_user),
) -> Dict[str, Any]:
    package = await _pilot_proof_package_for_request(
        current_user=current_user,
        workspace_id=workspace_id,
        days=days,
        limit=limit,
    )
    return {"ok": True, **package}


@router.get("/proof/case-study")
async def get_pilot_case_study_route(
    workspace_id: str,
    days: int = 30,
    limit: int = 1000,
    current_user=Depends(auth.get_current_user),
) -> Dict[str, Any]:
    package = await _pilot_proof_package_for_request(
        current_user=current_user,
        workspace_id=workspace_id,
        days=days,
        limit=limit,
    )
    return {"ok": True, "case_study": pilot_proof_service.build_case_study(package)}


@router.get("/proof/investor-memo")
async def get_pilot_investor_memo_route(
    workspace_id: str,
    days: int = 30,
    limit: int = 1000,
    current_user=Depends(auth.get_current_user),
) -> Dict[str, Any]:
    package = await _pilot_proof_package_for_request(
        current_user=current_user,
        workspace_id=workspace_id,
        days=days,
        limit=limit,
    )
    return {"ok": True, "investor_memo": pilot_proof_service.build_investor_memo(package)}


@router.get("/proof/ads-readiness")
async def get_pilot_ads_readiness_route(
    workspace_id: str,
    days: int = 30,
    limit: int = 1000,
    current_user=Depends(auth.get_current_user),
) -> Dict[str, Any]:
    package = await _pilot_proof_package_for_request(
        current_user=current_user,
        workspace_id=workspace_id,
        days=days,
        limit=limit,
    )
    return {"ok": True, "ads_readiness": pilot_proof_service.build_ads_readiness(package)}


@router.post("/invites")
async def create_pilot_invite_route(
    body: PilotInviteCreateRequest,
    current_user=Depends(auth.require_admin_access),
) -> Dict[str, Any]:
    invite = await pilot_invite_service.create_pilot_invite(
        email=body.email,
        role=body.role,
        plan_id=body.plan_id,
        max_uses=body.max_uses,
        expires_hours=body.expires_hours,
        created_by_user_id=str(current_user.get("user_id") or "").strip() or None,
    )
    return {"ok": True, "invite": invite}


@router.get("/invites")
async def list_pilot_invites_route(
    current_user=Depends(auth.require_admin_access),
) -> Dict[str, Any]:
    invites = await pilot_invite_service.list_pilot_invites()
    return {"ok": True, "items": invites}


@router.post("/invites/validate")
async def validate_pilot_invite_route(body: PilotInviteValidateRequest) -> Dict[str, Any]:
    result = await pilot_invite_service.validate_pilot_invite_code(body.code)
    return {"ok": True, **result}


@router.post("/invites/claim")
async def claim_pilot_invite_route(body: PilotInviteClaimRequest) -> Dict[str, Any]:
    result = await pilot_invite_service.claim_pilot_invite_code(body.code)
    return {"ok": True, **result}


@router.delete("/invites/{invite_id}")
async def revoke_pilot_invite_route(
    invite_id: str,
    current_user=Depends(auth.require_admin_access),
) -> Dict[str, Any]:
    invite = await pilot_invite_service.revoke_pilot_invite(invite_id)
    return {"ok": True, "invite": invite}

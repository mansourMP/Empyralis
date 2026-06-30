from __future__ import annotations

from typing import Optional

from fastapi import Depends

from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules.sage_profile_service import (
    answer_sage_profile_bootstrap,
    list_sage_profile,
    upsert_sage_profile,
)
from server_modules.schemas import (
    SageProfileBootstrapAnswerRequest,
    SageProfileUpdateRequest,
)


def register_sage_profile_routes(app) -> None:
    import server as _server

    module_globals = globals()
    for key, value in _server.__dict__.items():
        if key not in module_globals:
            module_globals[key] = value

    member_dependency = getattr(_server, "require_api_key")

    @app.get("/api/sage-profile", dependencies=[Depends(member_dependency)])
    async def get_workspace_sage_profile(
        workspace_id: Optional[str] = None,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = list_sage_profile(
            workspace_id=resolved_workspace_id,
            account_seed={
                "display_name": str((current_user or {}).get("name") or (current_user or {}).get("display_name") or "").strip(),
                "email": str((current_user or {}).get("email") or "").strip(),
            },
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.patch("/api/sage-profile", dependencies=[Depends(member_dependency)])
    async def update_workspace_sage_profile(
        body: SageProfileUpdateRequest,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = upsert_sage_profile(
            workspace_id=resolved_workspace_id,
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
            user_name=body.user_name,
            identity_summary=body.identity_summary,
            communication_style=body.communication_style,
            recurring_responsibility=body.recurring_responsibility,
            standing_rules=body.standing_rules,
            standing_rules_text=body.standing_rules_text,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.post("/api/sage-profile/bootstrap/answer", dependencies=[Depends(member_dependency)])
    async def answer_workspace_sage_profile_bootstrap(
        body: SageProfileBootstrapAnswerRequest,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = answer_sage_profile_bootstrap(
            workspace_id=resolved_workspace_id,
            answer=body.answer,
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

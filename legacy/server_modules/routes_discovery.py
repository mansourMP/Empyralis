from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from server_modules import auth as auth_module
from server_modules import discovery_feed_service


router = APIRouter()
get_current_user = auth_module.get_current_user


def _current_user_id(current_user: dict) -> Optional[str]:
    return str((current_user or {}).get("user_id") or "").strip() or None


def _current_user_creator_label(current_user: dict) -> Optional[str]:
    email = str((current_user or {}).get("email") or "").strip()
    if email and "@" in email:
        return f"@{email.split('@', 1)[0]}"
    user_id = _current_user_id(current_user)
    return f"@{user_id}" if user_id else None


@router.get("/workspaces/{workspace_id}/discovery/feed")
async def list_discovery_feed(
    workspace_id: str,
    filter: str = Query(default="all"),
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    return discovery_feed_service.list_discovery_feed(resolved_workspace_id, feed_filter=filter)


@router.post("/workspaces/{workspace_id}/discovery/items/{feed_item_id}/adopt")
async def adopt_discovery_item(
    workspace_id: str,
    feed_item_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    try:
        return discovery_feed_service.adopt_discovery_item(
            resolved_workspace_id,
            feed_item_id,
            actor_user_id=_current_user_id(current_user),
            actor_label=_current_user_creator_label(current_user),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

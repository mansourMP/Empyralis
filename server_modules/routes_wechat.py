"""WeChat inbound message handler — Gateway-only channel.

WeChat messages arrive through the Gateway (local bridge). This route
is the backend entry point. Commands are handled by the shared dispatcher;
normal messages route through the unified Sage ingress.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from server_modules.runtime_common import require_api_key

router = APIRouter()


@router.post("/sage/wechat/inbound")
async def wechat_inbound(
    request: Request,
    current_user=Depends(require_api_key),
) -> dict:
    """Receive inbound WeChat messages and route through Sage."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    text = str(body.get("text") or "").strip()
    sender_id = str(body.get("sender_id") or "").strip()
    workspace_id = str(body.get("workspace_id") or "default").strip()

    if not text:
        return {"ok": True}

    # ── Shared command dispatcher ──
    from server_modules.sage_command_dispatcher import dispatch_command
    cmd_reply = await dispatch_command(
        command=text,
        workspace_id=workspace_id,
        thread_id="sage-main",
        channel_origin="wechat_personal",
        sender_id=sender_id or None,
    )
    if cmd_reply is not None:
        return {"ok": True, "command_handled": True, "reply": cmd_reply}

    # ── Normal message: route through unified Sage ingress ──
    from server_modules.sage_turn_adapter import execute_sage_turn

    result = await execute_sage_turn(
        workspace_id=workspace_id,
        message=text,
        channel_origin="wechat_personal",
        channel_sender_id=sender_id,
        channel_sender_name=str(body.get("sender_name") or "").strip(),
    )

    return {"ok": True, "sage_replied": bool(result.message)}

"""Signal inbound message handler — Gateway-only channel.

Signal messages arrive through the Gateway's signald bridge (local process
in empyralis-gateway/src/).  The Gateway WebSocket bridge POSTs inbound
messages to this cloud-side handler.

Commands are handled by the shared dispatcher; normal messages route
through the unified Sage ingress.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from server_modules.runtime_common import require_api_key

router = APIRouter()


@router.post("/sage/signal/inbound")
async def signal_inbound(
    request: Request,
    current_user=Depends(require_api_key),
) -> dict:
    """Receive inbound Signal messages and route through Sage."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    text = str(body.get("text") or "").strip()
    sender_id = str(body.get("sender_id") or body.get("remote_jid") or "").strip()
    workspace_id = str(body.get("workspace_id") or "default").strip()

    if not text:
        return {"ok": True}

    # ── Shared command dispatcher ──
    from server_modules.sage_command_dispatcher import dispatch_command
    cmd_reply = await dispatch_command(
        command=text,
        workspace_id=workspace_id,
        thread_id="sage-main",
        channel_origin="signal_personal",
        sender_id=sender_id or None,
    )
    if cmd_reply is not None:
        return {"ok": True, "command_handled": True, "reply": cmd_reply}

    # ── Normal message: route through unified Sage ingress ──
    from server_modules.sage_turn_adapter import execute_sage_turn
    from server_modules.sage_command_dispatcher import classify_error
    from server_modules.error_notification import classify_error_notification

    try:
        result = await execute_sage_turn(
            workspace_id=workspace_id,
            message=text,
            channel_origin="signal_personal",
            channel_sender_id=sender_id,
            channel_sender_name=str(body.get("sender_name") or "").strip(),
        )
        return {"ok": True, "sage_replied": bool(result.message)}
    except Exception as _exc:
        import logging
        _logger = logging.getLogger(__name__)
        _logger.warning("execute_sage_turn failed for signal: %s", _exc)
        return {
            "ok": True,
            "sage_replied": False,
            "error_surfaced": True,
            "error_text": classify_error(str(_exc), raw_error=str(_exc)),
            "notification": classify_error_notification(
                str(_exc), raw_error=str(_exc),
            ).as_dict(),
        }

"""Inbound webhook routes for the official-WeChat (Official Account / WeCom)
cloud channel. See wechat_official_service.py's module doc for the full
architecture context and docs/design/reliability-audit-2-channels.md for why
this lives here (cloud control-plane process) rather than in the Gateway.

Route shape mirrors routes_sage_telegram_hosted.py's
telegram_agent_byo_webhook: the URL path carries the agent_install_id and
nothing else (Tencent's callers have no session), so agent_install_id is the
whole identity a request has to work with -- wechat_official_service.py's
handle_server_verification / handle_inbound_callback both resolve the bound
agent's own credentials via a bypass_rls lookup keyed on that id alone
(agent_bindings_repository.get_channel_binding_by_agent_unscoped).

Mounted (once server.py registers this router -- see this module's own
docstring note below) as: GET/POST /api/channels/wechat/webhook/{agent_install_id}
-- the exact URL a workspace owner pastes into the WeChat Official Account /
WeCom admin console's "Server Configuration" callback-URL field.

NOTE ON WIRING: this router is intentionally NOT registered in server.py by
this change -- that file is outside this task's declared edit scope
(server_modules/: new WeChat cloud channel service + inbound route +
catalog). To go live, server.py needs the same two-line addition every other
channel router already has (see its `sage_telegram_hosted_router` import at
line 260 and `app.include_router(sage_telegram_hosted_router, prefix="/api")`
at line 405):

    from server_modules.routes_wechat_official import router as wechat_official_router
    app.include_router(wechat_official_router, prefix="/api")
"""

from __future__ import annotations

import os
import threading
from typing import Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from server_modules import client_identity_service
from server_modules import request_window_quota_adapter
from server_modules import wechat_official_service as wechat

router = APIRouter()

# Same per-IP request-window limiter shape as
# routes_sage_telegram_hosted.py's _enforce_hosted_webhook_rate_limit --
# this webhook is mounted directly under /api (not through
# _dispatch_public_studio_webhook's throttling), so it needs its own guard
# against an unmetered public ingress.
_WECHAT_WEBHOOK_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("EMPYRALIS_PUBLIC_WEBHOOK_RATE_LIMIT_PER_MINUTE", "60")
)
_WECHAT_WEBHOOK_RATE_BUCKETS: Dict[str, list] = {}
_WECHAT_WEBHOOK_RATE_LOCK = threading.Lock()


def _enforce_wechat_webhook_rate_limit(request: Request, path: str) -> None:
    client_ip = client_identity_service.resolve_client_ip(request)
    decision = request_window_quota_adapter.evaluate_request_window(
        buckets=_WECHAT_WEBHOOK_RATE_BUCKETS,
        lock=_WECHAT_WEBHOOK_RATE_LOCK,
        key=f"{client_ip}:{path}",
        limit=max(1, _WECHAT_WEBHOOK_RATE_LIMIT_PER_MINUTE),
    )
    if not decision.get("allowed"):
        retry_after = int(decision.get("retry_after_seconds") or 1)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "public_webhook_rate_limited",
                "message": "WeChat/WeCom webhook ingress is receiving too many requests.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )


@router.get("/channels/wechat/webhook/{agent_install_id}")
async def wechat_server_verification(agent_install_id: str, request: Request) -> PlainTextResponse:
    """Tencent's one-time GET server-verification handshake: echo `echostr`
    back verbatim iff the signature checks out against this agent's own
    stored verify_token, else refuse with 403. Ported call shape from
    empyralis-gateway/src/channels/wechat/server.ts:120-131."""
    _enforce_wechat_webhook_rate_limit(request, "/channels/wechat/webhook")
    params = request.query_params
    echostr = params.get("echostr", "")
    result = await wechat.handle_server_verification(
        agent_install_id=agent_install_id,
        timestamp=params.get("timestamp", ""),
        nonce=params.get("nonce", ""),
        # WeCom's "safe mode" callback uses `msg_signature` instead of
        # `signature` for the same check -- accept either, matching
        # server.ts:115's identical fallback.
        signature=params.get("signature", "") or params.get("msg_signature", ""),
        echostr=echostr,
    )
    if result is None:
        raise HTTPException(status_code=403, detail="invalid_signature_or_unknown_agent")
    return PlainTextResponse(content=result, status_code=200)


@router.post("/channels/wechat/webhook/{agent_install_id}")
async def wechat_inbound_callback(agent_install_id: str, request: Request) -> PlainTextResponse:
    """Tencent's per-inbound-message POST callback.

    ACKs "success" once the signature verifies, even when the mapped
    message wasn't routed (non-text MsgType, or nothing to route) --
    mirrors server.ts:150-156's module doc: acking unconditionally past
    signature verification avoids a retry storm on messages this pass
    doesn't route. An unverified signature still gets a hard 403, never a
    silent 200 (matches the GET handshake's fail-closed behavior).

    A reply that was generated but could not be delivered gets a 503 (not a
    silent 200) so Tencent redelivers the same callback -- same contract as
    routes_sage_telegram_hosted.py's telegram_agent_byo_webhook's identical
    reply_sent check.
    """
    _enforce_wechat_webhook_rate_limit(request, "/channels/wechat/webhook")
    params = request.query_params
    raw_body_bytes = await request.body()
    raw_body = raw_body_bytes.decode("utf-8", errors="replace")

    result = await wechat.handle_inbound_callback(
        agent_install_id=agent_install_id,
        timestamp=params.get("timestamp", ""),
        nonce=params.get("nonce", ""),
        signature=params.get("signature", "") or params.get("msg_signature", ""),
        raw_body=raw_body,
    )

    if not result.get("routed"):
        reason = result.get("reason")
        if reason == "invalid_signature":
            raise HTTPException(status_code=403, detail="invalid_signature")
        raise HTTPException(status_code=404, detail="unknown_agent_or_no_active_wechat_binding")

    if result.get("processed") and result.get("reply_sent") is False:
        raise HTTPException(status_code=503, detail="reply_delivery_failed")

    return PlainTextResponse(content="success", status_code=200)

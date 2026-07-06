"""Single seam for surfacing durability failures loudly.

Durability failures — a run-state write that won't persist, a channel reply that
can't be delivered, an outbox event that can't be stored — were previously
swallowed into debug/warning logs. Silent rail failure is the exact class of bug
that most threatens a platform whose safety model is "structural rails, not
approval prompts": the platform keeps reporting healthy while a guarantee is off.

``capture_durability_failure`` makes such a failure loud through three channels:
an ERROR log, a Sentry capture, and (best-effort) an activity-ledger dead-letter
event an operator can see. It is exception-safe and non-blocking — safe to call
from any failure path, sync or async.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

LOGGER = logging.getLogger("empyralis.durability")


def _capture_to_sentry(context: str, exc: Optional[BaseException]) -> None:
    try:
        import sentry_sdk  # noqa: PLC0415
    except Exception:
        return
    try:
        if exc is not None:
            sentry_sdk.capture_exception(exc)
        else:
            sentry_sdk.capture_message(f"durability failure: {context}", level="error")
    except Exception:
        pass


def _emit_ledger_dead_letter(
    *,
    workspace_id: str,
    tenant_id: Optional[str],
    run_id: Optional[str],
    channel: Optional[str],
    event_class: str,
    action: str,
    summary: str,
) -> None:
    """Best-effort activity-ledger dead-letter event. Never blocks, never raises.

    Scheduled onto the running event loop when there is one (webhook / async
    worker paths). From a pure sync thread with no running loop we skip the
    ledger write — the ERROR log + Sentry capture already made the failure loud.
    """
    try:
        from server_modules import activity_ledger_service  # noqa: PLC0415
    except Exception:
        return

    async def _emit() -> None:
        try:
            await activity_ledger_service.append_activity_event(
                tenant_id=tenant_id or "default",
                workspace_id=workspace_id,
                actor_type="system",
                actor_id="durability_monitor",
                event_class=event_class,
                run_id=run_id,
                channel=channel,
                action=action,
                title="Durability dead-letter",
                summary=summary,
            )
        except Exception:
            LOGGER.debug("durability ledger emit failed", exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — log+Sentry already surfaced it
    loop.create_task(_emit())


def capture_durability_failure(
    context: str,
    exc: Optional[BaseException] = None,
    *,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    run_id: Optional[str] = None,
    channel: Optional[str] = None,
    event_class: str = "durability_dead_letter",
    action: Optional[str] = None,
    summary: Optional[str] = None,
) -> None:
    """Make a durability failure loud: ERROR log + Sentry + best-effort ledger.

    Exception-safe and non-blocking — designed to be called from failure paths
    that must never themselves raise.
    """
    real_exc = exc if isinstance(exc, BaseException) else None
    LOGGER.error(
        "Durable rail failure during %s: %s",
        context,
        real_exc if real_exc is not None else "(no exception)",
        exc_info=real_exc,
    )
    _capture_to_sentry(context, real_exc)
    if workspace_id:
        try:
            _emit_ledger_dead_letter(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                run_id=run_id,
                channel=channel,
                event_class=event_class,
                action=action or context,
                summary=summary or (str(exc) if exc else context),
            )
        except Exception:
            LOGGER.debug("durability ledger scheduling failed", exc_info=True)

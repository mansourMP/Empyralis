from __future__ import annotations

import logging

from server_modules import activity_ledger_service, failure_policy_service
from server_modules.channel_routing_models import ChannelExecutionResult, ChannelRoutingContext
from server_modules.error_contracts import OBSERVABILITY_FAILURE, SEVERITY_WARNING
from server_modules.channel_turn_request_service import coerce_dict


logger = logging.getLogger(__name__)


def _actor_type(owner_type: str) -> str:
    return "sage" if owner_type == "master" else "specialist"


def _actor_event_class(owner_type: str) -> str:
    return "sage_activity" if owner_type == "master" else "specialist_activity"


def _actor_id(context: ChannelRoutingContext) -> str:
    return context.responder_install_id or getattr(context.manifest, "manifest_id", "agent")


def _common_metadata(context: ChannelRoutingContext, *, outbound_event_id: str | None) -> dict[str, object]:
    return {
        **context.shared_metadata,
        "channel_event_id": outbound_event_id,
        "owner_type": context.owner_type,
    }


async def record_result(
    *,
    context: ChannelRoutingContext,
    result: ChannelExecutionResult,
    outbound_event_id: str | None,
) -> dict[str, object] | None:
    try:
        actor_type = _actor_type(context.owner_type)
        actor_id = _actor_id(context)
        metadata = _common_metadata(context, outbound_event_id=outbound_event_id)
        if result.limit_reason == "deployment_paused":
            await activity_ledger_service.append_activity_event(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_type="specialist",
                actor_id=actor_id,
                install_id=context.responder_install_id,
                thread_id=context.thread_id,
                session_key=context.session_key,
                channel=context.channel_key,
                direction="outbound",
                event_class="blocked_action",
                detail_level="timeline_detail",
                action="deployment_paused",
                title=f"{context.responder_label} is paused",
                summary=result.reply,
                status="paused",
                payload={"deployment_state": context.deployed_agent_state},
                metadata=metadata,
            )
            return
        if result.limit_reason in {"daily_message_limit_exceeded", "deployed_agent_daily_limit_exceeded"}:
            await activity_ledger_service.append_activity_event(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_type="specialist",
                actor_id=actor_id,
                install_id=context.responder_install_id,
                thread_id=context.thread_id,
                session_key=context.session_key,
                channel=context.channel_key,
                direction="outbound",
                event_class="blocked_action",
                detail_level="timeline_detail",
                action="daily_message_limit_exceeded",
                title=f"{context.responder_label} hit its daily free-tier limit",
                summary=result.reply,
                status="rate_limited",
                payload={
                    "daily_message_limit": result.metadata.get("daily_message_limit"),
                    "message_count": result.metadata.get("message_count"),
                    "remaining": result.metadata.get("remaining"),
                    "usage_day": result.metadata.get("usage_day"),
                },
                metadata=metadata,
            )
            return
        if result.limit_reason and result.limit_reason.startswith("incident_"):
            await activity_ledger_service.append_activity_event(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_type=actor_type,
                actor_id=actor_id,
                install_id=context.responder_install_id,
                thread_id=context.thread_id,
                session_key=context.session_key,
                channel=context.channel_key,
                direction="outbound",
                event_class="blocked_action",
                detail_level="timeline_detail",
                action=str(result.incident_mode or "blocked").strip().lower() or "blocked",
                title=f"{context.responder_label} action blocked",
                summary=str(result.reply or result.status or "Action blocked.").strip(),
                status=str(result.status or "paused").strip().lower() or "paused",
                payload={
                    "incident_scope": result.incident_scope,
                    "retry_after_seconds": result.retry_after_seconds,
                },
                metadata=metadata,
            )
            return

        artifact_value = result.artifact
        artifacts = artifact_value if isinstance(artifact_value, list) else [artifact_value] if isinstance(artifact_value, dict) else []
        await activity_ledger_service.append_activity_event(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_type=actor_type,
            actor_id=actor_id,
            install_id=context.responder_install_id,
            run_id=result.run_id,
            thread_id=context.thread_id,
            session_key=context.session_key,
            channel=context.channel_key,
            direction="outbound",
            event_class=_actor_event_class(context.owner_type),
            detail_level="timeline_detail",
            action=str(result.status or "completed").strip().lower() or "completed",
            title=f"{context.responder_label} responded",
            summary=result.reply,
            status=str(result.status or "completed").strip().lower() or "completed",
            review_required=bool(artifacts),
            artifacts=artifacts,
            payload={
                "limit_reason": result.limit_reason,
                "retry_after_seconds": result.retry_after_seconds,
                "quota_snapshot": result.quota_snapshot,
                "health_safety": result.health_safety,
            },
            metadata=metadata,
        )
        if result.health_safety and result.health_safety.get("red_flag_triggered"):
            await activity_ledger_service.append_activity_event(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_type=actor_type,
                actor_id=actor_id,
                install_id=context.responder_install_id,
                run_id=result.run_id,
                thread_id=context.thread_id,
                session_key=context.session_key,
                channel=context.channel_key,
                direction="outbound",
                event_class="blocked_action",
                detail_level="timeline_detail",
                action="escalated",
                title=f"{context.responder_label} escalated an urgent health concern",
                summary="Urgent symptom guidance was sent instead of a normal answer.",
                status="escalated",
                payload={
                    "escalated": True,
                    "resolution": "escalated",
                    "health_safety": result.health_safety,
                    "channel_event_id": outbound_event_id,
                },
                metadata=metadata,
            )
        if artifacts:
            await activity_ledger_service.append_activity_event(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_type=actor_type,
                actor_id=actor_id,
                install_id=context.responder_install_id,
                run_id=result.run_id,
                thread_id=context.thread_id,
                session_key=context.session_key,
                channel=context.channel_key,
                direction="outbound",
                event_class="artifact_created",
                detail_level="timeline_detail",
                action="artifact_created",
                title=f"{context.responder_label} created artifacts",
                summary=f"Created {len(artifacts)} artifact(s).",
                status="completed",
                review_required=True,
                artifacts=artifacts,
                payload={"artifact_count": len(artifacts)},
                metadata=metadata,
            )
        return None
    except Exception:
        return failure_policy_service.log_degraded_operation(
            logger=logger,
            code="channel_activity_record_failed",
            message="Failed to record channel activity.",
            error_class=OBSERVABILITY_FAILURE,
            degraded_component="channel_activity",
            severity=SEVERITY_WARNING,
            retryable=False,
            status_code=500,
            request_id=str(context.shared_metadata.get("request_id") or "").strip() or None,
            metadata={
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "channel_key": context.channel_key,
                "deployed_agent_id": context.deployed_agent_id,
                "session_key": context.session_key,
                "thread_id": context.thread_id,
                "status": result.status,
                "limit_reason": result.limit_reason,
                "run_id": result.run_id,
                "health_safety": bool(coerce_dict(result.health_safety)),
            },
        )

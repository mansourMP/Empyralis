from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from server_modules import healthguide_safety_service
from scripts.platform_execution import stack_start_command_hint


class TelegramRunDispatchService:
    def __init__(
        self,
        *,
        project_root: Path,
        default_timeout_seconds: int,
        default_max_reply_chars: int,
        send_ack: bool,
        include_run_meta: Callable[[], bool],
        humanize_run_summary: Callable[[str], str],
        truncate_one_line: Callable[[str, int], str],
        runs_get: Callable[[str], Any],
        latest_run_error_message: Callable[[Dict[str, Any]], str],
        is_non_retryable_run_error: Callable[[str], bool],
        friendly_run_error: Callable[[str], str],
        summarize_run_terminal_result: Callable[[Dict[str, Any], int], str],
        local_companion_snapshot: Callable[[], Dict[str, int]],
        can_auto_approve_wait: Callable[[Dict[str, Any]], bool],
        pending_confirmation_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
        emit_channel_run_delivery_event: Callable[..., Any],
        record_activity_event: Optional[Callable[..., Any]] = None,
        time_now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.project_root = Path(project_root)
        self.default_timeout_seconds = max(30, int(default_timeout_seconds or 30))
        self.default_max_reply_chars = max(80, int(default_max_reply_chars or 80))
        self.send_ack = bool(send_ack)
        self.include_run_meta = include_run_meta
        self.humanize_run_summary = humanize_run_summary
        self.truncate_one_line = truncate_one_line
        self.runs_get = runs_get
        self.latest_run_error_message = latest_run_error_message
        self.is_non_retryable_run_error = is_non_retryable_run_error
        self.friendly_run_error = friendly_run_error
        self.summarize_run_terminal_result = summarize_run_terminal_result
        self.local_companion_snapshot = local_companion_snapshot
        self.can_auto_approve_wait = can_auto_approve_wait
        self.pending_confirmation_payload = pending_confirmation_payload
        self.emit_channel_run_delivery_event = emit_channel_run_delivery_event
        self.record_activity_event = record_activity_event
        self.time_now = time_now
        self.sleep = sleep

    def delivery_idempotency_key(
        self,
        *,
        connector_id: str,
        chat_id: str,
        run_id: str,
        pending_message_id: Optional[str],
    ) -> str:
        operation = "edit" if str(pending_message_id or "").strip() else "send"
        pending_token = str(pending_message_id or "").strip() or "-"
        return f"telegram:{connector_id}:{chat_id}:{run_id}:{operation}:{pending_token}"

    def run_reply_text(self, status: str, run_id: str, summary: str) -> str:
        cleaned_summary = self.humanize_run_summary(summary)
        if not self.include_run_meta():
            return cleaned_summary
        status_label = "⬢ completed" if str(status or "").strip().lower() == "completed" else "⬢ failed"
        if str(run_id or "").strip():
            return f"{status_label}\nrun_id: {run_id}\n{cleaned_summary}"
        return f"{status_label}\n{cleaned_summary}"

    def wait_for_terminal_status(
        self,
        run_id: str,
        timeout_seconds: Optional[int] = None,
        max_reply_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        wait_timeout = max(30, int(timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds))
        summary_limit = int(max_reply_chars if max_reply_chars is not None else self.default_max_reply_chars)
        deadline = self.time_now() + wait_timeout
        last_status = "starting"
        no_worker_since: Optional[float] = None
        missing_run_since: Optional[float] = None
        saw_run = False
        no_worker_grace_seconds = max(8.0, min(20.0, float(wait_timeout) * 0.2))
        missing_run_grace_seconds = max(2.0, min(6.0, float(wait_timeout) * 0.1))
        while self.time_now() < deadline:
            run = self.runs_get(run_id)
            if not isinstance(run, dict):
                if missing_run_since is None:
                    missing_run_since = self.time_now()
                elif not saw_run and (self.time_now() - missing_run_since) >= missing_run_grace_seconds:
                    return {"status": "failed", "summary": "Run not found."}
                self.sleep(1.0)
                continue
            saw_run = True
            missing_run_since = None
            latest_error = self.latest_run_error_message(run)
            if latest_error and self.is_non_retryable_run_error(latest_error):
                return {
                    "status": "failed",
                    "summary": self.friendly_run_error(latest_error),
                    "auto_approved": False,
                }
            status = str(run.get("status") or "").strip().lower()
            if status:
                last_status = status
            if status in {"completed", "failed", "timeout"}:
                summary = self.summarize_run_terminal_result(run, summary_limit)
                return {
                    "status": status,
                    "summary": summary,
                    "auto_approved": False,
                }
            if status in {"queued_local", "running_local"}:
                local_state = self.local_companion_snapshot()
                if local_state["online_workers"] <= 0 and local_state["claimed_runs"] <= 0:
                    if no_worker_since is None:
                        no_worker_since = self.time_now()
                    elif (self.time_now() - no_worker_since) >= no_worker_grace_seconds:
                        return {
                            "status": "failed",
                            "summary": (
                                "Gateway is offline. "
                                f"pending={local_state['pending_runs']} claimed={local_state['claimed_runs']} "
                                f"online_workers={local_state['online_workers']}. "
                                f"Start it with: {stack_start_command_hint(self.project_root)}"
                            ),
                            "auto_approved": False,
                        }
                else:
                    no_worker_since = None
            self.sleep(1.0)
        run = self.runs_get(run_id)
        if not isinstance(run, dict) and not saw_run:
            return {"status": "failed", "summary": "Run not found."}
        if isinstance(run, dict):
            latest_error = self.latest_run_error_message(run)
            if latest_error:
                return {
                    "status": "failed",
                    "summary": self.friendly_run_error(latest_error),
                    "auto_approved": False,
                }
        if last_status in {"queued_local", "running_local"}:
            local_state = self.local_companion_snapshot()
            return {
                "status": "timeout",
                "summary": (
                    "Run timed out waiting on Local Companion. "
                    f"last_status={last_status} pending={local_state['pending_runs']} "
                    f"claimed={local_state['claimed_runs']} online_workers={local_state['online_workers']}."
                ),
                "auto_approved": False,
            }
        if last_status:
            return {
                "status": "timeout",
                "summary": f"Run timed out while waiting for completion (last_status={last_status}).",
                "auto_approved": False,
            }
        return {"status": "timeout", "summary": "Run timed out while waiting for completion."}

    def poll_run_terminal_result(
        self,
        run_id: str,
        *,
        max_reply_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        summary_limit = int(max_reply_chars if max_reply_chars is not None else self.default_max_reply_chars)
        run = self.runs_get(run_id)
        if not isinstance(run, dict):
            return {"ready": True, "status": "failed", "summary": "Run not found.", "auto_approved": False}
        latest_error = self.latest_run_error_message(run)
        if latest_error and self.is_non_retryable_run_error(latest_error):
            return {
                "ready": True,
                "status": "failed",
                "summary": self.friendly_run_error(latest_error),
                "auto_approved": False,
            }
        status = str(run.get("status") or "").strip().lower()
        if status in {"completed", "failed", "timeout"}:
            return {
                "ready": True,
                "status": status,
                "summary": self.summarize_run_terminal_result(run, summary_limit),
                "auto_approved": False,
            }
        return {
            "ready": False,
            "status": status or "starting",
            "summary": "",
            "auto_approved": False,
        }

    def schedule_final_delivery(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        chat_id: str,
        run_id: str,
        session_key: Optional[str] = None,
        pending_message_id: str,
        inbound_message_id: Optional[str],
        action: str,
        trace_id: str,
        source_event_id: str,
        tenant_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        owner_install_id: Optional[str] = None,
        owner_label: Optional[str] = None,
        owner_type: Optional[str] = None,
        deployed_agent_id: Optional[str] = None,
    ) -> None:
        provider_idempotency_key = self.delivery_idempotency_key(
            connector_id=connector_id,
            chat_id=chat_id,
            run_id=run_id,
            pending_message_id=pending_message_id,
        )
        self.emit_channel_run_delivery_event(
            channel="telegram",
            tenant_id=str(tenant_id or "").strip() or "default",
            workspace_id=workspace_id,
            run_id=run_id,
            connector_id=connector_id,
            trace_id=trace_id,
            idempotency_key=f"channel_run_delivery:telegram:{connector_id}:{run_id}",
            payload={
                "chat_id": str(chat_id or "").strip(),
                "session_key": str(session_key or "").strip() or None,
                "pending_message_id": str(pending_message_id or "").strip() or None,
                "parent_message_id": str(inbound_message_id or "").strip() or None,
                "action": str(action or "run").strip().lower() or "run",
                "source_event_id": str(source_event_id or "").strip() or None,
                "tenant_id": str(tenant_id or "").strip() or None,
                "thread_id": str(thread_id or "").strip() or None,
                "owner_install_id": str(owner_install_id or "").strip() or None,
                "owner_label": str(owner_label or "").strip() or None,
                "owner_type": str(owner_type or "").strip().lower() or None,
                "deployed_agent_id": str(deployed_agent_id or "").strip() or None,
                "delivery": {
                    "provider": "telegram",
                    "transport": (
                        "telegram_editMessageText"
                        if str(pending_message_id or "").strip()
                        else "telegram_sendMessage"
                    ),
                    "status": "pending",
                    "provider_idempotency_key": provider_idempotency_key,
                },
            },
        )

    def _record_health_safety_escalation(
        self,
        *,
        run_id: str,
        workspace_id: str,
        tenant_id: Optional[str],
        session_key: str,
        thread_id: Optional[str],
        owner_install_id: Optional[str],
        owner_label: Optional[str],
        owner_type: Optional[str],
        deployed_agent_id: Optional[str],
        trace_id: Optional[str],
        source_event_id: Optional[str],
        provider_idempotency_key: str,
        delivery_transport: str,
        outbound_message_id: Optional[str],
        delivered_reply: str,
    ) -> None:
        if not callable(self.record_activity_event):
            return
        resolved_tenant_id = str(tenant_id or "").strip()
        resolved_install_id = str(owner_install_id or "").strip()
        resolved_session_key = str(session_key or "").strip()
        resolved_thread_id = str(thread_id or "").strip()
        if not (resolved_tenant_id and resolved_install_id and resolved_session_key and resolved_thread_id):
            return
        run = self.runs_get(run_id)
        if not isinstance(run, dict):
            return
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        safety_context = healthguide_safety_service.resolve_health_safety_context(metadata=metadata)
        if not safety_context.get("enabled"):
            return
        safety_result = healthguide_safety_service.apply_health_safety_to_reply(
            reply=delivered_reply,
            user_message=context.get("user_goal") or "",
            assistant_name=str(safety_context.get("assistant_name") or "").strip() or None,
            response_payload=run.get("result_data") if isinstance(run.get("result_data"), dict) else None,
        )
        if not bool(safety_result.get("red_flag_triggered")):
            return
        actor_type = "sage" if str(owner_type or "").strip().lower() == "master" else "specialist"
        responder_label = str(owner_label or "").strip() or "Assistant"
        self.record_activity_event(
            tenant_id=resolved_tenant_id,
            workspace_id=workspace_id,
            actor_type=actor_type,
            actor_id=resolved_install_id,
            install_id=resolved_install_id,
            run_id=run_id,
            thread_id=resolved_thread_id,
            session_key=resolved_session_key,
            channel="telegram",
            direction="outbound",
            event_class="blocked_action",
            detail_level="timeline_detail",
            action="escalated",
            trace_id=str(trace_id or "").strip() or None,
            title=f"{responder_label} escalated an urgent health concern",
            summary="Urgent symptom guidance was sent instead of a normal answer.",
            status="escalated",
            payload={
                "escalated": True,
                "resolution": "escalated",
                "health_safety": safety_result,
                "delivery_transport": delivery_transport,
                "outbound_message_id": str(outbound_message_id or "").strip() or None,
            },
            metadata={
                "source_event_id": str(source_event_id or "").strip() or None,
                "provider_idempotency_key": provider_idempotency_key,
                "deployed_agent_id": str(deployed_agent_id or "").strip() or None,
            },
        )

    def deliver_final_response(
        self,
        *,
        bot_token: str,
        chat_id: str,
        workspace_id: str,
        connector_id: str,
        session_key: str,
        profile: Dict[str, Any],
        run_id: str,
        pending_message_id: Optional[str],
        inbound_message_id: Optional[str],
        action: str,
        trace_id: str,
        source_event_id: str,
        status: str,
        summary: str,
        record_channel_event: Callable[..., Any],
        send_message: Callable[..., str],
        edit_message: Callable[..., bool],
        set_connector_state: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        provider_idempotency_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        owner_install_id: Optional[str] = None,
        owner_label: Optional[str] = None,
        owner_type: Optional[str] = None,
        deployed_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_status = str(status or "").strip().lower() or "completed"
        compact_summary = self.truncate_one_line(
            str(summary or "Run finished."),
            self.default_max_reply_chars,
        )
        final_reply = self.run_reply_text(resolved_status, run_id, compact_summary)
        resolved_delivery_id = str(provider_idempotency_key or "").strip() or self.delivery_idempotency_key(
            connector_id=connector_id,
            chat_id=chat_id,
            run_id=run_id,
            pending_message_id=pending_message_id,
        )
        record_channel_event(
            channel="telegram",
            direction="system",
            event_type=f"run_{resolved_status if resolved_status in {'completed', 'failed', 'timeout'} else 'finished'}",
            text=compact_summary,
            workspace_id=workspace_id,
            session_key=session_key,
            session_id=session_key,
            parent_id=inbound_message_id or None,
            run_id=run_id,
            action=action,
            metadata={
                "connector_id": connector_id,
                "profile_id": profile.get("id"),
                "trace_id": trace_id,
                "source_event_id": source_event_id,
                "provider_idempotency_key": resolved_delivery_id,
            },
        )
        edited = False
        outbound_message_id = str(pending_message_id or "").strip() or None
        delivery_transport = "telegram_editMessageText" if pending_message_id else "telegram_sendMessage"
        if pending_message_id:
            edited = edit_message(
                bot_token=bot_token,
                chat_id=chat_id,
                message_id=pending_message_id,
                text=final_reply,
                workspace_id=workspace_id,
                action=action,
                run_id=run_id,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                idempotency_key=resolved_delivery_id,
            )
        if not edited:
            outbound_message_id = send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=final_reply,
                workspace_id=workspace_id,
                action=action,
                run_id=run_id,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                idempotency_key=resolved_delivery_id,
            )
            delivery_transport = "telegram_sendMessage"
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(self.time_now())))
        if callable(set_connector_state):
            set_connector_state(
                connector_id,
                {
                    "workspace_id": workspace_id,
                    "profile_id": profile.get("id"),
                    "last_run_id": run_id,
                    "last_action": action,
                    "last_chat_id": chat_id,
                    "last_error": None,
                    "last_error_category": None,
                    "last_error_at": None,
                    "last_processed_at": now_iso,
                    "last_outbound_message_id": outbound_message_id,
                    "last_delivery_status": "edited" if edited else "sent",
                    "last_delivery_receipt_at": now_iso,
                    "last_delivery_idempotency_key": resolved_delivery_id,
                    "last_delivery_transport": delivery_transport,
                },
            )
        self._record_health_safety_escalation(
            run_id=run_id,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            session_key=session_key,
            thread_id=thread_id,
            owner_install_id=owner_install_id,
            owner_label=owner_label,
            owner_type=owner_type,
            deployed_agent_id=deployed_agent_id,
            trace_id=trace_id,
            source_event_id=source_event_id,
            provider_idempotency_key=resolved_delivery_id,
            delivery_transport=delivery_transport,
            outbound_message_id=outbound_message_id,
            delivered_reply=compact_summary,
        )
        delivery = {
            "provider": "telegram",
            "transport": delivery_transport,
            "status": "edited" if edited else "sent",
            "provider_idempotency_key": resolved_delivery_id,
            "receipt": {
                "provider_message_id": outbound_message_id,
                "accepted_at": now_iso,
            },
            "replay_safe": True,
        }
        return {
            "status": resolved_status,
            "summary": compact_summary,
            "final_reply": final_reply,
            "edited": edited,
            "delivery": delivery,
        }

    def dispatch_run_action(
        self,
        *,
        bot_token: str,
        chat_id: str,
        workspace_id: str,
        connector_id: str,
        sender_id: str,
        update_id: int,
        inbound_message_id: Optional[str],
        profile_context: Optional[Dict[str, str]],
        media_attachments: Optional[list[Dict[str, Any]]],
        skill_override: Optional[Dict[str, Any]],
        trace_id: str,
        source_event_id: str,
        connector_entry: Optional[Dict[str, Any]],
        connector_context: Optional[Dict[str, Any]],
        session_key: str,
        profile: Dict[str, Any],
        action: str,
        goal: str,
        create_run: Callable[..., Dict[str, Any]],
        record_channel_event: Callable[..., Any],
        send_chat_action: Callable[..., Any],
        send_message: Callable[..., str],
        edit_message: Callable[..., bool],
    ) -> Dict[str, Any]:
        run_info = create_run(
            goal=goal,
            workspace_id=workspace_id,
            connector_id=connector_id,
            chat_id=chat_id,
            sender_id=sender_id,
            update_id=update_id,
            message_id=inbound_message_id or None,
            profile_context=profile_context,
            media_attachments=media_attachments,
            skill_override=skill_override,
            trace_id=trace_id,
            source_event_id=source_event_id,
            connector_entry=connector_entry,
            connector_context=connector_context,
        )
        run_id = str(run_info.get("run_id") or "").strip()
        pending_message_id = ""
        if run_id and self.send_ack:
            send_chat_action(bot_token, chat_id, action="typing")
            ack_text = "Thinking..."
            if self.include_run_meta():
                ack_text += f"\nrun_id: {run_id}"
            pending_message_id = send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=ack_text,
                workspace_id=workspace_id,
                action="thinking",
                run_id=run_id,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
            )

        if run_id:
            self.schedule_final_delivery(
                workspace_id=workspace_id,
                connector_id=connector_id,
                chat_id=chat_id,
                run_id=run_id,
                session_key=session_key,
                pending_message_id=pending_message_id,
                inbound_message_id=inbound_message_id or None,
                action=action,
                trace_id=trace_id,
                source_event_id=source_event_id,
            )
        return {
            "run_id": run_id,
            "status": "accepted" if run_id else "failed",
            "summary": "Run accepted for durable delivery." if run_id else "Run did not start.",
            "final_reply": "",
            "pending_message_id": pending_message_id,
            "edited": False,
            "route": run_info.get("route"),
        }

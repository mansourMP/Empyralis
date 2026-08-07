from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable, Optional

from server_modules import authority_mandate_service

_logger = logging.getLogger(__name__)


def _resolve_sync(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(value)
    finally:
        loop.close()


def _wake_request_payload(item: Any) -> dict[str, Any]:
    """A claimed wake request read straight back from the DB carries
    `payload` as a raw JSON string when no jsonb codec is registered on that
    connection (see bounded_scheduler_service.cancel_wake_request's own
    comment on this exact footgun) -- handle both shapes rather than assume
    one."""
    if not isinstance(item, dict):
        return {}
    payload = item.get("payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        import json as _json

        try:
            parsed = _json.loads(payload)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _resolve_heartbeat_scope(
    *,
    metadata: dict[str, Any],
    resolve_workspace_tenant_id: Optional[Callable[[str], Any]] = None,
) -> tuple[str, str]:
    workspace_id = str(metadata.get("workspace_id") or "").strip()
    if not workspace_id:
        raise ValueError("Heartbeat workspace scope is not configured.")

    tenant_id = str(metadata.get("tenant_id") or "").strip()
    if not tenant_id and callable(resolve_workspace_tenant_id):
        resolved = _resolve_sync(resolve_workspace_tenant_id(workspace_id))
        tenant_id = str(resolved or "").strip()
    if not tenant_id:
        raise ValueError("Heartbeat tenant scope is not configured.")

    return tenant_id, workspace_id


def _resolve_workspace_default_ai_provider(workspace_id: str) -> str:
    """The same "ONE AI ROAD, NO FALLBACK" resolution
    sage_agent_runtime_service._resolve_cloud_provider uses for a live chat
    turn (explicit workspace sage_ai_provider if set, else the platform
    DeepSeek default via entitlements, else a raw DeepSeek BYOK key) --
    reimplemented here rather than imported because that module is mid-edit
    elsewhere right now and its resolver is async/Sage-turn-specific. This
    intentionally does NOT fall back to "any provider with a key lying
    around" (that's what a first attempt at this fix did, via
    mini_app_invoke_service.resolve_cloud_provider_for_workspace -- it
    picked whichever cloud provider happened to be first in that function's
    fixed candidate order, landing on a rate-limited personal Anthropic
    OAuth session instead of this workspace's actual DeepSeek platform
    credits and failing the run for an unrelated reason). Returns "" (never
    raises) when nothing usable is configured -- the caller leaves
    metadata["provider"] unset and runs_execution._honest_no_provider_error
    fires its own explicit, correct message; guessing a provider here would
    make an honest "nothing is configured" failure look like a random
    provider-specific one instead.
    """
    from server_modules import control_plane_repository
    from server_modules import direct_chat_provider_service
    from server_modules import entitlements_service
    from server_modules import workspace_config_schema

    normalized_workspace_id = str(workspace_id or "default").strip() or "default"

    def _usable(provider: str) -> bool:
        credentials = direct_chat_provider_service.direct_chat_credentials(normalized_workspace_id, provider)
        return direct_chat_provider_service.supports_direct_message_native_chat(provider, credentials)

    active_provider = ""
    try:
        ws_record = _resolve_sync(control_plane_repository.get_workspace_by_id(normalized_workspace_id))
        ws_metadata = dict((ws_record or {}).get("metadata") or {})
        admin_defaults = workspace_config_schema.workspace_admin_defaults_from_metadata(ws_metadata)
        active_provider = str(admin_defaults.sage_ai_provider or "").strip().lower()
    except Exception as exc:
        _logger.info("Could not read workspace %s admin defaults for provider resolution: %s", normalized_workspace_id, exc)

    if active_provider:
        return active_provider if _usable(active_provider) else ""

    import os

    if str(os.getenv("DEEPSEEK_API_KEY") or "").strip():
        try:
            access = entitlements_service.hosted_sage_ai_access_state_for_workspace_id(workspace_id=normalized_workspace_id)
        except Exception as exc:
            _logger.info("Could not resolve platform AI entitlement for workspace %s: %s", normalized_workspace_id, exc)
            access = {}
        if access.get("allowed") and _usable("deepseek"):
            return "deepseek"
        if not access.get("allowed"):
            return ""

    return "deepseek" if _usable("deepseek") else ""


def build_heartbeat_turn_request(
    *,
    build_inbound_agent_turn_request: Callable[..., Any],
    tasks: list[str],
    metadata: dict[str, Any],
    pending_started: Any,
    authority_tier: str,
    wake_requests: Optional[list[dict[str, Any]]] = None,
    recent_changes: Optional[list[dict[str, Any]]] = None,
    scheduler_goals: Optional[list[str]] = None,
    user_preferences: Optional[str] = None,
    policy_bounds: Optional[dict[str, Any]] = None,
) -> Any:
    merged_metadata = dict(metadata or {})
    merged_metadata.update(
        {
            "source": "heartbeat",
            # This function only ever builds a turn for a scheduler-driven
            # execution (a heartbeat checklist tick or a claimed wake
            # request) -- never a live user message -- so trigger_source is
            # unconditionally "schedule", read by
            # run_service._enforce_rust_run_service_decision for attribution/
            # audit (previously unset here, so it silently fell back to the
            # generic "user" default on every autonomous run).
            "trigger_source": "schedule",
            "heartbeat_tasks": list(tasks),
            "heartbeat_pending_schedules": pending_started if isinstance(pending_started, list) else [],
            "heartbeat_trigger": str(metadata.get("trigger") or "scheduled"),
            "heartbeat_file": str(metadata.get("heartbeat_file") or ""),
        }
    )
    if wake_requests:
        merged_metadata["wake_request_ids"] = [
            str(item.get("id") or "").strip()
            for item in wake_requests
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        # docs/design/tasks-to-agents-research.md Section 4.4: a
        # task_assigned wake request (bounded_scheduler_service.
        # schedule_task_assigned_wakeup) carries task_id/task_title/
        # task_description in its payload -- thread it into merged_metadata
        # (-> context_hints["metadata"] below -> session_ctx, read by
        # direct_chat_generation_service._turn_metadata_from_session) so the
        # resulting turn's trace metadata carries the task id. This is the
        # ONLY seam that makes "task id in trace metadata" true; without it
        # the wake request would be indistinguishable from any other. At
        # most one task per heartbeat tick's tier group in practice (one
        # wakeup per assignment), so the first one found wins -- never
        # silently blended across multiple tasks.
        #
        # The SAME payload also carries `agent_id` -- the assignee
        # schedule_task_assigned_wakeup resolved (bounded_scheduler_service.py
        # ~868-873), which is a workspace_agent_installs.id, the exact id
        # space specialist_runtime_context.resolve_specialist_runtime_context
        # expects as active_agent_install_id (see project_tasks_service.
        # assign_task's own _agent_install_exists check against that same
        # table/column). Thread it under the SAME metadata key every other
        # read site already uses for this
        # (agent_turn.py's active_agent_install_id fallback for thread
        # tagging, run_service.py's _trace_root_agent_id_for_metadata and
        # _runtime_binding_install_id for hardware/gateway attachment
        # resolution) rather than a new key, so those existing consumers
        # pick it up for free -- this is the one and only place a
        # task-assigned wakeup's turn metadata gets built, so there is no
        # risk of colliding with a value set by another caller for a
        # different purpose. Without this, _execute_orion_result_via_agent_
        # engine (runs_execution.py) has nothing to resolve a specialist
        # context from and the turn runs as the workspace master (Sage).
        for item in wake_requests:
            if not isinstance(item, dict) or str(item.get("trigger_kind") or "").strip() != "task_assigned":
                continue
            task_payload = _wake_request_payload(item)
            task_id = str(task_payload.get("task_id") or "").strip()
            if not task_id:
                continue
            merged_metadata["task_id"] = task_id
            merged_metadata["assigned_task_title"] = str(task_payload.get("task_title") or "").strip()
            merged_metadata["assigned_task_description"] = str(task_payload.get("task_description") or "").strip()
            assigned_agent_id = str(task_payload.get("agent_id") or "").strip()
            if assigned_agent_id:
                merged_metadata["active_agent_install_id"] = assigned_agent_id
            break
        # Goals (build step 4, "the instruction layer"): a `goal` wake
        # request's payload carries goal_id/goal_text/instruction/
        # attempt_number/max_attempts/status -- the exact same threading
        # shape task_assigned uses just above (merged_metadata ->
        # context_hints["metadata"] -> session_ctx -> the resulting turn's
        # trace metadata), reused rather than inventing a second seam. Also
        # sets active_agent_install_id from the goal's own agent_id, same
        # reason task_assigned does: without it the turn runs as the
        # workspace master (Sage) instead of the specialist the goal
        # actually belongs to.
        for item in wake_requests:
            if not isinstance(item, dict) or str(item.get("trigger_kind") or "").strip() != "goal":
                continue
            goal_payload = _wake_request_payload(item)
            goal_id = str(goal_payload.get("goal_id") or "").strip()
            if not goal_id:
                continue
            merged_metadata["goal_id"] = goal_id
            merged_metadata["goal_text"] = str(goal_payload.get("goal_text") or "").strip()
            merged_metadata["goal_instruction"] = str(goal_payload.get("instruction") or "").strip()
            merged_metadata["goal_attempt_number"] = goal_payload.get("attempt_number")
            merged_metadata["goal_max_attempts"] = goal_payload.get("max_attempts")
            merged_metadata["goal_status"] = str(goal_payload.get("status") or "").strip()
            goal_agent_id = str(goal_payload.get("agent_id") or "").strip()
            if goal_agent_id:
                merged_metadata["active_agent_install_id"] = goal_agent_id
            break
    if recent_changes:
        merged_metadata["context_event_ids"] = [
            str(item.get("id") or "").strip()
            for item in recent_changes
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
    if policy_bounds:
        merged_metadata["scheduler_policy"] = dict(policy_bounds)
    actor_id = (
        str(merged_metadata.get("owner_user_id") or "").strip()
        or str(merged_metadata.get("owner_email") or "").strip().lower()
        or "anonymous"
    )
    actor_display_name = str(merged_metadata.get("owner_email") or actor_id).strip() or actor_id
    sections: list[str] = []
    if tasks:
        sections.append(
            "Heartbeat checklist tasks:\n"
            + "\n".join(f"- {task}" for task in tasks)
        )
    if wake_requests:
        wake_lines = []
        for item in wake_requests:
            if not isinstance(item, dict):
                continue
            trigger_kind = str(item.get("trigger_kind") or "wake").strip()
            summary = str(item.get("summary") or item.get("reason") or "").strip()
            if summary:
                wake_lines.append(f"- [{trigger_kind}] {summary}")
        if wake_lines:
            sections.append("Wake reasons:\n" + "\n".join(wake_lines))
    if merged_metadata.get("task_id"):
        # The seed prompt this wakeup exists for (Section 4.6 step 3): the
        # assigned task's title + description, verbatim, so the agent has
        # the actual work in front of it -- not just the one-line "Task
        # assigned: <title>" summary already folded into wake_lines above.
        task_lines = [f"Title: {merged_metadata.get('assigned_task_title') or '(untitled)'}"]
        task_description = str(merged_metadata.get("assigned_task_description") or "").strip()
        if task_description:
            task_lines.append(f"Description: {task_description}")
        sections.append("Assigned task:\n" + "\n".join(task_lines))
    if merged_metadata.get("goal_id"):
        # THE instruction layer (build step 4): goal_instruction is the
        # human-authored (or model-authored, via goal__update) escalation
        # rule -- "retry once, adjust the offer, escalate after 3
        # attempts" lives here, injected verbatim into every turn that
        # works this goal, exactly like assigned_task_description is above
        # for task_assigned wakeups. attempt_number/max_attempts/status are
        # real system-recorded data (bounded_scheduler_service._fire_goal
        # is the only writer of attempt_number), never the model's own
        # claim about how many times it has tried.
        goal_lines_for_turn = [f"Goal: {str(merged_metadata.get('goal_text') or '').strip()}"]
        goal_instruction = str(merged_metadata.get("goal_instruction") or "").strip()
        if goal_instruction:
            goal_lines_for_turn.append(f"Instruction: {goal_instruction}")
        attempt_number = merged_metadata.get("goal_attempt_number")
        max_attempts = merged_metadata.get("goal_max_attempts")
        if attempt_number is not None and max_attempts is not None:
            goal_lines_for_turn.append(f"Attempt {attempt_number} of {max_attempts}.")
        goal_status = str(merged_metadata.get("goal_status") or "").strip()
        if goal_status:
            goal_lines_for_turn.append(f"Current status: {goal_status}.")
        sections.append("Working goal:\n" + "\n".join(goal_lines_for_turn))
    if recent_changes:
        change_lines = []
        for item in recent_changes:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            if summary:
                source = str(item.get("source_app") or "context").strip()
                change_lines.append(f"- [{source}] {summary}")
        if change_lines:
            sections.append("Recent context changes:\n" + "\n".join(change_lines))
    goal_lines = [str(item or "").strip() for item in list(scheduler_goals or []) if str(item or "").strip()]
    if goal_lines:
        sections.append("Current goals:\n" + "\n".join(f"- {line}" for line in goal_lines))
    if user_preferences and str(user_preferences).strip():
        sections.append("User preferences:\n" + str(user_preferences).strip()[:2000])
    if policy_bounds:
        sections.append(
            "Scheduler policy bounds:\n"
            f"- quiet hours: {int(policy_bounds.get('quiet_hours_start', 23)):02d}:00 to {int(policy_bounds.get('quiet_hours_end', 7)):02d}:00\n"
            f"- max runtime seconds: {int(policy_bounds.get('max_runtime_seconds', 20))}\n"
            f"- plan tier: {str(policy_bounds.get('plan_tier') or 'standard')}"
        )
    sections.append(
        "Review the queued heartbeat and wake reasons. Decide whether any follow-up is needed now. "
        "If no follow-up is needed, explain briefly and stop. Stay inside policy and approval limits."
    )
    heartbeat_goal = "\n\n".join(section for section in sections if str(section).strip())
    # MAN-108 Bug 2: a heartbeat/wake-triggered turn (this function) never had
    # anything upstream populate metadata["provider"] -- only a chat turn does
    # that (via Sage's own entitlement-based _resolve_cloud_provider). Every
    # task-assignment wakeup therefore reached runs_execution._honest_no_
    # provider_error with metadata.provider empty and died there ~6s into
    # execution (after DAG compile/hydration), even on a workspace with a
    # perfectly good configured provider -- the run was never actually
    # attempted with it. That failure was also silent: run_orion_mission
    # catches it internally and only ever calls emit_log (into the run's own
    # ephemeral in-memory event buffer), never Python logging, so nothing
    # reached server logs and nothing reached the task. Auto-resolve the
    # workspace's own default provider here (see
    # _resolve_workspace_default_ai_provider below) so a task-assigned
    # wakeup actually runs instead of failing before it starts. If the
    # workspace genuinely has no provider configured anywhere, this is a
    # no-op (metadata.provider stays empty) and _honest_no_provider_error's
    # already-good, explicit message still fires -- that's the correct,
    # honest outcome for a workspace with nothing configured, not something
    # to paper over here.
    if not str(merged_metadata.get("provider") or "").strip():
        heartbeat_workspace_id = str(merged_metadata.get("workspace_id") or "").strip()
        if heartbeat_workspace_id:
            try:
                resolved_provider = _resolve_workspace_default_ai_provider(heartbeat_workspace_id)
            except Exception as exc:
                resolved_provider = ""
                _logger.info(
                    "Heartbeat/wake turn found no auto-resolvable cloud provider for workspace %s: %s",
                    heartbeat_workspace_id,
                    exc,
                )
            if resolved_provider:
                merged_metadata["provider"] = resolved_provider
    policy_context = {
        "trust_mode": str(merged_metadata.get("trust_mode") or "").strip() or None,
        "outcome_pack": str(merged_metadata.get("outcome_pack") or "").strip() or None,
        "execution_target": str(merged_metadata.get("execution_target") or "").strip() or None,
        "action_policy": merged_metadata.get("action_policy") if isinstance(merged_metadata.get("action_policy"), dict) else None,
    }
    context_hints = {
        "engine": str(merged_metadata.get("engine") or "orion").strip().lower() or "orion",
        "workflow_id": str(merged_metadata.get("workflow_id") or "").strip() or None,
        "provider": str(merged_metadata.get("provider") or "").strip() or None,
        "model": str(merged_metadata.get("model") or "").strip() or None,
        "credential_id": str(merged_metadata.get("credential_id") or "").strip() or None,
        "agent_role": str(merged_metadata.get("agent_role") or "orchestrator").strip() or "orchestrator",
        "max_iterations": merged_metadata.get("max_iterations"),
        "metadata": merged_metadata,
    }
    tenant_id, workspace_id = _resolve_heartbeat_scope(metadata=merged_metadata)
    return build_inbound_agent_turn_request(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        session_id=(
            str(merged_metadata.get("session_id") or "").strip()
            or str(merged_metadata.get("thread_id") or "").strip()
            or str(merged_metadata.get("parent_run_id") or "").strip()
            or str(merged_metadata.get("workflow_id") or "").strip()
            # Workspace-scoped, not the bare literal "run-start": that literal
            # is a single global key in runtime_sessions (session_id has no
            # workspace column in its lookup), so every workspace that ever
            # fell through to this fallback collided on the SAME row. Once
            # session_service.get_session_scoped's tenant-isolation check
            # enforces scope (default-on via ORION_ENFORCE_SCOPED_SESSION_
            # RESUME), every workspace except whichever one created that row
            # first raises SessionScopeViolationError here, so a task-
            # assigned wake-up's turn execution fails 100% of the time
            # (finalize_scheduler_wake_requests marks it
            # denial_reason="execution_failed") for every other workspace.
            # Scoping the fallback per-workspace makes the collision
            # impossible by construction instead of relying on enforcement
            # being loosened.
            or f"run-start:{workspace_id}"
        ),
        channel=str(merged_metadata.get("channel") or "web").strip() or "web",
        actor_type="user",
        actor_id=actor_id,
        actor_display_name=actor_display_name,
        message=heartbeat_goal,
        context_hints={key: value for key, value in context_hints.items() if value not in (None, "", [], {})},
        execution_mode="durable",
        response_mode="artifact",
        machine_target=(
            str(merged_metadata.get("machine_target") or "").strip()
            or str(merged_metadata.get("execution_target_selected") or "").strip()
            or str(merged_metadata.get("execution_target") or "").strip()
            or None
        ),
        policy_context={key: value for key, value in policy_context.items() if value not in (None, "", [], {})},
        authority_tier=authority_mandate_service.normalize_tier(authority_tier),
    )


def heartbeat_scheduler(*, lock: Any, scheduler: Any) -> Any:
    with lock:
        return scheduler


def ensure_heartbeat_scheduler_started(
    *,
    lock: Any,
    scheduler: Any,
    scheduler_factory: Callable[[], Any],
) -> Any:
    with lock:
        if scheduler is None:
            scheduler = scheduler_factory()
            scheduler.start()
        return scheduler


def heartbeat_status_payload(*, scheduler: Optional[Any]) -> dict[str, Any]:
    if scheduler is None:
        return {
            "ok": False,
            "detail": "Heartbeat scheduler is not configured.",
        }
    return {
        "ok": True,
        **scheduler.status(),
    }


def trigger_heartbeat_payload(*, scheduler: Optional[Any]) -> dict[str, Any]:
    if scheduler is None:
        raise RuntimeError("Heartbeat scheduler is not configured.")
    return {
        "ok": True,
        **scheduler.trigger_now(),
    }


def _extract_turn_run_id(result_payload: dict[str, Any]) -> Optional[str]:
    """execute_system_agent_turn's return shape differs by execution path:
    a durable dispatch nests the actual run dict (with its own "run_id") one
    level down under "result" (see run_service.py's execute_durable_turn_request
    -> {"kind": "durable_run", "result": {...}}), so a flat .get("run_id")
    on the outer dict is always None for every heartbeat/wake-request turn
    (they're always durable — see build_heartbeat_turn_request's
    execution_mode="durable"). Check both shapes rather than assume one,
    since a future execution path could return either."""
    flat = result_payload.get("run_id")
    if flat:
        return str(flat).strip() or None
    nested = result_payload.get("result")
    if isinstance(nested, dict):
        nested_run_id = nested.get("run_id")
        if nested_run_id:
            return str(nested_run_id).strip() or None
    return None


def _append_heartbeat_entry(workspace_id: str, result: dict) -> None:
    """Append a timestamped entry to HEARTBEAT.md after a heartbeat run."""
    try:
        from server_modules.workspace_context import (
            read_workspace_context_file,
            write_workspace_context_file,
        )
        from datetime import datetime, timezone

        acted = bool(result.get("acted"))
        summary = str(result.get("summary") or "").strip()
        scheduler_mode = str(result.get("scheduler_mode") or "heartbeat").strip()
        run_id = str(result.get("run_id") or "").strip() or None

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        date_str = now.strftime("%Y-%m-%d")

        status_icon = "✅" if acted else "⏸️"

        entry = (
            f"### {status_icon} {timestamp}\n"
            f"- **Mode**: {scheduler_mode}\n"
            f"- **Summary**: {summary}\n"
        )
        if run_id:
            entry += f"- **Run**: {run_id}\n"
        entry += "\n"

        existing = read_workspace_context_file(
            "HEARTBEAT.md",
            workspace_id=workspace_id,
        )

        # Keep last 50 entries — split on ### markers
        parts = (existing or "").split("### ")
        # parts[0] is the header/content before first ###
        header = parts[0].strip() if parts else ""
        entries = []
        for p in parts[1:]:
            p = p.strip()
            if p:
                entries.append("### " + p)

        # Add new entry, trim to 50
        entries.append(entry.strip())
        if len(entries) > 50:
            entries = entries[-50:]

        new_content = header + "\n\n" + "\n".join(entries) if header else "\n".join(entries)
        new_content = new_content.strip() + "\n"

        write_workspace_context_file(
            "HEARTBEAT.md",
            new_content,
            workspace_id=workspace_id,
        )
    except Exception:
        pass


def build_heartbeat_run_callback(
    *,
    build_inbound_agent_turn_request: Callable[..., Any],
    trigger_pending_heartbeat_schedules: Callable[..., Any],
    execute_system_agent_turn: Callable[..., Any],
    run_execution_services: Callable[[], Any],
    resolve_workspace_tenant_id: Optional[Callable[[str], Any]] = None,
    claim_due_scheduler_wake_requests: Optional[Callable[..., Any]] = None,
    build_wakeup_execution_bundle: Optional[Callable[..., Any]] = None,
    finalize_scheduler_wake_requests: Optional[Callable[..., Any]] = None,
    enqueue_lane_work: Optional[Callable[..., Any]] = None,
) -> Callable[[list[str], dict[str, Any]], dict[str, Any]]:
    def _execute_heartbeat_run(tasks: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
        scoped_metadata = dict(metadata or {})
        workspace_id = str(scoped_metadata.get("workspace_id") or "").strip()
        if not workspace_id:
            return {
                "acted": False,
                "summary": "Heartbeat workspace scope is not configured.",
            }

        if not str(scoped_metadata.get("tenant_id") or "").strip() and callable(resolve_workspace_tenant_id):
            resolved_tenant_id = _resolve_sync(resolve_workspace_tenant_id(workspace_id))
            if str(resolved_tenant_id or "").strip():
                scoped_metadata["tenant_id"] = str(resolved_tenant_id).strip()

        pending_schedule_result = trigger_pending_heartbeat_schedules(workspace_id=workspace_id)
        pending_started = pending_schedule_result.get("started") if isinstance(pending_schedule_result, dict) else []
        wake_requests: list[dict[str, Any]] = []
        execution_bundle: dict[str, Any] = {}
        tenant_id = str(scoped_metadata.get("tenant_id") or "").strip()
        if tenant_id and callable(claim_due_scheduler_wake_requests):
            claimed = _resolve_sync(
                claim_due_scheduler_wake_requests(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
            )
            if isinstance(claimed, dict):
                wake_requests = [
                    dict(item)
                    for item in list(claimed.get("items") or [])
                    if isinstance(item, dict)
                ]
            if wake_requests and callable(build_wakeup_execution_bundle):
                execution_bundle = _resolve_sync(
                    build_wakeup_execution_bundle(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        heartbeat_tasks=list(tasks),
                        wake_requests=wake_requests,
                    )
                )
        if not tasks and not wake_requests:
            if pending_started:
                first = pending_started[0] if isinstance(pending_started, list) and pending_started else {}
                return {
                    "acted": True,
                    "run_id": str((first or {}).get("run_id") or "").strip() or None,
                    "summary": f"Heartbeat started {len(pending_started)} pending schedule(s).",
                    "scheduler_mode": "exact_schedule",
                }
            return {
                "acted": False,
                "summary": "No pending heartbeat tasks.",
                "scheduler_mode": "idle",
            }
        # Groups to execute this cycle -- one turn per authority tier, never
        # blended. build_wakeup_execution_bundle already groups wake_requests
        # this way when it ran (i.e. when there were wake_requests at all);
        # otherwise (heartbeat-checklist-only tick, or a bundle builder that
        # doesn't return the "groups" shape) synthesize the right single
        # group ourselves -- HEARTBEAT.md is owner-only config, so a
        # tasks-only tick is owner tier; an ungrouped wake_requests list
        # (e.g. a caller supplying the legacy flat shape) fails safe to
        # audience via normalize_tier(None), never owner.
        groups = (
            execution_bundle.get("groups")
            if isinstance(execution_bundle, dict) and isinstance(execution_bundle.get("groups"), list)
            else None
        )
        if not groups:
            if wake_requests:
                fallback_tier = authority_mandate_service.normalize_tier(
                    execution_bundle.get("authority_tier") if isinstance(execution_bundle, dict) else None
                )
                bundle_metadata = execution_bundle.get("metadata") if isinstance(execution_bundle, dict) and isinstance(execution_bundle.get("metadata"), dict) else {}
                groups = [{
                    "authority_tier": fallback_tier,
                    "heartbeat_tasks": list(tasks) if fallback_tier == authority_mandate_service.TIER_OWNER else [],
                    "wake_requests": wake_requests,
                    "context_event_ids": list(bundle_metadata.get("context_event_ids") or []),
                    "scheduler_mode": str(bundle_metadata.get("scheduler_mode") or "").strip(),
                }]
            else:
                groups = [{
                    "authority_tier": authority_mandate_service.TIER_OWNER,
                    "heartbeat_tasks": list(tasks),
                    "wake_requests": [],
                }]

        results: list[dict[str, Any]] = []
        first_error: Optional[BaseException] = None
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_tier = authority_mandate_service.normalize_tier(group.get("authority_tier"))
            group_tasks = list(group.get("heartbeat_tasks") or [])
            group_wake_requests = list(group.get("wake_requests") or [])
            if not group_tasks and not group_wake_requests:
                continue
            try:
                turn_request = build_heartbeat_turn_request(
                    build_inbound_agent_turn_request=build_inbound_agent_turn_request,
                    tasks=group_tasks,
                    metadata=scoped_metadata,
                    pending_started=pending_started,
                    authority_tier=group_tier,
                    wake_requests=group_wake_requests,
                    recent_changes=execution_bundle.get("recent_changes") if isinstance(execution_bundle, dict) else None,
                    scheduler_goals=execution_bundle.get("scheduler_goals") if isinstance(execution_bundle, dict) else None,
                    user_preferences=execution_bundle.get("user_preferences") if isinstance(execution_bundle, dict) else None,
                    policy_bounds=execution_bundle.get("policy") if isinstance(execution_bundle, dict) else None,
                )
            except ValueError as exc:
                # Can only happen if workspace/tenant scope resolution itself
                # fails -- already validated above, so this is defensive.
                # Same as the pre-tier-grouping contract: abort the whole
                # cycle rather than guess at a partial result.
                return {"acted": False, "summary": str(exc)}
            try:
                result = execute_system_agent_turn(
                    turn_request=turn_request,
                    run_execution_services=run_execution_services(),
                )
            except Exception as exc:
                _logger.exception(
                    "Heartbeat/wake-request turn execution failed (workspace_id=%s, tier=%s, wake_request_ids=%s)",
                    workspace_id,
                    group_tier,
                    [str(item.get("id") or "").strip() for item in group_wake_requests],
                )
                if group_wake_requests and tenant_id and callable(finalize_scheduler_wake_requests):
                    _resolve_sync(
                        finalize_scheduler_wake_requests(
                            tenant_id=tenant_id,
                            workspace_id=workspace_id,
                            wake_requests=group_wake_requests,
                            status="failed",
                            denial_reason="execution_failed",
                            mark_context_seen=False,
                        )
                    )
                if first_error is None:
                    first_error = exc
                continue
            result_payload = result if isinstance(result, dict) else {}
            resolved_run_id = _extract_turn_run_id(result_payload)
            if group_wake_requests and tenant_id and callable(finalize_scheduler_wake_requests):
                _resolve_sync(
                    finalize_scheduler_wake_requests(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        wake_requests=group_wake_requests,
                        status="executed",
                        mark_context_seen=True,
                        metadata_patch={"run_id": resolved_run_id},
                    )
                )
            results.append({
                "authority_tier": group_tier,
                **result_payload,
                "run_id": resolved_run_id,
                "wake_request_ids": [
                    str(item.get("id") or "").strip()
                    for item in group_wake_requests
                    if str(item.get("id") or "").strip()
                ],
                "context_event_ids": list(group.get("context_event_ids") or []),
                "scheduler_mode": (
                    str(group.get("scheduler_mode") or "").strip()
                    or ("mixed" if group_wake_requests and group_tasks else ("wakeup" if group_wake_requests else "heartbeat"))
                ),
            })

        if first_error is not None and not results:
            raise first_error
        if not results:
            return {"acted": False, "summary": "No pending heartbeat tasks.", "scheduler_mode": "idle"}

        primary = results[0]
        _heartbeat_result = {
            "acted": True,
            "run_id": primary.get("run_id"),
            "summary": (
                str(execution_bundle.get("summary") or "").strip()
                if isinstance(execution_bundle, dict) and str(execution_bundle.get("summary") or "").strip()
                else (
                    f"Heartbeat started a run for {len(tasks)} task(s)."
                    + (f" Also started {len(pending_started)} pending schedule(s)." if pending_started else "")
                )
            ),
            "scheduler_mode": primary.get("scheduler_mode"),
            "wake_request_ids": [wid for r in results for wid in (r.get("wake_request_ids") or [])],
            "context_event_ids": [cid for r in results for cid in (r.get("context_event_ids") or [])],
            "groups": results,
        }
        _append_heartbeat_entry(workspace_id, _heartbeat_result)
        if first_error is not None:
            # At least one tier group's turn failed -- surface it (matches
            # the pre-tier-grouping contract of propagating turn-execution
            # failures) even though other groups already succeeded and were
            # finalized above; those results aren't lost, just not returned
            # to this caller since the callback contract is raise-on-error.
            raise first_error
        return _heartbeat_result

    def _start_heartbeat_run(tasks: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
        scoped_metadata = dict(metadata or {})
        workspace_id = str(scoped_metadata.get("workspace_id") or "").strip()
        if not workspace_id:
            return {
                "acted": False,
                "summary": "Heartbeat workspace scope is not configured.",
            }
        if callable(enqueue_lane_work):
            queue_result = enqueue_lane_work(
                lane="cron",
                label="Heartbeat follow-up" if tasks else "Scheduler wakeup",
                metadata={
                    "workspace_id": workspace_id,
                    "tenant_id": str(scoped_metadata.get("tenant_id") or "").strip() or None,
                    "trigger": str(scoped_metadata.get("trigger") or "scheduled").strip() or "scheduled",
                    "heartbeat_task_count": len(tasks),
                    "source": "heartbeat",
                },
                work=lambda: _execute_heartbeat_run(tasks, scoped_metadata),
            )
            queue_item_id = (
                str(queue_result.get("item_id") or "").strip()
                if isinstance(queue_result, dict)
                else ""
            )
            return {
                "acted": True,
                "queued": True,
                "lane": "cron",
                "queue_item_id": queue_item_id or None,
                "summary": (
                    "Queued heartbeat work in the cron lane."
                    + (f" Queue item: {queue_item_id}." if queue_item_id else "")
                ),
                "scheduler_mode": "queued",
            }
        return _execute_heartbeat_run(tasks, scoped_metadata)

    return _start_heartbeat_run


def build_heartbeat_notify_callback(
    *,
    handle_telegram_send_message: Callable[..., Any],
    workspace_id: Optional[str],
    dispatch_cloud_channel_outbound_fn: Optional[Callable[..., Any]] = None,
    resolve_cloud_telegram_session_id_fn: Optional[Callable[[], Optional[str]]] = None,
) -> Callable[[str], None]:
    scoped_workspace_id = str(workspace_id or "").strip() or None

    def _heartbeat_notify(message: str) -> None:
        if not scoped_workspace_id:
            return

        # Stage 6: Try cloud-session-manager path first (proactive delivery via GramJS)
        if callable(resolve_cloud_telegram_session_id_fn) and callable(dispatch_cloud_channel_outbound_fn):
            try:
                session_id = resolve_cloud_telegram_session_id_fn()
                if session_id:
                    asyncio.run(dispatch_cloud_channel_outbound_fn(
                        session_id=session_id,
                        text=message,
                        remote_jid="me",
                    ))
                    return
            except Exception:
                pass

        # Fall back to old Telegram bot path
        try:
            asyncio.run(handle_telegram_send_message(message, workspace_id=scoped_workspace_id))
        except Exception:
            return

    return _heartbeat_notify

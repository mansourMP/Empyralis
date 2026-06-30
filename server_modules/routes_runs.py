from fastapi import APIRouter, Depends, HTTPException, Request
from server_modules.auth import enforce_workspace_access
from server_modules.runtime_events_api import register_inbox_routes
from server_modules.runtime_runs_api import register_run_routes
from server_modules.runtime_runtime_api import register_runtime_routes
from server_modules.runtime_common import require_admin_api_key, require_api_key
from server_modules.runtime_models import (
    CronSchedulePatchRequest,
    CronScheduleUpsertRequest,
    LocalQueueCleanupRequest,
    WeeklySchedulePatchRequest,
    WeeklyScheduleUpsertRequest,
)
from server_modules import runs_core as core
from server_modules import runs_history as history

router = APIRouter()

register_run_routes(router)
register_inbox_routes(router)
register_runtime_routes(router)


def _schedule_workspace_or_404(schedule_id: str) -> str:
    with core.SCHEDULES_LOCK:
        schedule = core.WEEKLY_SCHEDULES.get(schedule_id)
        if not isinstance(schedule, dict):
            raise HTTPException(status_code=404, detail="Schedule not found")
        workspace_id = str(schedule.get("workspace_id") or "").strip()
        if not workspace_id:
            raise HTTPException(status_code=409, detail="Schedule scope is invalid")
        return workspace_id


async def list_weekly_schedules(request: Request, current_user=Depends(require_api_key)):
    workspace_id = enforce_workspace_access(current_user, request.query_params.get("workspace_id"))
    return await core.list_weekly_schedules(workspace_id=workspace_id)


async def list_schedules(request: Request, current_user=Depends(require_api_key)):
    workspace_id = enforce_workspace_access(current_user, request.query_params.get("workspace_id"))
    return await core.list_schedules(workspace_id=workspace_id)


async def create_weekly_schedule(body: WeeklyScheduleUpsertRequest, current_user=Depends(require_api_key)):
    body.workspace_id = enforce_workspace_access(current_user, body.workspace_id)
    return await core.create_weekly_schedule(body)


async def create_schedule(body: CronScheduleUpsertRequest, current_user=Depends(require_api_key)):
    body.workspace_id = enforce_workspace_access(current_user, body.workspace_id)
    return await core.create_schedule(body)


async def update_schedule(
    schedule_id: str,
    body: CronSchedulePatchRequest,
    current_user=Depends(require_api_key),
):
    enforce_workspace_access(current_user, _schedule_workspace_or_404(schedule_id))
    return await core.update_schedule(schedule_id, body)


async def update_weekly_schedule(
    schedule_id: str,
    body: WeeklySchedulePatchRequest,
    current_user=Depends(require_api_key),
):
    enforce_workspace_access(current_user, _schedule_workspace_or_404(schedule_id))
    return await core.update_weekly_schedule(schedule_id, body)


async def delete_weekly_schedule(schedule_id: str, current_user=Depends(require_api_key)):
    enforce_workspace_access(current_user, _schedule_workspace_or_404(schedule_id))
    return await core.delete_weekly_schedule(schedule_id)


async def delete_schedule(schedule_id: str, current_user=Depends(require_api_key)):
    enforce_workspace_access(current_user, _schedule_workspace_or_404(schedule_id))
    return await core.delete_schedule(schedule_id)


async def trigger_schedule_now(schedule_id: str, current_user=Depends(require_api_key)):
    enforce_workspace_access(current_user, _schedule_workspace_or_404(schedule_id))
    return await core.trigger_schedule_now(schedule_id)


async def trigger_weekly_schedule_now(schedule_id: str, current_user=Depends(require_api_key)):
    enforce_workspace_access(current_user, _schedule_workspace_or_404(schedule_id))
    return await core.trigger_weekly_schedule_now(schedule_id)


async def get_schedule_logs(schedule_id: str, current_user=Depends(require_api_key)):
    enforce_workspace_access(current_user, _schedule_workspace_or_404(schedule_id))
    return await core.get_schedule_logs(schedule_id)


async def get_local_run_queue(request: Request, current_user=Depends(require_api_key)):
    workspace_id = enforce_workspace_access(current_user, request.query_params.get("workspace_id"))
    limit_raw = request.query_params.get("limit")
    try:
        limit = int(limit_raw) if limit_raw is not None else 50
    except Exception:
        raise HTTPException(status_code=400, detail="limit must be an integer.")
    return await core.get_local_run_queue(workspace_id=workspace_id, limit=limit)


async def cleanup_local_run_queue(body: LocalQueueCleanupRequest, current_user=Depends(require_api_key)):
    body.validate_fields()
    workspace_id = enforce_workspace_access(current_user, body.workspace_id, minimum_role="member")
    return await core.cleanup_local_run_queue(
        workspace_id=workspace_id,
        older_than_seconds=body.older_than_seconds,
        limit=body.limit,
        dry_run=body.dry_run,
        reason=body.reason,
    )

# Phase 3: Approval API routes removed — agent acts on its own reasoning
router.add_api_route("/schedules", list_schedules, methods=['GET'])
router.add_api_route("/schedules", create_schedule, methods=['POST'])
router.add_api_route("/schedules/{schedule_id}", update_schedule, methods=['PATCH'])
router.add_api_route("/schedules/{schedule_id}", delete_schedule, methods=['DELETE'])
router.add_api_route("/schedules/{schedule_id}/logs", get_schedule_logs, methods=['GET'])
router.add_api_route("/schedules/{schedule_id}/run-now", trigger_schedule_now, methods=['POST'])
router.add_api_route("/schedules/weekly", list_weekly_schedules, methods=['GET'])
router.add_api_route("/schedules/weekly", create_weekly_schedule, methods=['POST'])
router.add_api_route("/schedules/weekly/{schedule_id}", update_weekly_schedule, methods=['PATCH'])
router.add_api_route("/schedules/weekly/{schedule_id}", delete_weekly_schedule, methods=['DELETE'])
router.add_api_route("/schedules/weekly/{schedule_id}/run-now", trigger_weekly_schedule_now, methods=['POST'])
router.add_api_route("/metrics", core.get_runtime_metrics, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/kpis", core.get_runtime_kpis, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/runs/queue/local", get_local_run_queue, methods=['GET'])
router.add_api_route("/runs/queue/local/cleanup", cleanup_local_run_queue, methods=['POST'])

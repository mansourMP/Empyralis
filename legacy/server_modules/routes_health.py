from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from server_modules.auth import enforce_workspace_access, get_current_user
from server_modules.runtime_common import require_api_key
from server_modules.runtime_models import (
    MemorySearchRequest,
    MemoryUpsertRequest,
    RuntimeSkillsStateUpsertRequest,
    SkillInstallRequest,
    SkillPublishRequest,
    SkillUpdateRequest,
)
from server_modules import health_core as core
from server_modules import health_diagnostics as diagnostics
from server_modules import skills_registry
from server_modules import db as runtime_db

router = APIRouter()

async def skills_state(request: Request, body: Optional[RuntimeSkillsStateUpsertRequest] = None):
    if request.method.upper() == "GET":
        return await diagnostics.get_runtime_skills_state()
    if body is None:
        raise HTTPException(status_code=422, detail="Runtime skills state payload is required.")
    return await diagnostics.put_runtime_skills_state(body)


async def skills_list():
    return skills_registry.list_marketplace_skills()


async def skills_install(body: SkillInstallRequest):
    body.validate_fields()
    try:
        return skills_registry.install_marketplace_skill(
            source_url=str(body.source_url or "").strip() or None,
            zip_path=str(body.zip_path or "").strip() or None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def skills_registry_index(request: Request):
    source = str(request.query_params.get("url") or "").strip() or None
    try:
        return skills_registry.fetch_marketplace_registry(source)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def skills_publish(body: SkillPublishRequest):
    body.validate_fields()
    try:
        return skills_registry.publish_marketplace_skill(
            str(body.name or "").strip(),
            str(body.output_path or "").strip() or None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def skills_update(skill_name: str, body: SkillUpdateRequest):
    try:
        return skills_registry.set_marketplace_skill_enabled(skill_name, body.enabled)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def skills_delete(skill_name: str):
    try:
        return skills_registry.uninstall_marketplace_skill(skill_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def memory_search(body: MemorySearchRequest, current_user=Depends(require_api_key)):
    body.workspace_id = enforce_workspace_access(current_user, body.workspace_id)
    return await core.memory_search(body)


async def memory_upsert(body: MemoryUpsertRequest, current_user=Depends(require_api_key)):
    body.workspace_id = enforce_workspace_access(current_user, body.workspace_id)
    return await core.memory_upsert(body)


async def db_health():
    return {
        "postgres": await runtime_db.postgres_health_status(),
        "sqlite": runtime_db.sqlite_health_status(),
        "durable_required": runtime_db.durable_runtime_required(),
        "durability_mode": runtime_db.durability_mode(),
        "sqlite_fallback_allowed": runtime_db.sqlite_fallback_allowed(),
    }


async def public_health():
    payload = await core.health()
    return {"ok": bool((payload or {}).get("ok"))}


async def internal_health():
    return await core.health()


router.add_api_route("/contract", core.runtime_contract, methods=['GET'], dependencies=[Depends(get_current_user)])
router.add_api_route("/memory/health", core.memory_health, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/memory/search", memory_search, methods=['POST'])
router.add_api_route("/memory/upsert", memory_upsert, methods=['POST'])
router.add_api_route("/health", public_health, methods=['GET'])
router.add_api_route("/health/db", db_health, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/health/internal", internal_health, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/health/internal/db", db_health, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/mobile/handoff", core.mobile_handoff, methods=['GET'], dependencies=[Depends(get_current_user)])
router.add_api_route("/validation/latest", core.validation_latest, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/validation/history", core.validation_history, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/doctor", diagnostics.doctor, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/doctor/history", diagnostics.doctor_history, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/solutions/state", diagnostics.get_runtime_solutions_state, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/skills", skills_list, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/skills/install", skills_install, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/skills/registry", skills_registry_index, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/skills/publish", skills_publish, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/skills/state", skills_state, methods=['GET', 'PUT'], dependencies=[Depends(require_api_key)])
router.add_api_route("/skills/{skill_name}", skills_update, methods=['PUT'], dependencies=[Depends(require_api_key)])
router.add_api_route("/skills/{skill_name}", skills_delete, methods=['DELETE'], dependencies=[Depends(require_api_key)])
router.add_api_route("/api/solutions/{solution_id}/{subpath:path}", diagnostics.dispatch_installed_solution_api, methods=["GET", "POST", "PUT"], dependencies=[Depends(require_api_key)])
router.add_api_route("/probe", core.probe, methods=['GET'], dependencies=[Depends(get_current_user)])
router.add_api_route("/setup/sessions", core.create_setup_session, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/setup/sessions/{session_id}", core.get_setup_session, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/setup/sessions/{session_id}/actions", core.setup_session_action, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/setup/sessions/{session_id}/cancel", core.cancel_setup_session, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/setup/sessions/{session_id}/resume", core.resume_setup_session, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/onboarding/sessions", core.create_onboarding_session, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/onboarding/sessions/{session_id}", core.get_onboarding_session, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/onboarding/sessions/{session_id}/actions", core.onboarding_session_action, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/onboarding/sessions/{session_id}/cancel", core.cancel_onboarding_session, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/onboarding/sessions/{session_id}/resume", core.resume_onboarding_session, methods=['POST'], dependencies=[Depends(require_api_key)])

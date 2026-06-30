from __future__ import annotations

from typing import Any, Dict, List, Optional
import threading

from server_modules.schemas import (
    AppCaptainBridgeRequest,
    AppRuntimeBridgeRequest,
    AppSpecialistBridgeRequest,
    SageAppBridgeRequest,
    WorkflowCreate,
    WorkflowDelete,
    WorkflowUpdate,
)


APP_REGISTRY_LOCK = threading.Lock()


def _default_app_registry() -> Dict[str, Any]:
    return {"apps": [], "updated_at": _utc_now_iso()}


def _load_app_registry() -> Dict[str, Any]:
    # server._safe_read_json requires an explicit fallback value.
    data = _safe_read_json(ORION_APP_REGISTRY_FILE, {})
    if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
        data = _default_app_registry()
        _safe_write_json(ORION_APP_REGISTRY_FILE, data)
        return data

    # Merge in any new default apps that aren't present yet.
    defaults = _default_app_registry()
    existing = {str(item.get("id")): item for item in data.get("apps", []) if isinstance(item, dict)}
    updated = False
    for app in defaults.get("apps", []):
        app_id = str(app.get("id"))
        if app_id and app_id not in existing:
            data["apps"].append(app)
            updated = True

    normalized_apps = []
    for app in data.get("apps", []):
        if not isinstance(app, dict):
            continue
        before = dict(app)
        normalized = _normalize_registry_item(app)
        normalized_apps.append(normalized)
        if normalized != before:
            updated = True
    data["apps"] = normalized_apps

    if updated:
        _save_app_registry(data)
    return data


def _normalize_registry_item(app: Dict[str, Any]) -> Dict[str, Any]:
    if not app.get("source"):
        app["source"] = "platform"
    return app


def _apply_install_metadata(app_item: Dict[str, Any], body: Dict[str, Any]) -> None:
    package_id = str(body.get("package_id") or app_item.get("package_id") or "").strip()
    release_channel = str(body.get("release_channel") or app_item.get("release_channel") or "stable").strip()
    install_source = str(body.get("install_source") or app_item.get("install_source") or "platform_registry").strip()

    if package_id:
        app_item["package_id"] = package_id
    if release_channel:
        app_item["release_channel"] = release_channel
    if install_source:
        app_item["install_source"] = install_source


def _save_app_registry(data: Dict[str, Any]) -> None:
    data["updated_at"] = _utc_now_iso()
    _safe_write_json(ORION_APP_REGISTRY_FILE, data)


def _find_app(data: Dict[str, Any], app_id: str) -> Optional[Dict[str, Any]]:
    for app in data.get("apps", []):
        if str(app.get("id")) == app_id:
            return app
    return None


def resolve_app_permissions(app_id: str) -> List[str]:
    app_id = str(app_id or "").strip()
    if not app_id:
        return []
    with APP_REGISTRY_LOCK:
        data = _load_app_registry()
        app_item = _find_app(data, app_id)
    if not app_item:
        return []
    if str(app_item.get("status") or "").strip().lower() != "installed":
        return []
    permissions = app_item.get("permissions")
    if not isinstance(permissions, list):
        return []
    return [str(item).strip().lower() for item in permissions if str(item).strip()]


def _model_to_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    return dict(value) if isinstance(value, dict) else {}


def register_app_registry_routes(app) -> None:
    import server as _server

    module_globals = globals()
    for key, value in _server.__dict__.items():
        if key not in module_globals:
            module_globals[key] = value

    @app.get("/apps/registry", dependencies=[Depends(require_api_key)])
    async def get_app_registry():
        with APP_REGISTRY_LOCK:
            data = _load_app_registry()
        return {"items": data.get("apps", []), "updated_at": data.get("updated_at")}

    @app.get("/apps/installed", dependencies=[Depends(require_api_key)])
    async def get_installed_apps():
        with APP_REGISTRY_LOCK:
            data = _load_app_registry()
        items = [app for app in data.get("apps", []) if app.get("status") == "installed"]
        return {"items": items}

    @app.get("/apps/store", dependencies=[Depends(require_api_key)])
    async def get_store_apps():
        with APP_REGISTRY_LOCK:
            data = _load_app_registry()
        items = [app for app in data.get("apps", []) if app.get("status") != "installed"]
        return {"items": items}

    @app.get("/apps/updates", dependencies=[Depends(require_api_key)])
    async def get_update_apps():
        with APP_REGISTRY_LOCK:
            data = _load_app_registry()
        items = [
            app
            for app in data.get("apps", [])
            if app.get("status") == "installed"
            and app.get("latest_version")
            and app.get("latest_version") != app.get("version")
        ]
        return {"items": items}

    @app.get("/apps/manifest/{app_id}", dependencies=[Depends(require_api_key)])
    async def get_app_manifest(app_id: str):
        with APP_REGISTRY_LOCK:
            data = _load_app_registry()
            app_item = _find_app(data, app_id)
        if not app_item:
            raise HTTPException(status_code=404, detail="App not found")
        return {"item": app_item}

    @app.post("/apps/install", dependencies=[Depends(require_api_key)])
    async def install_app(body: WorkflowCreate):
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        app_id = str(payload.get("app_id") or "").strip()
        if not app_id:
            raise HTTPException(status_code=400, detail="app_id required")
        with APP_REGISTRY_LOCK:
            data = _load_app_registry()
            app_item = _find_app(data, app_id)
            if not app_item:
                raise HTTPException(status_code=404, detail="App not found")
            _apply_install_metadata(app_item, payload)
            app_item["status"] = "installed"
            app_item["installed_at"] = _utc_now_iso()
            _save_app_registry(data)
        return {"status": "ok", "app": app_item}

    @app.post("/apps/uninstall", dependencies=[Depends(require_api_key)])
    async def uninstall_app(body: WorkflowDelete):
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        app_id = str(payload.get("app_id") or "").strip()
        if not app_id:
            raise HTTPException(status_code=400, detail="app_id required")
        with APP_REGISTRY_LOCK:
            data = _load_app_registry()
            app_item = _find_app(data, app_id)
            if not app_item:
                raise HTTPException(status_code=404, detail="App not found")
            app_item["status"] = "available"
            _save_app_registry(data)
        return {"status": "ok", "app": app_item}

    @app.post("/apps/update", dependencies=[Depends(require_api_key)])
    async def update_app(body: WorkflowUpdate):
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        app_id = str(payload.get("app_id") or "").strip()
        if not app_id:
            raise HTTPException(status_code=400, detail="app_id required")
        with APP_REGISTRY_LOCK:
            data = _load_app_registry()
            app_item = _find_app(data, app_id)
            if not app_item:
                raise HTTPException(status_code=404, detail="App not found")
            latest = app_item.get("latest_version")
            if not latest:
                raise HTTPException(status_code=409, detail="No update available")
            _apply_install_metadata(app_item, payload)
            app_item["version"] = latest
            app_item["updated_at"] = _utc_now_iso()
            _save_app_registry(data)
        return {"status": "ok", "app": app_item}

    @app.post("/apps/bridge/captain", dependencies=[Depends(require_api_key)])
    async def app_bridge_captain(body: AppCaptainBridgeRequest, current_user=Depends(require_api_key)):
        from server_modules import app_bridge_service
        from server_modules import agent_registry_api
        from server_modules.auth import enforce_workspace_access, workspace_tenant_id

        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        workspace_id = enforce_workspace_access(current_user, payload.get("workspace_id"), minimum_role="viewer")
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        request_text = str(payload.get("request_text") or "").strip()
        bridge = app_bridge_service.normalize_bridge_contract(
            app_id=str(payload.get("app_id") or "").strip(),
            bridge_kind="app_to_sage",
            bridge_type=str(payload.get("bridge_type") or "").strip(),
            context_envelope=payload.get("context_envelope") if isinstance(payload.get("context_envelope"), dict) else None,
            metadata={
                **(dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}),
                "request_text": request_text or None,
            },
            installed_only=True,
        )
        turn_payload: Dict[str, Any] = {}
        if request_text:
            master_install = await agent_registry_api.agent_registry_repository.get_workspace_master_agent_install(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                created_by_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
            )
            if not isinstance(master_install, dict):
                raise HTTPException(status_code=500, detail="Workspace master agent is unavailable.")
            turn_result = await agent_registry_api.execute_install_agent_turn(
                install_id=str(master_install.get("id") or "").strip(),
                current_user=current_user,
                message=request_text,
                channel="web",
                execution_mode="durable",
                response_mode="artifact",
                metadata_overrides={
                    "source": "apps.bridge.captain",
                    "app_id": bridge.get("app_id"),
                    "app_bridge": bridge,
                    "app_context_envelope": bridge.get("context_envelope"),
                },
            )
            turn_payload = _model_to_dict(turn_result)
        audit = await app_bridge_service.record_app_bridge_audit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="application",
            actor_id=str(bridge.get("app_id") or "").strip(),
            app_id=str(bridge.get("app_id") or "").strip(),
            bridge_kind=str(bridge.get("bridge_kind") or "").strip(),
            bridge_type=str(bridge.get("bridge_type") or "").strip(),
            target=bridge.get("target") if isinstance(bridge.get("target"), dict) else None,
            metadata={"source": "apps.bridge.captain"},
        )
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "bridge": bridge,
            "run_metadata": {
                "app_id": bridge.get("app_id"),
                "app_bridge": bridge,
                "app_context_envelope": bridge.get("context_envelope"),
            },
            "audit": {"activity_event_id": str((audit or {}).get("id") or "").strip() or None},
            "turn_result": turn_payload,
            "run_id": str(turn_payload.get("run_id") or "").strip() or None,
            "thread_id": str(turn_payload.get("thread_id") or "").strip() or None,
            "session_id": str(turn_payload.get("session_id") or "").strip() or None,
        }

    @app.post("/apps/bridge/specialist", dependencies=[Depends(require_api_key)])
    async def app_bridge_specialist(body: AppSpecialistBridgeRequest, current_user=Depends(require_api_key)):
        from server_modules import app_bridge_service
        from server_modules import agent_registry_api
        from server_modules.auth import enforce_workspace_access, workspace_tenant_id

        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        workspace_id = enforce_workspace_access(current_user, payload.get("workspace_id"), minimum_role="viewer")
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        request_text = str(payload.get("request_text") or "").strip()
        bridge = app_bridge_service.normalize_bridge_contract(
            app_id=str(payload.get("app_id") or "").strip(),
            bridge_kind="app_to_specialist",
            bridge_type=str(payload.get("bridge_type") or "").strip(),
            target={
                "target_install_id": str(payload.get("target_install_id") or "").strip() or None,
                "target_capability": str(payload.get("target_capability") or "").strip() or None,
            },
            context_envelope=payload.get("context_envelope") if isinstance(payload.get("context_envelope"), dict) else None,
            metadata={
                **(dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}),
                "request_text": request_text or None,
            },
            installed_only=True,
        )
        resolved_target = dict(bridge.get("target") or {}) if isinstance(bridge.get("target"), dict) else {}
        if request_text:
            target_install = await agent_registry_api.resolve_specialist_install_for_app_bridge(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                target_install_id=str(resolved_target.get("target_install_id") or "").strip() or None,
                target_capability=str(resolved_target.get("target_capability") or "").strip() or None,
            )
            resolved_target["target_install_id"] = str(target_install.get("id") or "").strip() or resolved_target.get("target_install_id")
        bridge = {
            **bridge,
            "target": resolved_target,
        }
        turn_payload: Dict[str, Any] = {}
        if request_text:
            turn_result = await agent_registry_api.execute_install_agent_turn(
                install_id=str(resolved_target.get("target_install_id") or "").strip(),
                current_user=current_user,
                message=request_text,
                channel="web",
                execution_mode="durable",
                response_mode="artifact",
                metadata_overrides={
                    "source": "apps.bridge.specialist",
                    "app_id": bridge.get("app_id"),
                    "app_bridge": bridge,
                    "app_context_envelope": bridge.get("context_envelope"),
                },
            )
            turn_payload = _model_to_dict(turn_result)
        audit = await app_bridge_service.record_app_bridge_audit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="application",
            actor_id=str(bridge.get("app_id") or "").strip(),
            app_id=str(bridge.get("app_id") or "").strip(),
            bridge_kind=str(bridge.get("bridge_kind") or "").strip(),
            bridge_type=str(bridge.get("bridge_type") or "").strip(),
            target=bridge.get("target") if isinstance(bridge.get("target"), dict) else None,
            metadata={"source": "apps.bridge.specialist"},
        )
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "bridge": bridge,
            "run_metadata": {
                "app_id": bridge.get("app_id"),
                "app_bridge": bridge,
                "app_context_envelope": bridge.get("context_envelope"),
            },
            "audit": {"activity_event_id": str((audit or {}).get("id") or "").strip() or None},
            "turn_result": turn_payload,
            "run_id": str(turn_payload.get("run_id") or "").strip() or None,
            "thread_id": str(turn_payload.get("thread_id") or "").strip() or None,
            "session_id": str(turn_payload.get("session_id") or "").strip() or None,
        }

    @app.post("/apps/bridge/runtime-action", dependencies=[Depends(require_api_key)])
    async def app_bridge_runtime_action(body: AppRuntimeBridgeRequest, current_user=Depends(require_api_key)):
        from server_modules import app_bridge_service
        from server_modules.auth import enforce_workspace_access, workspace_tenant_id

        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        workspace_id = enforce_workspace_access(current_user, payload.get("workspace_id"), minimum_role="viewer")
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        bridge = app_bridge_service.normalize_bridge_contract(
            app_id=str(payload.get("app_id") or "").strip(),
            bridge_kind="app_to_connector_runtime",
            bridge_type=str(payload.get("bridge_type") or "").strip(),
            target={
                "connector_id": str(payload.get("connector_id") or "").strip() or None,
                "workflow_id": str(payload.get("workflow_id") or "").strip() or None,
                "route_key": str(payload.get("route_key") or "").strip() or None,
            },
            context_envelope=payload.get("context_envelope") if isinstance(payload.get("context_envelope"), dict) else None,
            metadata=dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else None,
            installed_only=True,
        )
        audit = await app_bridge_service.record_app_bridge_audit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="application",
            actor_id=str(bridge.get("app_id") or "").strip(),
            app_id=str(bridge.get("app_id") or "").strip(),
            bridge_kind=str(bridge.get("bridge_kind") or "").strip(),
            bridge_type=str(bridge.get("bridge_type") or "").strip(),
            target=bridge.get("target") if isinstance(bridge.get("target"), dict) else None,
            metadata={"source": "apps.bridge.runtime-action"},
        )
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "bridge": bridge,
            "run_metadata": {
                "app_id": bridge.get("app_id"),
                "app_bridge": bridge,
                "app_context_envelope": bridge.get("context_envelope"),
            },
            "audit": {"activity_event_id": str((audit or {}).get("id") or "").strip() or None},
        }

    @app.post("/apps/bridge/handoff", dependencies=[Depends(require_api_key)])
    async def sage_bridge_app(body: SageAppBridgeRequest, current_user=Depends(require_api_key)):
        from server_modules import app_bridge_service
        from server_modules.auth import enforce_workspace_access, workspace_tenant_id

        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        workspace_id = enforce_workspace_access(current_user, payload.get("workspace_id"), minimum_role="viewer")
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        bridge = app_bridge_service.normalize_bridge_contract(
            app_id=str(payload.get("app_id") or "").strip(),
            bridge_kind="sage_to_app",
            bridge_type=str(payload.get("bridge_type") or "").strip(),
            target={"target_app_id": str(payload.get("target_app_id") or payload.get("app_id") or "").strip() or None},
            metadata={
                **(dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}),
                "handoff_payload": dict(payload.get("handoff_payload") or {}) if isinstance(payload.get("handoff_payload"), dict) else {},
            },
            installed_only=True,
        )
        audit = await app_bridge_service.record_app_bridge_audit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="sage",
            actor_id=str(current_user.get("user_id") or "sage").strip() or "sage",
            app_id=str(bridge.get("app_id") or "").strip(),
            bridge_kind=str(bridge.get("bridge_kind") or "").strip(),
            bridge_type=str(bridge.get("bridge_type") or "").strip(),
            target=bridge.get("target") if isinstance(bridge.get("target"), dict) else None,
            metadata={"source": "apps.bridge.handoff"},
        )
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "bridge": bridge,
            "audit": {"activity_event_id": str((audit or {}).get("id") or "").strip() or None},
        }

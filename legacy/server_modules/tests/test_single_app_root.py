import asyncio
import importlib.util
import sys
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[2]
SERVER_PATH = ROOT_DIR / "server.py"
SHARED_PATH = ROOT_DIR / "server_modules" / "shared.py"


class _Route:
    def __init__(self, path: str) -> None:
        self.path = path


class _APIRouter:
    def __init__(self) -> None:
        self.routes: list[_Route] = []

    def get(self, path: str):
        def _decorator(func):
            self.routes.append(_Route(path))
            return func

        return _decorator


class _FastAPI:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.routes: list[_Route] = []
        self.middleware_handlers: list[tuple[str, object]] = []
        self.middleware_stack: list[tuple[object, dict[str, object]]] = []
        self.exception_handlers: dict[object, object] = {}

    def add_middleware(self, middleware_class, **kwargs) -> None:
        self.middleware_stack.append((middleware_class, kwargs))

    def middleware(self, kind: str):
        def _decorator(func):
            self.middleware_handlers.append((kind, func))
            return func

        return _decorator

    def exception_handler(self, exc_class):
        def _decorator(func):
            self.exception_handlers[exc_class] = func
            return func

        return _decorator

    def include_router(self, router, prefix: str = "") -> None:
        for route in getattr(router, "routes", []):
            self.routes.append(_Route(f"{prefix}{route.path}"))


class _DummyAcpManager:
    def __init__(self) -> None:
        self.runs = {}
        self.run_history = []
        self.setup_sessions = {}
        self.provider_profiles = {}
        self.idempotency_records = {}
        self.local_pending_run_ids = set()
        self.local_claimed_runs = {}
        self.local_worker_registry = {}

    def reconfigure_paths(self, **_kwargs) -> None:
        return None


def _load_module_from_path(module_name: str, path: Path, injected_modules: Dict[str, types.ModuleType]):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    with patch.dict(sys.modules, injected_modules, clear=False):
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module


def _server_modules_package(
    *,
    runtime_config_module: types.ModuleType,
    acp_manager_module: types.ModuleType,
    extra_modules: Optional[Dict[str, types.ModuleType]] = None,
) -> Dict[str, types.ModuleType]:
    package = types.ModuleType("server_modules")
    package.__path__ = [str(ROOT_DIR / "server_modules")]
    modules: Dict[str, types.ModuleType] = {
        "server_modules": package,
        "server_modules.runtime_config": runtime_config_module,
        "server_modules.acp_manager": acp_manager_module,
    }
    setattr(package, "runtime_config", runtime_config_module)
    setattr(package, "acp_manager", acp_manager_module)
    for name, module in (extra_modules or {}).items():
        modules[f"server_modules.{name}"] = module
        setattr(package, name, module)
    return modules


def _build_router(path: str) -> _APIRouter:
    router = _APIRouter()

    @router.get(path)
    async def _route():
        return {"path": path}

    return router


class SingleAppRootTests(unittest.TestCase):
    def test_shared_module_does_not_create_fastapi_app_and_keeps_lifespan_helper(self) -> None:
        lifecycle_events: list[str] = []

        @asynccontextmanager
        async def _fake_empyralist_mcp_lifespan():
            lifecycle_events.append("enter")
            try:
                yield
            finally:
                lifecycle_events.append("exit")

        runtime_config_module = types.ModuleType("server_modules.runtime_config")
        runtime_config_module.empyralist_mcp_lifespan = _fake_empyralist_mcp_lifespan
        runtime_config_module.ORION_TELEGRAM_AUTOPILOT_ENABLED = False
        runtime_config_module.ORION_WHATSAPP_AUTOPILOT_ENABLED = False

        acp_manager_module = types.ModuleType("server_modules.acp_manager")
        acp_manager_module.DEFAULT_ACP_MANAGER = _DummyAcpManager()

        shared_module = _load_module_from_path(
            "shared_under_test",
            SHARED_PATH,
            _server_modules_package(
                runtime_config_module=runtime_config_module,
                acp_manager_module=acp_manager_module,
            ),
        )

        self.assertIsNone(shared_module.app)

        async def _run_lifespan() -> None:
            async with shared_module.app_lifespan(object()):
                lifecycle_events.append("inside")

        asyncio.run(_run_lifespan())
        self.assertEqual(lifecycle_events, ["enter", "inside", "exit"])

    def test_server_module_constructs_single_fastapi_root_and_assigns_shared_alias(self) -> None:
        lifecycle_events: list[object] = []

        runtime_config_module = types.ModuleType("server_modules.runtime_config")
        runtime_config_module.ORION_MEMORY_ENABLED = False
        runtime_config_module.ORION_MEMORY_LANCEDB_URI = ""
        runtime_config_module.ORION_MEMORY_DB_PATH = Path("/tmp/memory.db")
        runtime_config_module.ORION_MEMORY_READ_K = 5
        runtime_config_module.ORION_MEMORY_RETENTION_DAYS_DEFAULT = 30
        runtime_config_module.ORION_MEMORY_MAX_TEXT_CHARS = 1000
        runtime_config_module.ORION_CHANNEL_EVENTS_LIMIT = 200
        runtime_config_module.ORION_CHANNEL_SESSIONS_LIMIT = 80
        runtime_config_module.ORION_CHANNEL_EVENTS_FILE = Path("/tmp/channel-events.json")
        runtime_config_module.ORION_RUNTIME_STATE_DB = Path("/tmp/runtime-state.db")
        runtime_config_module.FRONTEND_ORIGINS = "http://127.0.0.1:3000"
        runtime_config_module.RuntimeMemoryManager = object
        runtime_config_module.AGENT_MACHINE_MODE = "personal"
        runtime_config_module.AGENT_MACHINE_OWNER = ""
        runtime_config_module.ORION_TELEGRAM_AUTOPILOT_ENABLED = False
        runtime_config_module.ORION_WHATSAPP_AUTOPILOT_ENABLED = False
        runtime_config_module.empyralist_mcp_lifespan = lambda: None

        acp_manager_module = types.ModuleType("server_modules.acp_manager")
        acp_manager_module.DEFAULT_ACP_MANAGER = _DummyAcpManager()

        shared_module = _load_module_from_path(
            "server_modules.shared",
            SHARED_PATH,
            _server_modules_package(
                runtime_config_module=runtime_config_module,
                acp_manager_module=acp_manager_module,
            ),
        )

        logging_config_module = types.ModuleType("server_modules.logging_config")
        logging_config_module.configure_logging = lambda: None

        runtime_common_module = types.ModuleType("server_modules.runtime_common")
        runtime_common_module._utc_now = lambda: None
        runtime_common_module._utc_now_iso = lambda: "2026-01-01T00:00:00Z"
        runtime_common_module._parse_utc_ts = lambda value: value
        runtime_common_module._normalize_workspace_id = lambda value: str(value or "default").strip() or "default"
        runtime_common_module._safe_read_json = lambda *_args, **_kwargs: {}

        runs_core_module = types.ModuleType("server_modules.runs_core")
        runs_core_module.configure_runtime_memory = lambda **_kwargs: None
        runs_core_module.initialize_runtime_services = lambda: lifecycle_events.append("initialize_runtime_services")
        runs_core_module.emit_log = lambda *_args, **_kwargs: None

        runs_output_module = types.ModuleType("server_modules.runs_output")
        runs_output_module._json_safe = lambda value: value
        runs_output_module._compact_event_text = lambda value: str(value or "").strip()
        runs_output_module._refresh_archived_run_snapshot = lambda *_args, **_kwargs: None

        runs_execution_module = types.ModuleType("server_modules.runs_execution")
        runs_execution_module.configure_runtime_events = lambda **_kwargs: None
        runs_execution_module.list_channel_events = lambda *_args, **_kwargs: []
        runs_execution_module.replace_channel_events = lambda *_args, **_kwargs: None
        runs_execution_module.append_channel_event = lambda *_args, **_kwargs: None

        connectors_core_module = types.ModuleType("server_modules.connectors_core")
        connectors_core_module.mount_empyralist_mcp = lambda app: lifecycle_events.append(("mount_empyralist_mcp", app))

        health_core_module = types.ModuleType("server_modules.health_core")

        health_diagnostics_module = types.ModuleType("server_modules.health_diagnostics")
        health_diagnostics_module.control_plane_guard_middleware = lambda request, call_next: call_next(request)

        control_plane_repository_module = types.ModuleType("server_modules.control_plane_repository")

        async def _ensure_control_plane_schema():
            lifecycle_events.append("ensure_control_plane_schema")

        control_plane_repository_module.ensure_control_plane_schema = _ensure_control_plane_schema

        direct_chat_tool_catalog_service_module = types.ModuleType("server_modules.direct_chat_tool_catalog_service")
        direct_chat_tool_catalog_service_module.registered_direct_chat_tool_names_for_logging = lambda: []

        route_modules = {
            "routes_agents": types.ModuleType("server_modules.routes_agents"),
            "routes_agent_traces": types.ModuleType("server_modules.routes_agent_traces"),
            "routes_auth": types.ModuleType("server_modules.routes_auth"),
            "routes_billing": types.ModuleType("server_modules.routes_billing"),
            "routes_builder": types.ModuleType("server_modules.routes_builder"),
            "routes_connectors": types.ModuleType("server_modules.routes_connectors"),
            "routes_deployed_agents": types.ModuleType("server_modules.routes_deployed_agents"),
            "routes_gateway": types.ModuleType("server_modules.routes_gateway"),
            "routes_health": types.ModuleType("server_modules.routes_health"),
            "routes_marketplace": types.ModuleType("server_modules.routes_marketplace"),
            "routes_mini_apps": types.ModuleType("server_modules.routes_mini_apps"),
            "routes_personal_channels": types.ModuleType("server_modules.routes_personal_channels"),
            "routes_pilot": types.ModuleType("server_modules.routes_pilot"),
            "routes_platform_analytics": types.ModuleType("server_modules.routes_platform_analytics"),
            "routes_runs": types.ModuleType("server_modules.routes_runs"),
            "routes_studio": types.ModuleType("server_modules.routes_studio"),
            "routes_workspaces": types.ModuleType("server_modules.routes_workspaces"),
            "routes_workflows": types.ModuleType("server_modules.routes_workflows"),
        }
        route_modules["routes_agents"].router = _build_router("/agents-router")
        route_modules["routes_agent_traces"].router = _build_router("/agent-traces-router")
        route_modules["routes_auth"].router = _build_router("/auth-router")
        route_modules["routes_billing"].router = _build_router("/billing-router")
        route_modules["routes_builder"].router = _build_router("/builder-router")
        route_modules["routes_connectors"].router = _build_router("/connectors-router")
        route_modules["routes_deployed_agents"].router = _build_router("/deployed-agents-router")
        route_modules["routes_gateway"].router = _build_router("/gateway-router")
        route_modules["routes_health"].router = _build_router("/health-router")
        route_modules["routes_marketplace"].router = _build_router("/marketplace-router")
        route_modules["routes_mini_apps"].router = _build_router("/mini-apps-router")
        route_modules["routes_personal_channels"].router = _build_router("/personal-channels-router")
        route_modules["routes_pilot"].router = _build_router("/pilot-router")
        route_modules["routes_platform_analytics"].router = _build_router("/platform-analytics-router")
        route_modules["routes_runs"].router = _build_router("/runs-router")
        route_modules["routes_studio"].router = _build_router("/studio-router")
        route_modules["routes_workspaces"].router = _build_router("/workspaces-router")
        route_modules["routes_workflows"].router = _build_router("/workflows-router")

        fastapi_module = types.ModuleType("fastapi")
        fastapi_module.FastAPI = _FastAPI
        fastapi_module.Request = type("Request", (), {})
        fastapi_module.HTTPException = type("HTTPException", (Exception,), {})

        fastapi_exceptions_module = types.ModuleType("fastapi.exceptions")
        fastapi_exceptions_module.RequestValidationError = type("RequestValidationError", (Exception,), {})

        fastapi_cors_module = types.ModuleType("fastapi.middleware.cors")
        fastapi_cors_module.CORSMiddleware = type("CORSMiddleware", (), {})

        starlette_exceptions_module = types.ModuleType("starlette.exceptions")
        starlette_exceptions_module.HTTPException = type("HTTPException", (Exception,), {})

        sentry_sdk_module = types.ModuleType("sentry_sdk")
        sentry_sdk_module.init = lambda **_kwargs: None

        sentry_integrations_module = types.ModuleType("sentry_sdk.integrations")
        sentry_fastapi_module = types.ModuleType("sentry_sdk.integrations.fastapi")
        sentry_sqlalchemy_module = types.ModuleType("sentry_sdk.integrations.sqlalchemy")

        class _FastApiIntegration:
            pass

        class _SqlalchemyIntegration:
            pass

        sentry_fastapi_module.FastApiIntegration = _FastApiIntegration
        sentry_sqlalchemy_module.SqlalchemyIntegration = _SqlalchemyIntegration

        uvicorn_module = types.ModuleType("uvicorn")
        uvicorn_module.run = lambda *_args, **_kwargs: None

        shared_modules = _server_modules_package(
            runtime_config_module=runtime_config_module,
            acp_manager_module=acp_manager_module,
            extra_modules={
                "shared": shared_module,
                "logging_config": logging_config_module,
                "runtime_common": runtime_common_module,
                "runs_core": runs_core_module,
                "runs_history": types.ModuleType("server_modules.runs_history"),
                "control_plane_repository": control_plane_repository_module,
                "runs_execution": runs_execution_module,
                "runs_output": runs_output_module,
                "runs_delegation": types.ModuleType("server_modules.runs_delegation"),
                "runs_engine": types.ModuleType("server_modules.runs_engine"),
                "health_core": health_core_module,
                "health_diagnostics": health_diagnostics_module,
                "connectors_core": connectors_core_module,
                "connectors_actions": types.ModuleType("server_modules.connectors_actions"),
                "direct_chat_tool_catalog_service": direct_chat_tool_catalog_service_module,
                **route_modules,
            },
        )
        injected_modules = {
            **shared_modules,
            "fastapi": fastapi_module,
            "fastapi.exceptions": fastapi_exceptions_module,
            "fastapi.middleware": types.ModuleType("fastapi.middleware"),
            "fastapi.middleware.cors": fastapi_cors_module,
            "sentry_sdk": sentry_sdk_module,
            "sentry_sdk.integrations": sentry_integrations_module,
            "sentry_sdk.integrations.fastapi": sentry_fastapi_module,
            "sentry_sdk.integrations.sqlalchemy": sentry_sqlalchemy_module,
            "starlette.exceptions": starlette_exceptions_module,
            "uvicorn": uvicorn_module,
        }

        server_module = _load_module_from_path("server_under_test", SERVER_PATH, injected_modules)

        self.assertIsInstance(server_module.app, _FastAPI)
        self.assertIs(shared_module.app, server_module.app)

        route_paths = [route.path for route in server_module.app.routes]
        expected_paths = {
            "/workflows-router",
            "/agents-router",
            "/runs-router",
            "/auth-router",
            "/health-router",
            "/connectors-router",
            "/builder-router",
            "/api/runs-router",
            "/api/health-router",
            "/api/gateway-router",
            "/api/personal-channels-router",
            "/api/workspaces-router",
            "/api/billing-router",
            "/api/deployed-agents-router",
            "/api/agent-traces-router",
            "/api/platform-analytics-router",
            "/api/marketplace-router",
            "/api/mini-apps-router",
            "/api/pilot-router",
            "/api/studio-router",
            "/api/v1/auth-router",
        }
        for path in expected_paths:
            self.assertEqual(route_paths.count(path), 1, path)

        @asynccontextmanager
        async def _fake_shared_lifespan(app_instance):
            lifecycle_events.append(("shared_lifespan_enter", app_instance))
            try:
                yield
            finally:
                lifecycle_events.append(("shared_lifespan_exit", app_instance))

        shared_module.app_lifespan = _fake_shared_lifespan

        async def _exercise_runtime_lifespan():
            async with server_module.runtime_app_lifespan(server_module.app):
                lifecycle_events.append("lifespan_body")

        asyncio.run(_exercise_runtime_lifespan())

        self.assertIn("ensure_control_plane_schema", lifecycle_events)
        self.assertIn("initialize_runtime_services", lifecycle_events)
        self.assertIn(("shared_lifespan_enter", server_module.app), lifecycle_events)
        self.assertIn(("shared_lifespan_exit", server_module.app), lifecycle_events)


if __name__ == "__main__":
    unittest.main()

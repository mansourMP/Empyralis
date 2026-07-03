"""
Composition root for the active runtime application.

Prompt 01 freezes `server.py` as wiring only:
- create the FastAPI app
- register middleware and lifecycle hooks
- mount routers and shared service configuration

It must not become a product-policy owner for turn execution, connector behavior,
memory, quota, or deployed-agent runtime logic.
"""

import argparse
import os
import logging
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from contextlib import asynccontextmanager
import uvicorn

from server_modules.logging_config import configure_logging


configure_logging()

# Load .env for local development (production uses real env vars)
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_path)
except Exception:
    pass

try:
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
except Exception as _startup_exc:  # pragma: no cover - optional dependency guard
    SqlalchemyIntegration = None  # type: ignore[assignment]

sentry_integrations = [FastApiIntegration()]
if SqlalchemyIntegration is not None:
    try:
        sentry_integrations.append(SqlalchemyIntegration())
    except Exception as _startup_exc:
        logging.getLogger(__name__).debug("SQLAlchemy Sentry integration is unavailable.", exc_info=True)

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN", ""),
    integrations=sentry_integrations,
    traces_sample_rate=0.1,
    environment=os.environ.get("ORION_ENV", "development"),
    before_send=lambda event, hint: event if os.environ.get("SENTRY_DSN") else None,
)

from fastapi import FastAPI, Request
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from server_modules import runtime_config as runtime_config
from server_modules import shared as shared
from server_modules import runtime_common as runtime_common
from server_modules import runs_core as runs_core
from server_modules import runs_history as runs_history
from server_modules import control_plane_repository as control_plane_repository
from server_modules import runs_execution as runs_execution
from server_modules import runs_output as runs_output
from server_modules import runs_delegation as runs_delegation
from server_modules import runs_engine as runs_engine
from server_modules import health_core as health_core
from server_modules import health_diagnostics as health_diagnostics
from server_modules import connectors_core as connectors_core
from server_modules import connectors_actions as connectors_actions
from server_modules import error_response_service as error_response_service
from server_modules.cloud_cutover_config import assert_cloud_cutover_config
from server_modules.direct_chat_tool_catalog_service import registered_direct_chat_tool_names_for_logging
from server_modules.preflight import preflight_or_raise


LOGGER = logging.getLogger(__name__)
DEFAULT_MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_WEBHOOK_BODY_BYTES = 1 * 1024 * 1024
DEFAULT_MAX_AUDIO_BODY_BYTES = 25 * 1024 * 1024


class RequestBodyTooLargeError(Exception):
    pass


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default)) or default), 1)
    except (TypeError, ValueError):
        return default


def _request_body_limit_for_path(path: str) -> int:
    normalized = str(path or "").strip().lower()
    if normalized.endswith("/stt") or "/stt/" in normalized:
        return _env_int("EMPYRALIS_MAX_AUDIO_BODY_BYTES", DEFAULT_MAX_AUDIO_BODY_BYTES)
    if "/webhook" in normalized or normalized.endswith("/channels/slack/events"):
        return _env_int("EMPYRALIS_MAX_WEBHOOK_BODY_BYTES", DEFAULT_MAX_WEBHOOK_BODY_BYTES)
    return _env_int("EMPYRALIS_MAX_REQUEST_BODY_BYTES", DEFAULT_MAX_REQUEST_BODY_BYTES)


def _request_body_too_large_response(request: Request, *, limit: int):
    return error_response_service.json_response_from_platform_error(
        error_response_service.platform_error(
            code="request_body_too_large",
            message="Request body is too large.",
            error_class=error_response_service.USER_INPUT_ERROR,
            retryable=False,
            status_code=413,
            request_id=error_response_service.request_id_from_request(request),
            details={"max_body_bytes": int(limit)},
        )
    )

for module in (
    runtime_config,
    shared,
    runtime_common,
    runs_core,
    runs_history,
    runs_execution,
    runs_output,
    runs_delegation,
    runs_engine,
    health_core,
    health_diagnostics,
    connectors_core,
    connectors_actions,
):
    globals().update({key: value for key, value in vars(module).items() if not key.startswith("__")})


assert_cloud_cutover_config(os.environ)


def _log_machine_mode_startup() -> None:
    LOGGER.info("Registered tools: %s", registered_direct_chat_tool_names_for_logging())
    configured_owner = str(runtime_config.AGENT_MACHINE_OWNER or "").strip()
    if runtime_config.AGENT_MACHINE_MODE == "agent":
        if configured_owner:
            LOGGER.warning(
                "AGENT MACHINE MODE ACTIVE — all tool executions bypass approval for configured machine owner %s.",
                configured_owner,
            )
        else:
            LOGGER.warning("AGENT MACHINE MODE ACTIVE but AGENT_MACHINE_OWNER is empty.")
            LOGGER.warning("AGENT MACHINE MODE is enabled but AGENT_MACHINE_OWNER is empty; falling back to personal-mode approvals.")
    else:
        LOGGER.info("Personal machine mode — approvals required for destructive tools")


_log_machine_mode_startup()


def _warn_project_local_secret_files() -> None:
    for relative_path in (".gws-config/client_secret.json", ".orion-stack/stack.env"):
        if os.path.exists(relative_path):
            LOGGER.warning(
                "Project-local secret file detected at %s. Keep it gitignored and rotate/move secrets before shared or production use.",
                relative_path,
            )


_warn_project_local_secret_files()


def _check_runtime_kernel_startup() -> None:
    """Warn loudly when the Rust runtime kernel binary is missing/unbuilt.

    Without the kernel every governance decision fails closed
    (``runtime_kernel_unavailable``), so the app is effectively
    unusable.  This check runs at import time so the warning is
    visible before any request is served.
    """
    try:
        from server_modules.rust_runtime_kernel_client import runtime_kernel_binary, KERNEL_ENV_VAR
    except ImportError:
        return  # client module not available — probably a partial environment
    binary = runtime_kernel_binary()
    if binary is not None:
        return
    repo_root = os.environ.get("EMPYRALIS_REPO_ROOT") or os.getcwd()
    build_cmd = "cargo build --manifest-path empyralis-runtime-kernel/Cargo.toml"
    LOGGER.warning("=" * 72)
    LOGGER.warning("RUST RUNTIME KERNEL IS NOT BUILT")
    LOGGER.warning("=" * 72)
    LOGGER.warning("The Rust runtime kernel binary was not found.")
    LOGGER.warning("Every governance decision will fail with 'runtime_kernel_unavailable'.")
    LOGGER.warning("")
    LOGGER.warning("To fix, build the kernel:")
    LOGGER.warning("  cd %s && %s", repo_root, build_cmd)
    LOGGER.warning("")
    LOGGER.warning("Or set %s to the binary path.", KERNEL_ENV_VAR)
    LOGGER.warning("=" * 72)


_check_runtime_kernel_startup()

configure_runtime_memory(
    memory_enabled=ORION_MEMORY_ENABLED,
    memory_lancedb_uri=ORION_MEMORY_LANCEDB_URI,
    memory_db_path=str(ORION_MEMORY_DB_PATH),
    memory_read_k=ORION_MEMORY_READ_K,
    memory_retention_days_default=ORION_MEMORY_RETENTION_DAYS_DEFAULT,
    memory_max_text_chars=ORION_MEMORY_MAX_TEXT_CHARS,
    memory_buckets=shared.MEMORY_BUCKETS,
    runtime_memory_manager=RuntimeMemoryManager,
    utc_now=runtime_common._utc_now,
    utc_now_iso=runtime_common._utc_now_iso,
    parse_utc_ts=runtime_common._parse_utc_ts,
    normalize_workspace_id=runtime_common._normalize_workspace_id,
    json_safe=runs_output._json_safe,
    compact_event_text=runs_output._compact_event_text,
    emit_log=runs_core.emit_log,
    refresh_archived_run_snapshot=runs_output._refresh_archived_run_snapshot,
)

configure_runtime_events(
    channel_events=shared.CHANNEL_EVENTS,
    channel_events_lock=shared.CHANNEL_EVENTS_LOCK,
    channel_events_limit=ORION_CHANNEL_EVENTS_LIMIT,
    channel_sessions_limit=ORION_CHANNEL_SESSIONS_LIMIT,
    channel_events_file=ORION_CHANNEL_EVENTS_FILE,
    runtime_state_db=str(ORION_RUNTIME_STATE_DB),
    list_channel_events_fn=list_channel_events,
    replace_channel_events_fn=replace_channel_events,
    append_channel_event_fn=append_channel_event,
    utc_now_iso=runtime_common._utc_now_iso,
    parse_utc_ts=runtime_common._parse_utc_ts,
    normalize_workspace_id=runtime_common._normalize_workspace_id,
    normalize_tenant_id=lambda value: str(value or "default").strip() or "default",
    compact_event_text=runs_output._compact_event_text,
    json_safe=runs_output._json_safe,
    safe_read_json=runtime_common._safe_read_json,
)

from server_modules.routes_agents import router as agents_router
from server_modules.routes_agent_traces import router as agent_traces_router
from server_modules.routes_auth import router as auth_router
from server_modules.routes_billing import router as billing_router
from server_modules.routes_builder import router as builder_router
from server_modules.routes_connections import router as connections_router
from server_modules.routes_connectors import router as connectors_router
from server_modules.routes_deployed_agents import router as deployed_agents_router
from server_modules.routes_gateway import router as gateway_router
from server_modules.routes_health import router as health_router
from server_modules.routes_marketplace import router as marketplace_router
from server_modules.routes_personal_channels import router as personal_channels_router
from server_modules.routes_pilot import router as pilot_router
from server_modules.routes_platform_analytics import router as platform_analytics_router
from server_modules.routes_runs import router as runs_router
from server_modules.routes_studio import router as studio_router
from server_modules.routes_workspaces import router as workspaces_router
from server_modules.routes_workflows import router as workflows_router
from server_modules.routes_doctor import router as doctor_router
from server_modules.routes_sage_telegram_hosted import router as sage_telegram_hosted_router
from server_modules.connectors.discord_bot_runtime_service import DiscordBotRuntimeService


docs_url = "/docs" if os.getenv("ENV") == "development" else None
redoc_url = "/redoc" if os.getenv("ENV") == "development" else None
openapi_url = "/openapi.json" if os.getenv("ENV") == "development" else None

def _launch_discord_bot_runtime() -> None:
    """Start Discord bot gateway listener(s) in daemon threads.

    Falls back to DISCORD_BOT_TOKEN from env when no vault connector
    exists.  Failures are logged but never crash the server.
    """
    try:
        svc = DiscordBotRuntimeService()
        result = svc.start(block=False)
        LOGGER.info(
            "DiscordBotRuntime started=%s statuses=%s",
            result.get("started"), result.get("statuses"),
        )
    except Exception:
        LOGGER.exception("DiscordBotRuntime failed to start — continuing without bot.")


@asynccontextmanager
async def runtime_app_lifespan(app_instance: FastAPI):
    await preflight_or_raise()
    await control_plane_repository.ensure_control_plane_schema()
    runs_core.initialize_runtime_services()
    # ── Discord chatbot v1: persistent gateway listener for DM→Sage ──
    _launch_discord_bot_runtime()
    async with shared.app_lifespan(app_instance):
        yield

app = FastAPI(
    title="Empyralis Runtime API",
    lifespan=runtime_app_lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
)
existing_shared_app = getattr(shared, "app", None)
if existing_shared_app is not None and existing_shared_app is not app:
    import logging
    _log = logging.getLogger("server")
    _log.warning("shared.app was already set (type=%s). Overwriting with server-owned FastAPI app.", type(existing_shared_app).__name__)
shared.app = app

@app.exception_handler(FastAPIHTTPException)
async def fastapi_http_exception_handler(request: Request, exc: FastAPIHTTPException):
    return error_response_service.http_exception_response(request, exc)


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    return error_response_service.http_exception_response(request, exc)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return error_response_service.request_validation_error_response(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return error_response_service.unhandled_exception_response(request, exc)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in FRONTEND_ORIGINS.split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
mount_empyralist_mcp(app)


@app.middleware("http")
async def control_plane_guard(request: Request, call_next):
    return await control_plane_guard_middleware(request, call_next)


@app.middleware("http")
async def request_body_limit_guard(request: Request, call_next):
    limit = _request_body_limit_for_path(str(request.url.path or ""))
    content_length = str(request.headers.get("content-length") or "").strip()
    if content_length:
        try:
            if int(content_length) > limit:
                return _request_body_too_large_response(request, limit=limit)
        except ValueError:
            return _request_body_too_large_response(request, limit=limit)

    received = 0
    original_receive = request._receive  # type: ignore[attr-defined]

    async def limited_receive():
        nonlocal received
        message = await original_receive()
        if message.get("type") == "http.request":
            body = message.get("body") or b""
            received += len(body)
            if received > limit:
                raise RequestBodyTooLargeError()
        return message

    request._receive = limited_receive  # type: ignore[attr-defined]
    try:
        return await call_next(request)
    except RequestBodyTooLargeError:
        return _request_body_too_large_response(request, limit=limit)


app.include_router(workflows_router)
app.include_router(agents_router)
app.include_router(runs_router)
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(connectors_router)
app.include_router(builder_router)
app.include_router(agents_router, prefix="/api")
app.include_router(runs_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(connectors_router, prefix="/api")
app.include_router(connections_router, prefix="/api")
app.include_router(gateway_router, prefix="/api")
# Fallback: gateway registration without /api prefix — some gateway builds
# construct the URL differently.  Same handler, no-auth (pairing token is auth).
from server_modules.routes_gateway import register_gateway as _register_gateway_fallback
app.post("/gateway/registrations")(_register_gateway_fallback)
app.include_router(personal_channels_router, prefix="/api")
app.include_router(workspaces_router, prefix="/api")
app.include_router(billing_router, prefix="/api")
app.include_router(deployed_agents_router, prefix="/api")
app.include_router(agent_traces_router, prefix="/api")
app.include_router(platform_analytics_router, prefix="/api")
app.include_router(marketplace_router, prefix="/api")
app.include_router(pilot_router, prefix="/api")
app.include_router(studio_router, prefix="/api")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api")
app.include_router(doctor_router)
app.include_router(sage_telegram_hosted_router, prefix="/api")


def _runtime_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Empyralis runtime API")
    parser.add_argument(
        "app_path",
        nargs="?",
        default="server:app",
        help="Compatibility positional accepted from uvicorn-style launchers.",
    )
    parser.add_argument("--host", default=os.getenv("ORION_RUNTIME_HOST") or os.getenv("HOST") or "127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT") or os.getenv("ORION_RUNTIME_PORT") or "8001"),
    )
    parser.add_argument("--reload", action="store_true", help="Enable reload when launched as a script.")
    args = parser.parse_args()
    # Keep compatibility with the existing Tauri launcher, which passes `server:app`.
    if str(args.app_path).strip() not in {"", "server:app"}:
        parser.error(f"Unsupported app target: {args.app_path}")
    return args


def main() -> None:
    args = _runtime_cli_args()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=False,
    )


if __name__ == "__main__":
    main()

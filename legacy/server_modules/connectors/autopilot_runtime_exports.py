from __future__ import annotations

import os
from typing import Optional

from server_modules.connectors.autopilot_connector_config import (
    _telegram_get_updates_process_lock,
    _resolve_state_file,
    _resolve_state_dir,
    _AUTOPILOT_EVENT_DEDUP,
    _AUTOPILOT_EVENT_DEDUP_LOCK,
    _CHANNEL_DEAD_LETTER_LOCK,
    DEFAULT_CHAT_PREFIX,
    EMPYRALIST_RUNTIME_URL,
    ORION_CHANNEL_DEAD_LETTER_LIMIT,
    ORION_LOCAL_LEASE_SECONDS,
    ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES,
    ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS,
    ORION_TELEGRAM_AUTOPILOT_STATE_FILE,
    ORION_TELEGRAM_MEDIA_DIR,
    ORION_TELEGRAM_MEDIA_MAX_ITEMS,
    ORION_WHATSAPP_AUTOPILOT_STATE_FILE,
)
from server_modules.connectors.autopilot_connector_shell_builder import build_autopilot_connector_shell_service
from server_modules.connectors.autopilot_connector_shell_service import AutopilotConnectorShellService

_server = None
_SYNC_SERVER_GLOBALS = (
    "TELEGRAM_AUTOPILOT_THREAD",
    "TELEGRAM_AUTOPILOT_STATE",
    "WHATSAPP_AUTOPILOT_STATE",
)
_AUTOPILOT_CONNECTOR_SHELL_SERVICE: Optional[AutopilotConnectorShellService] = None
ORION_TELEGRAM_AUTOPILOT_TRUST_MODE = str(os.getenv("ORION_TELEGRAM_AUTOPILOT_TRUST_MODE", "guarded")).strip().lower() or "guarded"
ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET = (
    str(os.getenv("ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET", "local_companion")).strip().lower() or "local_companion"
)
ORION_WHATSAPP_AUTOPILOT_TRUST_MODE = str(os.getenv("ORION_WHATSAPP_AUTOPILOT_TRUST_MODE", "guarded")).strip().lower() or "guarded"
ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET = (
    str(os.getenv("ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET", "local_companion")).strip().lower() or "local_companion"
)


def _autopilot_connector_shell_service() -> AutopilotConnectorShellService:
    global _AUTOPILOT_CONNECTOR_SHELL_SERVICE
    if _AUTOPILOT_CONNECTOR_SHELL_SERVICE is None:
        _AUTOPILOT_CONNECTOR_SHELL_SERVICE = build_autopilot_connector_shell_service(
            global_namespace=globals(),
            sync_server_globals=_SYNC_SERVER_GLOBALS,
            server_getter=lambda: _server,
            server_setter=lambda value: globals().__setitem__("_server", value),
            import_server=lambda: __import__("server", fromlist=["*"]),
        )
    return _AUTOPILOT_CONNECTOR_SHELL_SERVICE


def _load_telegram_autopilot_state() -> None:
    _autopilot_connector_shell_service().runtime_facade_service().load_telegram_autopilot_state()


def _load_whatsapp_autopilot_state() -> None:
    _autopilot_connector_shell_service().runtime_facade_service().load_whatsapp_autopilot_state()


def _telegram_autopilot_snapshot():
    return _autopilot_connector_shell_service().runtime_facade_service().telegram_autopilot_snapshot()


def _whatsapp_autopilot_snapshot():
    return _autopilot_connector_shell_service().runtime_facade_service().whatsapp_autopilot_snapshot()


def _whatsapp_autopilot_activate() -> None:
    _autopilot_connector_shell_service().runtime_facade_service().whatsapp_autopilot_activate()


def _whatsapp_webhook_secrets(request) -> dict[str, str]:
    return {
        "query_secret": str(request.query_params.get("secret") or "").strip(),
        "header_secret": str(
            request.headers.get("x-orion-webhook-secret")
            or request.headers.get("X-Orion-Webhook-Secret")
            or ""
        ).strip(),
        "twilio_signature": str(
            request.headers.get("x-twilio-signature")
            or request.headers.get("X-Twilio-Signature")
            or ""
        ).strip(),
    }


def _whatsapp_public_request_url(request) -> str:
    public_base_url = str(os.getenv("ORION_WHATSAPP_AUTOPILOT_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    path = str(getattr(request.url, "path", "") or "").strip()
    query = str(getattr(request.url, "query", "") or "").strip()
    if public_base_url and path:
        url = f"{public_base_url}{path}"
        if query:
            url = f"{url}?{query}"
        return url
    return str(request.url)


async def handle_whatsapp_twilio_webhook(request):
    secrets = _whatsapp_webhook_secrets(request)
    return await _autopilot_connector_shell_service().runtime_facade_service().handle_whatsapp_webhook(
        raw_body=await request.body(),
        request_url=_whatsapp_public_request_url(request),
        twilio_signature=secrets["twilio_signature"],
        query_secret=secrets["query_secret"],
        header_secret=secrets["header_secret"],
    )


async def handle_telegram_webhook(request, connector_id: str):
    header_secret = str(
        request.headers.get("x-telegram-bot-api-secret-token")
        or request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        or ""
    ).strip()
    return await _autopilot_connector_shell_service().runtime_facade_service().handle_telegram_webhook(
        raw_body=await request.body(),
        connector_id=str(connector_id or "").strip(),
        header_secret=header_secret,
    )


async def handle_telegram_autopilot_status():
    return await _autopilot_connector_shell_service().runtime_facade_service().telegram_status_payload()


async def handle_whatsapp_autopilot_status():
    return await _autopilot_connector_shell_service().runtime_facade_service().whatsapp_status_payload()


async def handle_list_autopilot_profiles():
    return await _autopilot_connector_shell_service().runtime_facade_service().autopilot_profiles_payload()


async def handle_telegram_send_message(
    *,
    text: str,
    workspace_id: str | None = None,
    session_key: str | None = None,
    chat_id: str | None = None,
):
    return await _autopilot_connector_shell_service().runtime_facade_service().handle_telegram_send_message(
        text=text,
        workspace_id=workspace_id,
        session_key=session_key,
        chat_id=chat_id,
    )


async def handle_telegram_autopilot_test_message(
    *,
    text: str,
    workspace_id: str | None = None,
    session_key: str | None = None,
    chat_id: str | None = None,
    connector_id: str | None = None,
    sender_id: str | None = None,
    timeout_seconds: int | None = None,
):
    return await _autopilot_connector_shell_service().runtime_facade_service().handle_telegram_autopilot_test_message(
        text=text,
        workspace_id=workspace_id,
        session_key=session_key,
        chat_id=chat_id,
        connector_id=connector_id,
        sender_id=sender_id,
        timeout_seconds=timeout_seconds,
    )


def _run_telegram_autopilot_forever() -> None:
    _autopilot_connector_shell_service().runtime_facade_service().run_telegram_autopilot_forever()


def _init() -> None:
    _autopilot_connector_shell_service().runtime_facade_service().init_runtime()


def _telegram_run_dispatch_service():
    return _autopilot_connector_shell_service().registry_facade_service().telegram_service_registry().telegram_run_dispatch_service()


def _autopilot_run_entry_service():
    return _autopilot_connector_shell_service().registry_facade_service().runtime_service_registry().run_entry_service()

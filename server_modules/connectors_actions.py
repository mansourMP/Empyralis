import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from server_modules import runtime_config as config
from server_modules import secrets_broker, tool_broker
from server_modules import shared as shared
from server_modules import runtime_common as common
from server_modules.connectors.github_connector import (
    build_run_goal_from_event as github_build_run_goal_from_event,
    event_matches_connector as github_event_matches_connector,
    parse_inbound_event as github_parse_inbound_event,
    request_signature_header as github_request_signature_header,
    should_trigger_agent_run as github_should_trigger_agent_run,
    verify_request_signature as github_verify_request_signature,
)
from server_modules.connectors.discord_connector import (
    build_run_goal_from_event as discord_build_run_goal_from_event,
    event_matches_connector as discord_event_matches_connector,
    interaction_signature_headers as discord_interaction_signature_headers,
    parse_inbound_event as discord_parse_inbound_event,
    send_dm as discord_send_dm,
    should_trigger_agent_run as discord_should_trigger_agent_run,
    verify_interaction_signature as discord_verify_interaction_signature,
)
from server_modules.connectors.slack_connector import (
    build_run_goal_from_event as slack_build_run_goal_from_event,
    exchange_oauth_code as slack_exchange_oauth_code,
    event_matches_connector as slack_event_matches_connector,
    get_channel_history as slack_get_channel_history,
    list_channels as slack_list_channels,
    parse_inbound_event as slack_parse_inbound_event,
    send_dm as slack_send_dm_message,
    send_message as slack_send_channel_message,
    should_trigger_agent_run as slack_should_trigger_agent_run,
    verify_request_signature as slack_verify_request_signature,
)
from server_modules.schemas import ConnectorCreate, ConnectorDocumentCreateRequest, ConnectorSpreadsheetCreateRequest
from server_modules.tool_availability_truth import capability_verification_metadata

globals().update({key: value for key, value in vars(config).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(shared).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(common).items() if not key.startswith("__")})


def _origin_from_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _assert_slack_redirect_uri_allowed(redirect_uri: str) -> None:
    redirect_origin = _origin_from_url(redirect_uri)
    if not redirect_origin:
        raise HTTPException(status_code=400, detail="Slack redirect_uri is invalid.")
    allowed_origins = {
        _origin_from_url(origin)
        for origin in str(os.getenv("FRONTEND_ORIGINS") or FRONTEND_ORIGINS or "").split(",")
        if str(origin or "").strip()
    }
    if redirect_origin not in allowed_origins:
        raise HTTPException(status_code=400, detail="Slack redirect_uri is not allowed.")


CONNECTOR_CLASS_API = "api_connector"
CONNECTOR_CLASS_BROWSER = "browser_connector"
CONNECTOR_CLASS_MEDIA = "media_generation_connector"
SUPPORTED_CONNECTOR_CLASSES = {
    CONNECTOR_CLASS_API,
    CONNECTOR_CLASS_BROWSER,
    CONNECTOR_CLASS_MEDIA,
}
EXECUTION_PATH_HUMAN_ADMIN_ROUTE = "human_admin_route"
EXECUTION_PATH_BROKERED_RUNTIME = "brokered_runtime"
EXECUTION_PATH_PUBLIC_WEBHOOK = "public_webhook"
SUPPORTED_CONNECTOR_EXECUTION_PATHS = {
    EXECUTION_PATH_HUMAN_ADMIN_ROUTE,
    EXECUTION_PATH_BROKERED_RUNTIME,
    EXECUTION_PATH_PUBLIC_WEBHOOK,
}
CONNECTOR_SURFACE_DEEP_APP = "deep_application_connector"
CONNECTOR_SURFACE_CHANNEL_SHELL = "channel_shell"
TELEGRAM_WEBHOOK_REGISTRATION_METADATA_KEY = "telegram_webhook_registration"

_CONNECTOR_CLASS_DEFAULTS: Dict[str, Dict[str, Any]] = {
    CONNECTOR_CLASS_API: {
        "capability_patterns": [
            "list_records",
            "read_record",
            "search_records",
            "create_record",
            "update_record",
            "delete_record",
            "send_message",
            "sync_webhook_ingress",
        ],
        "allowed_secret_fields": ["access_token"],
        "fallback_connectors": [CONNECTOR_CLASS_BROWSER],
    },
    CONNECTOR_CLASS_BROWSER: {
        "capability_patterns": [
            "navigate_surface",
            "interactive_auth",
            "scrape_page",
            "form_fill",
            "upload_file",
            "interactive_review",
        ],
        "allowed_secret_fields": [],
        "fallback_connectors": [],
    },
    CONNECTOR_CLASS_MEDIA: {
        "capability_patterns": [
            "generate_image",
            "edit_image",
            "generate_video",
            "render_asset",
            "upscale_asset",
            "variation_asset",
        ],
        "allowed_secret_fields": ["access_token", "api_key"],
        "fallback_connectors": [],
    },
}
_CONNECTOR_CONTRACT_OVERRIDES: Dict[str, Dict[str, Any]] = {
    # DEPRECATED: google_workspace custom tools replaced by MCP pipeline (google-gmail, google-calendar, google-drive servers).
    # capability_patterns and actions cleared — agent now uses MCP tools instead. Remove entry after 30-day stability period.
    "google_workspace": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": [],
        "allowed_secret_fields": ["access_token"],
        "actions": {},
    },
    "microsoft_365": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": ["browse_drive", "read_mail", "create_calendar_event"],
        "allowed_secret_fields": ["access_token"],
        "actions": {
            "browse_drive": {"action_class": "read", "elevated": False},
        },
    },
    # DEPRECATED: github custom tools replaced by MCP pipeline (api.githubcopilot.com/mcp/).
    # Requires GitHub Copilot or Copilot Enterprise seat. Remove entry after 30-day stability period.
    "github": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": [],
        "allowed_secret_fields": ["personal_access_token", "app_id", "installation_id", "private_key_pem"],
        "actions": {},
    },
    # DEPRECATED: notion custom tools replaced by MCP pipeline (mcp.notion.com/mcp).
    # Remove entry after 30-day stability period.
    "notion": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": [],
        "allowed_secret_fields": ["integration_token", "access_token"],
        "actions": {},
    },
    # DEPRECATED: linear custom tools replaced by MCP pipeline (mcp.linear.app/mcp).
    # Remove entry after 30-day stability period.
    "linear": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": [],
        "allowed_secret_fields": ["api_key", "access_token"],
        "actions": {},
    },
    "s3": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": ["list_bucket", "read_object", "upload_object", "delete_object"],
        "allowed_secret_fields": ["aws_access_key_id", "aws_secret_access_key", "region"],
    },
    # DEPRECATED: dropbox custom tools replaced by MCP pipeline (mcp.dropbox.com/mcp).
    # Remove entry after 30-day stability period.
    "dropbox": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": [],
        "allowed_secret_fields": ["access_token"],
        "actions": {},
    },
    "smtp": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": ["draft_email", "send_email"],
        "allowed_secret_fields": ["host", "port", "username", "password", "use_tls"],
    },
    "telegram_bot": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_CHANNEL_SHELL,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": ["receive_channel_message", "send_channel_message", "sync_channel_state"],
        "allowed_secret_fields": ["bot_token", "chat_id"],
    },
    "whatsapp_twilio": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_CHANNEL_SHELL,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": ["receive_channel_message", "send_channel_message", "sync_channel_state"],
        "allowed_secret_fields": ["account_sid", "auth_token", "from_number", "to_number"],
    },
    "slack": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_CHANNEL_SHELL,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": ["receive_channel_message", "send_channel_message", "sync_channel_state"],
        "allowed_secret_fields": ["bot_token", "user_token", "team_id", "team_name"],
    },
    "discord_bot": {
        "connector_class": CONNECTOR_CLASS_API,
        "surface_role": CONNECTOR_SURFACE_CHANNEL_SHELL,
        "preferred_execution": CONNECTOR_CLASS_API,
        "capability_patterns": ["receive_channel_message", "send_channel_message", "sync_channel_state"],
        "allowed_secret_fields": [
            "bot_token",
            "channel_id",
            "guild_id",
            "application_id",
            "public_key",
            "application_public_key",
        ],
    },
    "browser_automation": {
        "connector_class": CONNECTOR_CLASS_BROWSER,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_BROWSER,
        "capability_patterns": ["navigate_surface", "interactive_auth", "scrape_page", "form_fill", "interactive_review"],
    },
    "remote_browser": {
        "connector_class": CONNECTOR_CLASS_BROWSER,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_BROWSER,
        "capability_patterns": ["navigate_surface", "interactive_auth", "scrape_page", "form_fill", "interactive_review"],
    },
    "openai_image": {
        "connector_class": CONNECTOR_CLASS_MEDIA,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_MEDIA,
        "capability_patterns": ["generate_image", "edit_image", "variation_asset"],
        "allowed_secret_fields": ["access_token", "api_key"],
    },
    "runway_video": {
        "connector_class": CONNECTOR_CLASS_MEDIA,
        "surface_role": CONNECTOR_SURFACE_DEEP_APP,
        "preferred_execution": CONNECTOR_CLASS_MEDIA,
        "capability_patterns": ["generate_video", "render_asset"],
        "allowed_secret_fields": ["access_token", "api_key"],
    },
}


def _normalize_connector_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_connector_class(value: Any) -> str:
    token = _normalize_connector_token(value)
    if token not in SUPPORTED_CONNECTOR_CLASSES:
        raise HTTPException(status_code=400, detail=f"Unsupported connector class '{value}'.")
    return token


def _ordered_unique_tokens(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    ordered: List[str] = []
    seen: set[str] = set()
    for item in values:
        token = str(item or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _default_connector_class_for_id(connector_id: str) -> str:
    token = _normalize_connector_token(connector_id)
    if token in {"browser_automation", "remote_browser"}:
        return CONNECTOR_CLASS_BROWSER
    if token in {"openai_image", "openai_video", "replicate_image", "runway_video", "veo_video"}:
        return CONNECTOR_CLASS_MEDIA
    return CONNECTOR_CLASS_API


def connector_contract(connector_id: str) -> Dict[str, Any]:
    token = _normalize_connector_token(connector_id)
    if not token:
        raise HTTPException(status_code=400, detail="connector_id is required.")
    catalog_entry = CONNECTOR_CATALOG.get(token) or CHANNEL_REGISTRY.get(token) or {}
    override = dict(_CONNECTOR_CONTRACT_OVERRIDES.get(token, {}))
    connector_class = _normalize_connector_class(override.get("connector_class") or _default_connector_class_for_id(token))
    class_defaults = _CONNECTOR_CLASS_DEFAULTS[connector_class]
    label = str(override.get("label") or catalog_entry.get("label") or token.replace("_", " ").title()).strip()
    surface_role = str(
        override.get("surface_role")
        or (
            CONNECTOR_SURFACE_CHANNEL_SHELL
            if token in {"telegram_bot", "whatsapp_twilio", "slack", "discord_bot", "wechat_work"}
            else CONNECTOR_SURFACE_DEEP_APP
        )
    ).strip() or CONNECTOR_SURFACE_DEEP_APP
    preferred_execution = _normalize_connector_class(override.get("preferred_execution") or connector_class)
    fallback_connectors = _ordered_unique_tokens(list(class_defaults.get("fallback_connectors") or []) + list(override.get("fallback_connectors") or []))
    capability_patterns = _ordered_unique_tokens(list(class_defaults.get("capability_patterns") or []) + list(override.get("capability_patterns") or []))
    allowed_secret_fields = _ordered_unique_tokens(
        list(override.get("allowed_secret_fields") or [])
        or list(catalog_entry.get("auth") or [])
        or list(class_defaults.get("allowed_secret_fields") or [])
    )
    action_contracts = {
        str(action_id).strip().lower(): dict(payload)
        for action_id, payload in dict(override.get("actions") or {}).items()
        if str(action_id).strip()
    }
    return {
        "connector_id": token,
        "label": label,
        "connector_class": connector_class,
        "surface_role": surface_role,
        "preferred_execution": preferred_execution,
        "fallback_connectors": fallback_connectors,
        "capability_patterns": capability_patterns,
        "allowed_secret_fields": allowed_secret_fields,
        "requires_brokered_runtime_for_agent_execution": True,
        "allows_human_admin_route": True,
        "actions": action_contracts,
    }


def connector_action_contract(connector_id: str, action_id: str) -> Dict[str, Any]:
    contract = connector_contract(connector_id)
    action_token = _normalize_connector_token(action_id).replace("-", "_")
    explicit = dict(contract.get("actions", {}).get(action_token) or {})
    if not explicit:
        if action_token.startswith(("browse", "list", "get", "read", "search", "fetch")):
            explicit = {"action_class": "read", "elevated": False}
        elif contract["connector_class"] == CONNECTOR_CLASS_BROWSER and action_token.startswith(
            ("navigate", "authenticate", "scrape", "form", "interactive", "upload", "click", "type")
        ):
            explicit = {"action_class": "execute", "elevated": True}
        elif contract["connector_class"] == CONNECTOR_CLASS_MEDIA:
            explicit = {"action_class": "execute", "elevated": True}
        else:
            explicit = {"action_class": "write", "elevated": True}
    action_class = str(explicit.get("action_class") or "read").strip().lower() or "read"
    capability_pattern = str(explicit.get("capability_pattern") or action_token).strip().lower() or action_token
    return {
        "connector_id": contract["connector_id"],
        "connector_class": contract["connector_class"],
        "surface_role": contract["surface_role"],
        "action_id": action_token,
        "action_class": action_class,
        "elevated": bool(explicit.get("elevated", action_class != "read")),
        "capability_pattern": capability_pattern,
    }


def _normalized_execution_context(execution_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = dict(execution_context or {})
    execution_path = _normalize_connector_token(payload.get("execution_path"))
    if execution_path not in SUPPORTED_CONNECTOR_EXECUTION_PATHS:
        execution_path = ""
    payload["execution_path"] = execution_path or None
    return payload


def _authorize_connector_execution(
    *,
    connector_id: str,
    action_id: str,
    workspace_id: Optional[str],
    execution_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    contract = connector_contract(connector_id)
    action = connector_action_contract(connector_id, action_id)
    context = _normalized_execution_context(execution_context)
    execution_path = str(context.get("execution_path") or "").strip().lower()
    if execution_path == EXECUTION_PATH_HUMAN_ADMIN_ROUTE:
        return {
            "contract": contract,
            "action": action,
            "execution_path": execution_path,
            "broker_claims": None,
            "context": context,
        }
    if execution_path == EXECUTION_PATH_BROKERED_RUNTIME:
        required = {
            "capability_token": str(context.get("capability_token") or "").strip(),
            "manifest_id": str(context.get("manifest_id") or "").strip(),
            "tenant_id": str(context.get("tenant_id") or "").strip(),
            "workspace_id": str(context.get("workspace_id") or workspace_id or "").strip(),
            "runtime_mode": str(context.get("runtime_mode") or "").strip(),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"brokered runtime connector execution is missing required context: {', '.join(missing)}.",
            )
        try:
            claims = tool_broker.authorize_connector_action(
                capability_token=required["capability_token"],
                manifest_id=required["manifest_id"],
                tenant_id=required["tenant_id"],
                workspace_id=required["workspace_id"],
                runtime_mode=required["runtime_mode"],
                connector_scope=contract["connector_id"],
                connector_class=contract["connector_class"],
                action_class=action["action_class"],
                elevated=action["elevated"],
            )
        except tool_broker.ToolExecutionDeniedError as exc:
            raise HTTPException(status_code=403, detail=exc.detail) from exc
        return {
            "contract": contract,
            "action": action,
            "execution_path": execution_path,
            "broker_claims": claims,
            "context": context,
        }
    raise HTTPException(
        status_code=403,
        detail="Direct connector execution is not allowed. Use an authenticated owner route or a brokered runtime path.",
    )


def _resolve_connector_secret_for_execution(
    *,
    credential_id: str,
    connector_id: str,
    action_id: str,
    workspace_id: Optional[str],
    execution_context: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    authorization = _authorize_connector_execution(
        connector_id=connector_id,
        action_id=action_id,
        workspace_id=workspace_id,
        execution_context=execution_context,
    )
    contract = authorization["contract"]
    context = authorization["context"]
    resolved_workspace_id = str(context.get("workspace_id") or workspace_id or "").strip() or workspace_id
    actor_type = str(context.get("actor_type") or ("user" if authorization["execution_path"] == EXECUTION_PATH_HUMAN_ADMIN_ROUTE else "runtime")).strip().lower()
    actor_id = str(context.get("actor_id") or "").strip() or None
    try:
        secret = secrets_broker.resolve_connector_secret(
            load_vault,
            _openssl_decrypt,
            tenant_id=str(context.get("tenant_id") or "").strip() or None,
            workspace_id=resolved_workspace_id,
            credential_id=credential_id,
            connector_id=contract["connector_id"],
            connector_class=contract["connector_class"],
            action_id=action_id,
            tool_name=str(context.get("tool_name") or f"{contract['connector_id']}.{action_id}").strip().lower(),
            run_id=str(context.get("run_id") or "").strip() or None,
            runtime_mode=str(context.get("runtime_mode") or "").strip() or None,
            allowed_fields=list(contract.get("allowed_secret_fields") or []),
            actor_type=actor_type,
            actor_id=actor_id,
            purpose=str(context.get("purpose") or "connector_action_execution").strip().lower() or "connector_action_execution",
        )
    except secrets_broker.SecretAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=exc.detail) from exc
    return authorization, secret

async def browse_microsoft_connector_drive(
    connector_id: str,
    workspace_id: Optional[str] = None,
    path: Optional[str] = None,
    execution_context: Optional[Dict[str, Any]] = None,
):
    try:
        _, secret = _resolve_connector_secret_for_execution(
            credential_id=connector_id,
            connector_id="microsoft_365",
            action_id="browse_drive",
            workspace_id=workspace_id,
            execution_context=execution_context,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if str(secret.get("_provider") or "").strip() != "microsoft_365":
        raise HTTPException(status_code=400, detail="Connector is not Microsoft 365.")

    try:
        listing = microsoft_365_list_drive_children(
            secret,
            http_json_request,
            path=str(path or ""),
            top=50,
        )
        return {
            "ok": True,
            "path": listing.get("path") or "onedrive:/",
            "items": listing.get("items") if isinstance(listing.get("items"), list) else [],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

# DEPRECATED: replaced by MCP pipeline. Remove after 30-day stability period.
async def browse_google_connector_drive(
    connector_id: str,
    workspace_id: Optional[str] = None,
    path: Optional[str] = None,
    execution_context: Optional[Dict[str, Any]] = None,
):
    import logging
    logging.getLogger(__name__).warning(
        "browse_google_connector_drive is DEPRECATED — use MCP pipeline (google-drive MCP server) instead."
    )
    try:
        _, secret = _resolve_connector_secret_for_execution(
            credential_id=connector_id,
            connector_id="google_workspace",
            action_id="browse_drive",
            workspace_id=workspace_id,
            execution_context=execution_context,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if str(secret.get("_provider") or "").strip() != "google_workspace":
        raise HTTPException(status_code=400, detail="Connector is not Google Workspace.")

    try:
        listing = google_workspace_list_drive_children(
            secret,
            path=str(path or ""),
            top=50,
        )
        return {
            "ok": True,
            "path": listing.get("path") or "gdrive:/",
            "items": listing.get("items") if isinstance(listing.get("items"), list) else [],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

# DEPRECATED: replaced by MCP pipeline. Remove after 30-day stability period.
async def create_google_connector_document(
    connector_id: str,
    body: Optional[ConnectorDocumentCreateRequest] = None,
    workspace_id: Optional[str] = None,
    execution_context: Optional[Dict[str, Any]] = None,
):
    import logging
    logging.getLogger(__name__).warning(
        "create_google_connector_document is DEPRECATED — use MCP pipeline (google-drive MCP server) instead."
    )
    try:
        _, secret = _resolve_connector_secret_for_execution(
            credential_id=connector_id,
            connector_id="google_workspace",
            action_id="create_document",
            workspace_id=workspace_id,
            execution_context=execution_context,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if str(secret.get("_provider") or "").strip() != "google_workspace":
        raise HTTPException(status_code=400, detail="Connector is not Google Workspace.")

    payload = {}
    if body is not None:
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    title = str(payload.get("title") or "").strip() or f"Empyralis Doc {_utc_now().strftime('%Y-%m-%d %H:%M')}"
    try:
        created = google_workspace_create_document(secret, title)
        document_id = str(created.get("documentId") or "").strip()
        web_url = f"https://docs.google.com/document/d/{document_id}/edit" if document_id else None
        return {
            "ok": True,
            "title": title,
            "documentId": document_id or None,
            "webUrl": web_url,
            "raw": created,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

# DEPRECATED: replaced by MCP pipeline. Remove after 30-day stability period.
async def create_google_connector_spreadsheet(
    connector_id: str,
    body: Optional[ConnectorSpreadsheetCreateRequest] = None,
    workspace_id: Optional[str] = None,
    execution_context: Optional[Dict[str, Any]] = None,
):
    import logging
    logging.getLogger(__name__).warning(
        "create_google_connector_spreadsheet is DEPRECATED — use MCP pipeline (google-drive MCP server) instead."
    )
    try:
        _, secret = _resolve_connector_secret_for_execution(
            credential_id=connector_id,
            connector_id="google_workspace",
            action_id="create_spreadsheet",
            workspace_id=workspace_id,
            execution_context=execution_context,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if str(secret.get("_provider") or "").strip() != "google_workspace":
        raise HTTPException(status_code=400, detail="Connector is not Google Workspace.")

    payload = {}
    if body is not None:
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    title = str(payload.get("title") or "").strip() or f"Empyralis Sheet {_utc_now().strftime('%Y-%m-%d %H:%M')}"
    try:
        created = google_workspace_create_spreadsheet(secret, title)
        spreadsheet_id = str(created.get("spreadsheetId") or "").strip()
        web_url = str(created.get("spreadsheetUrl") or "").strip() or (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit" if spreadsheet_id else ""
        )
        return {
            "ok": True,
            "title": title,
            "spreadsheetId": spreadsheet_id or None,
            "webUrl": web_url or None,
            "raw": created,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

async def whatsapp_twilio_webhook(request: Request):
    return await handle_whatsapp_twilio_webhook(request)

async def telegram_webhook(request: Request, connector_id: str):
    return await handle_telegram_webhook(request, connector_id)


def _canonical_route_error_response(error: Exception, error_response):
    from server_modules import agent_channel_router

    if isinstance(error, agent_channel_router.ChannelIngressValidationError):
        return error_response(400, str(error))
    if isinstance(error, agent_channel_router.ChannelOwnerNotFoundError):
        return error_response(404, str(error))
    if isinstance(error, agent_channel_router.ChannelSecurityDeniedError):
        return error_response(403, str(error))
    raise error


async def _resolve_connector_tenant_id(entry: Dict[str, Any], workspace_id: str) -> str:
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    for candidate in (entry.get("tenant_id"), metadata.get("tenant_id")):
        token = str(candidate or "").strip()
        if token:
            return token
    from server_modules import control_plane_repository

    workspace = await control_plane_repository.get_workspace_by_id(str(workspace_id or "").strip())
    tenant_id = str((workspace or {}).get("tenant_id") or "").strip()
    if tenant_id:
        return tenant_id
    raise RuntimeError("Connector is not scoped to a tenant.")


def _resolve_channel_endpoint_key(
    *,
    entry: Dict[str, Any],
    channel_key: str,
    connector_id: str,
    fallback: str = "",
) -> str:
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    bindings = metadata.get("channel_registry_bindings") if isinstance(metadata.get("channel_registry_bindings"), dict) else {}
    binding = bindings.get(channel_key) if isinstance(bindings.get(channel_key), dict) else {}
    candidates = (
        binding.get("endpoint_key"),
        metadata.get(f"{channel_key}_endpoint_key"),
        fallback,
        connector_id,
        channel_key,
    )
    for candidate in candidates:
        token = str(candidate or "").strip()
        if token:
            return token
    return channel_key


async def telegram_webhook_canonical(request: Request, connector_id: str):
    from server_modules import agent_channel_router
    from server_modules.connectors.autopilot_runtime_exports import _autopilot_connector_shell_service

    shell = _autopilot_connector_shell_service()
    shell.runtime_facade_service().init_runtime()
    bridge = shell.bridge_facade_service()
    header_secret = str(
        request.headers.get("x-telegram-bot-api-secret-token")
        or request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        or ""
    ).strip()
    auth_result = shell.autopilot_endpoint_service().telegram_webhook_auth_result(
        enabled=bridge.telegram_enabled_getter(),
        delivery_mode=bridge.telegram_delivery_mode_getter(),
        configured_secret=bridge.telegram_configured_webhook_secret_getter(),
        header_secret=header_secret,
    )
    if int(auth_result.get("status_code") or 200) != 200:
        return bridge.error_response(
            int(auth_result.get("status_code") or 500),
            str(auth_result.get("content") or "forbidden"),
        )

    registry = shell.telegram_service_registry()
    ingress = registry.telegram_ingress_service()
    connector_token = str(connector_id or "").strip()

    try:
        update = ingress.parse_update(await request.body())
        connector_entry = ingress.get_connector_entry(connector_token)
        workspace_id = ingress.resolve_workspace_scope(
            connector_entry,
            ingress.default_workspace_id,
            "Telegram connector",
        )
        profile = ingress.resolve_profile(connector_entry)
        secret = ingress.resolve_secret(connector_entry)
        bot_token = str(secret.get("bot_token") or "").strip()
        configured_chat_id = str(secret.get("chat_id") or "").strip()
        if not bot_token or not configured_chat_id:
            raise RuntimeError("Connector is missing bot_token or chat_id.")

        envelope = ingress.normalize_telegram_update(
            source="webhook",
            entry=connector_entry,
            connector_id=connector_token,
            workspace_id=workspace_id,
            profile=profile,
            bot_token=bot_token,
            configured_chat_id=configured_chat_id,
            update=update,
        )
        if not bool(envelope.get("handled")):
            return {"ok": True, **envelope}

        dispatch_result = ingress._dispatch_public_deployed_agent_envelope(
            entry=connector_entry,
            workspace_id=workspace_id,
            connector_id=connector_token,
            bot_token=bot_token,
            configured_chat_id=configured_chat_id,
            profile=profile,
            envelope=envelope,
        )
        return {"ok": True, **(dispatch_result if isinstance(dispatch_result, dict) else {})}
    except LookupError as exc:
        return bridge.error_response(404, str(exc))
    except ValueError as exc:
        return bridge.error_response(400, str(exc))
    except RuntimeError as exc:
        return bridge.error_response(503, str(exc))
    except Exception as exc:
        return _canonical_route_error_response(exc, bridge.error_response)

async def telegram_autopilot_status():
    return await handle_telegram_autopilot_status()

async def whatsapp_autopilot_status():
    return await handle_whatsapp_autopilot_status()

async def list_autopilot_profiles():
    return await handle_list_autopilot_profiles()

async def telegram_send_message(body: TelegramSendRequest):
    body.validate_fields()
    try:
        return await handle_telegram_send_message(
            text=body.text,
            workspace_id=body.workspace_id,
            session_key=body.session_key,
            chat_id=body.chat_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

async def telegram_autopilot_test_message(body: TelegramAutopilotTestRequest):
    body.validate_fields()
    try:
        return await handle_telegram_autopilot_test_message(
            text=body.text,
            workspace_id=body.workspace_id,
            session_key=body.session_key,
            chat_id=body.chat_id,
            connector_id=body.connector_id,
            sender_id=body.sender_id,
            timeout_seconds=body.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _telegram_webhook_base_url() -> str:
    return str(os.getenv("ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL") or "").strip().rstrip("/")


def _telegram_connector_webhook_url(connector_id: str) -> str:
    base_url = _telegram_webhook_base_url()
    if not base_url:
        return ""
    return f"{base_url}/channels/telegram/webhook/{str(connector_id or '').strip()}"


def _telegram_webhook_registration_result(
    *,
    connector_id: str,
    credentials: Dict[str, Any],
) -> Dict[str, Any]:
    checked_at = _utc_now_iso()
    delivery_mode = str(ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE or "polling").strip().lower() or "polling"
    webhook_url = _telegram_connector_webhook_url(connector_id)
    if delivery_mode != "webhook":
        return {
            "status": "skipped",
            "webhook_url": webhook_url or None,
            "checked_at": checked_at,
            "registered_at": None,
            "last_error": None,
            "delivery_mode": delivery_mode,
        }
    secret_token = str(ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET or "").strip()
    bot_token = str(credentials.get("bot_token") or credentials.get("token") or "").strip()
    if not webhook_url:
        return {
            "status": "failed",
            "webhook_url": None,
            "checked_at": checked_at,
            "registered_at": None,
            "last_error": "ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL is not configured.",
            "delivery_mode": delivery_mode,
        }
    if not secret_token:
        return {
            "status": "failed",
            "webhook_url": webhook_url,
            "checked_at": checked_at,
            "registered_at": None,
            "last_error": "ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET is not configured.",
            "delivery_mode": delivery_mode,
        }
    if not bot_token:
        return {
            "status": "failed",
            "webhook_url": webhook_url,
            "checked_at": checked_at,
            "registered_at": None,
            "last_error": "Telegram bot token is missing.",
            "delivery_mode": delivery_mode,
        }
    try:
        set_result = http_json_request(
            f"https://api.telegram.org/bot{bot_token}/setWebhook",
            method="POST",
            payload={
                "url": webhook_url,
                "secret_token": secret_token,
                "allowed_updates": ["message"],
            },
            timeout=20,
        )
        set_body = set_result.get("json") if isinstance(set_result.get("json"), dict) else {}
        if int(set_result.get("status") or 500) >= 400 or set_body.get("ok") is False:
            detail = str(set_body.get("description") or set_result.get("text") or "Telegram setWebhook failed.").strip()
            raise RuntimeError(detail)
        info_result = http_json_request(
            f"https://api.telegram.org/bot{bot_token}/getWebhookInfo",
            method="GET",
            timeout=20,
        )
        info_body = info_result.get("json") if isinstance(info_result.get("json"), dict) else {}
        info_payload = info_body.get("result") if isinstance(info_body.get("result"), dict) else {}
        actual_url = str(info_payload.get("url") or "").strip()
        if int(info_result.get("status") or 500) >= 400 or info_body.get("ok") is False:
            detail = str(info_body.get("description") or info_result.get("text") or "Telegram getWebhookInfo failed.").strip()
            raise RuntimeError(detail)
        if actual_url and actual_url != webhook_url:
            raise RuntimeError(f"Telegram webhook URL mismatch: {actual_url}")
        registered_at = _utc_now_iso()
        return {
            "status": "registered",
            "webhook_url": webhook_url,
            "checked_at": registered_at,
            "registered_at": registered_at,
            "last_error": None,
            "delivery_mode": delivery_mode,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "webhook_url": webhook_url,
            "checked_at": _utc_now_iso(),
            "registered_at": None,
            "last_error": str(exc).strip() or "Telegram webhook registration failed.",
            "delivery_mode": delivery_mode,
        }


def _persist_connector_metadata_patch(credential_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    vault = load_vault()
    items = vault.get("credentials", [])
    if not isinstance(items, list):
        return None
    updated_entry: Optional[Dict[str, Any]] = None
    next_items: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            next_items.append(item)
            continue
        if str(item.get("id") or "").strip() != str(credential_id or "").strip():
            next_items.append(item)
            continue
        next_item = dict(item)
        metadata = dict(next_item.get("metadata") if isinstance(next_item.get("metadata"), dict) else {})
        metadata.update(patch)
        next_item["metadata"] = _sanitize_connector_metadata(metadata)
        next_item["updated_at"] = _utc_now_iso()
        updated_entry = next_item
        next_items.append(next_item)
    if updated_entry is None:
        return None
    vault["credentials"] = next_items
    save_vault(vault)
    return updated_entry


def _register_telegram_webhook_for_connector(
    *,
    connector_id: str,
    credentials: Dict[str, Any],
) -> Dict[str, Any]:
    registration = _telegram_webhook_registration_result(
        connector_id=connector_id,
        credentials=credentials,
    )
    _persist_connector_metadata_patch(
        connector_id,
        {TELEGRAM_WEBHOOK_REGISTRATION_METADATA_KEY: registration},
    )
    return registration


def _delete_telegram_webhook_best_effort(credentials: Dict[str, Any]) -> None:
    bot_token = str(credentials.get("bot_token") or credentials.get("token") or "").strip()
    if not bot_token:
        return
    try:
        http_json_request(
            f"https://api.telegram.org/bot{bot_token}/deleteWebhook",
            method="POST",
            payload={"drop_pending_updates": False},
            timeout=15,
        )
    except Exception:
        return


def _upsert_slack_oauth_connector_entry(
    *,
    workspace_id: Optional[str],
    label: str,
    credentials: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
    test_result: Dict[str, Any],
) -> Dict[str, Any]:
    now = _utc_now_iso()
    connector = "slack"
    safe_metadata = dict(metadata or {})
    team = test_result.get("team") if isinstance(test_result.get("team"), dict) else {}
    bot = test_result.get("bot") if isinstance(test_result.get("bot"), dict) else {}
    authed_user = test_result.get("authed_user") if isinstance(test_result.get("authed_user"), dict) else {}
    connector_metadata = {
        **_connector_public_metadata(connector, credentials),
        **safe_metadata,
    }
    connector_metadata["capability_verification"] = capability_verification_metadata(connector, test_result)
    if str(team.get("id") or "").strip():
        connector_metadata["team_id"] = str(team.get("id")).strip()
    if str(team.get("name") or "").strip():
        connector_metadata["team_name"] = str(team.get("name")).strip()
    if str(bot.get("user_id") or "").strip():
        connector_metadata["bot_user_id"] = str(bot.get("user_id")).strip()
    if str(bot.get("bot_status") or "").strip():
        connector_metadata["bot_status"] = str(bot.get("bot_status")).strip()
    if str(authed_user.get("id") or "").strip():
        connector_metadata["authed_user_id"] = str(authed_user.get("id")).strip()

    vault = load_vault()
    items = vault.get("credentials", [])
    if not isinstance(items, list):
        items = []
    duplicate = _find_duplicate_connector_entry(connector, credentials, workspace_id)
    entry_id = str((duplicate or {}).get("id") or uuid.uuid4()).strip()
    created_at = str((duplicate or {}).get("created_at") or now).strip() or now
    found = False
    next_items: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            next_items.append(item)
            continue
        if str(item.get("id") or "").strip() != entry_id:
            next_items.append(item)
            continue
        found = True
        next_items.append(
            {
                **item,
                "label": label.strip() or str(item.get("label") or "Slack").strip() or "Slack",
                "provider": connector,
                "workspace_id": _normalize_workspace_id(workspace_id),
                "mode": "connector",
                "metadata": _sanitize_connector_metadata(connector_metadata),
                "created_at": created_at,
                "updated_at": now,
                "encrypted_secret": _openssl_encrypt(json.dumps(credentials, separators=(",", ":"))),
            }
        )
    if not found:
        next_items.append(
            {
                "id": entry_id,
                "label": label.strip() or str(team.get("name") or "Slack").strip() or "Slack",
                "provider": connector,
                "workspace_id": _normalize_workspace_id(workspace_id),
                "mode": "connector",
                "metadata": _sanitize_connector_metadata(connector_metadata),
                "created_at": created_at,
                "updated_at": now,
                "encrypted_secret": _openssl_encrypt(json.dumps(credentials, separators=(",", ":"))),
            }
        )
    vault["credentials"] = next_items
    save_vault(vault)

    return {
        "id": entry_id,
        "label": label.strip() or str(team.get("name") or "Slack").strip() or "Slack",
        "connector": connector,
        "workspace_id": _normalize_workspace_id(workspace_id),
        "metadata": _sanitize_connector_metadata(connector_metadata),
        "created_at": created_at,
        "updated_at": now,
        "test": test_result,
    }


async def slack_oauth_callback(request: Request, current_user: Optional[Dict[str, Any]] = None):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    code = str(payload.get("code") or "").strip()
    redirect_uri = str(payload.get("redirect_uri") or "").strip()
    workspace_id = _normalize_workspace_id(payload.get("workspace_id"))
    label = str(payload.get("label") or "Slack").strip() or "Slack"
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if not code:
        raise HTTPException(status_code=400, detail="Slack OAuth code is required.")
    if not redirect_uri:
        raise HTTPException(status_code=400, detail="Slack redirect_uri is required.")
    _assert_slack_redirect_uri_allowed(redirect_uri)
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")
    if current_user is not None:
        from server_modules.auth import enforce_workspace_access

        # Single-user platform — session IS ownership. Viewer role always passes.
        workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )

    try:
        exchange = slack_exchange_oauth_code(code, redirect_uri, http_json_request=http_json_request)
        credentials = exchange.get("credentials") if isinstance(exchange.get("credentials"), dict) else {}
        test = validate_slack_connector(credentials)
        persisted_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        result = _upsert_slack_oauth_connector_entry(
            workspace_id=workspace_id,
            label=label,
            credentials=persisted_credentials,
            metadata=metadata,
            test_result=test,
        )
        return {"ok": True, **result}
    except HTTPException:
        raise
    except Exception as exc:
        from server_modules import agent_channel_router

        if isinstance(exc, agent_channel_router.ChannelIngressValidationError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if isinstance(exc, agent_channel_router.ChannelOwnerNotFoundError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, agent_channel_router.ChannelSecurityDeniedError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc))


async def slack_events_webhook(request: Request):
    raw_body = await request.body()
    headers = dict(request.headers.items())
    try:
        if not slack_verify_request_signature(headers, raw_body):
            raise HTTPException(status_code=401, detail="Slack request signature is invalid.")
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        parsed = slack_parse_inbound_event(payload if isinstance(payload, dict) else {})
        if parsed.get("kind") == "url_verification":
            return {"challenge": str(parsed.get("challenge") or "").strip()}

        if parsed.get("kind") == "event":
            append_fn = globals().get("_append_channel_event")
            from server_modules import agent_channel_router

            channel_id = str(parsed.get("channel") or "").strip() or "slack"
            vault = load_vault()
            items = vault.get("credentials", [])
            if not isinstance(items, list):
                items = []
            slack_rows = [
                item
                for item in items
                if isinstance(item, dict) and str(item.get("provider") or "").strip().lower() == "slack"
            ]
            handled = 0
            triggered = 0
            triggered_run_id = ""
            for item in slack_rows:
                row_id = str(item.get("id") or "").strip()
                if not row_id:
                    continue
                workspace_id = _normalize_workspace_id(item.get("workspace_id"))
                try:
                    secret = resolve_vault_credential(row_id, workspace_id)
                except Exception:
                    secret = {}
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                if not slack_event_matches_connector(parsed, secret, metadata):
                    continue
                handled += 1
                trace_token = str(parsed.get("event_id") or parsed.get("message_ts") or parsed.get("ts") or "").strip() or uuid.uuid4().hex
                message_id = str(parsed.get("message_ts") or parsed.get("ts") or parsed.get("event_id") or "").strip()
                session_key = str(parsed.get("thread_ts") or parsed.get("channel") or "").strip() or "slack"
                if callable(append_fn):
                    append_fn(
                        channel="slack",
                        direction="inbound",
                        event_type=str(parsed.get("event_type") or parsed.get("message_type") or "event"),
                        text=str(parsed.get("text") or parsed.get("reaction") or "").strip() or None,
                        workspace_id=workspace_id,
                        session_key=session_key,
                        message_id=message_id or None,
                        trace_id=f"slack:{trace_token}",
                        metadata=parsed,
                    )
                if not slack_should_trigger_agent_run(parsed, secret, metadata=metadata):
                    continue
                goal = slack_build_run_goal_from_event(parsed)
                if not goal:
                    continue
                route_result = await agent_channel_router.route_inbound_channel_message(
                    tenant_id=await _resolve_connector_tenant_id(item, workspace_id),
                    workspace_id=workspace_id,
                    channel_key="slack",
                    endpoint_key=_resolve_channel_endpoint_key(
                        entry=item,
                        channel_key="slack",
                        connector_id=row_id,
                        fallback=channel_id,
                    ),
                    customer_message=goal,
                    session_key=session_key,
                    message_id=message_id or None,
                    actor_id=str(parsed.get("user_id") or "").strip() or None,
                    actor_display_name=str(parsed.get("user_id") or "").strip() or None,
                    metadata={
                        "connector_id": row_id,
                        "delivery_source": "webhook",
                        "slack_team_id": str(parsed.get("team_id") or "").strip() or None,
                        "slack_channel_id": channel_id,
                        "slack_thread_ts": str(parsed.get("thread_ts") or "").strip() or None,
                        "slack_message_ts": message_id or None,
                        "source_event_id": trace_token,
                    },
                    allow_master_fallback=False,
                )
                route_payload = route_result if isinstance(route_result, dict) else {}
                if str(route_payload.get("run_id") or "").strip():
                    triggered += 1
                    if not triggered_run_id:
                        triggered_run_id = str(route_payload.get("run_id") or "").strip()
            return {"ok": True, "handled": handled, "triggered": triggered, "run_id": triggered_run_id or None}
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _discord_candidate_public_keys(rows: List[Dict[str, Any]]) -> List[str]:
    candidates: List[str] = []
    env_public_key = str(os.getenv("DISCORD_APP_PUBLIC_KEY") or "").strip()
    if env_public_key:
        candidates.append(env_public_key)
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            secret = resolve_vault_credential(str(row.get("id") or "").strip(), _normalize_workspace_id(row.get("workspace_id")))
        except Exception:
            continue
        for key in (
            str(secret.get("public_key") or "").strip(),
            str(secret.get("application_public_key") or "").strip(),
        ):
            if key and key not in candidates:
                candidates.append(key)
    return candidates


def _github_candidate_webhook_secrets(rows: List[Dict[str, Any]]) -> List[str]:
    secrets: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("id") or "").strip()
        if not row_id:
            continue
        try:
            credentials = resolve_vault_credential(row_id, _normalize_workspace_id(row.get("workspace_id")))
        except Exception:
            continue
        webhook_secret = str(credentials.get("webhook_secret") or "").strip()
        if webhook_secret and webhook_secret not in secrets:
            secrets.append(webhook_secret)
    return secrets


async def discord_webhook(request: Request):
    raw_body = await request.body()
    headers = dict(request.headers.items())
    try:
        verification_headers = discord_interaction_signature_headers(headers)
        if not verification_headers["signature"] or not verification_headers["timestamp"]:
            raise HTTPException(status_code=401, detail="Discord request signature headers are required.")

        vault = load_vault()
        items = vault.get("credentials", [])
        if not isinstance(items, list):
            items = []
        discord_rows = [
            item
            for item in items
            if isinstance(item, dict) and str(item.get("provider") or "").strip().lower() == "discord_bot"
        ]
        public_keys = _discord_candidate_public_keys(discord_rows)
        if not public_keys:
            raise HTTPException(status_code=503, detail="Discord interaction public key is not configured.")
        if not any(discord_verify_interaction_signature(headers, raw_body, key) for key in public_keys):
            raise HTTPException(status_code=401, detail="Discord request signature is invalid.")

        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(payload, dict):
            payload = {}

        parsed = discord_parse_inbound_event(
            payload,
            event_type=str(headers.get("x-discord-event") or headers.get("X-Discord-Event") or "").strip(),
        )
        if parsed.get("kind") == "ping":
            return {"type": 1}

        append_fn = globals().get("_append_channel_event")
        from server_modules import agent_channel_router

        handled = 0
        triggered = 0
        triggered_run_id = ""
        triggered_reply = ""
        for row in discord_rows:
            row_id = str(row.get("id") or "").strip()
            if not row_id:
                continue
            workspace_id = _normalize_workspace_id(row.get("workspace_id"))
            try:
                secret = resolve_vault_credential(row_id, workspace_id)
            except Exception:
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if not discord_event_matches_connector(parsed, secret, metadata):
                continue
            handled += 1
            trace_id = (
                str(parsed.get("interaction_id") or parsed.get("message_id") or "").strip()
                or uuid.uuid4().hex
            )
            session_key = (
                str(parsed.get("channel_id") or "").strip()
                or str(parsed.get("guild_id") or "").strip()
                or "discord"
            )
            if callable(append_fn):
                append_fn(
                    channel="discord",
                    direction="inbound",
                    event_type=str(parsed.get("event_type") or parsed.get("message_type") or "event"),
                    text=str(parsed.get("text") or parsed.get("reaction") or "").strip() or None,
                    workspace_id=workspace_id,
                    session_key=session_key,
                    message_id=str(parsed.get("message_id") or parsed.get("interaction_id") or "").strip() or None,
                    trace_id=f"discord:{trace_id}",
                    metadata=parsed,
                )
            if not discord_should_trigger_agent_run(parsed, secret, metadata=metadata):
                continue
            goal = discord_build_run_goal_from_event(parsed)
            if not goal:
                continue

            # ── Deduplication guard ──────────────────────────────────────
            # Uses interaction_id (for Interactions) or message_id (for
            # MESSAGE_CREATE events forwarded through this endpoint).
            # Discord retries Interactions with the same ID on timeout,
            # so this prevents double-processing.
            _dedup_msg_id = str(parsed.get("interaction_id") or parsed.get("message_id") or "").strip()
            _dedup_origin = "discord_personal" if str(parsed.get("message_type") or "").strip().lower() == "direct_message" else "discord_guild"
            if _dedup_msg_id:
                from server_modules.connectors.discord_connector import _is_duplicate_discord_message
                if _is_duplicate_discord_message(_dedup_msg_id, _dedup_origin):
                    import logging as _dd_log
                    _dd_log.getLogger(__name__).info(
                        "Discord webhook dedup: skipping duplicate id=%s origin=%s",
                        _dedup_msg_id, _dedup_origin,
                    )
                    continue

            # ── DM routing through unified Sage ingress ──
            # NOTE: This block handles MESSAGE_CREATE events with
            # message_type="direct_message".  It is *not* reachable via the
            # standard Discord Interactions endpoint (which only sends type
            # 2/3/5 payloads).  It is kept as a forward-compatible path in
            # case a Gateway-event-forwarding proxy or future Discord feature
            # delivers regular message events to this webhook.
            # The canonical DM path is _handle_dm_via_gateway() in
            # discord_connector.py (Path C).
            _message_type = str(parsed.get("message_type") or "").strip().lower()
            if _message_type == "direct_message":
                try:
                    from server_modules.sage_command_dispatcher import dispatch_command
                    from server_modules.sage_turn_adapter import execute_sage_turn
                    from server_modules.channel_adapter import filter_outbound_reply

                    _dm_user_id = str(parsed.get("user_id") or "").strip()
                    _dm_text = str(parsed.get("text") or "").strip()
                    if _dm_user_id and _dm_text:
                        # Shared command dispatcher first
                        _cmd_reply = await dispatch_command(
                            command=_dm_text,
                            workspace_id=workspace_id,
                            thread_id="sage-main",
                            channel_origin="discord_personal",
                            sender_id=_dm_user_id or None,
                        )
                        if _cmd_reply is not None:
                            discord_send_dm(
                                credentials=dict(secret),
                                user_id=_dm_user_id,
                                content=_cmd_reply,
                            )
                            triggered += 1
                            continue
                        # Route through unified Sage ingress
                        _sage_result = await execute_sage_turn(
                            workspace_id=workspace_id,
                            message=_dm_text,
                            channel_origin="discord_personal",
                            channel_sender_id=_dm_user_id,
                            channel_sender_name=str(parsed.get("username") or "").strip() or None,
                        )
                        _sage_reply = str(_sage_result.message or "").strip()
                        if _sage_reply:
                            _filtered = filter_outbound_reply(_sage_reply)
                            if _filtered:
                                discord_send_dm(
                                    credentials=dict(secret),
                                    user_id=_dm_user_id,
                                    content=_filtered,
                                )
                                triggered += 1
                except Exception:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "Discord webhook DM Sage ingress failed", exc_info=True
                    )
                continue
            route_result = await agent_channel_router.route_inbound_channel_message(
                tenant_id=await _resolve_connector_tenant_id(row, workspace_id),
                workspace_id=workspace_id,
                channel_key="discord",
                endpoint_key=_resolve_channel_endpoint_key(
                    entry=row,
                    channel_key="discord",
                    connector_id=row_id,
                    fallback=str(parsed.get("channel_id") or parsed.get("guild_id") or "").strip(),
                ),
                customer_message=goal,
                session_key=session_key,
                message_id=str(parsed.get("message_id") or parsed.get("interaction_id") or "").strip() or None,
                actor_id=str(parsed.get("user_id") or "").strip() or None,
                actor_display_name=str(parsed.get("username") or "").strip() or None,
                metadata={
                    "connector_id": row_id,
                    "delivery_source": "webhook",
                    "discord_channel_id": str(parsed.get("channel_id") or "").strip() or None,
                    "discord_guild_id": str(parsed.get("guild_id") or "").strip() or None,
                    "discord_message_id": str(parsed.get("message_id") or "").strip() or None,
                    "discord_interaction_id": str(parsed.get("interaction_id") or "").strip() or None,
                    "source_event_id": str(parsed.get("message_id") or parsed.get("interaction_id") or "").strip() or None,
                },
                allow_master_fallback=False,
            )
            route_payload = route_result if isinstance(route_result, dict) else {}
            if str(route_payload.get("run_id") or "").strip():
                triggered += 1
                if not triggered_run_id:
                    triggered_run_id = str(route_payload.get("run_id") or "").strip()
                if not triggered_reply:
                    triggered_reply = str(route_payload.get("reply") or "").strip()

        if parsed.get("kind") == "interaction":
            if triggered_run_id:
                return {
                    "type": 4,
                    "data": {
                        "content": triggered_reply or f"Started run {triggered_run_id}.",
                        "flags": 64,
                    },
                }
            return {
                "type": 4,
                "data": {
                    "content": "Received.",
                    "flags": 64,
                },
            }
        return {"ok": True, "handled": handled, "triggered": triggered}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


async def github_events_webhook(request: Request):
    raw_body = await request.body()
    headers = dict(request.headers.items())
    try:
        vault = load_vault()
        items = vault.get("credentials", [])
        if not isinstance(items, list):
            items = []
        github_rows = [
            item
            for item in items
            if isinstance(item, dict) and str(item.get("provider") or "").strip().lower() == "github"
        ]
        secrets = _github_candidate_webhook_secrets(github_rows)
        if not secrets:
            raise HTTPException(status_code=503, detail="GitHub webhook is not configured: no webhook secret is available.")

        if not github_request_signature_header(headers):
            raise HTTPException(status_code=401, detail="GitHub webhook signature header is required.")

        if not any(github_verify_request_signature(headers, raw_body, secret) for secret in secrets):
            raise HTTPException(status_code=401, detail="GitHub webhook signature is invalid.")

        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(payload, dict):
            payload = {}
        parsed = github_parse_inbound_event(
            payload,
            event_type=str(headers.get("x-github-event") or headers.get("X-GitHub-Event") or "").strip(),
            delivery_id=str(headers.get("x-github-delivery") or headers.get("X-GitHub-Delivery") or "").strip(),
        )
        append_fn = globals().get("_append_channel_event")
        from server_modules import agent_channel_router

        handled = 0
        triggered = 0
        triggered_run_id = ""
        repository = str(parsed.get("repository") or "").strip() or "github"
        message_id = str(parsed.get("delivery_id") or parsed.get("after") or parsed.get("pull_request_number") or parsed.get("issue_number") or "").strip()
        for item in github_rows:
            row_id = str(item.get("id") or "").strip()
            if not row_id:
                continue
            workspace_id = _normalize_workspace_id(item.get("workspace_id"))
            try:
                secret = resolve_vault_credential(row_id, workspace_id)
            except Exception:
                secret = {}
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if not github_event_matches_connector(parsed, secret, metadata):
                continue
            handled += 1
            if callable(append_fn):
                append_fn(
                    channel="github",
                    direction="inbound",
                    event_type=str(parsed.get("event_type") or "event"),
                    text=(
                        str(parsed.get("head_commit") or parsed.get("title") or parsed.get("action") or "").strip()
                        or None
                    ),
                    workspace_id=workspace_id,
                    session_key=repository,
                    message_id=str(parsed.get("delivery_id") or parsed.get("after") or "").strip() or None,
                    trace_id=f"github:{str(parsed.get('delivery_id') or parsed.get('after') or uuid.uuid4()).strip()}",
                    metadata=parsed,
                )
            if not github_should_trigger_agent_run(parsed, secret, metadata=metadata):
                continue
            goal = github_build_run_goal_from_event(parsed)
            if not goal:
                continue
            route_result = await agent_channel_router.route_inbound_channel_message(
                tenant_id=await _resolve_connector_tenant_id(item, workspace_id),
                workspace_id=workspace_id,
                channel_key="github",
                endpoint_key=_resolve_channel_endpoint_key(
                    entry=item,
                    channel_key="github",
                    connector_id=row_id,
                    fallback=repository,
                ),
                customer_message=goal,
                session_key=repository,
                message_id=message_id or None,
                actor_id=str(parsed.get("sender") or "").strip() or None,
                actor_display_name=str(parsed.get("sender") or "").strip() or None,
                metadata={
                    "connector_id": row_id,
                    "delivery_source": "webhook",
                    "github_repository": repository,
                    "github_event_type": str(parsed.get("event_type") or "").strip() or None,
                    "github_delivery_id": str(parsed.get("delivery_id") or "").strip() or None,
                    "source_event_id": message_id or str(parsed.get("delivery_id") or "").strip() or None,
                },
                allow_master_fallback=False,
            )
            route_payload = route_result if isinstance(route_result, dict) else {}
            if str(route_payload.get("run_id") or "").strip():
                triggered += 1
                if not triggered_run_id:
                    triggered_run_id = str(route_payload.get("run_id") or "").strip()
        return {"ok": True, "handled": handled, "triggered": triggered, "run_id": triggered_run_id or None}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

async def create_connector_vault(body: ConnectorCreate):
    body.validate_fields()
    connector = body.connector.lower().strip()
    credentials = body.credentials

    # Default validation result for connectors without a dedicated validator
    test: Dict[str, Any] = {"ok": True, "status": "healthy", "message": "OAuth token stored."}

    try:
        # DEPRECATED: google_workspace validation replaced by MCP pipeline. Remove after 30-day stability period.
        if connector == "google_workspace":
            test = validate_google_workspace_connector(credentials)
        elif connector == "microsoft_365":
            test = validate_microsoft_365_connector(credentials)
        elif connector == "smtp":
            test = validate_smtp_connector(credentials)
        elif connector == "telegram_bot":
            test = validate_telegram_connector(credentials)
        elif connector == "wechat_work":
            test = validate_wechat_work_connector(credentials)
        elif connector == "whatsapp_twilio":
            test = validate_whatsapp_twilio_connector(credentials)
        elif connector == "discord_bot":
            test = validate_discord_bot_connector(credentials)
        elif connector == "slack":
            test = validate_slack_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "github":
            test = validate_github_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "dropbox":
            test = validate_dropbox_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "figma":
            test = validate_figma_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "todoist":
            test = validate_todoist_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "airtable":
            test = validate_airtable_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "canva":
            test = validate_canva_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "asana":
            test = validate_asana_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "hubspot":
            test = validate_hubspot_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "zoom":
            test = validate_zoom_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "calendly":
            test = validate_calendly_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "clickup":
            test = validate_clickup_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "jira":
            test = validate_jira_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "stripe":
            test = validate_stripe_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "salesforce":
            test = validate_salesforce_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "webflow":
            test = validate_webflow_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "monday":
            test = validate_monday_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "box":
            test = validate_box_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "gitlab":
            test = validate_gitlab_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "bitbucket":
            test = validate_bitbucket_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "confluence":
            test = validate_confluence_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "miro":
            test = validate_miro_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "mailchimp":
            test = validate_mailchimp_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "pipedrive":
            test = validate_pipedrive_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "intercom":
            test = validate_intercom_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "docusign":
            test = validate_docusign_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "square":
            test = validate_square_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "typeform":
            test = validate_typeform_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "quickbooks":
            test = validate_quickbooks_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "xero":
            test = validate_xero_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "freshbooks":
            test = validate_freshbooks_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "vercel":
            test = validate_vercel_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "s3":
            test = validate_s3_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "notion":
            test = validate_notion_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "linear":
            test = validate_linear_connector(credentials)
            credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else credentials
        elif connector == "instagram_business":
            test = validate_instagram_business_connector(credentials)
        elif connector == "irc":
            test = validate_irc_connector(credentials)
        else:
            raise RuntimeError(f"Unsupported connector '{connector}'")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    now = _utc_now_iso()
    connector_metadata = {
        **_connector_public_metadata(connector, credentials),
        **(body.metadata or {}),
    }
    connector_metadata["capability_verification"] = capability_verification_metadata(connector, test)
    if connector == "google_workspace" and isinstance(test, dict):
        profile = test.get("profile") if isinstance(test.get("profile"), dict) else {}
        email_address = str(profile.get("emailAddress") or "").strip() if isinstance(profile, dict) else ""
        auth_mode = str(credentials.get("auth_mode") or credentials.get("authMode") or "").strip().lower()
        drive = test.get("drive") if isinstance(test.get("drive"), dict) else {}
        drive_type = str(drive.get("driveType") or "").strip() if isinstance(drive, dict) else ""
        if email_address:
            connector_metadata["emailAddress"] = email_address
        if auth_mode:
            connector_metadata["auth_mode"] = auth_mode
        if drive_type:
            connector_metadata["drive_type"] = drive_type
    if connector == "microsoft_365" and isinstance(test, dict):
        profile = test.get("profile") if isinstance(test.get("profile"), dict) else {}
        display_name = str(profile.get("displayName") or "").strip() if isinstance(profile, dict) else ""
        mail = str(profile.get("mail") or "").strip() if isinstance(profile, dict) else ""
        user_principal_name = str(profile.get("userPrincipalName") or "").strip() if isinstance(profile, dict) else ""
        account_id = str(profile.get("id") or "").strip() if isinstance(profile, dict) else ""
        drive = test.get("drive") if isinstance(test.get("drive"), dict) else {}
        drive_id = str(drive.get("id") or "").strip() if isinstance(drive, dict) else ""
        drive_type = str(drive.get("driveType") or "").strip() if isinstance(drive, dict) else ""
        if display_name:
            connector_metadata["displayName"] = display_name
        if mail:
            connector_metadata["mail"] = mail
            connector_metadata["email"] = mail
        if user_principal_name:
            connector_metadata["userPrincipalName"] = user_principal_name
        if drive_id:
            connector_metadata["drive_id"] = drive_id
        if drive_type:
            connector_metadata["drive_type"] = drive_type
        if account_id:
            credentials = {**credentials, "account_id": account_id}
        if user_principal_name and not credentials.get("userPrincipalName"):
            credentials = {**credentials, "userPrincipalName": user_principal_name}
        if drive_id and not credentials.get("drive_id"):
            credentials = {**credentials, "drive_id": drive_id}
    if connector == "slack" and isinstance(test, dict):
        team = test.get("team") if isinstance(test.get("team"), dict) else {}
        bot = test.get("bot") if isinstance(test.get("bot"), dict) else {}
        authed_user = test.get("authed_user") if isinstance(test.get("authed_user"), dict) else {}
        team_id = str(team.get("id") or "").strip()
        team_name = str(team.get("name") or "").strip()
        bot_user_id = str(bot.get("user_id") or "").strip()
        bot_status = str(bot.get("bot_status") or "").strip()
        authed_user_id = str(authed_user.get("id") or "").strip()
        if team_id:
            connector_metadata["team_id"] = team_id
        if team_name:
            connector_metadata["team_name"] = team_name
        if bot_user_id:
            connector_metadata["bot_user_id"] = bot_user_id
        if bot_status:
            connector_metadata["bot_status"] = bot_status
        if authed_user_id:
            connector_metadata["authed_user_id"] = authed_user_id
        merged_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else {}
        if merged_credentials:
            credentials = {**credentials, **merged_credentials}
    if connector == "discord_bot" and isinstance(test, dict):
        bot = test.get("bot") if isinstance(test.get("bot"), dict) else {}
        guild = test.get("guild") if isinstance(test.get("guild"), dict) else {}
        channel = test.get("channel") if isinstance(test.get("channel"), dict) else {}
        application = test.get("application") if isinstance(test.get("application"), dict) else {}
        bot_id = str(bot.get("id") or "").strip()
        bot_username = str(bot.get("username") or "").strip()
        bot_status = str(bot.get("bot_status") or "active").strip()
        guild_id = str(guild.get("id") or test.get("guild_id") or credentials.get("guild_id") or "").strip()
        guild_name = str(guild.get("name") or "").strip()
        channel_id = str(channel.get("id") or test.get("channel_id") or credentials.get("channel_id") or "").strip()
        channel_name = str(channel.get("name") or "").strip()
        application_id = str(application.get("id") or credentials.get("application_id") or "").strip()
        if bot_id:
            connector_metadata["bot_id"] = bot_id
        if bot_username:
            connector_metadata["bot_username"] = bot_username
        if bot_status:
            connector_metadata["bot_status"] = bot_status
        if guild_id:
            connector_metadata["guild_id"] = guild_id
        if guild_name:
            connector_metadata["guild_name"] = guild_name
        if channel_id:
            connector_metadata["channel_id"] = channel_id
        if channel_name:
            connector_metadata["channel_name"] = channel_name
        if application_id:
            connector_metadata["application_id"] = application_id
        merged_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else {}
        if merged_credentials:
            credentials = {**credentials, **merged_credentials}
    if connector == "github" and isinstance(test, dict):
        profile = test.get("profile") if isinstance(test.get("profile"), dict) else {}
        username = str(test.get("username") or profile.get("login") or "").strip()
        auth_mode = str(test.get("auth_mode") or credentials.get("auth_mode") or "").strip().lower()
        profile_type = str(profile.get("type") or "").strip()
        if username:
            connector_metadata["username"] = username
        if auth_mode:
            connector_metadata["auth_mode"] = auth_mode
        if profile_type:
            connector_metadata["profile_type"] = profile_type
        merged_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else {}
        if merged_credentials:
            credentials = {**credentials, **merged_credentials}
    if connector == "dropbox" and isinstance(test, dict):
        display_name = str(test.get("display_name") or "").strip()
        email = str(test.get("email") or "").strip()
        account_id = str(test.get("account_id") or "").strip()
        auth_mode = str(test.get("auth_mode") or credentials.get("auth_mode") or "").strip().lower()
        if display_name:
            connector_metadata["display_name"] = display_name
        if email:
            connector_metadata["email"] = email
        if account_id:
            connector_metadata["account_id"] = account_id
        if auth_mode:
            connector_metadata["auth_mode"] = auth_mode
        merged_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else {}
        if merged_credentials:
            credentials = {**credentials, **merged_credentials}
    if connector in {
        "figma",
        "todoist",
        "airtable",
        "canva",
        "asana",
        "hubspot",
        "zoom",
        "calendly",
        "clickup",
        "jira",
        "stripe",
        "salesforce",
        "webflow",
        "monday",
        "box",
        "gitlab",
        "bitbucket",
        "confluence",
        "miro",
        "mailchimp",
        "pipedrive",
        "intercom",
        "docusign",
        "square",
        "typeform",
        "quickbooks",
        "xero",
        "freshbooks",
        "vercel",
    } and isinstance(test, dict):
        profile = test.get("profile") if isinstance(test.get("profile"), dict) else {}
        auth_mode = str(test.get("auth_mode") or credentials.get("auth_mode") or "").strip().lower()
        if auth_mode:
            connector_metadata["auth_mode"] = auth_mode
        for key in ("id", "user_id", "user_id_string", "email", "handle", "name", "display_name"):
            value = str(profile.get(key) or credentials.get(key) or "").strip()
            if value:
                connector_metadata[key] = value
        merged_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else {}
        if merged_credentials:
            credentials = {**credentials, **merged_credentials}
    if connector == "s3" and isinstance(test, dict):
        region = str(test.get("region") or "").strip()
        access_key_hint = str(test.get("access_key_hint") or "").strip()
        bucket_count = test.get("bucket_count")
        auth_mode = str(test.get("auth_mode") or credentials.get("auth_mode") or "").strip().lower()
        if region:
            connector_metadata["region"] = region
        if access_key_hint:
            connector_metadata["access_key_hint"] = access_key_hint
        if bucket_count not in {None, ""}:
            connector_metadata["bucket_count"] = bucket_count
        if auth_mode:
            connector_metadata["auth_mode"] = auth_mode
        merged_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else {}
        if merged_credentials:
            credentials = {**credentials, **merged_credentials}
    if connector == "notion" and isinstance(test, dict):
        workspace_name = str(test.get("workspace_name") or "").strip()
        workspace_id = str(test.get("workspace_id") or "").strip()
        auth_mode = str(test.get("auth_mode") or credentials.get("auth_mode") or "").strip().lower()
        if workspace_name:
            connector_metadata["workspace_name"] = workspace_name
        if workspace_id:
            connector_metadata["workspace_id"] = workspace_id
        if auth_mode:
            connector_metadata["auth_mode"] = auth_mode
        merged_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else {}
        if merged_credentials:
            credentials = {**credentials, **merged_credentials}
    if connector == "linear" and isinstance(test, dict):
        organization_name = str(test.get("organization_name") or "").strip()
        organization_id = str(test.get("organization_id") or "").strip()
        username = str(test.get("username") or "").strip()
        auth_mode = str(test.get("auth_mode") or credentials.get("auth_mode") or "").strip().lower()
        if organization_name:
            connector_metadata["organization_name"] = organization_name
        if organization_id:
            connector_metadata["organization_id"] = organization_id
        if username:
            connector_metadata["username"] = username
        if auth_mode:
            connector_metadata["auth_mode"] = auth_mode
        merged_credentials = test.get("credentials") if isinstance(test.get("credentials"), dict) else {}
        if merged_credentials:
            credentials = {**credentials, **merged_credentials}
    if connector == "smtp" and isinstance(test, dict):
        profile = test.get("profile") if isinstance(test.get("profile"), dict) else {}
        host = str(profile.get("host") or credentials.get("host") or "").strip()
        username = str(profile.get("username") or credentials.get("username") or "").strip()
        port = str(profile.get("port") or credentials.get("port") or "").strip()
        if host:
            connector_metadata["host"] = host
        if username:
            connector_metadata["username"] = username
        if port:
            connector_metadata["port"] = port
        connector_metadata["use_tls"] = bool(credentials.get("use_tls"))

    duplicate = _find_duplicate_connector_entry(connector, credentials, body.workspace_id)
    entry_id = str((duplicate or {}).get("id") or uuid.uuid4()).strip()
    created_at = str((duplicate or {}).get("created_at") or now).strip() or now

    entry = {
        "id": entry_id,
        "label": body.label.strip(),
        "provider": connector,
        "workspace_id": _normalize_workspace_id(body.workspace_id),
        "mode": "connector",
        "metadata": _sanitize_connector_metadata(connector_metadata),
        "created_at": created_at,
        "updated_at": now,
        "encrypted_secret": _openssl_encrypt(json.dumps(credentials, separators=(",", ":"))),
    }

    vault = load_vault()
    existing = vault.get("credentials", [])
    if not isinstance(existing, list):
        existing = []
    # Upsert: replace existing entry with same identity, otherwise append
    found = False
    next_items: List[Dict[str, Any]] = []
    for item in existing:
        if not isinstance(item, dict):
            next_items.append(item)
            continue
        if str(item.get("id") or "").strip() != entry_id:
            next_items.append(item)
            continue
        found = True
        next_items.append(entry)
    if not found:
        next_items.append(entry)
    vault["credentials"] = next_items
    save_vault(vault)

    if connector == "telegram_bot":
        registration = _register_telegram_webhook_for_connector(
            connector_id=str(entry["id"]),
            credentials=credentials,
        )
        entry = {
            **entry,
            "metadata": {
                **dict(entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}),
                TELEGRAM_WEBHOOK_REGISTRATION_METADATA_KEY: registration,
            },
        }

    return {
        "id": entry["id"],
        "label": entry["label"],
        "connector": connector,
        "workspace_id": entry.get("workspace_id"),
        "metadata": entry["metadata"],
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "test": test,
    }

async def update_connector_vault(credential_id: str, body: ConnectorPatchRequest):
    body.validate_fields()
    vault = load_vault()
    existing = vault.get("credentials", [])
    if not isinstance(existing, list):
        existing = []

    found = False
    updated_entry: Dict[str, Any] | None = None
    next_items: List[Dict[str, Any]] = []
    requested_workspace = _normalize_workspace_id(body.workspace_id)
    for item in existing:
        if not isinstance(item, dict):
            next_items.append(item)
            continue
        if str(item.get("id") or "").strip() != credential_id:
            next_items.append(item)
            continue
        found = True
        if str(item.get("mode") or "").strip().lower() != "connector":
            raise HTTPException(status_code=400, detail="Credential is not a connector.")
        if not _workspace_visible(item.get("workspace_id"), requested_workspace):
            raise HTTPException(status_code=403, detail="Connector is not accessible for this workspace.")
        next_item = dict(item)
        if body.label is not None:
            next_item["label"] = body.label.strip()
        if body.metadata is not None:
            merged_metadata = dict(next_item.get("metadata") if isinstance(next_item.get("metadata"), dict) else {})
            for key, value in body.metadata.items():
                if value is None:
                    merged_metadata.pop(str(key), None)
                else:
                    merged_metadata[str(key)] = value
            next_item["metadata"] = _sanitize_connector_metadata(merged_metadata)
        next_item["updated_at"] = _utc_now_iso()
        updated_entry = next_item
        next_items.append(next_item)
    if not found or not isinstance(updated_entry, dict):
        raise HTTPException(status_code=404, detail="Connector not found.")

    vault["credentials"] = next_items
    save_vault(vault)
    if str(updated_entry.get("provider") or "").strip().lower() == "telegram_bot":
        try:
            secret = resolve_vault_credential(str(updated_entry.get("id") or "").strip(), requested_workspace)
        except Exception:
            secret = {}
        registration = _register_telegram_webhook_for_connector(
            connector_id=str(updated_entry.get("id") or "").strip(),
            credentials=secret,
        )
        updated_entry = {
            **updated_entry,
            "metadata": {
                **dict(updated_entry.get("metadata") if isinstance(updated_entry.get("metadata"), dict) else {}),
                TELEGRAM_WEBHOOK_REGISTRATION_METADATA_KEY: registration,
            },
        }
    return {
        "id": updated_entry["id"],
        "label": updated_entry["label"],
        "connector": str(updated_entry.get("provider") or "").strip().lower(),
        "workspace_id": updated_entry.get("workspace_id"),
        "metadata": updated_entry.get("metadata") if isinstance(updated_entry.get("metadata"), dict) else {},
        "created_at": updated_entry.get("created_at"),
        "updated_at": updated_entry.get("updated_at"),
    }

async def test_connector_vault(credential_id: str, workspace_id: Optional[str] = None):
    def _persist_capability_verification(test_result: Any) -> None:
        vault = load_vault()
        items = vault.get("credentials", [])
        if not isinstance(items, list):
            return
        updated = False
        next_items: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                next_items.append(item)
                continue
            if str(item.get("id") or "").strip() != str(credential_id or "").strip():
                next_items.append(item)
                continue
            next_item = dict(item)
            metadata = dict(next_item.get("metadata") if isinstance(next_item.get("metadata"), dict) else {})
            metadata["capability_verification"] = capability_verification_metadata(connector, test_result)
            if connector == "telegram_bot":
                metadata[TELEGRAM_WEBHOOK_REGISTRATION_METADATA_KEY] = _telegram_webhook_registration_result(
                    connector_id=str(credential_id or "").strip(),
                    credentials=credentials,
                )
            next_item["metadata"] = _sanitize_connector_metadata(metadata)
            next_item["updated_at"] = _utc_now_iso()
            next_items.append(next_item)
            updated = True
        if updated:
            vault["credentials"] = next_items
            save_vault(vault)

    try:
        credentials = resolve_vault_credential(credential_id, workspace_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    connector = str(credentials.get("_provider") or "").lower().strip()
    test_result: Dict[str, Any]
    if connector == "google_workspace":
        try:
            test_result = validate_google_workspace_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "smtp":
        try:
            test_result = validate_smtp_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "microsoft_365":
        try:
            test_result = validate_microsoft_365_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "telegram_bot":
        try:
            test_result = validate_telegram_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "wechat_work":
        try:
            test_result = validate_wechat_work_connector(credentials, send_test=True)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "whatsapp_twilio":
        try:
            test_result = validate_whatsapp_twilio_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "discord_bot":
        try:
            test_result = validate_discord_bot_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "slack":
        try:
            test_result = validate_slack_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "github":
        try:
            test_result = validate_github_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "dropbox":
        try:
            test_result = validate_dropbox_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "figma":
        try:
            test_result = validate_figma_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "todoist":
        try:
            test_result = validate_todoist_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "airtable":
        try:
            test_result = validate_airtable_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "canva":
        try:
            test_result = validate_canva_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "asana":
        try:
            test_result = validate_asana_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "hubspot":
        try:
            test_result = validate_hubspot_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "zoom":
        try:
            test_result = validate_zoom_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "calendly":
        try:
            test_result = validate_calendly_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "clickup":
        try:
            test_result = validate_clickup_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "jira":
        try:
            test_result = validate_jira_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "stripe":
        try:
            test_result = validate_stripe_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "salesforce":
        try:
            test_result = validate_salesforce_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "webflow":
        try:
            test_result = validate_webflow_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "monday":
        try:
            test_result = validate_monday_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "box":
        try:
            test_result = validate_box_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "gitlab":
        try:
            test_result = validate_gitlab_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "bitbucket":
        try:
            test_result = validate_bitbucket_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "confluence":
        try:
            test_result = validate_confluence_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "miro":
        try:
            test_result = validate_miro_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "mailchimp":
        try:
            test_result = validate_mailchimp_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "pipedrive":
        try:
            test_result = validate_pipedrive_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "intercom":
        try:
            test_result = validate_intercom_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "docusign":
        try:
            test_result = validate_docusign_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "square":
        try:
            test_result = validate_square_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "typeform":
        try:
            test_result = validate_typeform_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "quickbooks":
        try:
            test_result = validate_quickbooks_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "xero":
        try:
            test_result = validate_xero_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "freshbooks":
        try:
            test_result = validate_freshbooks_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "vercel":
        try:
            test_result = validate_vercel_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "s3":
        try:
            test_result = validate_s3_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "notion":
        try:
            test_result = validate_notion_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "linear":
        try:
            test_result = validate_linear_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "instagram_business":
        try:
            test_result = validate_instagram_business_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    elif connector == "irc":
        try:
            test_result = validate_irc_connector(credentials)
        except Exception as exc:
            _persist_capability_verification({"ok": False, "status": 400, "message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported connector '{connector}'")

    _persist_capability_verification(test_result)
    return test_result

async def delete_connector_vault(credential_id: str, workspace_id: Optional[str] = None):
    vault = load_vault()
    existing = vault.get("credentials", [])
    if not isinstance(existing, list):
        existing = []
    found = False
    next_items = []
    for item in existing:
        if not isinstance(item, dict):
            next_items.append(item)
            continue
        if str(item.get("id") or "").strip() != credential_id:
            next_items.append(item)
            continue
        found = True
        if str(item.get("mode") or "").strip().lower() != "connector":
            raise HTTPException(status_code=400, detail="Credential is not a connector.")
        if not _workspace_visible(item.get("workspace_id"), workspace_id):
            raise HTTPException(status_code=403, detail="Connector is not accessible for this workspace.")
        if str(item.get("provider") or "").strip().lower() == "telegram_bot":
            try:
                secret = resolve_vault_credential(str(item.get("id") or "").strip(), workspace_id)
            except Exception:
                secret = {}
            _delete_telegram_webhook_best_effort(secret)
    if not found:
        raise HTTPException(status_code=404, detail="Connector not found.")
    vault["credentials"] = next_items
    save_vault(vault)
    return {"status": "ok"}

async def create_vault_credential(body: CredentialUpsertRequest):
    body.validate_fields()
    provider = normalize_provider_id(body.provider)
    credentials = dict(body.credentials or {})
    auth_mode = normalize_auth_mode(provider, credentials=credentials)
    if auth_mode and not provider_supports_auth_mode(provider, auth_mode):
        raise HTTPException(status_code=400, detail=f"Unsupported auth mode '{auth_mode}' for provider '{provider}'.")
    if auth_mode:
        credentials["auth_mode"] = auth_mode
    models: List[str] = []
    if not bool(getattr(body, "skip_validation", False)):
        _, _, adapter = resolve_provider_adapter(provider, credentials)
        try:
            check = adapter.validate(credentials)
            if not check.get("ok"):
                raise RuntimeError(check.get("message", "Credential validation failed."))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    now = _utc_now_iso()
    entry_metadata = {
        **_provider_public_metadata(provider, credentials),
        **(body.metadata or {}),
    }
    entry = {
        "id": str(uuid.uuid4()),
        "label": body.label.strip(),
        "provider": provider,
        "workspace_id": _normalize_workspace_id(body.workspace_id),
        "mode": body.mode,
        "metadata": entry_metadata,
        "created_at": now,
        "updated_at": now,
        "encrypted_secret": _openssl_encrypt(json.dumps(credentials, separators=(",", ":"))),
    }

    vault = load_vault()
    existing = vault.get("credentials", [])
    if not isinstance(existing, list):
        existing = []
    existing.append(entry)
    vault["credentials"] = existing
    save_vault(vault)

    return {
        "id": entry["id"],
        "label": entry["label"],
        "provider": entry["provider"],
        "workspace_id": entry.get("workspace_id"),
        "mode": entry["mode"],
        "metadata": entry["metadata"],
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "models_preview": models[:25],
    }

async def delete_vault_credential(credential_id: str, workspace_id: Optional[str] = None):
    vault = load_vault()
    existing = vault.get("credentials", [])
    if not isinstance(existing, list):
        existing = []
    found = False
    next_items = []
    for item in existing:
        if item.get("id") != credential_id:
            next_items.append(item)
            continue
        found = True
        if not _workspace_visible(item.get("workspace_id"), workspace_id):
            raise HTTPException(status_code=403, detail="Credential is not accessible for this workspace.")
    if not found:
        raise HTTPException(status_code=404, detail="Credential not found.")
    vault["credentials"] = next_items
    save_vault(vault)
    return {"status": "ok"}

async def test_vault_credential(credential_id: str, workspace_id: Optional[str] = None):
    try:
        credentials = resolve_vault_credential(credential_id, workspace_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        provider, _, adapter = resolve_provider_adapter(credentials.get("_provider"), credentials)
        result = adapter.validate(credentials)
        models = adapter.list_models(credentials) if result.get("ok") else []
        return {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "message": result.get("message", "Validation complete."),
            "provider": provider,
            "models_preview": models[:25],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

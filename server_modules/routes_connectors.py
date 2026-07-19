import os
import threading
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from server_modules.auth import enforce_workspace_access
from server_modules import client_identity_service, provider_catalog_service, request_window_quota_adapter
from server_modules import channel_lane_contract_service, connection_oauth_service
from server_modules.runtime_common import require_admin_api_key, require_api_key
from server_modules.runtime_models import (
    ConnectorPatchRequest,
    CredentialUpsertRequest,
    ProviderProfileUpsertRequest,
    ToolContractUpdateRequest,
    VaultExportRequest,
    VaultImportRequest,
)
from server_modules import connectors_core as core
from server_modules import connectors_actions as actions
from server_modules.connectors.discord_bot_runtime_service import DiscordBotRuntimeService
from server_modules.provider_profiles import normalize_provider_id, resolve_provider_adapter, resolve_vault_credential
from server_modules.schemas import ConnectorCreate, ConnectorDocumentCreateRequest, ConnectorSpreadsheetCreateRequest

router = APIRouter()
admin_deps = [Depends(require_admin_api_key)]
_TELEGRAM_PUBLIC_WEBHOOK_LANE_PATH = "/channels/telegram/webhook"
PUBLIC_WEBHOOK_RATE_LIMIT_LOCK = threading.Lock()
PUBLIC_WEBHOOK_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
PUBLIC_WEBHOOK_RATE_LIMIT_PER_MINUTE = int(os.getenv("EMPYRALIS_PUBLIC_WEBHOOK_RATE_LIMIT_PER_MINUTE", "60"))


def _human_admin_connector_execution_context(current_user: Optional[dict], *, workspace_id: str, purpose: str) -> Dict[str, Optional[str]]:
    payload = dict(current_user or {})
    return {
        "execution_path": actions.EXECUTION_PATH_HUMAN_ADMIN_ROUTE,
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(workspace_id or "").strip() or None,
        "actor_type": "user",
        "actor_id": str(payload.get("user_id") or payload.get("email") or "").strip() or None,
        "purpose": str(purpose or "").strip().lower() or "human_admin_connector_route",
    }


def _assert_public_studio_webhook_lane(path: str) -> Dict[str, str]:
    try:
        return channel_lane_contract_service.assert_public_studio_webhook_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _dispatch_public_studio_webhook(
    *,
    request: Request,
    path: str,
    delegate: Callable[[], Awaitable[Any]],
) -> Any:
    _assert_public_studio_webhook_lane(path)
    client_ip = client_identity_service.resolve_client_ip(request)
    decision = request_window_quota_adapter.evaluate_request_window(
        buckets=PUBLIC_WEBHOOK_RATE_LIMIT_BUCKETS,
        lock=PUBLIC_WEBHOOK_RATE_LIMIT_LOCK,
        key=f"{client_ip}:{path}",
        limit=max(1, int(PUBLIC_WEBHOOK_RATE_LIMIT_PER_MINUTE or 60)),
    )
    if not decision.get("allowed"):
        retry_after = int(decision.get("retry_after_seconds") or 1)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "public_webhook_rate_limited",
                "message": "Webhook ingress is receiving too many requests.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
    return await delegate()


async def discord_bot_runtime_status():
    service = DiscordBotRuntimeService()
    return {
        "runtime": "discord_bot",
        "execution": "bot_token_gateway_process",
        "preflight": service.preflight(),
    }

async def provider_profiles(
    request: Request,
    body: Optional[ProviderProfileUpsertRequest] = None,
    current_user=Depends(require_api_key),
):
    if request.method.upper() == "GET":
        workspace_id = enforce_workspace_access(
            current_user,
            request.query_params.get("workspace_id"),
            minimum_role="owner",
            capability_id="connectors.manage",
        )
        return await core.list_provider_profiles(
            workspace_id=workspace_id,
            provider=request.query_params.get("provider"),
        )
    if body is None:
        raise HTTPException(status_code=422, detail="Provider profile payload is required.")
    body.workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await core.upsert_provider_profile(body)


async def provider_profiles_health(
    request: Request,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await core.provider_profiles_health(workspace_id=workspace_id)


async def providers_catalog(
    request: Request,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="member",
    )
    return await core.list_providers(workspace_id=workspace_id)


async def providers_model_catalog(
    request: Request,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="member",
    )
    return await provider_catalog_service.list_workspace_provider_catalog(workspace_id=workspace_id)


async def providers_health_check(
    request: Request,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    payload = await core.list_credentials_vault(workspace_id=workspace_id)
    items = payload.get("items") if isinstance(payload, dict) else []
    results: Dict[str, str] = {}
    for item in items if isinstance(items, list) else []:
        provider_id = normalize_provider_id(item.get("provider"))
        credential_id = str(item.get("id") or "").strip()
        if not provider_id or not credential_id:
            continue
        try:
            credentials = resolve_vault_credential(credential_id, workspace_id)
            _provider, _adapter_key, adapter = resolve_provider_adapter(provider_id, credentials)
            check = adapter.validate(credentials)
            status = int(check.get("status") or 500)
            state = "ok" if bool(check.get("ok")) else f"failed: {status}"
        except Exception as exc:
            state = f"failed: {str(exc).strip() or 'validation error'}"
        if state == "ok" or provider_id not in results:
            results[provider_id] = state
    return results


async def whatsapp_twilio_webhook(request: Request):
    # This route stays public because Twilio authenticates through the webhook secret boundary.
    return await _dispatch_public_studio_webhook(
        request=request,
        path=str(request.url.path),
        delegate=lambda: actions.whatsapp_twilio_webhook(request),
    )


async def sms_twilio_webhook(request: Request):
    # This route stays public because Twilio signs each request with the
    # platform account's auth token (validated in actions.sms_twilio_webhook).
    return await _dispatch_public_studio_webhook(
        request=request,
        path=str(request.url.path),
        delegate=lambda: actions.sms_twilio_webhook(request),
    )


async def telegram_webhook(request: Request, connector_id: str):
    # This route stays public because Telegram authenticates through the webhook secret boundary.
    return await _dispatch_public_studio_webhook(
        request=request,
        path=_TELEGRAM_PUBLIC_WEBHOOK_LANE_PATH,
        delegate=lambda: actions.telegram_webhook_canonical(request, connector_id),
    )


async def discord_webhook(request: Request):
    # This route stays public because Discord signs requests at the edge.
    return await _dispatch_public_studio_webhook(
        request=request,
        path=str(request.url.path),
        delegate=lambda: actions.discord_webhook(request),
    )


async def github_webhook(request: Request):
    # This route stays public because GitHub signs requests at the edge.
    return await _dispatch_public_studio_webhook(
        request=request,
        path=str(request.url.path),
        delegate=lambda: actions.github_events_webhook(request),
    )


async def slack_events_webhook(request: Request):
    # This route stays public because Slack signs requests at the edge.
    return await _dispatch_public_studio_webhook(
        request=request,
        path=str(request.url.path),
        delegate=lambda: actions.slack_events_webhook(request),
    )


async def slack_oauth_callback(request: Request, current_user=Depends(require_api_key)):
    return await actions.slack_oauth_callback(request, current_user=current_user)


async def update_tool_contract(
    request: Request,
    tool_id: str,
    body: ToolContractUpdateRequest,
    current_user=Depends(require_admin_api_key),
):
    if body is None:
        raise HTTPException(status_code=422, detail="Tool contract payload is required.")
    return await core.update_tool_contract_state(tool_id, body.enabled)


async def list_credentials_vault(
    request: Request,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.read",
    )
    return await core.list_credentials_vault(workspace_id=workspace_id)


async def list_connectors_vault(
    request: Request,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.read",
    )
    return await core.list_connectors_vault(workspace_id=workspace_id)


async def browse_microsoft_connector_drive(
    request: Request,
    connector_id: str,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
        connector_id=connector_id,
    )
    return await actions.browse_microsoft_connector_drive(
        connector_id=connector_id,
        workspace_id=workspace_id,
        path=request.query_params.get("path"),
        execution_context=_human_admin_connector_execution_context(
            current_user,
            workspace_id=workspace_id,
            purpose="human_admin_connector_browse_drive",
        ),
    )


async def browse_google_connector_drive(
    request: Request,
    connector_id: str,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
        connector_id=connector_id,
    )
    return await actions.browse_google_connector_drive(
        connector_id=connector_id,
        workspace_id=workspace_id,
        path=request.query_params.get("path"),
        execution_context=_human_admin_connector_execution_context(
            current_user,
            workspace_id=workspace_id,
            purpose="human_admin_connector_browse_drive",
        ),
    )


async def create_google_connector_document(
    request: Request,
    connector_id: str,
    body: Optional[ConnectorDocumentCreateRequest] = None,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await actions.create_google_connector_document(
        connector_id=connector_id,
        body=body,
        workspace_id=workspace_id,
        execution_context=_human_admin_connector_execution_context(
            current_user,
            workspace_id=workspace_id,
            purpose="human_admin_connector_create_document",
        ),
    )


async def create_google_connector_spreadsheet(
    request: Request,
    connector_id: str,
    body: Optional[ConnectorSpreadsheetCreateRequest] = None,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await actions.create_google_connector_spreadsheet(
        connector_id=connector_id,
        body=body,
        workspace_id=workspace_id,
        execution_context=_human_admin_connector_execution_context(
            current_user,
            workspace_id=workspace_id,
            purpose="human_admin_connector_create_spreadsheet",
        ),
    )


async def create_connector_vault(
    request: Request,
    body: ConnectorCreate,
    current_user=Depends(require_api_key),
):
    body.workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
        connector_id=body.connector,
    )
    return await actions.create_connector_vault(body)


async def update_connector_vault(
    request: Request,
    credential_id: str,
    body: ConnectorPatchRequest,
    current_user=Depends(require_api_key),
):
    body.workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await actions.update_connector_vault(credential_id, body)


async def test_connector_vault(
    request: Request,
    credential_id: str,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await actions.test_connector_vault(credential_id, workspace_id=workspace_id)


async def delete_connector_vault(
    request: Request,
    credential_id: str,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await actions.delete_connector_vault(credential_id, workspace_id=workspace_id)


async def create_vault_credential(
    request: Request,
    body: CredentialUpsertRequest,
    current_user=Depends(require_api_key),
):
    body.workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await actions.create_vault_credential(body)


async def delete_vault_credential(
    request: Request,
    credential_id: str,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await actions.delete_vault_credential(credential_id, workspace_id=workspace_id)


async def test_vault_credential(
    request: Request,
    credential_id: str,
    current_user=Depends(require_api_key),
):
    workspace_id = enforce_workspace_access(
        current_user,
        request.query_params.get("workspace_id"),
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await actions.test_vault_credential(credential_id, workspace_id=workspace_id)


async def export_vault_credentials(
    body: VaultExportRequest,
    current_user=Depends(require_api_key),
):
    body.workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await core.export_vault_credentials(body)


async def import_vault_credentials(
    body: VaultImportRequest,
    current_user=Depends(require_api_key),
):
    body.workspace_id = enforce_workspace_access(
        current_user,
        body.workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await core.import_vault_credentials(body)


async def complete_app_oauth_for_mcp(
    request: Request,
    provider: str,
    current_user=Depends(require_api_key),
):
    """Bridge: complete OAuth for an app and register its MCP server with credential injection."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = str(body.get("code") or "").strip()
    redirect_uri = str(body.get("redirect_uri") or "").strip()
    workspace_id = str(body.get("workspace_id") or "").strip()
    if not code or not redirect_uri or not workspace_id:
        raise HTTPException(status_code=400, detail="code, redirect_uri, and workspace_id are required.")
    workspace_id = enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await connection_oauth_service.connect_app_via_oauth_to_mcp(
        workspace_id=workspace_id,
        provider=provider,
        oauth_code=code,
        redirect_uri=redirect_uri,
    )


router.add_api_route("/apps/{provider}/oauth/complete", complete_app_oauth_for_mcp, methods=['POST'])
router.add_api_route("/providers/profiles", provider_profiles, methods=['GET', 'POST'])
router.add_api_route("/providers/profiles/{profile_id}/enable", core.enable_provider_profile, methods=['POST'], dependencies=admin_deps)
router.add_api_route("/providers/profiles/{profile_id}/disable", core.disable_provider_profile, methods=['POST'], dependencies=admin_deps)
router.add_api_route("/providers/profiles/{profile_id}", core.delete_provider_profile, methods=['DELETE'], dependencies=admin_deps)
router.add_api_route("/providers/profiles/health", provider_profiles_health, methods=['GET'])
router.add_api_route("/tools/contracts", core.get_tool_contracts, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/tools/contracts/{tool_id}", update_tool_contract, methods=['PUT'], dependencies=admin_deps)
router.add_api_route("/tools/policy/evaluate", core.evaluate_tools_policy, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/providers", providers_catalog, methods=['GET'])
router.add_api_route("/providers/catalog", providers_model_catalog, methods=['GET'])
router.add_api_route("/providers/anthropic/local-cli/status", core.get_anthropic_local_cli_status, methods=['GET'], dependencies=admin_deps)
router.add_api_route("/providers/anthropic/local-cli/login", core.start_anthropic_local_cli_login, methods=['POST'], dependencies=admin_deps)
router.add_api_route("/providers/gemini/local-cli/status", core.get_gemini_local_cli_status, methods=['GET'], dependencies=admin_deps)
router.add_api_route("/providers/test", core.test_provider_credentials, methods=['POST'], dependencies=admin_deps)
router.add_api_route("/providers/health-check", providers_health_check, methods=['GET'], dependencies=admin_deps)
router.add_api_route("/providers/model-aliases", core.get_model_alias_catalog, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/providers/{provider}/probe", core.probe_provider, methods=['POST'], dependencies=admin_deps)
router.add_api_route("/providers/{provider}/models", core.get_provider_models, methods=['GET'], dependencies=admin_deps)
router.add_api_route("/credentials/vault", list_credentials_vault, methods=['GET'])
router.add_api_route("/connectors", core.list_connectors, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/connectors/vault", list_connectors_vault, methods=['GET'])
router.add_api_route("/connectors/vault/{connector_id}/microsoft-drive", browse_microsoft_connector_drive, methods=['GET'])
router.add_api_route("/connectors/vault/{connector_id}/google-drive", browse_google_connector_drive, methods=['GET'])
router.add_api_route("/connectors/vault/{connector_id}/google-doc", create_google_connector_document, methods=['POST'])
router.add_api_route("/connectors/vault/{connector_id}/google-sheet", create_google_connector_spreadsheet, methods=['POST'])
router.add_api_route("/channels/whatsapp/twilio/webhook", whatsapp_twilio_webhook, methods=['POST'])
router.add_api_route("/channels/sms/twilio/webhook", sms_twilio_webhook, methods=['POST'])
router.add_api_route("/channels/telegram/webhook/{connector_id}", telegram_webhook, methods=['POST'])
router.add_api_route("/channels/slack/events", slack_events_webhook, methods=['POST'])
router.add_api_route("/channels/github/webhook", github_webhook, methods=['POST'])
router.add_api_route("/connectors/discord/webhook", discord_webhook, methods=['POST'])
router.add_api_route("/channels/telegram/autopilot/status", actions.telegram_autopilot_status, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/channels/whatsapp/autopilot/status", actions.whatsapp_autopilot_status, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/channels/discord/bot-runtime/status", discord_bot_runtime_status, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/channels/autopilot/profiles", actions.list_autopilot_profiles, methods=['GET'], dependencies=[Depends(require_api_key)])
router.add_api_route("/channels/telegram/send", actions.telegram_send_message, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/channels/telegram/autopilot/test-message", actions.telegram_autopilot_test_message, methods=['POST'], dependencies=[Depends(require_api_key)])
router.add_api_route("/connectors/slack/oauth/callback", slack_oauth_callback, methods=['POST'])
router.add_api_route("/connectors/vault", create_connector_vault, methods=['POST'])
router.add_api_route("/connectors/vault/{credential_id}", update_connector_vault, methods=['PATCH'])
router.add_api_route("/connectors/vault/{credential_id}/test", test_connector_vault, methods=['POST'])
router.add_api_route("/connectors/vault/{credential_id}", delete_connector_vault, methods=['DELETE'])
router.add_api_route("/credentials/vault", create_vault_credential, methods=['POST'])
router.add_api_route("/credentials/vault/{credential_id}", delete_vault_credential, methods=['DELETE'])
router.add_api_route("/credentials/vault/{credential_id}/test", test_vault_credential, methods=['POST'])
router.add_api_route("/credentials/vault/rotate-key", core.rotate_vault_key, methods=['POST'], dependencies=admin_deps)
router.add_api_route("/credentials/vault/export", export_vault_credentials, methods=['POST'])
router.add_api_route("/credentials/vault/import", import_vault_credentials, methods=['POST'])

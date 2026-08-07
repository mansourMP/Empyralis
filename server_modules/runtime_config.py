import os
import uuid
import threading
import queue
import json
import csv
import time
import subprocess
import shutil
import secrets
import base64
import re
import hashlib
import html
import mimetypes
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Set, Tuple
from pathlib import Path
from urllib import request as urlrequest
from urllib import error as urlerror
from urllib.parse import quote_plus, parse_qs, urlencode
import ssl
import certifi
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from dotenv import load_dotenv
from mcp_server import (
    EMPYRALIST_MCP_ENDPOINT,
    EMPYRALIST_MCP_TOOLS,
    mount_empyralist_mcp,
    empyralist_mcp_lifespan,
)
from scripts.platform_execution import (
    capability_metadata,
    capability_tool_id,
    default_local_companion_allow_prefixes,
    supported_capability_catalog,
)
from server_modules import (
    build_doctor_report,
    build_runtime_contract_payload,
    build_probe_payload,
    collect_local_queue_counts,
    collect_runtime_counts,
    probe_openai_credential,
)
from server_modules.provider_profiles import (
    PROVIDER_CATALOG,
    normalize_provider_id,
    normalize_auth_mode,
    provider_supports_auth_mode,
    provider_requires_credential,
    secretless_provider_credentials,
    resolve_provider_adapter,
    claude_code_cli_available,
    gemini_cli_available,
    ProviderAdapter,
    OpenAIAdapter,
    AnthropicAdapter,
    GeminiAdapter,
    PROVIDER_ADAPTERS,
    PROVIDER_COST_PER_1K,
    estimate_tokens,
    masked_cost_band,
    build_masked_usage,
    _persist_provider_profiles,
    _load_provider_profiles,
    _profile_cooldown_seconds_for_error,
    _profile_ready,
    _mark_profile_success,
    _mark_profile_failure,
    _sorted_profiles,
    _build_provider_credential_candidates,
)
from server_modules.local_queue import (
    LocalRunClaimRequest,
    LocalRunHeartbeatPayload,
    LocalWorkerHeartbeatPayload,
    LocalRunCompletePayload,
    LocalRunFailPayload,
    _cleanup_stale_local_claims,
    _is_worker_online,
    handle_cleanup_local_run_queue,
    handle_get_local_run_queue,
    handle_get_local_workers_status,
    handle_heartbeat_local_worker,
    handle_claim_local_run,
    handle_heartbeat_local_run,
    handle_complete_local_run,
    handle_fail_local_run,
)
from server_modules.connectors.autopilot_runtime_exports import (
    _load_telegram_autopilot_state,
    _load_whatsapp_autopilot_state,
    _run_telegram_autopilot_forever,
    _whatsapp_autopilot_activate,
    _telegram_autopilot_snapshot,
    _whatsapp_autopilot_snapshot,
    handle_telegram_webhook,
    handle_whatsapp_twilio_webhook,
    handle_telegram_autopilot_status,
    handle_whatsapp_autopilot_status,
    handle_list_autopilot_profiles,
    handle_telegram_send_message,
    handle_telegram_autopilot_test_message,
)
from server_modules.connector_validators import (
    validate_airtable_connector as _validate_airtable_connector,
    validate_asana_connector as _validate_asana_connector,
    validate_box_connector as _validate_box_connector,
    validate_calendly_connector as _validate_calendly_connector,
    validate_canva_connector as _validate_canva_connector,
    validate_clickup_connector as _validate_clickup_connector,
    validate_confluence_connector as _validate_confluence_connector,
    validate_docusign_connector as _validate_docusign_connector,
    validate_dropbox_connector as _validate_dropbox_connector,
    validate_figma_connector as _validate_figma_connector,
    validate_github_connector as _validate_github_connector,
    validate_gitlab_connector as _validate_gitlab_connector,
    validate_higgsfield_connector as _validate_higgsfield_connector,
    validate_hubspot_connector as _validate_hubspot_connector,
    validate_intercom_connector as _validate_intercom_connector,
    validate_jira_connector as _validate_jira_connector,
    validate_linear_connector as _validate_linear_connector,
    validate_miro_connector as _validate_miro_connector,
    validate_monday_connector as _validate_monday_connector,
    validate_notion_connector as _validate_notion_connector,
    validate_generic_oauth_connector as _validate_generic_oauth_connector,
    validate_google_workspace_connector as _validate_google_workspace_connector,
    validate_microsoft_365_connector as _validate_microsoft_365_connector,
    validate_s3_connector as _validate_s3_connector,
    validate_smtp_connector as _validate_smtp_connector,
    validate_telegram_connector as _validate_telegram_connector,
    validate_wechat_work_connector as _validate_wechat_work_connector,
    validate_whatsapp_twilio_connector as _validate_whatsapp_twilio_connector,
    validate_discord_bot_connector as _validate_discord_bot_connector,
    validate_slack_connector as _validate_slack_connector,
    validate_salesforce_connector as _validate_salesforce_connector,
    validate_square_connector as _validate_square_connector,
    validate_stripe_connector as _validate_stripe_connector,
    validate_todoist_connector as _validate_todoist_connector,
    validate_typeform_connector as _validate_typeform_connector,
    validate_vercel_connector as _validate_vercel_connector,
    validate_webflow_connector as _validate_webflow_connector,
    validate_zoom_connector as _validate_zoom_connector,
    validate_instagram_business_connector as _validate_instagram_business_connector,
    validate_irc_connector as _validate_irc_connector,
)
from server_modules.vault_store import (
    _vault_passphrase,
    _set_vault_passphrase,
    _vault_encrypt_with_passphrase,
    _vault_decrypt_v2_with_passphrase,
    _openssl_encrypt_with_passphrase,
    _openssl_decrypt_with_passphrase,
    _openssl_encrypt,
    _openssl_decrypt,
    load_vault,
    save_vault,
)
from server_modules.google_workspace_cli import (
    google_workspace_uses_local_cli,
    google_workspace_local_create_calendar_event,
    google_workspace_local_create_draft,
    google_workspace_local_get_profile,
    google_workspace_local_list_calendar_events,
    google_workspace_local_list_drive_children,
    google_workspace_local_list_recent_messages,
    google_workspace_local_send_message,
    _gmail_message_summary,
)
from server_modules.google_drive_api import (
    google_workspace_list_drive_children,
    google_workspace_create_document,
    google_workspace_create_spreadsheet,
)
from server_modules.installed_skills import (
    active_installed_skill_ids,
    build_active_skill_prompt_append,
    list_installed_skills,
    merge_skill_prompt_append,
)
from server_modules.installed_solutions import (
    active_installed_solutions,
    call_installed_solution_hook,
    find_installed_solution,
    list_installed_solutions,
)
from server_modules.connector_metadata import (
    _sanitize_connector_metadata,
    _connector_public_metadata,
    _provider_public_metadata,
    _connector_identity_signature,
    _find_duplicate_connector_entry,
)
from server_modules.customer_ops_pack import (
    classify_inbox_priority,
    classify_inbox_category,
    classify_lead_stage,
    extract_lead_name,
)
from server_modules.outcome_packs import execute_outcome_pack
from server_modules.microsoft_365_graph import (
    microsoft_365_create_calendar_event,
    microsoft_365_create_draft,
    microsoft_365_download_drive_file,
    microsoft_365_get_profile,
    microsoft_365_list_drive_children,
    microsoft_graph_request,
    microsoft_365_normalize_drive_path,
    microsoft_365_send_message,
    microsoft_365_upload_drive_file,
)
from server_modules.office_ooxml import (
    DOCX_MIME,
    PPTX_MIME,
    build_docx,
    build_pptx,
    build_updated_docx,
    build_updated_pptx,
    normalize_deck_slides,
    normalize_doc_sections,
)
from server_modules.setup_sessions import (
    SetupSessionCreateRequest,
    SetupSessionActionRequest,
    _cleanup_setup_sessions_locked,
    _load_setup_sessions,
    handle_create_setup_session,
    handle_get_setup_session,
    handle_setup_session_action,
    handle_cancel_setup_session,
    handle_resume_setup_session,
    handle_create_onboarding_session,
    handle_get_onboarding_session,
    handle_onboarding_session_action,
    handle_cancel_onboarding_session,
    handle_resume_onboarding_session,
)
from server_modules.idempotency import (
    _idempotency_record_key,
    _prune_idempotency_locked,
    _persist_idempotency,
    _load_idempotency,
    _idempotency_get,
    _idempotency_store,
)
from server_modules.vault_helpers import (
    normalize_workspace_id as _normalize_workspace_id_impl,
    workspace_visible as _workspace_visible_impl,
    list_vault_credentials as _list_vault_credentials_impl,
    list_vault_connectors as _list_vault_connectors_impl,
    resolve_vault_credential as _resolve_vault_credential_impl,
    parse_iso_datetime as _parse_iso_datetime_impl,
    resolve_default_vault_credential as _resolve_default_vault_credential_impl,
    credential_identity as _credential_identity_impl,
    sanitize_bearer_token as _sanitize_bearer_token_impl,
    codex_token_from_vault as _codex_token_from_vault_impl,
    openai_env_bearer_with_source as _openai_env_bearer_with_source_impl,
    openai_bearer_from_credentials as _openai_bearer_from_credentials_impl,
)
from server_modules.runtime_policy import *
from server_modules.policy_service import (
    action_policy_from_app_permissions,
    apply_execution_route_metadata,
    decide_execution_target,
    enforce_tool_policy,
    evaluate_action_policy,
    evaluate_tool_policy_decision,
    merge_action_policies,
    resolve_runtime_policy_mode,
    summarize_action_policy_eval,
    tool_policy_snapshot,
)
from server_modules.runtime_state_store import (
    init_runtime_state_db,
    upsert_live_run_state,
    delete_live_run_state,
    list_live_run_states,
    upsert_chat_stream_state,
    get_chat_stream_state,
    list_chat_stream_states,
    delete_expired_chat_stream_states,
    replace_local_runtime_state,
    load_local_runtime_state,
    upsert_run_history_item,
    replace_run_history,
    list_run_history,
    append_channel_event,
    replace_channel_events,
    list_channel_events,
)
from server_modules.memory_service import (
    configure_runtime_memory,
    _memory_health_snapshot,
    _memory_manager_or_503,
    _memory_prompt_context_block,
    _memory_search_scoped,
    _normalize_memory_bucket,
    _trim_memory_trace,
)
from server_modules.conversation_memory_facade_service import (
    persist_run_memory as _persist_run_memory,
    hydrate_run_memory_context as _hydrate_run_memory_context,
)
from server_modules.runtime_events import (
    configure_runtime_events,
    load_channel_events as _load_channel_events,
    append_channel_event_item as _append_channel_event,
    channel_event_matches as _channel_event_matches,
    iter_channel_events_stream as _iter_channel_events_stream,
    summarize_channel_sessions as _summarize_channel_sessions,
)
from server_modules.runtime_runs_api import register_run_routes
from server_modules.runtime_events_api import register_inbox_routes
from server_modules.agent_workspace_api import AGENT_WORKSPACE_LABELS, register_agent_workspace_routes
from server_modules.profile_api import register_profile_routes
from server_modules.app_registry_api import register_app_registry_routes, resolve_app_permissions
from server_modules.config_loader import config_bool, config_float, config_int, config_str, config_value


EMPYRALIS_STATE_HOME = Path(
    config_str("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()


def _resolve_state_file(env_name: str, default_relative: str, legacy_filename: Optional[str] = None) -> Path:
    explicit = config_str(env_name, "")
    if explicit is not None and explicit.strip():
        return Path(explicit.strip()).expanduser()
    return (EMPYRALIS_STATE_HOME / default_relative).expanduser()


def _resolve_state_dir(env_name: str, default_relative: str, legacy_dirname: Optional[str] = None) -> Path:
    explicit = config_str(env_name, "")
    if explicit is not None and explicit.strip():
        return Path(explicit.strip()).expanduser()
    return (EMPYRALIS_STATE_HOME / default_relative).expanduser()


from server_modules.runtime_models import (
    configure_runtime_model_context,
    RunStartRequest,
    RunDelegationRequest,
    RunAutoDelegationRequest,
    RunDelegationRetryRequest,
    MemoryUpsertRequest,
    MemorySearchRequest,
    CronScheduleUpsertRequest,
    CronSchedulePatchRequest,
    WeeklyScheduleUpsertRequest,
    WeeklySchedulePatchRequest,
    DecisionPayload,
    ToolPolicyEvaluateRequest,
    RuntimeSkillsStateUpsertRequest,
    ProviderProfileUpsertRequest,
    CredentialUpsertRequest,
    CredentialTestRequest,
    ConnectorUpsertRequest,
    ConnectorPatchRequest,
    TelegramSendRequest,
    TelegramAutopilotTestRequest,
    VaultRotateKeyRequest,
    VaultExportRequest,
    VaultImportRequest,
)

try:
    from python_engine.memory_manager import MemoryManager as RuntimeMemoryManager
except Exception:
    RuntimeMemoryManager = None  # type: ignore[assignment]


def _resolved_environment() -> str:
    return str(os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or os.getenv("ENV") or os.getenv("NODE_ENV") or "").strip().lower()


def _should_load_dotenv() -> bool:
    return _resolved_environment() in {"dev", "development", "local", "test", "testing"}


# MAN-202 / MAN-268: this used to be a bare load_dotenv(), which lets
# python-dotenv search UP the directory tree from wherever the process
# happens to be invoked, looking for the nearest ".env". A subagent working
# inside a git worktree nested under the real checkout
# (.claude/worktrees/<name>/) has no .env of its own, so that search walked
# straight past the worktree and into the *real* repo root's .env, silently
# handing a "throwaway" local stack production-adjacent credentials
# (DATABASE_URL included) while the agent believed it was running in
# isolation. That is very likely what let an agent wipe the founder's local
# database, and it was hit again for real running the test suite (MAN-268).
# Anchoring the path to this file's own location means only *this
# checkout's* repo-root .env is ever read -- never a parent directory's.
_RUNTIME_CONFIG_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


# 1. Load Secrets
if _should_load_dotenv():
    load_dotenv(_RUNTIME_CONFIG_ENV_PATH)
# Avoid interactive trace prompts and color issues in headless runtime
os.environ.setdefault("RICH_DISABLE_COLOR", "1")
os.environ.setdefault("RICH_NO_COLOR", "1")
os.environ.setdefault("NO_COLOR", "1")
os.environ.setdefault("TERM", "dumb")

@asynccontextmanager
async def app_lifespan(_: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(empyralist_mcp_lifespan())
        yield



# --- CONFIG ---
FRONTEND_ORIGINS = config_str("FRONTEND_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000")


def _assert_frontend_origins_safe_for_environment() -> None:
    """Refuse to start if FRONTEND_ORIGINS points at localhost in staging/production."""
    resolved_env = _resolved_environment()
    if resolved_env not in {"staging", "production", "prod"}:
        return
    origins = [o.strip() for o in FRONTEND_ORIGINS.split(",") if o.strip()]
    if not origins:
        raise RuntimeError(
            "FRONTEND_ORIGINS is empty. In staging/production it must be set to HTTPS origin(s)."
        )
    blocked_hostnames = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    for origin in origins:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            hostname = (parsed.hostname or "").lower()
        except Exception:
            hostname = ""
            parsed = None
        if origin == "*" or not parsed or not parsed.scheme or not parsed.netloc:
            raise RuntimeError(
                f"FRONTEND_ORIGINS origin '{origin}' must be an absolute HTTPS origin in staging/production."
            )
        if hostname in blocked_hostnames:
            raise RuntimeError(
                f"FRONTEND_ORIGINS contains localhost origin '{origin}'. "
                f"In staging/production only HTTPS origins are allowed. "
                f"Set FRONTEND_ORIGINS to your production frontend URL(s)."
            )
        if parsed.scheme and parsed.scheme != "https":
            raise RuntimeError(
                f"FRONTEND_ORIGINS origin '{origin}' must use HTTPS in staging/production."
            )


def _assert_auth_secrets_safe_for_environment() -> None:
    resolved_env = _resolved_environment()
    if resolved_env not in {"staging", "production", "prod"}:
        return
    from server_modules import jwt_secret

    jwt_secret.assert_explicit_secret_safe_for_environment(resolved_env)
    broker_secret_names = ("EMPYRALIS_SECRETS_BROKER_SECRET", "EMPYRALIS_TOOL_BROKER_SECRET")
    placeholders = {
        "empyralis-dev-tool-broker-secret",
        "empyralis-dev-secrets-broker-secret",
        "replace-with-a-random-32-plus-character-secret",
        "change-me",
        "changeme",
        "secret",
    }
    for name in broker_secret_names:
        value = str(os.getenv(name) or "").strip()
        if len(value) < 32 or value.lower() in placeholders:
            raise RuntimeError(f"{name} must be explicitly set to a non-placeholder 32+ character secret in staging/production.")
    mini_app_share_secret = str(os.getenv("EMPYRALIS_MINI_APP_SHARE_SECRET") or "").strip()
    if len(mini_app_share_secret) < 32 or mini_app_share_secret.lower() in placeholders:
        raise RuntimeError("EMPYRALIS_MINI_APP_SHARE_SECRET must be set in staging and production environments.")


_assert_frontend_origins_safe_for_environment()
_assert_auth_secrets_safe_for_environment()
EMPYRALIS_BILLING_PROVIDER = config_str("EMPYRALIS_BILLING_PROVIDER", "stripe")
EMPYRALIS_STRIPE_SECRET_KEY = config_str("EMPYRALIS_STRIPE_SECRET_KEY", config_str("STRIPE_SECRET_KEY", ""))
EMPYRALIS_STRIPE_WEBHOOK_SECRET = config_str("EMPYRALIS_STRIPE_WEBHOOK_SECRET", "")
EMPYRALIS_STRIPE_PRICE_IDS = config_str("EMPYRALIS_STRIPE_PRICE_IDS", "")
EMPYRALIS_BILLING_FRONTEND_ORIGIN = config_str("EMPYRALIS_BILLING_FRONTEND_ORIGIN", "")
EMPYRALIS_BILLING_SUCCESS_URL = config_str("EMPYRALIS_BILLING_SUCCESS_URL", "")
EMPYRALIS_BILLING_CANCEL_URL = config_str("EMPYRALIS_BILLING_CANCEL_URL", "")
EMPYRALIS_BILLING_PORTAL_RETURN_URL = config_str("EMPYRALIS_BILLING_PORTAL_RETURN_URL", "")
ORION_API_KEY = config_value("ORION_API_KEY")
_ORION_AUTH_REQUIRED_RAW = config_value("ORION_AUTH_REQUIRED")
ORION_DEV_INSECURE_NO_AUTH = config_bool("ORION_DEV_INSECURE_NO_AUTH", False)
# Fail-closed by default (OpenClaw-style): auth is on unless explicitly disabled.
ORION_AUTH_REQUIRED = (_ORION_AUTH_REQUIRED_RAW != "0") if _ORION_AUTH_REQUIRED_RAW is not None else True
if ORION_DEV_INSECURE_NO_AUTH:
    resolved_env = _resolved_environment()
    if resolved_env not in {"local", "test"}:
        raise RuntimeError(
            "ORION_DEV_INSECURE_NO_AUTH is allowed only when ORION_ENV is explicitly 'local' or 'test'. Refusing to start."
        )
    ORION_AUTH_REQUIRED = False
    print("[WARN] ORION_DEV_INSECURE_NO_AUTH=1 set; runtime API auth is disabled for this process.")


def _assert_http_mcp_dev_override_safe_for_environment() -> None:
    if not str(os.getenv("EMPYRALIS_DEV_ALLOW_HTTP_MCP") or "").strip():
        return
    resolved_env = _resolved_environment()
    if resolved_env in {"staging", "production", "prod"}:
        raise RuntimeError(
            "EMPYRALIS_DEV_ALLOW_HTTP_MCP is allowed only for local MCP development. Refusing to start in staging/production."
        )


_assert_http_mcp_dev_override_safe_for_environment()
ORION_ENABLE_LEGACY_LOCAL_ROUTES = config_bool("ORION_ENABLE_LEGACY_LOCAL_ROUTES", False)
ORION_ALLOW_SYSTEM_PROXY = config_bool("ORION_ALLOW_SYSTEM_PROXY", False)
ORION_RUN_TIMEOUT_SECONDS = config_int("ORION_RUN_TIMEOUT_SECONDS", 300)
ORION_MAX_RETRIES = config_int("ORION_MAX_RETRIES", 2)
ORION_RETRY_BACKOFF_SECONDS = config_float("ORION_RETRY_BACKOFF_SECONDS", 1.5)
ORION_MAX_EVENT_BUFFER = config_int("ORION_MAX_EVENT_BUFFER", 2000)
ORION_HISTORY_LIMIT = config_int("ORION_HISTORY_LIMIT", 800)
ORION_DIRECT_CHAT_SESSION_MANAGER = config_bool("ORION_DIRECT_CHAT_SESSION_MANAGER", False)
ORION_HISTORY_FILE = _resolve_state_file("ORION_HISTORY_FILE", "runtime/run_history.json")
ORION_RUNTIME_STATE_DB = _resolve_state_file("ORION_RUNTIME_STATE_DB", "runtime/state.db")
ORION_CHANNEL_EVENTS_LIMIT = config_int("ORION_CHANNEL_EVENTS_LIMIT", 2000)
ORION_CHANNEL_SESSIONS_LIMIT = config_int("ORION_CHANNEL_SESSIONS_LIMIT", 80)
ORION_CHANNEL_EVENTS_FILE = _resolve_state_file("ORION_CHANNEL_EVENTS_FILE", "channels/events.json")
ORION_CHANNEL_DEAD_LETTER_FILE = _resolve_state_file(
    "ORION_CHANNEL_DEAD_LETTER_FILE",
    "channels/dead_letters.json",
)
ORION_CHANNEL_DEAD_LETTER_LIMIT = config_int("ORION_CHANNEL_DEAD_LETTER_LIMIT", 500)
ORION_SCHEDULES_FILE = _resolve_state_file("ORION_SCHEDULES_FILE", "automations/weekly_schedules.json")
ORION_WEBHOOK_TRIGGERS_FILE = _resolve_state_file("ORION_WEBHOOK_TRIGGERS_FILE", "automations/webhooks.json")
ORION_SETUP_SESSIONS_FILE = _resolve_state_file("ORION_SETUP_SESSIONS_FILE", "setup/sessions.json")
ORION_SETUP_SESSION_TTL_SECONDS = config_int("ORION_SETUP_SESSION_TTL_SECONDS", 1800)
ORION_PROVIDER_PROFILES_FILE = _resolve_state_file(
    "ORION_PROVIDER_PROFILES_FILE",
    "providers/profiles.json",
)
ORION_RUNTIME_SKILLS_FILE = _resolve_state_file("ORION_RUNTIME_SKILLS_FILE", "runtime/skills.json")
ORION_TOOL_STATE_FILE = _resolve_state_file("ORION_TOOL_STATE_FILE", "runtime/tools_state.json")
ORION_APP_REGISTRY_FILE = _resolve_state_file("ORION_APP_REGISTRY_FILE", "apps/registry.json")
ORION_PROFILE_ROOT = _resolve_state_dir("ORION_PROFILE_ROOT", "profiles")
ORION_PROFILE_DEFAULT_FILE = _resolve_state_file("ORION_PROFILE_DEFAULT_FILE", "profiles/default.json")
ORION_VALIDATION_REPORT_DIR = _resolve_state_dir("ORION_VALIDATION_REPORT_DIR", "validation")
ORION_VALIDATION_LATEST_FILE = Path(
    config_str("ORION_VALIDATION_LATEST_FILE", str(ORION_VALIDATION_REPORT_DIR / "latest_core_smoke.json"))
)
ORION_DOCTOR_REPORT_FILE = _resolve_state_file("ORION_DOCTOR_REPORT_FILE", "diagnostics/doctor_latest.json")
ORION_DOCTOR_HISTORY_FILE = _resolve_state_file("ORION_DOCTOR_HISTORY_FILE", "diagnostics/doctor_history.json")
ORION_DOCTOR_HISTORY_LIMIT = config_int("ORION_DOCTOR_HISTORY_LIMIT", 120)
ORION_PROFILE_COOLDOWN_AUTH_SECONDS = config_int("ORION_PROFILE_COOLDOWN_AUTH_SECONDS", 600)
ORION_PROFILE_COOLDOWN_RATE_LIMIT_SECONDS = config_int("ORION_PROFILE_COOLDOWN_RATE_LIMIT_SECONDS", 120)
ORION_PROFILE_COOLDOWN_TRANSIENT_SECONDS = config_int("ORION_PROFILE_COOLDOWN_TRANSIENT_SECONDS", 60)
ORION_APPROVAL_TTL_SECONDS = config_int("ORION_APPROVAL_TTL_SECONDS", 180)
ORION_IDEMPOTENCY_FILE = _resolve_state_file("ORION_IDEMPOTENCY_FILE", "runtime/idempotency.json")
ORION_IDEMPOTENCY_TTL_SECONDS = config_int("ORION_IDEMPOTENCY_TTL_SECONDS", 86400)
ORION_SCHEDULER_ENABLED = config_bool("ORION_SCHEDULER_ENABLED", True)
ORION_SCHEDULER_POLL_SECONDS = config_int("ORION_SCHEDULER_POLL_SECONDS", 20)
ORION_LOCAL_COMPANION_ENABLED = config_bool("ORION_LOCAL_COMPANION_ENABLED", True)
ORION_LOCAL_LEASE_SECONDS = config_int("ORION_LOCAL_LEASE_SECONDS", 120)

# Wave 2 authority map. This is the declared source-of-truth contract for
# runtime state classes and is intentionally explicit so server code does not
# treat SQLite or JSON side stores as peer authorities.
RUNTIME_STATE_AUTHORITIES: Dict[str, str] = {
    "live_runs": "postgres",
    "runtime_outbox": "postgres",
    "local_queue_claims": "postgres",
    "server_runtime_sessions": "postgres",
    "local_runtime_checkpoint": "sqlite_checkpoint",
    "channel_events_checkpoint": "sqlite_checkpoint",
    "chat_stream_checkpoint": "sqlite_checkpoint",
    "notification_checkpoint": "sqlite_checkpoint",
    "artifact_records_and_objects": "artifact_service",
}

RUNTIME_STATE_JSON_SIDE_STORES: Dict[str, str] = {
    "ORION_HISTORY_FILE": "legacy_json_mirror",
    "ORION_CHANNEL_EVENTS_FILE": "legacy_json_mirror",
    "ORION_CHANNEL_DEAD_LETTER_FILE": "legacy_json_mirror",
    "ORION_SCHEDULES_FILE": "config_state",
    "ORION_WEBHOOK_TRIGGERS_FILE": "config_state",
    "ORION_SETUP_SESSIONS_FILE": "config_state",
    "ORION_PROVIDER_PROFILES_FILE": "config_state",
    "ORION_RUNTIME_SKILLS_FILE": "config_state",
    "ORION_TOOL_STATE_FILE": "config_state",
    "ORION_APP_REGISTRY_FILE": "config_state",
    "ORION_IDEMPOTENCY_FILE": "legacy_json_mirror",
}

ACTIVE_GOVERNANCE_MIGRATION_DISCIPLINE: Dict[str, Any] = {
    "id": "bootstrap_schema_manifest_v1",
    "mode": "bootstrap_schema_manifest_v1",
    "control_plane_initializer": "server_modules.control_plane_repository.ensure_control_plane_schema",
    "runtime_initializer": "server_modules.run_state_repository.ensure_live_run_tables",
    "checkpoint_initializer": "server_modules.runtime_state_store.init_runtime_state_db",
    "artifact_authority": "server_modules.artifact_service",
}


def _compat_env(primary: str, legacy: str, default: str) -> str:
    value = config_value(primary)
    if value is not None:
        return str(value)
    value = config_value(legacy)
    if value is not None:
        return str(value)
    return default

ORION_TELEGRAM_AUTOPILOT_ENABLED = config_bool("ORION_TELEGRAM_AUTOPILOT_ENABLED", True)
ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS = config_float("ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS", 3.0)
ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES = config_int("ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES", 20)
ORION_TELEGRAM_AUTOPILOT_RUN_TIMEOUT_SECONDS = config_int("ORION_TELEGRAM_AUTOPILOT_RUN_TIMEOUT_SECONDS", 180)
ORION_TELEGRAM_AUTOPILOT_ENGINE = config_str("ORION_TELEGRAM_AUTOPILOT_ENGINE", "codex").strip() or "codex"
ORION_TELEGRAM_AUTOPILOT_WORKSPACE_ID = (config_str("ORION_TELEGRAM_AUTOPILOT_WORKSPACE_ID", "").strip() or None)
ORION_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX = (
    _compat_env("EMPYRALIS_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX", "ORION_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX", "0") == "1"
)
ORION_TELEGRAM_AUTOPILOT_PREFIX = (
    _compat_env("EMPYRALIS_TELEGRAM_AUTOPILOT_PREFIX", "ORION_TELEGRAM_AUTOPILOT_PREFIX", "/empyralis").strip()
    or "/empyralis"
)
ORION_TELEGRAM_AUTOPILOT_PROFILE = (
    _compat_env("EMPYRALIS_TELEGRAM_AUTOPILOT_PROFILE", "ORION_TELEGRAM_AUTOPILOT_PROFILE", "assistant").strip().lower()
    or "assistant"
)
ORION_TELEGRAM_AUTOPILOT_SEND_ACK = config_bool("ORION_TELEGRAM_AUTOPILOT_SEND_ACK", False)
ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT = config_bool("ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT", False)
ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE = config_str("ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE", "polling").strip().lower() or "polling"
if ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE not in {"polling", "webhook"}:
    ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE = "polling"
ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET = config_str("ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET", "").strip()
ORION_TELEGRAM_AUTOPILOT_TRUST_MODE = config_str("ORION_TELEGRAM_AUTOPILOT_TRUST_MODE", "guarded").strip().lower() or "guarded"
ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET = (
    config_str("ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET", "local_companion").strip().lower() or "local_companion"
)
ORION_TELEGRAM_AUTOPILOT_STATE_FILE = _resolve_state_file(
    "ORION_TELEGRAM_AUTOPILOT_STATE_FILE",
    "channels/telegram/autopilot_state.json",
)
ORION_TELEGRAM_AUTOPILOT_MAX_REPLY_CHARS = config_int("ORION_TELEGRAM_AUTOPILOT_MAX_REPLY_CHARS", 1400)
ORION_WHATSAPP_AUTOPILOT_ENABLED = config_bool("ORION_WHATSAPP_AUTOPILOT_ENABLED", True)
ORION_WHATSAPP_AUTOPILOT_PROFILE = (
    _compat_env("EMPYRALIS_WHATSAPP_AUTOPILOT_PROFILE", "ORION_WHATSAPP_AUTOPILOT_PROFILE", "assistant").strip().lower()
    or "assistant"
)
ORION_WHATSAPP_AUTOPILOT_ENGINE = config_str("ORION_WHATSAPP_AUTOPILOT_ENGINE", "codex").strip() or "codex"
ORION_WHATSAPP_AUTOPILOT_WORKSPACE_ID = (config_str("ORION_WHATSAPP_AUTOPILOT_WORKSPACE_ID", "").strip() or None)
ORION_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX = (
    _compat_env("EMPYRALIS_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX", "ORION_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX", "0") == "1"
)
ORION_WHATSAPP_AUTOPILOT_PREFIX = (
    _compat_env("EMPYRALIS_WHATSAPP_AUTOPILOT_PREFIX", "ORION_WHATSAPP_AUTOPILOT_PREFIX", "/empyralis").strip()
    or "/empyralis"
)
ORION_WHATSAPP_AUTOPILOT_SEND_ACK = config_bool("ORION_WHATSAPP_AUTOPILOT_SEND_ACK", False)
ORION_WHATSAPP_AUTOPILOT_TRUST_MODE = config_str("ORION_WHATSAPP_AUTOPILOT_TRUST_MODE", "guarded").strip().lower() or "guarded"
ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET = (
    config_str("ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET", "local_companion").strip().lower() or "local_companion"
)
ORION_WHATSAPP_AUTOPILOT_RUN_TIMEOUT_SECONDS = config_int("ORION_WHATSAPP_AUTOPILOT_RUN_TIMEOUT_SECONDS", 180)
ORION_WHATSAPP_AUTOPILOT_MAX_REPLY_CHARS = config_int("ORION_WHATSAPP_AUTOPILOT_MAX_REPLY_CHARS", 700)
ORION_WHATSAPP_AUTOPILOT_STATE_FILE = _resolve_state_file(
    "ORION_WHATSAPP_AUTOPILOT_STATE_FILE",
    "channels/whatsapp/autopilot_state.json",
)
ORION_WHATSAPP_AUTOPILOT_WEBHOOK_SECRET = config_str("ORION_WHATSAPP_AUTOPILOT_WEBHOOK_SECRET", "").strip()
OPENAI_API_URL = config_str("OPENAI_API_URL", "https://api.openai.com/v1/models")
OPENAI_RESPONSES_URL = config_str("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses")
OPENAI_ORG_ID = config_value("OPENAI_ORG_ID")
OPENAI_PROJECT_ID = config_value("OPENAI_PROJECT_ID")
OPENAI_ACCESS_TOKEN = config_value("OPENAI_ACCESS_TOKEN")
OPENAI_OAUTH_TOKEN = config_value("OPENAI_OAUTH_TOKEN")
CODEX_OAUTH_TOKEN = config_value("CODEX_OAUTH_TOKEN")
OPENAI_HEALTHCHECK = config_bool("OPENAI_HEALTHCHECK", True)
ORION_AUTH_MODE = (config_str("ORION_AUTH_MODE", "codex").strip().lower() or "codex")
ORION_DISABLE_OPENAI_API_KEY = config_bool("ORION_DISABLE_OPENAI_API_KEY", True)
ORION_SINGLE_AGENT_MODE = _compat_env("EMPYRALIS_SINGLE_AGENT_MODE", "ORION_SINGLE_AGENT_MODE", "0") == "1"
ORION_SINGLE_AGENT_ROLE = "orchestrator"


def _resolve_agent_machine_mode(raw_mode: Any) -> str:
    mode = str(raw_mode or "personal").strip().lower() or "personal"
    if mode not in {"personal", "agent"}:
        return "personal"
    if mode == "agent" and _resolved_environment() in {"staging", "production", "prod"}:
        raise RuntimeError(
            "AGENT_MACHINE_MODE=agent is not allowed in staging/production. "
            "Use AGENT_MACHINE_MODE=personal."
        )
    return mode


AGENT_MACHINE_MODE = _resolve_agent_machine_mode(config_str("AGENT_MACHINE_MODE", "personal"))
AGENT_MACHINE_OWNER = config_str("AGENT_MACHINE_OWNER", "").strip()
CODEX_AUTH_FILE = Path(
    config_str("CODEX_AUTH_FILE", str(Path.home() / ".codex" / "auth.json"))
).expanduser()
CODEX_MODEL = config_str("CODEX_MODEL", "gpt-4.1")
ORION_CODEX_SYSTEM_PROMPT = config_str(
    "ORION_CODEX_SYSTEM_PROMPT",
    "You are Empyralis runtime assistant. Be concise, accurate, and action-focused.",
)
ORION_PLANNER_SYSTEM_PROMPT = config_str(
    "ORION_PLANNER_SYSTEM_PROMPT",
    "You are Empyralis Planner. Produce deterministic execution plans. Be explicit about side effects.",
)
ORION_OPERATOR_SYSTEM_PROMPT = config_str(
    "ORION_OPERATOR_SYSTEM_PROMPT",
    "You are Empyralis Operator. Execute safely and report outcomes clearly.",
)
DEFAULT_LOCAL_COMPANION_ALLOW_PREFIXES = default_local_companion_allow_prefixes(Path(__file__).resolve().parent)
VAULT_FILE = _resolve_state_file(
    "CREDENTIAL_VAULT_FILE",
    "vault/credentials.json",
)


def agent_machine_full_trust_enabled(owner_user_id: Optional[str] = None) -> bool:
    configured_owner = str(AGENT_MACHINE_OWNER or "").strip()
    effective_owner = str(owner_user_id or "").strip()
    return (
        AGENT_MACHINE_MODE == "agent"
        and bool(configured_owner)
        and bool(effective_owner)
        and secrets.compare_digest(effective_owner, configured_owner)
    )


def agent_machine_inherited_owner_user_id(owner_user_id: Optional[str] = None) -> str:
    effective_owner = str(owner_user_id or "").strip()
    if effective_owner:
        return effective_owner
    if AGENT_MACHINE_MODE != "agent":
        return ""
    return str(AGENT_MACHINE_OWNER or "").strip()


VAULT_KEY_FILE = _resolve_state_file(
    "CREDENTIAL_VAULT_KEY_FILE",
    "vault/key",
)
VAULT_KEY_ENV = config_value("CREDENTIAL_VAULT_KEY")
ORION_VAULT_CIPHER_PREFIX = config_str("ORION_VAULT_CIPHER_PREFIX", "orion.v2:")
ORION_VAULT_KDF_ITERATIONS = max(
    120000,
    min(config_int("ORION_VAULT_KDF_ITERATIONS", 390000), 3000000),
)
ORION_VAULT_LEGACY_OPENSSL_DECRYPT = config_bool("ORION_VAULT_LEGACY_OPENSSL_DECRYPT", True)
ORION_VAULT_LEGACY_OPENSSL_ENCRYPT_FALLBACK = (
    config_bool("ORION_VAULT_LEGACY_OPENSSL_ENCRYPT_FALLBACK", True)
)
PROVIDER_TIMEOUT_SECONDS = config_int("PROVIDER_TIMEOUT_SECONDS", 12)
CONTROL_PLANE_ORIGINS = [origin.strip() for origin in config_str("CONTROL_PLANE_ORIGINS", FRONTEND_ORIGINS).split(",") if origin.strip()]
CONTROL_PLANE_RATE_LIMIT_PER_MINUTE = config_int("CONTROL_PLANE_RATE_LIMIT_PER_MINUTE", 60)
CONTROL_PLANE_RATE_LIMIT_BURST = config_int("CONTROL_PLANE_RATE_LIMIT_BURST", 20)
ORION_RUNTIME_API_VERSION = config_str("ORION_RUNTIME_API_VERSION", "1.0.0").strip() or "1.0.0"
ORION_RUNTIME_API_MIN_CLI_VERSION = (
    config_str("ORION_RUNTIME_API_MIN_CLI_VERSION", "2026.2.0").strip() or "2026.2.0"
)
ORION_RUNTIME_CONTRACT_SCHEMA_VERSION = (
    config_str("ORION_RUNTIME_CONTRACT_SCHEMA_VERSION", "2026.2.0").strip() or "2026.2.0"
)
ORION_MEMORY_ENABLED = config_bool("ORION_MEMORY_ENABLED", True)
ORION_MEMORY_READ_K = max(1, min(config_int("ORION_MEMORY_READ_K", 5), 20))
ORION_MEMORY_MAX_TEXT_CHARS = max(400, min(config_int("ORION_MEMORY_MAX_TEXT_CHARS", 2400), 12000))
ORION_MEMORY_RETENTION_DAYS_DEFAULT = max(
    1,
    min(config_int("ORION_MEMORY_RETENTION_DAYS_DEFAULT", 365), 3650),
)
ORION_MEMORY_DB_PATH = (
    config_str(
        "ORION_MEMORY_DB_PATH",
        str(EMPYRALIS_STATE_HOME / "memory" / "agency_memory.db"),
    ).strip()
    or str(EMPYRALIS_STATE_HOME / "memory" / "agency_memory.db")
)
ORION_MEMORY_LANCEDB_URI = (
    config_str(
        "ORION_MEMORY_LANCEDB_URI",
        str(EMPYRALIS_STATE_HOME / "memory" / "lancedb"),
    ).strip()
    or str(EMPYRALIS_STATE_HOME / "memory" / "lancedb")
)

CONNECTOR_CATALOG = {
    "google_workspace": {
        "label": "Google Workspace",
        "auth": ["access_token"],
    },
    "gmail": {
        "label": "Gmail",
        "auth": ["access_token"],
        "parent": "google_workspace",
    },
    "google_calendar": {
        "label": "Google Calendar",
        "auth": ["access_token"],
        "parent": "google_workspace",
    },
    "google_drive": {
        "label": "Google Drive",
        "auth": ["access_token"],
        "parent": "google_workspace",
    },
    "microsoft_365": {
        "label": "Microsoft 365",
        "auth": ["access_token"],
    },
    "outlook": {
        "label": "Outlook",
        "auth": ["access_token"],
        "parent": "microsoft_365",
    },
    "outlook_calendar": {
        "label": "Outlook Calendar",
        "auth": ["access_token"],
        "parent": "microsoft_365",
    },
    "smtp": {
        "label": "SMTP Email",
        "auth": ["host", "port", "username", "password", "use_tls"],
        "surface_type": "app",
    },
    "github": {
        "label": "GitHub",
        "auth": ["personal_access_token", "app_id", "installation_id", "private_key_pem"],
    },
    "dropbox": {
        "label": "Dropbox",
        "auth": ["access_token"],
    },
    "figma": {
        "label": "Figma",
        "auth": ["access_token"],
    },
    "todoist": {
        "label": "Todoist",
        "auth": ["access_token"],
    },
    "airtable": {
        "label": "Airtable",
        "auth": ["access_token"],
    },
    "canva": {
        "label": "Canva",
        "auth": ["access_token"],
    },
    "asana": {
        "label": "Asana",
        "auth": ["access_token"],
    },
    "hubspot": {
        "label": "HubSpot",
        "auth": ["access_token"],
    },
    "zoom": {
        "label": "Zoom",
        "auth": ["access_token"],
    },
    "calendly": {
        "label": "Calendly",
        "auth": ["access_token"],
    },
    "clickup": {
        "label": "ClickUp",
        "auth": ["access_token"],
    },
    "jira": {
        "label": "Jira",
        "auth": ["access_token"],
    },
    "stripe": {
        "label": "Stripe",
        "auth": ["access_token"],
    },
    "salesforce": {
        "label": "Salesforce",
        "auth": ["access_token"],
    },
    "webflow": {
        "label": "Webflow",
        "auth": ["access_token"],
    },
    "monday": {
        "label": "monday.com",
        "auth": ["access_token"],
    },
    "box": {
        "label": "Box",
        "auth": ["access_token"],
    },
    "gitlab": {
        "label": "GitLab",
        "auth": ["access_token"],
    },
    "confluence": {
        "label": "Confluence",
        "auth": ["access_token"],
    },
    "miro": {
        "label": "Miro",
        "auth": ["access_token"],
    },
    "intercom": {
        "label": "Intercom",
        "auth": ["access_token"],
    },
    "docusign": {
        "label": "Docusign",
        "auth": ["access_token"],
    },
    "square": {
        "label": "Square",
        "auth": ["access_token"],
    },
    "typeform": {
        "label": "Typeform",
        "auth": ["access_token"],
    },
    "vercel": {
        "label": "Vercel",
        "auth": ["access_token"],
    },
    "higgsfield": {
        "label": "Higgsfield",
        "auth": ["access_token"],
    },
    "zapier": {
        "label": "Zapier",
        "auth": ["access_token"],
    },
    "paypal": {
        "label": "PayPal",
        "auth": ["access_token"],
    },
    "sentry": {
        "label": "Sentry",
        "auth": ["access_token"],
    },
    "attio": {
        "label": "Attio",
        "auth": ["access_token"],
    },
    "cloudflare": {
        "label": "Cloudflare",
        "auth": ["access_token"],
    },
    "gusto": {
        "label": "Gusto",
        "auth": ["access_token"],
    },
    "deel": {
        "label": "Deel",
        "auth": ["access_token"],
    },
    "remote_com": {
        "label": "Remote",
        "auth": ["access_token"],
    },
    "ashby": {
        "label": "Ashby",
        "auth": ["access_token"],
    },
    "klaviyo": {
        "label": "Klaviyo",
        "auth": ["access_token"],
    },
    "customer_io": {
        "label": "Customer.io",
        "auth": ["access_token"],
    },
    "netlify": {
        "label": "Netlify",
        "auth": ["access_token"],
    },
    "supabase": {
        "label": "Supabase",
        "auth": ["access_token"],
    },
    "planetscale": {
        "label": "PlanetScale",
        "auth": ["access_token"],
    },
    "neon": {
        "label": "Neon",
        "auth": ["access_token"],
    },
    "railway": {
        "label": "Railway",
        "auth": ["access_token"],
    },
    "heroku": {
        "label": "Heroku",
        "auth": ["access_token"],
    },
    "sourcegraph": {
        "label": "Sourcegraph",
        "auth": ["access_token"],
    },
    "replit": {
        "label": "Replit",
        "auth": ["access_token"],
    },
    "postman": {
        "label": "Postman",
        "auth": ["access_token"],
    },
    "buildkite": {
        "label": "Buildkite",
        "auth": ["access_token"],
    },
    "socket": {
        "label": "Socket",
        "auth": ["access_token"],
    },
    "whimsical": {
        "label": "Whimsical",
        "auth": ["access_token"],
    },
    "ramp": {
        "label": "Ramp",
        "auth": ["access_token"],
    },
    "brex": {
        "label": "Brex",
        "auth": ["access_token"],
    },
    "mercury": {
        "label": "Mercury",
        "auth": ["access_token"],
    },
    "robinhood": {
        "label": "Robinhood",
        "auth": ["access_token"],
    },
    "amplitude": {
        "label": "Amplitude",
        "auth": ["access_token"],
    },
    "mixpanel": {
        "label": "Mixpanel",
        "auth": ["access_token"],
    },
    "posthog": {
        "label": "PostHog",
        "auth": ["access_token"],
    },
    "meta_ads": {
        "label": "Meta Ads",
        "auth": ["access_token"],
    },
    "semrush": {
        "label": "Semrush",
        "auth": ["access_token"],
    },
    "ahrefs": {
        "label": "Ahrefs",
        "auth": ["access_token"],
    },
    "close_crm": {
        "label": "Close",
        "auth": ["access_token"],
    },
    "apollo_io": {
        "label": "Apollo.io",
        "auth": ["access_token"],
    },
    "outreach": {
        "label": "Outreach",
        "auth": ["access_token"],
    },
    "salesloft": {
        "label": "Salesloft",
        "auth": ["access_token"],
    },
    "clay": {
        "label": "Clay",
        "auth": ["access_token"],
    },
    "fireflies": {
        "label": "Fireflies.ai",
        "auth": ["access_token"],
    },
    "fathom": {
        "label": "Fathom",
        "auth": ["access_token"],
    },
    "coda": {
        "label": "Superhuman Docs (formerly Coda)",
        "auth": ["access_token"],
    },
    "s3": {
        "label": "Amazon S3",
        "auth": ["aws_access_key_id", "aws_secret_access_key", "region"],
    },
    "notion": {
        "label": "Notion",
        "auth": ["integration_token", "access_token"],
    },
    "linear": {
        "label": "Linear",
        "auth": ["api_key", "access_token"],
    },
    "instagram_business": {
        "label": "Instagram Business",
        "auth": ["access_token", "instagram_account_id", "page_id"],
    },
}

# Annotate all connector catalog entries with surface_type "app"
for _entry in CONNECTOR_CATALOG.values():
    if "surface_type" not in _entry:
        _entry["surface_type"] = "app"


CHANNEL_REGISTRY = {
    "telegram_bot": {
        "label": "Telegram Bot",
        "auth": ["bot_token", "chat_id"],
        "surface_type": "channel",
    },
    "wechat_work": {
        "label": "WeChat Work",
        "auth": ["webhook_url"],
        "surface_type": "channel",
    },
    "whatsapp_twilio": {
        "label": "WhatsApp (Twilio)",
        "auth": ["account_sid", "auth_token", "from_number", "to_number"],
        "surface_type": "channel",
    },
    "apple_messages_business": {
        "label": "Apple Messages for Business",
        "auth": ["msp_provider", "business_account_id", "api_key", "webhook_secret"],
        "surface_type": "channel",
    },
    "discord_bot": {
        "label": "Discord (Bot API)",
        "auth": ["bot_token", "channel_id", "guild_id", "application_id", "public_key", "application_public_key"],
        "surface_type": "channel",
    },
    "slack": {
        "label": "Slack",
        "auth": ["bot_token", "user_token", "team_id", "team_name"],
        "surface_type": "channel",
    },
    "irc": {
        "label": "IRC (Server + Nick)",
        "auth": ["server", "port", "nick", "channel", "password", "use_tls"],
        "surface_type": "channel",
    },
}

TELEGRAM_AUTOPILOT_PROFILE_CATALOG: Dict[str, Dict[str, Any]] = {
    "assistant": {
        "label": "Assistant",
        "description": "Best for chat UX. Free text starts runs; help/status commands are available.",
        "allow_free_text": True,
        "allow_status": True,
        "allow_help": True,
    },
    "commands_only": {
        "label": "Commands Only",
        "description": "Only explicit commands are accepted: run/status/help.",
        "allow_free_text": False,
        "allow_status": True,
        "allow_help": True,
    },
    "run_only": {
        "label": "Run Only",
        "description": "Focus on run requests; status command is disabled.",
        "allow_free_text": True,
        "allow_status": False,
        "allow_help": True,
    },
}
WHATSAPP_AUTOPILOT_PROFILE_CATALOG: Dict[str, Dict[str, Any]] = {
    "assistant": {
        "label": "Assistant",
        "description": "Best for chat UX. Free text starts runs; help/status commands are available.",
        "allow_free_text": True,
        "allow_status": True,
        "allow_help": True,
    },
    "commands_only": {
        "label": "Commands Only",
        "description": "Only explicit commands are accepted: run/status/help.",
        "allow_free_text": False,
        "allow_status": True,
        "allow_help": True,
    },
    "run_only": {
        "label": "Run Only",
        "description": "Focus on run requests; status command is disabled.",
        "allow_free_text": True,
        "allow_status": False,
        "allow_help": True,
    },
}

RUNTIME_BUILTIN_SKILLS: List[Dict[str, Any]] = [
    {
        "id": "ops-commander",
        "title": "Ops Commander",
        "intent": "Diagnose incidents, propose fixes, and keep execution logs concise.",
        "tools": ["read_logs", "query_metrics", "send_message"],
        "guardrail": "Requires approval for outbound actions in guarded mode.",
        "runtime_tools": ["send_message", "draft_email", "create_calendar_event"],
        "preferred_target": "cloud",
        "preferred_trust_mode": "guarded",
        "policy_mode": "warn",
        "version": "1.0.0",
        "author": "Empyralis",
        "category": "Operations",
    },
    {
        "id": "founder-assistant",
        "title": "Founder Assistant",
        "intent": "Turn rough ideas into concrete tasks, priorities, and weekly plans.",
        "tools": ["summarize", "create_task", "draft_email"],
        "guardrail": "Never auto-send externally without explicit approval.",
        "runtime_tools": ["draft_email"],
        "preferred_target": "cloud",
        "preferred_trust_mode": "guarded",
        "policy_mode": "warn",
        "version": "1.0.0",
        "author": "Empyralis",
        "category": "Execution",
    },
    {
        "id": "exam-coach",
        "title": "Exam Coach",
        "intent": "Build study plans, drills, and daily check-ins with accountability.",
        "tools": ["plan", "memory.search", "send_message"],
        "guardrail": "No destructive actions. Keep focus on learning workflow.",
        "runtime_tools": ["send_message"],
        "preferred_target": "cloud",
        "preferred_trust_mode": "guarded",
        "policy_mode": "warn",
        "version": "1.0.0",
        "author": "Empyralis",
        "category": "Learning",
    },
]


def build_governance_backup_restore_manifest(
    runtime_checkpoint_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    from server_modules.artifact_service import build_artifact_backup_manifest
    from server_modules.runtime_state_store import build_runtime_checkpoint_manifest

    checkpoint_path = Path(runtime_checkpoint_db_path or ORION_RUNTIME_STATE_DB).expanduser().resolve()
    postgres_configured = bool(str(os.getenv("DATABASE_URL") or "").strip())
    return {
        "manifest_version": "governance_restore_manifest_v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "migration_discipline": dict(ACTIVE_GOVERNANCE_MIGRATION_DISCIPLINE),
        "stores": [
            {
                "store_id": "control_plane_postgres",
                "authority_mode": "postgres_authoritative",
                "connection_env": "DATABASE_URL",
                "configured": postgres_configured,
                "restore_mode": "operator_managed_external_restore",
                "authoritative_tables": [
                    "tenants",
                    "users",
                    "workspaces",
                    "workspace_memberships",
                    "agent_threads",
                    "agent_sessions",
                    "agent_turns",
                    "workspace_agent_installs",
                    "agent_channel_events",
                    "activity_ledger_events",
                    "governance_holds",
                ],
            },
            {
                "store_id": "runtime_postgres",
                "authority_mode": "postgres_authoritative",
                "connection_env": "DATABASE_URL",
                "configured": postgres_configured,
                "restore_mode": "operator_managed_external_restore",
                "authoritative_state_classes": sorted(
                    [
                        state_class
                        for state_class, authority in RUNTIME_STATE_AUTHORITIES.items()
                        if authority == "postgres"
                    ]
                ),
            },
            build_runtime_checkpoint_manifest(checkpoint_path),
            build_artifact_backup_manifest(),
        ],
        "json_side_stores": dict(RUNTIME_STATE_JSON_SIDE_STORES),
    }


def run_governance_restore_rehearsal(
    runtime_checkpoint_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    from server_modules.artifact_service import rehearse_artifact_restore
    from server_modules.runtime_state_store import rehearse_runtime_checkpoint_restore

    manifest = build_governance_backup_restore_manifest(runtime_checkpoint_db_path)
    checkpoint_path = Path(runtime_checkpoint_db_path or ORION_RUNTIME_STATE_DB).expanduser().resolve()
    runtime_checkpoint = rehearse_runtime_checkpoint_restore(checkpoint_path)
    artifact_restore = rehearse_artifact_restore()
    postgres_manifest_checks = {
        "ok": all(
            bool(store.get("configured"))
            for store in manifest["stores"]
            if store.get("store_id") in {"control_plane_postgres", "runtime_postgres"}
        ),
        "mode": "operator_managed_external_restore",
        "required_store_ids": ["control_plane_postgres", "runtime_postgres"],
    }
    return {
        "ok": bool(runtime_checkpoint.get("ok")) and bool(artifact_restore.get("ok")) and bool(postgres_manifest_checks["ok"]),
        "manifest": manifest,
        "rehearsals": {
            "runtime_checkpoint_sqlite": runtime_checkpoint,
            "artifact_store": artifact_restore,
            "postgres_manifest_checks": postgres_manifest_checks,
        },
    }

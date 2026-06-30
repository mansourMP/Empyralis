from __future__ import annotations

import ssl
from datetime import datetime
from typing import Any, Callable, Dict, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

import certifi


def probe_openai_credential(
    *,
    openai_key: str,
    openai_env_source: str,
    openai_healthcheck: bool,
    openai_api_url: str,
    openai_org_id: str | None,
    openai_project_id: str | None,
    resolve_default_vault_credential: Callable[[str, Any], Dict[str, Any]],
    provider_adapters: Mapping[str, Any],
) -> Dict[str, Any]:
    key_valid = False
    openai_status: Any = None
    openai_error: str | None = None
    openai_credential_source = "none"
    openai_vault_present = False

    if openai_key and openai_healthcheck:
        headers = {"Authorization": f"Bearer {openai_key}"}
        if openai_org_id:
            headers["OpenAI-Organization"] = openai_org_id
        if openai_project_id:
            headers["OpenAI-Project"] = openai_project_id
        try:
            req = urlrequest.Request(openai_api_url, headers=headers, method="GET")
            context = ssl.create_default_context(cafile=certifi.where())
            with urlrequest.urlopen(req, timeout=3, context=context) as resp:
                key_valid = resp.status == 200
                openai_status = resp.status
                openai_credential_source = openai_env_source
        except urlerror.HTTPError as exc:
            openai_status = exc.code
            key_valid = False
            openai_error = str(exc)
        except Exception as exc:
            openai_status = "unreachable"
            key_valid = False
            openai_error = str(exc)
    elif openai_key and not openai_healthcheck:
        key_valid = True
        openai_status = "skipped"
        openai_credential_source = openai_env_source

    if not key_valid:
        try:
            openai_secret = resolve_default_vault_credential("openai", None)
            openai_vault_present = True
            probe = provider_adapters["openai"].validate(openai_secret)
            status = probe.get("status")
            openai_status = f"vault:{status}"
            if probe.get("ok"):
                key_valid = True
                openai_credential_source = "vault"
                openai_error = None
        except Exception as exc:
            if openai_error is None:
                openai_error = str(exc)
            if openai_status is None:
                openai_status = "vault:unavailable"

    return {
        "openai_key_present": bool(openai_key),
        "openai_vault_present": openai_vault_present,
        "openai_key_valid": key_valid,
        "openai_credential_source": openai_credential_source,
        "openai_status": openai_status,
        "openai_error": openai_error,
    }


def collect_runtime_counts(
    *,
    weekly_schedules: Dict[str, Any],
    schedules_lock: Any,
    setup_sessions: Dict[str, Any],
    setup_sessions_lock: Any,
    cleanup_setup_sessions_locked: Callable[[], None],
    provider_profiles: Dict[str, Any],
    profiles_lock: Any,
    idempotency_records: Dict[str, Any],
    idempotency_lock: Any,
    prune_idempotency_locked: Callable[[], None],
) -> Dict[str, int]:
    with schedules_lock:
        schedule_count = len(weekly_schedules)
    with setup_sessions_lock:
        cleanup_setup_sessions_locked()
        setup_session_count = len(setup_sessions)
        active_setup_session_count = len(
            [
                item
                for item in setup_sessions.values()
                if isinstance(item, dict)
                and str(item.get("state") or "") not in {"complete", "cancelled", "expired"}
            ]
        )
    with profiles_lock:
        profile_count = len(provider_profiles)
    with idempotency_lock:
        prune_idempotency_locked()
        idempotency_count = len(idempotency_records)

    return {
        "weekly_schedules": schedule_count,
        "setup_sessions_total": setup_session_count,
        "setup_sessions_active": active_setup_session_count,
        "provider_profiles_total": profile_count,
        "idempotency_records": idempotency_count,
    }


def collect_local_queue_counts(
    *,
    cleanup_stale_local_claims: Callable[[], None],
    local_queue_lock: Any,
    local_worker_registry: Dict[str, Any],
    local_pending_run_ids: Any,
    local_claimed_runs: Dict[str, Any],
    utc_now: Callable[[], datetime],
    is_worker_online: Callable[[Dict[str, Any], datetime], bool],
) -> Dict[str, int]:
    cleanup_stale_local_claims()
    with local_queue_lock:
        now = utc_now()
        known_workers = len(local_worker_registry)
        online_workers = len(
            [
                record
                for record in local_worker_registry.values()
                if isinstance(record, dict) and is_worker_online(record, now)
            ]
        )
        pending_runs = len(local_pending_run_ids)
        claimed_runs = len(local_claimed_runs)
    return {
        "local_workers_known": known_workers,
        "local_workers_online": online_workers,
        "local_pending_runs": pending_runs,
        "local_claimed_runs": claimed_runs,
    }


def build_runtime_contract_payload(
    *,
    contract_schema_version: str,
    runtime_api_version: str,
    runtime_api_min_cli_version: str,
    auth_mode: str,
    openai_api_key_allowed: bool,
    supported_engines: list[str],
    supported_providers: list[str],
    supported_execution_targets: list[str],
    codex_oauth_interactive_supported: bool,
    codex_token_credential_supported: bool,
    openai_supported_credential_fields: list[str],
    cognitive_operator_policy: Dict[str, Any] | None = None,
    local_execution_capabilities: list[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    payload = {
        "contract_schema_version": str(contract_schema_version or "").strip() or "2026.2.0",
        "runtime_api_version": str(runtime_api_version or "").strip(),
        "runtime_api_min_cli_version": str(runtime_api_min_cli_version or "").strip(),
        "auth_mode": str(auth_mode or "").strip() or "unknown",
        "openai_api_key_allowed": bool(openai_api_key_allowed),
        "supported_engines": [str(item) for item in supported_engines if str(item).strip()],
        "supported_providers": [str(item) for item in supported_providers if str(item).strip()],
        "supported_execution_targets": [str(item) for item in supported_execution_targets if str(item).strip()],
        "codex_oauth_interactive_supported": bool(codex_oauth_interactive_supported),
        "codex_token_credential_supported": bool(codex_token_credential_supported),
        "openai_supported_credential_fields": [
            str(item) for item in openai_supported_credential_fields if str(item).strip()
        ],
    }
    if isinstance(cognitive_operator_policy, dict):
        payload["cognitive_operator_policy"] = cognitive_operator_policy
    if isinstance(local_execution_capabilities, list):
        payload["local_execution_capabilities"] = local_execution_capabilities
    return payload


def build_probe_payload(
    *,
    runtime_valid: bool,
    errors: list[str],
    scheduler_enabled: bool,
    runtime_counts: Dict[str, int],
    local_queue_counts: Dict[str, int],
    generated_at_iso: str,
) -> Dict[str, Any]:
    status = "ok" if runtime_valid else "degraded"
    return {
        "status": status,
        "runtime_valid": runtime_valid,
        "errors": errors,
        "scheduler_enabled": scheduler_enabled,
        "weekly_schedules": runtime_counts["weekly_schedules"],
        "active_setup_sessions": runtime_counts["setup_sessions_active"],
        "provider_profiles": runtime_counts["provider_profiles_total"],
        "idempotency_records": runtime_counts["idempotency_records"],
        "local_workers_known": local_queue_counts["local_workers_known"],
        "local_workers_online": local_queue_counts["local_workers_online"],
        "local_pending_runs": local_queue_counts["local_pending_runs"],
        "local_claimed_runs": local_queue_counts["local_claimed_runs"],
        "generated_at": generated_at_iso,
    }

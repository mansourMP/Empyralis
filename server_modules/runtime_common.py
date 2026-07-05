import hashlib
import http.client
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import ssl
import time
from typing import Any, Dict, Optional
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote_plus, urlencode
import uuid

import certifi
from fastapi import Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from server_modules import runtime_config as config
from server_modules import client_identity_service, downstream_resilience_service, egress_policy
from server_modules import error_response_service, quota_policy_service, quota_response_service
from server_modules import rust_runtime_kernel_client
from server_modules.error_contracts import AUTHORIZATION_ERROR, IDEMPOTENCY_CONFLICT
from server_modules import secrets_broker
from server_modules import shared as shared
from server_modules.telemetry import record_control_plane_request

globals().update({key: value for key, value in vars(config).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(shared).items() if not key.startswith("__")})

EMPYRALIST_WORKFLOW_API_URL = (
    str(
        os.getenv("EMPYRALIST_WORKFLOW_API_URL")
        or os.getenv("ORION_API_URL")
        or "http://127.0.0.1:8001"
    ).strip().rstrip("/")
    or "http://127.0.0.1:8001"
)
_LOCAL_ENV_TOKENS = {"", "dev", "development", "local", "test", "testing"}


def _resolved_environment() -> str:
    return str(os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or os.getenv("ENV") or os.getenv("NODE_ENV") or "").strip().lower()


def _control_plane_origins_required() -> bool:
    return _resolved_environment() not in _LOCAL_ENV_TOKENS


def metrics_inc(key: str, amount: float = 1):
    with METRICS_LOCK:
        RUNTIME_METRICS[key] = RUNTIME_METRICS.get(key, 0) + amount


def metrics_add(key: str, amount: float):
    with METRICS_LOCK:
        RUNTIME_METRICS[key] = RUNTIME_METRICS.get(key, 0) + amount


class RuntimeCommonRustGateError(RuntimeError):
    pass


def _enforce_runtime_common_json_write(path: Path, payload: Dict[str, Any], serialized: str) -> None:
    metadata = {
        "path": str(path),
        "payload_type": type(payload).__name__,
        "keys": sorted(str(key) for key in payload.keys())[:80] if isinstance(payload, dict) else [],
        "payload_bytes": len(serialized.encode("utf-8")),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_runtime_common_json",
            state_class="runtime_common_json_files",
            actor_id="system",
            status="active",
            payload=metadata,
            payload_bytes=int(metadata["payload_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_runtime_common_json":
            raise RuntimeCommonRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise RuntimeCommonRustGateError(exc.reason) from exc


def _safe_write_json(path: Path, payload: Dict[str, Any]):
    serialized = json.dumps(payload, indent=2)
    _enforce_runtime_common_json_write(path, payload, serialized)
    parent = path.parent if path.parent != Path("") else Path(".")
    parent.mkdir(parents=True, exist_ok=True)
    # Use per-write temp file names so concurrent writers cannot race on a shared tmp path.
    tmp_path = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(path)


def _safe_read_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            return parsed
        return fallback
    except Exception:
        return fallback

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _parse_utc_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except Exception:
        return None


def _is_retryable_http_transport_error(exc: Exception) -> bool:
    if isinstance(exc, (http.client.IncompleteRead, ConnectionResetError, TimeoutError, urlerror.URLError)):
        return True
    message = str(exc).lower()
    retryable_markers = (
        "incompleteread",
        "incomplete read",
        "remote end closed connection",
        "connection reset",
        "connection aborted",
        "eof occurred",
        "unexpected eof",
        "ssl",
        "timed out",
        "timeout",
    )
    return any(marker in message for marker in retryable_markers)


def _is_control_plane_mutation(request: Request) -> bool:
    return request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


def _extract_request_api_key(request: Request) -> str:
    direct = request.headers.get("x-api-key")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _check_control_plane_origin(request: Request) -> Optional[JSONResponse]:
    if not _is_control_plane_mutation(request):
        return None
    origin = request.headers.get("origin")
    if not CONTROL_PLANE_ORIGINS:
        if origin and _control_plane_origins_required():
            return error_response_service.json_response_from_platform_error(
                error_response_service.platform_error(
                    code="origin_configuration_required",
                    message="CONTROL_PLANE_ORIGINS must be configured before browser write access is enabled.",
                    error_class=AUTHORIZATION_ERROR,
                    retryable=False,
                    status_code=503,
                    request_id=error_response_service.request_id_from_request(request),
                )
            )
        return None
    if not origin:
        return None
    if origin not in CONTROL_PLANE_ORIGINS:
        return error_response_service.json_response_from_platform_error(
            error_response_service.platform_error(
                code="origin_not_allowed",
                message="Origin is not allowed.",
                error_class=AUTHORIZATION_ERROR,
                retryable=False,
                status_code=403,
                request_id=error_response_service.request_id_from_request(request),
                details={
                    "origin": origin,
                    "allowed_origins": CONTROL_PLANE_ORIGINS,
                },
            )
        )
    return None


def _is_control_plane_rate_limited_path(request_path: str) -> bool:
    path = str(request_path or "").strip()
    if not path:
        return False
    if path in {"/turn", "/api/turn", "/sessions", "/api/sessions"}:
        return False
    if (path.startswith("/threads/") or path.startswith("/api/threads/")) and path.endswith("/turns"):
        return False
    runtime_prefixes = ("/runtime/runtimes/", "/api/runtime/runtimes/")
    if path.startswith(runtime_prefixes):
        if path.endswith("/heartbeat") or "/control/" in path:
            return False
        return True
    if path.startswith(("/runtime/tasks/", "/api/runtime/tasks/")):
        return False
    return True


def _control_plane_rate_limit(request: Request) -> Optional[JSONResponse]:
    if not _is_control_plane_mutation(request):
        return None
    request_path = str(request.url.path or "")
    if not _is_control_plane_rate_limited_path(request_path):
        # Runtime claims/heartbeats and canonical turn streaming are chatty by design.
        # Keep control-plane limits focused on configuration-style mutation APIs.
        return None
    client_host = client_identity_service.resolve_client_ip(request)
    api_key = _extract_request_api_key(request)
    identity = f"{client_host}:{api_key or 'anon'}"
    limit = max(1, CONTROL_PLANE_RATE_LIMIT_PER_MINUTE + CONTROL_PLANE_RATE_LIMIT_BURST)
    decision = quota_policy_service.evaluate_request_window_quota(
        profile_name=quota_policy_service.CONTROL_PLANE_MUTATION_PROFILE.name,
        subject=quota_policy_service.QuotaSubject(
            surface_kind=quota_policy_service.CONTROL_PLANE_MUTATION_PROFILE.surface_kind,
            request_path=request_path,
            client_ip=client_host,
            actor_id=api_key or "anon",
        ),
        buckets=RATE_LIMIT_BUCKETS,
        lock=RATE_LIMIT_LOCK,
        key=identity,
        limit=limit,
    )
    if not decision.allowed:
        return quota_response_service.json_response_from_quota_decision(decision)
    return None







async def control_plane_guard_middleware(request: Request, call_next):
    started_mono = time.monotonic()
    response: Optional[Response] = None
    error_type: Optional[str] = None
    try:
        origin_failure = _check_control_plane_origin(request)
        if origin_failure is not None:
            response = origin_failure
            return response

        rate_limit_failure = _control_plane_rate_limit(request)
        if rate_limit_failure is not None:
            response = rate_limit_failure
            return response

        if not _is_control_plane_mutation(request):
            response = await call_next(request)
            return response

        idempotency_key = request.headers.get("x-idempotency-key") or request.headers.get("X-Idempotency-Key")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            response = await call_next(request)
            return response

        body = await request.body()
        body_hash = hashlib.sha256(body).hexdigest()
        lookup = _idempotency_get(request.method, request.url.path, idempotency_key.strip(), body_hash)
        if lookup.get("hit") and lookup.get("conflict"):
            response = error_response_service.json_response_from_platform_error(
                error_response_service.platform_error(
                    code="idempotency_key_reused_with_different_payload",
                    message="Idempotency key reused with different payload.",
                    error_class=IDEMPOTENCY_CONFLICT,
                    retryable=False,
                    status_code=409,
                    request_id=error_response_service.request_id_from_request(request),
                )
            )
            return response
        if lookup.get("hit") and isinstance(lookup.get("record"), dict):
            record = lookup["record"]
            headers = {"X-Idempotent-Replay": "1"}
            response = JSONResponse(status_code=int(record.get("status_code", 200)), content=record.get("response"), headers=headers)
            return response

        response = await call_next(request)
        if response.status_code >= 500:
            return response

        if response.media_type and "application/json" not in response.media_type.lower():
            return response
        if response.headers.get("content-type") and "application/json" not in response.headers.get("content-type", "").lower():
            return response

        body_bytes = b""
        async for chunk in response.body_iterator:
            body_bytes += chunk
        replay_response = Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        try:
            parsed = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except Exception:
            parsed = None
        if parsed is not None:
            _idempotency_store(request.method, request.url.path, idempotency_key.strip(), body_hash, response.status_code, parsed)
        response = replay_response
        return response
    except Exception as exc:
        error_type = type(exc).__name__
        raise
    finally:
        record_control_plane_request(
            method=str(request.method or "GET").upper(),
            path=str(request.url.path or "").strip() or "/",
            status_code=int(getattr(response, "status_code", 500) or 500),
            duration_ms=max(0.0, (time.monotonic() - started_mono) * 1000.0),
            recorded_at=_utc_now_iso(),
            error_type=error_type,
        )

def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    from server_modules.auth import get_current_user

    user = get_current_user(
        request=request,
        authorization=authorization,
        x_api_key=x_api_key,
    )
    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def require_viewer_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    return require_api_key(
        request=request,
        authorization=authorization,
        x_api_key=x_api_key,
    )


def require_member_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    from server_modules.auth import enforce_minimum_role

    return enforce_minimum_role(
        require_api_key(
            request=request,
            authorization=authorization,
            x_api_key=x_api_key,
        ),
        "member",
    )


def require_admin_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    from server_modules.auth import enforce_minimum_role

    return enforce_minimum_role(
        require_api_key(
            request=request,
            authorization=authorization,
            x_api_key=x_api_key,
        ),
        "owner",
    )

def http_json_request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Any] = None,
    method: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    request_headers = dict(headers or {})
    body: Optional[bytes] = None
    verb = (method or ("POST" if payload is not None else "GET")).upper()
    egress_policy.enforce_outbound_request(url=url, method=verb)
    if payload is not None:
        if isinstance(payload, (bytes, bytearray)):
            body = bytes(payload)
        elif request_headers.get("Content-Type") == "application/x-www-form-urlencoded":
            if isinstance(payload, str):
                body = payload.encode("utf-8")
            else:
                body = urlencode(payload, doseq=True).encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
    req = urlrequest.Request(url, data=body, headers=request_headers, method=verb)
    context = ssl.create_default_context(cafile=certifi.where())
    def _execute_once() -> Dict[str, Any]:
        try:
            with urlrequest.urlopen(req, timeout=timeout, context=context) as resp:
                raw = resp.read().decode("utf-8")
                parsed: Any = None
                try:
                    parsed = json.loads(raw) if raw else None
                except Exception:
                    parsed = None
                return {
                    "status": int(getattr(resp, "status", resp.getcode())),
                    "text": raw,
                    "json": parsed,
                    "headers": dict(resp.headers.items()),
                }
        except urlerror.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            parsed: Any = None
            try:
                parsed = json.loads(raw) if raw else None
            except Exception:
                parsed = None
            return {
                "status": int(getattr(exc, "code", 500)),
                "text": raw,
                "json": parsed,
                "headers": dict(getattr(exc, "headers", {}).items()) if getattr(exc, "headers", None) else {},
            }
        except Exception as exc:
            # Some provider edges occasionally drop urllib TLS sessions in this
            # environment even though the same request succeeds via curl. Preserve
            # HTTPError handling above so bad credentials still return provider
            # status codes; only retry low-level transport failures here.
            is_telegram = "api.telegram.org" in str(url).lower()
            if not (is_telegram or _is_retryable_http_transport_error(exc)) or not shutil.which("curl"):
                raise
            command = [
                "curl",
                "-sS",
                "-L",
                "-X",
                verb,
                "--max-time",
                str(max(1, int(timeout))),
                "-w",
                "\n__CODEX_STATUS__:%{http_code}",
            ]
            for key, value in request_headers.items():
                command.extend(["-H", f"{key}: {value}"])
            if body is not None:
                command.extend(["--data-binary", "@-"])
            command.append(url)
            completed = subprocess.run(
                command,
                input=body,
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                raise exc
            raw_output = completed.stdout.decode("utf-8", errors="replace")
            marker = "\n__CODEX_STATUS__:"
            body_text, _, status_text = raw_output.rpartition(marker)
            if not status_text:
                body_text = raw_output
                status_code = 200
            else:
                try:
                    status_code = int(status_text.strip() or "0")
                except Exception:
                    status_code = 0
            parsed: Any = None
            try:
                parsed = json.loads(body_text) if body_text else None
            except Exception:
                parsed = None
            return {
                "status": status_code,
                "text": body_text,
                "json": parsed,
                "headers": {},
            }

    return downstream_resilience_service.call_http_json_with_retries(
        name=f"http_json:{verb}:{url.split('?', 1)[0]}",
        operation=_execute_once,
    )


def _workflow_api_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    api_key = str(os.getenv("ORION_API_KEY") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def fetch_workflow_snapshot(
    workflow_id: Any,
    *,
    workflow_version_id: Any = None,
    workspace_id: Any = None,
    tenant_id: Any = None,
) -> Optional[Dict[str, Any]]:
    token = str(workflow_id or "").strip()
    if not token:
        return None
    from server_modules import workflow_service

    snapshot = workflow_service.fetch_workflow_snapshot_sync(
        token,
        workflow_version_id=str(workflow_version_id or "").strip() or None,
        workspace_id=str(workspace_id or "").strip() or None,
        tenant_id=str(tenant_id or "").strip() or None,
    )
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=404, detail=f"Unable to load workflow '{token}'.")
    return snapshot

_normalize_workspace_id = _normalize_workspace_id_impl
_workspace_visible = _workspace_visible_impl
_parse_iso_datetime = _parse_iso_datetime_impl
_credential_identity = _credential_identity_impl
_sanitize_bearer_token = _sanitize_bearer_token_impl
_openai_bearer_from_credentials = _openai_bearer_from_credentials_impl

# Combined catalog for vault classification: entries in CONNECTOR_CATALOG or CHANNEL_REGISTRY
# are treated as "connectors"; everything else is a "credential".
_VAULT_CONNECTOR_CATALOG = {**CONNECTOR_CATALOG, **CHANNEL_REGISTRY}
list_vault_credentials = lambda workspace_id=None: _list_vault_credentials_impl(load_vault, _VAULT_CONNECTOR_CATALOG, workspace_id)
list_vault_connectors = lambda workspace_id=None, agent_install_id=None: _list_vault_connectors_impl(load_vault, _VAULT_CONNECTOR_CATALOG, workspace_id, agent_install_id)


def resolve_vault_credential(credential_id, workspace_id=None, **scope):
    return secrets_broker.resolve_connector_secret(
        load_vault,
        _openssl_decrypt,
        tenant_id=scope.get("tenant_id"),
        workspace_id=workspace_id,
        credential_id=credential_id,
        connector_id=scope.get("connector_id"),
        action_id=scope.get("action_id"),
        tool_name=scope.get("tool_name"),
        run_id=scope.get("run_id"),
        runtime_mode=scope.get("runtime_mode"),
        allowed_fields=scope.get("allowed_fields"),
        actor_type=scope.get("actor_type"),
        actor_id=scope.get("actor_id"),
        purpose=scope.get("purpose"),
        target_domain=scope.get("target_domain"),
        allowed_domains=scope.get("allowed_domains"),
        allowed_tools=scope.get("allowed_tools"),
        session_id=scope.get("session_id"),
        require_approval=scope.get("require_approval"),
        approval_id=scope.get("approval_id"),
        approval_actor_id=scope.get("approval_actor_id"),
        approval_reason=scope.get("approval_reason"),
        credential_risk_tags=scope.get("credential_risk_tags"),
        ttl_seconds=scope.get("ttl_seconds") or secrets_broker.DEFAULT_SECRET_GRANT_TTL_SECONDS,
    )


def resolve_default_vault_credential(provider, workspace_id=None, **scope):
    return secrets_broker.resolve_provider_secret(
        load_vault,
        _openssl_decrypt,
        tenant_id=scope.get("tenant_id"),
        workspace_id=workspace_id,
        provider_id=provider,
        credential_id=scope.get("credential_id"),
        tool_name=scope.get("tool_name"),
        run_id=scope.get("run_id"),
        runtime_mode=scope.get("runtime_mode"),
        allowed_fields=scope.get("allowed_fields"),
        actor_type=scope.get("actor_type"),
        actor_id=scope.get("actor_id"),
        purpose=scope.get("purpose"),
        target_domain=scope.get("target_domain"),
        allowed_domains=scope.get("allowed_domains"),
        allowed_tools=scope.get("allowed_tools"),
        session_id=scope.get("session_id"),
        require_approval=scope.get("require_approval"),
        approval_id=scope.get("approval_id"),
        approval_actor_id=scope.get("approval_actor_id"),
        approval_reason=scope.get("approval_reason"),
        credential_risk_tags=scope.get("credential_risk_tags"),
        ttl_seconds=scope.get("ttl_seconds") or secrets_broker.DEFAULT_SECRET_GRANT_TTL_SECONDS,
    )

_codex_token_from_vault = lambda: _codex_token_from_vault_impl(CODEX_AUTH_FILE, _safe_read_json)

validate_google_workspace_connector = lambda credentials: _validate_google_workspace_connector(credentials, http_json_request)
validate_microsoft_365_connector = lambda credentials: _validate_microsoft_365_connector(credentials, http_json_request)
validate_smtp_connector = lambda credentials: _validate_smtp_connector(credentials, http_json_request)
validate_telegram_connector = lambda credentials: _validate_telegram_connector(credentials, http_json_request)
validate_wechat_work_connector = lambda credentials, send_test=False: _validate_wechat_work_connector(credentials, http_json_request, send_test=send_test)
validate_whatsapp_twilio_connector = lambda credentials: _validate_whatsapp_twilio_connector(credentials, http_json_request)
validate_discord_bot_connector = lambda credentials: _validate_discord_bot_connector(credentials, http_json_request)
validate_slack_connector = lambda credentials: _validate_slack_connector(credentials, http_json_request)
validate_github_connector = lambda credentials: _validate_github_connector(credentials, http_json_request)
validate_dropbox_connector = lambda credentials: _validate_dropbox_connector(credentials, http_json_request)
validate_figma_connector = lambda credentials: _validate_figma_connector(credentials, http_json_request)
validate_todoist_connector = lambda credentials: _validate_todoist_connector(credentials, http_json_request)
validate_airtable_connector = lambda credentials: _validate_airtable_connector(credentials, http_json_request)
validate_canva_connector = lambda credentials: _validate_canva_connector(credentials, http_json_request)
validate_asana_connector = lambda credentials: _validate_asana_connector(credentials, http_json_request)
validate_hubspot_connector = lambda credentials: _validate_hubspot_connector(credentials, http_json_request)
validate_zoom_connector = lambda credentials: _validate_zoom_connector(credentials, http_json_request)
validate_calendly_connector = lambda credentials: _validate_calendly_connector(credentials, http_json_request)
validate_clickup_connector = lambda credentials: _validate_clickup_connector(credentials, http_json_request)
validate_jira_connector = lambda credentials: _validate_jira_connector(credentials, http_json_request)
validate_stripe_connector = lambda credentials: _validate_stripe_connector(credentials, http_json_request)
validate_salesforce_connector = lambda credentials: _validate_salesforce_connector(credentials, http_json_request)
validate_webflow_connector = lambda credentials: _validate_webflow_connector(credentials, http_json_request)
validate_monday_connector = lambda credentials: _validate_monday_connector(credentials, http_json_request)
validate_box_connector = lambda credentials: _validate_box_connector(credentials, http_json_request)
validate_gitlab_connector = lambda credentials: _validate_gitlab_connector(credentials, http_json_request)
validate_confluence_connector = lambda credentials: _validate_confluence_connector(credentials, http_json_request)
validate_miro_connector = lambda credentials: _validate_miro_connector(credentials, http_json_request)
validate_intercom_connector = lambda credentials: _validate_intercom_connector(credentials, http_json_request)
validate_docusign_connector = lambda credentials: _validate_docusign_connector(credentials, http_json_request)
validate_square_connector = lambda credentials: _validate_square_connector(credentials, http_json_request)
validate_typeform_connector = lambda credentials: _validate_typeform_connector(credentials, http_json_request)
validate_vercel_connector = lambda credentials: _validate_vercel_connector(credentials, http_json_request)
validate_s3_connector = lambda credentials: _validate_s3_connector(credentials, http_json_request)
validate_notion_connector = lambda credentials: _validate_notion_connector(credentials, http_json_request)
validate_linear_connector = lambda credentials: _validate_linear_connector(credentials, http_json_request)
validate_instagram_business_connector = lambda credentials: _validate_instagram_business_connector(credentials, http_json_request)
validate_irc_connector = lambda credentials: _validate_irc_connector(credentials)


def list_recent_connector_messages(
    credentials: Dict[str, Any],
    *,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    provider = str(credentials.get("_provider") or "").strip().lower()
    safe_limit = max(1, min(int(limit or 3), 10))
    if provider == "google_workspace":
        if google_workspace_uses_local_cli(credentials):
            return google_workspace_local_list_recent_messages(credentials, limit=safe_limit)

        listing = http_json_request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q=in%3Ainbox&maxResults={safe_limit}",
            headers={"Authorization": f"Bearer {str(credentials.get('access_token') or '').strip()}"},
            payload=None,
            timeout=20,
        )
        body = listing.get("json") if isinstance(listing.get("json"), dict) else {}
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        out: List[Dict[str, Any]] = []
        for item in messages[:safe_limit]:
            if not isinstance(item, dict):
                continue
            message_id = str(item.get("id") or "").strip()
            if not message_id:
                continue
            detail = http_json_request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote_plus(message_id)}?format=full",
                headers={"Authorization": f"Bearer {str(credentials.get('access_token') or '').strip()}"},
                payload=None,
                timeout=20,
            )
            payload = detail.get("json") if isinstance(detail.get("json"), dict) else {}
            out.append(_gmail_message_summary(payload, message_id))
        return out

    if provider == "microsoft_365":
        payload = microsoft_graph_request(
            http_json_request,
            credentials,
            f"/me/mailFolders/inbox/messages?$top={safe_limit}&$select=id,subject,receivedDateTime,bodyPreview,from",
        )
        items = payload.get("value") if isinstance(payload.get("value"), list) else []
        return [
            {
                "id": item.get("id"),
                "threadId": None,
                "snippet": str(item.get("bodyPreview") or "").strip(),
                "subject": str(item.get("subject") or "").strip(),
                "from": str((((item.get("from") or {}).get("emailAddress") or {}).get("address")) or "").strip()
                if isinstance(item, dict)
                else "",
                "to": "",
                "date": str(item.get("receivedDateTime") or "").strip() if isinstance(item, dict) else "",
            }
            for item in items
            if isinstance(item, dict)
        ]

    raise RuntimeError(f"Recent message listing is unavailable for connector '{provider or 'unknown'}'.")


def _openai_env_bearer_with_source() -> tuple[str, str]:
    return _openai_env_bearer_with_source_impl(
        codex_oauth_token=CODEX_OAUTH_TOKEN,
        openai_oauth_token=OPENAI_OAUTH_TOKEN,
        openai_access_token=OPENAI_ACCESS_TOKEN,
        codex_vault_token=_codex_token_from_vault(),
        disable_openai_api_key=ORION_DISABLE_OPENAI_API_KEY,
        auth_mode=ORION_AUTH_MODE,
    )

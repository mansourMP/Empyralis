import logging
import os

_logger = logging.getLogger(__name__)

from server_modules import runtime_config as config
from server_modules import shared as shared
from server_modules import runtime_common as common
from server_modules import secrets_broker
from server_modules import secret_redaction_service
from server_modules import vault_helpers
from server_modules import provider_profiles as provider_profiles_service
from server_modules import rust_runtime_kernel_client
from server_modules.model_router import list_model_aliases
from server_modules.vault_store import upsert_credential, rotate_credential_secrets

globals().update({key: value for key, value in vars(config).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(shared).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(common).items() if not key.startswith("__")})

_PROCESS_LIFECYCLE_EXPECTED_NEXT_ACTIONS = {
    "start": "spawn",
}


def _enforce_process_lifecycle_decision(**payload: Any) -> Dict[str, Any]:
    operation = str(payload.get("operation") or "process").strip() or "process"
    try:
        decision = rust_runtime_kernel_client.process_lifecycle_decision(**payload)
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raw = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_process_lifecycle_denied",
                "operation": operation,
                "reason": raw.get("reason") or str(exc),
                "decision": raw.get("decision") or "block",
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "rust_process_lifecycle_unavailable",
                "operation": operation,
                "reason": str(exc),
            },
        ) from exc
    if not isinstance(decision, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "rust_process_lifecycle_invalid_response",
                "operation": operation,
            },
        )
    expected_next_action = _PROCESS_LIFECYCLE_EXPECTED_NEXT_ACTIONS.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_action and next_action != expected_next_action:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_process_lifecycle_invalid_next_action",
                "operation": operation,
                "reason": f"unexpected_next_action:{next_action or 'missing'}",
                "decision": str(decision.get("decision") or "").strip().lower() or "block",
            },
        )
    return decision


def _credential_type_from_openai_env_source(source: Any) -> str:
    normalized = str(source or "").strip().lower()
    if normalized in {"env_codex_oauth_token", "codex_token_vault"}:
        return "codex_token"
    if normalized == "env_api_key":
        return "api_key"
    if normalized in {"env_oauth_token", "env_access_token"}:
        return "oauth_token"
    return "oauth_token"


def _openai_env_credentials(token: str, source: Any) -> Dict[str, Any]:
    sanitized = str(token or "").strip()
    if not sanitized:
        return {}
    credential_type = _credential_type_from_openai_env_source(source)
    credentials: Dict[str, Any] = {
        "credential_type": credential_type,
        "org_id": OPENAI_ORG_ID,
        "project_id": OPENAI_PROJECT_ID,
    }
    if credential_type == "api_key":
        credentials["api_key"] = sanitized
        credentials["auth_mode"] = "api_key"
    elif credential_type == "codex_token":
        credentials["oauth_token"] = sanitized
        credentials["auth_mode"] = "oauth_token"
    else:
        credentials["access_token"] = sanitized
        credentials["auth_mode"] = "access_token"
    return credentials


def _resolve_hosted_openai_bearer(
    *,
    workspace_id: Optional[str],
    purpose: str,
) -> tuple[str, str]:
    fallback_key, fallback_source = _openai_env_bearer_with_source()
    resolution = secrets_broker.resolve_hosted_openai_bearer(
        tenant_id="default",
        workspace_id=workspace_id,
        fallback_token=fallback_key,
        fallback_source=fallback_source,
        tool_name="connectors_core",
        purpose=purpose,
    )
    return str(resolution.value or "").strip(), str(resolution.source or "").strip().lower()


def _resolve_hosted_provider_api_key(
    *,
    provider_id: str,
    workspace_id: Optional[str],
    purpose: str,
) -> str:
    resolution = secrets_broker.resolve_hosted_provider_secret(
        tenant_id="default",
        workspace_id=workspace_id,
        provider_id=provider_id,
        field="api_key",
        tool_name="connectors_core",
        purpose=purpose,
    )
    return str(resolution.value or "").strip()

def _serialize_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now()
    cooldown_until = _parse_utc_ts(profile.get("cooldown_until"))
    provider_id = normalize_provider_id(profile.get("provider"))
    return {
        "id": profile.get("id"),
        "provider": provider_id,
        "label": profile.get("label"),
        "credential_id": profile.get("credential_id"),
        "auth_mode": normalize_auth_mode(provider_id, profile.get("auth_mode")),
        "workspace_id": profile.get("workspace_id"),
        "priority": profile.get("priority", 100),
        "enabled": bool(profile.get("enabled", True)),
        "model": provider_profiles_service.normalize_provider_model_id(provider_id, profile.get("model"), fallback_to_default=True),
        "health": "cooldown" if cooldown_until and cooldown_until > now else ("healthy" if bool(profile.get("enabled", True)) else "disabled"),
        "cooldown_until": profile.get("cooldown_until"),
        "last_error": profile.get("last_error"),
        "last_used_at": profile.get("last_used_at"),
        "last_success_at": profile.get("last_success_at"),
        "last_failure_at": profile.get("last_failure_at"),
        "success_count": int(profile.get("success_count", 0)),
        "failure_count": int(profile.get("failure_count", 0)),
        "created_at": profile.get("created_at"),
        "updated_at": profile.get("updated_at"),
        "metadata": secret_redaction_service.sanitize_mapping(profile.get("metadata")),
    }

async def list_provider_profiles(workspace_id: Optional[str] = None, provider: Optional[str] = None):
    requested_ws = _normalize_workspace_id(workspace_id) or "default"
    requested_provider = normalize_provider_id(provider) if provider else None
    with PROFILES_LOCK:
        items = [dict(item) for item in PROVIDER_PROFILES.values() if isinstance(item, dict)]
    out: List[Dict[str, Any]] = []
    for item in items:
        if str(item.get("workspace_id") or "default").strip() != requested_ws:
            continue
        if requested_provider and normalize_provider_id(item.get("provider")) != requested_provider:
            continue
        out.append(_serialize_profile(item))
    out.sort(key=lambda p: (str(p.get("provider") or ""), int(p.get("priority") or 100), str(p.get("label") or "")))
    return {"items": out}

async def upsert_provider_profile(body: ProviderProfileUpsertRequest):
    body.validate_fields()
    workspace_id = _normalize_workspace_id(body.workspace_id) or "default"
    provider_id = normalize_provider_id(body.provider)
    auth_mode = normalize_auth_mode(provider_id, body.auth_mode)
    if auth_mode and not provider_supports_auth_mode(provider_id, auth_mode):
        raise HTTPException(status_code=400, detail=f"Unsupported auth mode '{body.auth_mode}' for provider '{provider_id}'.")
    credential_id = str(body.credential_id or "").strip()
    if provider_requires_credential(provider_id, auth_mode):
        if len(credential_id) < 6:
            raise HTTPException(status_code=400, detail="credential_id is required.")
        _ = resolve_vault_credential(credential_id, workspace_id)
    else:
        credential_id = ""
    now = _utc_now_iso()
    profile_id = str(body.id or "").strip() or str(uuid.uuid4())
    with PROFILES_LOCK:
        existing = PROVIDER_PROFILES.get(profile_id, {})
        if existing and not isinstance(existing, dict):
            existing = {}
        normalized_model = provider_profiles_service.normalize_provider_model_id(
            provider_id,
            body.model,
            fallback_to_default=bool(body.model),
        ) or None
        profile = {
            "id": profile_id,
            "provider": provider_id,
            "label": str(body.label).strip(),
            "credential_id": credential_id,
            "auth_mode": auth_mode or None,
            "workspace_id": workspace_id,
            "priority": int(body.priority),
            "enabled": bool(body.enabled),
            "model": normalized_model,
            "cooldown_until": existing.get("cooldown_until"),
            "last_error": existing.get("last_error"),
            "last_used_at": existing.get("last_used_at"),
            "last_success_at": existing.get("last_success_at"),
            "last_failure_at": existing.get("last_failure_at"),
            "success_count": int(existing.get("success_count", 0)),
            "failure_count": int(existing.get("failure_count", 0)),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "metadata": dict(body.metadata) if isinstance(body.metadata, dict) else dict(existing.get("metadata") or {}),
        }
        PROVIDER_PROFILES[profile_id] = profile
    _persist_provider_profiles()
    return {"item": _serialize_profile(profile)}

async def enable_provider_profile(profile_id: str):
    with PROFILES_LOCK:
        profile = PROVIDER_PROFILES.get(profile_id)
        if not isinstance(profile, dict):
            raise HTTPException(status_code=404, detail="Profile not found.")
        profile["enabled"] = True
        profile["updated_at"] = _utc_now_iso()
        PROVIDER_PROFILES[profile_id] = profile
    _persist_provider_profiles()
    return {"item": _serialize_profile(profile)}

async def disable_provider_profile(profile_id: str):
    with PROFILES_LOCK:
        profile = PROVIDER_PROFILES.get(profile_id)
        if not isinstance(profile, dict):
            raise HTTPException(status_code=404, detail="Profile not found.")
        profile["enabled"] = False
        profile["updated_at"] = _utc_now_iso()
        PROVIDER_PROFILES[profile_id] = profile
    _persist_provider_profiles()
    return {"item": _serialize_profile(profile)}

async def delete_provider_profile(profile_id: str):
    with PROFILES_LOCK:
        profile = PROVIDER_PROFILES.get(profile_id)
        if not isinstance(profile, dict):
            raise HTTPException(status_code=404, detail="Profile not found.")
        del PROVIDER_PROFILES[profile_id]
    _persist_provider_profiles()
    return {"status": "ok"}

async def provider_profiles_health(workspace_id: Optional[str] = None):
    requested_ws = _normalize_workspace_id(workspace_id) or "default"
    projection = provider_profiles_service.build_provider_runtime_truth(requested_ws)
    return {
        "workspace_id": projection.get("workspace_id"),
        "summary": projection.get("summary"),
        "items": projection.get("items"),
        "providers": projection.get("providers"),
        "providers_by_id": projection.get("providers_by_id"),
        "openai_profile_ready": projection.get("openai_profile_ready"),
        "codex_profile_ready": projection.get("codex_profile_ready"),
    }

async def get_tool_contracts():
    items: List[Dict[str, Any]] = []
    for tool_id, contract in TOOL_CONTRACTS.items():
        if not isinstance(contract, dict):
            continue
        items.append(
            {
                "tool_id": tool_id,
                "description": contract.get("description"),
                "optional": bool(contract.get("optional", True)),
                "allowlist_roles": contract.get("allowlist_roles", []),
                "denylist_roles": contract.get("denylist_roles", []),
                "input_schema": contract.get("input_schema", {}),
                "enabled": bool(is_tool_enabled(tool_id)),
            }
        )
    items.sort(key=lambda item: str(item.get("tool_id") or ""))
    return {"items": items}


async def update_tool_contract_state(tool_id: str, enabled: bool):
    normalized = normalize_action_id(tool_id) or str(tool_id or "").strip().lower()
    if not normalized or normalized not in TOOL_CONTRACTS:
        raise HTTPException(status_code=404, detail="Tool contract not found.")
    set_tool_enabled(normalized, bool(enabled))
    return {"ok": True, "tool_id": normalized, "enabled": bool(is_tool_enabled(normalized))}

async def evaluate_tools_policy(body: ToolPolicyEvaluateRequest):
    body.validate_fields()
    trust_mode = normalize_trust_mode(body.trust_mode)
    target = normalize_execution_target(body.target)
    metadata = body.metadata if isinstance(body.metadata, dict) else {}
    runtime_policy = resolve_runtime_policy_mode(metadata, selected_target=target)

    evaluations: List[Dict[str, Any]] = []
    for raw_tool in body.tool_ids:
        tool_id = normalize_action_id(raw_tool)
        if not tool_id:
            continue
        evaluations.append(
            evaluate_tool_policy_decision(
                tool_id=tool_id,
                trust_mode=trust_mode,
                target=target,
                metadata=metadata,
            )
        )

    return {
        "ok": True,
        "trust_mode": trust_mode,
        "policy_mode": runtime_policy.get("policy_mode"),
        "target": target,
        "count": len(evaluations),
        "items": evaluations,
        "tool_policy": tool_policy_snapshot(metadata),
    }

async def list_providers(workspace_id: Optional[str] = None):
    requested_ws = _normalize_workspace_id(workspace_id) or "default"
    projection = provider_profiles_service.build_workspace_provider_connection_truth(requested_ws)
    return {
        "workspace_id": projection.get("workspace_id"),
        "summary": projection.get("summary"),
        "providers": projection.get("providers"),
    }

async def get_anthropic_local_cli_status():
    if not shutil.which("claude"):
        return {
            "ok": False,
            "available": False,
            "loggedIn": False,
            "message": "Claude CLI is not installed on this machine.",
        }
    try:
        result = subprocess.run(
            ["claude", "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "available": True,
            "loggedIn": False,
            "message": str(exc),
        }

    raw = str(result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return {
            "ok": False,
            "available": True,
            "loggedIn": False,
            "message": raw or f"Claude auth status failed with exit code {result.returncode}.",
        }
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {}
    logged_in = bool(payload.get("loggedIn"))
    return {
        "ok": True,
        "available": True,
        "loggedIn": logged_in,
        "authMethod": str(payload.get("authMethod") or "").strip(),
        "apiProvider": str(payload.get("apiProvider") or "").strip(),
        "message": "Claude subscription is signed in on this machine." if logged_in else "Claude subscription is not signed in yet.",
    }


async def get_gemini_local_cli_status():
    available = gemini_cli_available()
    return {
        "ok": True,
        "available": available,
        "message": "Gemini CLI is installed on this machine." if available else "Gemini CLI is not installed on this machine.",
    }

async def start_anthropic_local_cli_login():
    if not shutil.which("claude"):
        raise HTTPException(status_code=400, detail="Claude CLI is not installed on this machine.")
    _enforce_process_lifecycle_decision(
        operation="start",
        run_id="anthropic-local-cli-login",
        status="not_started",
        max_runtime_seconds=900,
        grace_seconds=10,
        process_kind="provider_cli_login",
        provider="anthropic",
        executable="claude",
    )
    try:
        subprocess.Popen(
            ["claude", "auth", "login"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {
            "ok": True,
            "message": "Claude login has been started. Complete the sign-in flow, then return here and refresh status.",
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

async def test_provider_credentials(body: CredentialTestRequest):
    body.validate_fields()
    provider, _, adapter = resolve_provider_adapter(body.provider, body.credentials)
    try:
        result = adapter.validate(body.credentials)
        models = adapter.list_models(body.credentials) if result.get("ok") else []
        return {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "message": result.get("message", "Validation complete."),
            "provider": provider,
            "models_preview": models[:25],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


async def probe_provider(
    provider: str,
    credential_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    profile_id: Optional[str] = None,
):
    provider_id = normalize_provider_id(provider)
    if provider_id not in PROVIDER_CATALOG:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider}'")

    credentials: Dict[str, Any] = {}
    resolved_provider = provider_id
    if profile_id:
        with PROFILES_LOCK:
            profile = PROVIDER_PROFILES.get(profile_id)
        if not isinstance(profile, dict):
            raise HTTPException(status_code=404, detail="Profile not found.")
        resolved_provider = normalize_provider_id(profile.get("provider") or provider_id)
        ws = _normalize_workspace_id(workspace_id) or str(profile.get("workspace_id") or "default").strip() or "default"
        profile_auth_mode = normalize_auth_mode(resolved_provider, profile.get("auth_mode"))
        profile_credential_id = str(profile.get("credential_id") or "").strip()
        if profile_credential_id:
            try:
                credentials = resolve_vault_credential(profile_credential_id, ws)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=str(exc))
        elif not provider_requires_credential(resolved_provider, profile_auth_mode):
            credentials = secretless_provider_credentials(resolved_provider, profile_auth_mode)
        workspace_id = ws
    elif credential_id:
        try:
            credentials = resolve_vault_credential(credential_id, workspace_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        resolved_provider = normalize_provider_id(credentials.get("_provider") or provider_id)
    elif provider_id == "openai":
        try:
            credentials = resolve_default_vault_credential("openai", workspace_id)
        except Exception:
            key, source = _resolve_hosted_openai_bearer(
                workspace_id=workspace_id,
                purpose="provider_probe",
            )
            if key:
                credentials = _openai_env_credentials(key, source)
    elif provider_id == "openai-codex":
        try:
            credentials = resolve_default_vault_credential("openai-codex", workspace_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    elif provider_id == "anthropic":
        try:
            credentials = resolve_default_vault_credential("anthropic", workspace_id)
        except Exception:
            if claude_code_cli_available():
                credentials = secretless_provider_credentials("anthropic", "local_cli")
    elif provider_id == "gemini":
        try:
            credentials = resolve_default_vault_credential("gemini", workspace_id)
        except Exception:
            env_key = _resolve_hosted_provider_api_key(
                provider_id="gemini",
                workspace_id=workspace_id,
                purpose="provider_probe",
            )
            if env_key:
                credentials = {"api_key": env_key}
    elif provider_id == "ollama_cloud":
        try:
            credentials = resolve_default_vault_credential("ollama_cloud", workspace_id)
        except Exception:
            env_key = str(os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_API_KEY") or os.getenv("OLLAMA_API_KEY") or "").strip()
            if env_key:
                credentials = {"api_key": env_key}

    if not credentials:
        raise HTTPException(status_code=400, detail="No credential available for this provider.")

    try:
        resolved_provider, _, adapter = resolve_provider_adapter(resolved_provider, credentials)
        result = adapter.probe(credentials)
        return {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "message": result.get("message", "Live probe complete."),
            "provider": resolved_provider,
            "model": result.get("model"),
            "reply": result.get("reply"),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

async def get_provider_models(
    provider: str,
    credential_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    profile_id: Optional[str] = None,
):
    provider_id = normalize_provider_id(provider)
    if provider_id not in PROVIDER_CATALOG:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider}'")
    credentials: Dict[str, Any] = {}
    if profile_id:
        with PROFILES_LOCK:
            profile = PROVIDER_PROFILES.get(profile_id)
        if not isinstance(profile, dict):
            raise HTTPException(status_code=404, detail="Profile not found.")
        if normalize_provider_id(profile.get("provider")) != provider_id:
            raise HTTPException(status_code=400, detail="Profile provider mismatch.")
        ws = _normalize_workspace_id(workspace_id) or str(profile.get("workspace_id") or "default").strip() or "default"
        profile_auth_mode = normalize_auth_mode(provider_id, profile.get("auth_mode"))
        profile_credential_id = str(profile.get("credential_id") or "").strip()
        if profile_credential_id:
            try:
                credentials = resolve_vault_credential(profile_credential_id, ws)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=str(exc))
        elif not provider_requires_credential(provider_id, profile_auth_mode):
            credentials = secretless_provider_credentials(provider_id, profile_auth_mode)
    elif credential_id:
        try:
            credentials = resolve_vault_credential(credential_id, workspace_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    elif provider_id == "openai":
        try:
            credentials = resolve_default_vault_credential("openai", workspace_id)
        except Exception:
            key, source = _resolve_hosted_openai_bearer(
                workspace_id=workspace_id,
                purpose="provider_models_list",
            )
            if key:
                credentials = _openai_env_credentials(key, source)
    elif provider_id == "ollama_cloud":
        try:
            credentials = resolve_default_vault_credential("ollama_cloud", workspace_id)
        except Exception:
            env_key = str(os.getenv("ORION_LOCAL_WORKER_OLLAMA_CLOUD_API_KEY") or os.getenv("OLLAMA_API_KEY") or "").strip()
            if env_key:
                credentials = {"api_key": env_key}

    if not credentials:
        # Local or credentialless providers (e.g. Ollama on Agent Computer)
        # return an empty model list instead of a 400 error so the frontend
        # can render the provider card without a hard failure.
        return {"provider": provider_id, "models": [], "credential_required": True}

    try:
        _, _, adapter = resolve_provider_adapter(provider_id, credentials)
        models = adapter.list_models(credentials)
        return {"provider": provider_id, "models": models}
    except Exception as exc:
        _logger.warning("Failed to list models for provider %s: %s", provider_id, exc)
        return {"provider": provider_id, "models": [], "error": str(exc)}


async def get_model_alias_catalog():
    return {"models": list_model_aliases()}

async def list_credentials_vault(workspace_id: Optional[str] = None):
    requested_ws = _normalize_workspace_id(workspace_id) or "default"
    return {"items": list_vault_credentials(requested_ws)}

async def list_connectors():
    items = []
    for connector_id, info in CONNECTOR_CATALOG.items():
        items.append(
            {
                "id": connector_id,
                "label": info.get("label", connector_id),
                "auth": info.get("auth", []),
                "parent": info.get("parent"),
            }
        )
    return {"connectors": items}

async def list_connectors_vault(workspace_id: Optional[str] = None):
    requested_ws = _normalize_workspace_id(workspace_id) or "default"
    return {"items": list_vault_connectors(requested_ws)}

async def rotate_vault_key(body: VaultRotateKeyRequest):
    body.validate_fields()
    if VAULT_KEY_ENV and VAULT_KEY_ENV.strip():
        raise HTTPException(
            status_code=400,
            detail="Vault key rotation is disabled while CREDENTIAL_VAULT_KEY env var is active.",
        )

    old_passphrase = _vault_passphrase()
    new_passphrase = body.new_passphrase.strip()
    if old_passphrase == new_passphrase:
        return {"status": "ok", "rotated": 0, "message": "New key matches current key."}

    vault = load_vault()
    items = vault.get("credentials", [])
    if not isinstance(items, list):
        items = []

    # Re-encrypt every secret in memory first (decrypt-old → encrypt-new). If any
    # secret fails to decrypt, we raise here BEFORE writing anything — the vault
    # is left untouched under the old key.
    rotated_pairs: list = []
    for entry in items:
        encrypted = entry.get("encrypted_secret")
        if not isinstance(encrypted, str) or not encrypted:
            continue
        plain = _openssl_decrypt_with_passphrase(encrypted, old_passphrase)
        entry["encrypted_secret"] = _openssl_encrypt_with_passphrase(plain, new_passphrase)
        rotated_pairs.append((entry.get("id"), entry["encrypted_secret"]))

    # Persist ALL re-encrypted secrets in a SINGLE transaction (all-or-nothing).
    # A partial rotation would leave some secrets under the old key and some under
    # the new — unrecoverable — so this must be atomic, not per-row.
    rotated = rotate_credential_secrets(rotated_pairs)
    _set_vault_passphrase(new_passphrase)
    return {"status": "ok", "rotated": rotated}

async def export_vault_credentials(body: VaultExportRequest):
    body.validate_fields()
    workspace_id = _normalize_workspace_id(body.workspace_id)
    passphrase = body.passphrase.strip()

    vault = load_vault()
    items = vault.get("credentials", [])
    if not isinstance(items, list):
        items = []

    export_items = []
    for entry in items:
        # Platform-internal rows (the OAuth dynamic-client registration store)
        # are not the user's credentials and must not leave in their export.
        if vault_helpers.is_internal_vault_entry(entry):
            continue
        if not _workspace_visible(entry.get("workspace_id"), workspace_id):
            continue
        encrypted = entry.get("encrypted_secret")
        if not isinstance(encrypted, str) or not encrypted:
            continue
        try:
            plain = _openssl_decrypt(encrypted)
            creds = json.loads(plain)
        except Exception:
            continue
        if not isinstance(creds, dict):
            continue
        export_items.append({
            "label": entry.get("label"),
            "provider": entry.get("provider"),
            "workspace_id": entry.get("workspace_id"),
            "mode": entry.get("mode", "byok"),
            "metadata": entry.get("metadata", {}),
            "credentials": creds,
        })

    export_doc = {
        "version": 1,
        "workspace_id": workspace_id,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "credentials": export_items,
    }
    bundle = _openssl_encrypt_with_passphrase(
        json.dumps(export_doc, separators=(",", ":")),
        passphrase,
    )
    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "count": len(export_items),
        "bundle": bundle,
    }

async def import_vault_credentials(body: VaultImportRequest):
    body.validate_fields()
    workspace_override = _normalize_workspace_id(body.workspace_id)
    passphrase = body.passphrase.strip()

    try:
        plain = _openssl_decrypt_with_passphrase(body.bundle.strip(), passphrase)
        parsed = json.loads(plain)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid import bundle: {exc}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Import bundle payload is invalid.")
    raw_items = parsed.get("credentials")
    if not isinstance(raw_items, list):
        raise HTTPException(status_code=400, detail="Import bundle is missing credentials list.")

    vault = load_vault()
    existing = vault.get("credentials", [])
    if not isinstance(existing, list):
        existing = []

    index_by_identity = {}
    for idx, item in enumerate(existing):
        provider = str(item.get("provider") or "").strip().lower()
        label = str(item.get("label") or "").strip()
        if not provider or not label:
            continue
        key = _credential_identity(provider, label, item.get("workspace_id"))
        index_by_identity[key] = idx

    imported = 0
    overwritten = 0
    skipped = 0
    now = datetime.utcnow().isoformat() + "Z"

    for raw in raw_items:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        provider = str(raw.get("provider") or "").strip().lower()
        label = str(raw.get("label") or "").strip()
        mode = str(raw.get("mode") or "byok").strip().lower()
        metadata = raw.get("metadata")
        credentials = raw.get("credentials")
        item_workspace_id = workspace_override if workspace_override is not None else _normalize_workspace_id(raw.get("workspace_id"))

        if provider not in PROVIDER_CATALOG or len(label) < 2:
            skipped += 1
            continue
        if mode not in ["byok", "managed", "vertex"]:
            mode = "byok"
        if not isinstance(credentials, dict) or len(credentials) == 0:
            skipped += 1
            continue
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = {
            **_provider_public_metadata(provider, credentials),
            **metadata,
        }

        encrypted_secret = _openssl_encrypt(json.dumps(credentials, separators=(",", ":")))
        identity = _credential_identity(provider, label, item_workspace_id)
        existing_idx = index_by_identity.get(identity)
        if existing_idx is not None:
            if not body.overwrite:
                skipped += 1
                continue
            previous = existing[existing_idx]
            existing[existing_idx] = {
                "id": previous.get("id") or str(uuid.uuid4()),
                "label": label,
                "provider": provider,
                "workspace_id": item_workspace_id,
                "mode": mode,
                "metadata": metadata,
                "created_at": previous.get("created_at") or now,
                "updated_at": now,
                "encrypted_secret": encrypted_secret,
            }
            # Persist this overwritten row as a single-row upsert instead of a
            # whole-vault save (save_vault is removed under the Postgres vault).
            upsert_credential(existing[existing_idx])
            overwritten += 1
            continue

        new_entry = {
            "id": str(uuid.uuid4()),
            "label": label,
            "provider": provider,
            "workspace_id": item_workspace_id,
            "mode": mode,
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
            "encrypted_secret": encrypted_secret,
        }
        existing.append(new_entry)
        # Persist this imported row as a single-row upsert instead of a
        # whole-vault save (save_vault is removed under the Postgres vault).
        upsert_credential(new_entry)
        index_by_identity[identity] = len(existing) - 1
        imported += 1

    return {
        "status": "ok",
        "imported": imported,
        "overwritten": overwritten,
        "skipped": skipped,
        "workspace_id": workspace_override,
    }

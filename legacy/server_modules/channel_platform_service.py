from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from server_modules import (
    agent_specialist_repository,
    channel_lane_contract_service,
    control_plane_repository,
    connectors_actions,
    connection_catalog_service,
    gateway_state_repository,
    personal_channels_repository,
    runtime_common,
    secret_redaction_service,
    security_audit_service,
)
from server_modules.runtime_models import CredentialUpsertRequest


class ChannelPlatformError(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_RESERVED_PRIVATE_RUNTIME_ITEMS = channel_lane_contract_service.reserved_private_runtime_channel_catalog()
_PERSONAL_RUNTIME_LANE = channel_lane_contract_service.PERSONAL_GATEWAY_RUNTIME_LANE
_STUDIO_RUNTIME_LANE = channel_lane_contract_service.STUDIO_CONNECTOR_RUNTIME_LANE
_SENSITIVE_ACCOUNT_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "bot_token",
        "credential",
        "credentials",
        "gateway_token",
        "key",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "session_string",
        "token",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _token(value: Any) -> str:
    return _text(value).lower()


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _assert_scope(tenant_id: str, workspace_id: str) -> tuple[str, str]:
    resolved_tenant_id = _text(tenant_id)
    resolved_workspace_id = _text(workspace_id)
    if not resolved_tenant_id or not resolved_workspace_id:
        raise ChannelPlatformError(400, "tenant_id and workspace_id are required.")
    return resolved_tenant_id, resolved_workspace_id


def _catalog_items() -> list[Dict[str, Any]]:
    return connection_catalog_service.studio_channel_catalog_items()


def _catalog_by_key() -> Dict[str, Dict[str, Any]]:
    return {_token(item.get("channel_key")): dict(item) for item in _catalog_items()}


def _catalog_by_binding_key() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in _catalog_items():
        binding_key = _token(item.get("binding_channel_key") or item.get("channel_key"))
        out.setdefault(binding_key, dict(item))
    return out


def _catalog_item(catalog_id: str) -> Dict[str, Any]:
    item = _catalog_by_key().get(_token(catalog_id))
    if not item:
        raise ChannelPlatformError(404, "Channel catalog item not found.")
    return dict(item)


def _supported_account_providers(item: Dict[str, Any]) -> set[str]:
    primary = _token(item.get("account_provider") or item.get("connector_id") or item.get("provider"))
    aliases = {
        "whatsapp_business": {"whatsapp_twilio", "twilio_whatsapp"},
        "slack": {"slack", "slack_app"},
        "discord_bot": {"discord_bot", "discord"},
        "microsoft_365": {"microsoft_365", "outlook"},
    }.get(_token(item.get("channel_key")), set())
    return {token for token in {primary, *aliases} if token}


def _redacted_metadata(metadata: Any) -> Dict[str, Any]:
    clean = secret_redaction_service.sanitize_mapping(metadata)
    return {
        str(key): value
        for key, value in clean.items()
        if not secret_redaction_service.is_sensitive_key(key)
        and str(key or "").strip().lower() not in _SENSITIVE_ACCOUNT_METADATA_KEYS
    }


def _vault_account_from_connector(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    credential_id = _text(row.get("id"))
    provider = _token(row.get("connector") or row.get("provider"))
    if not credential_id or not provider:
        return None
    label = _text(row.get("label")) or provider.replace("_", " ").title()
    return {
        "id": f"vault:{credential_id}",
        "account_ref": f"vault:{credential_id}",
        "secret_ref": f"vault://credential/{credential_id}",
        "credential_id": credential_id,
        "provider": provider,
        "label": label,
        "scope": "workspace",
        "runtime_lane": _STUDIO_RUNTIME_LANE,
        "status": "available",
        "workspace_id": _text(row.get("workspace_id")) or None,
        "metadata": _redacted_metadata(row.get("metadata")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _personal_account_from_state(
    *,
    gateway: Dict[str, Any],
    channel_key: str,
    state: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not state:
        return None
    status = _token(state.get("status")) or "unknown"
    if status in {"idle", "qr_pending", "login_required", "disconnected"}:
        return None
    gateway_id = _text(gateway.get("gateway_id"))
    if not gateway_id:
        return None
    linked_name = _text(state.get("linked_name")) or _text(state.get("linked_username"))
    label = linked_name or _text(gateway.get("display_name")) or "Agent Computer"
    return {
        "id": f"agent_computer:{gateway_id}:{channel_key}",
        "account_ref": f"agent_computer:{gateway_id}:{channel_key}",
        "secret_ref": None,
        "credential_id": None,
        "provider": channel_key,
        "label": label,
        "scope": "user",
        "runtime_lane": _PERSONAL_RUNTIME_LANE,
        "status": status,
        "gateway_id": gateway_id,
        "requires_agent_computer": True,
        "workspace_id": _text(gateway.get("workspace_id")) or None,
        "metadata": {
            "device": _text(gateway.get("display_name")) or None,
            "platform": _text(gateway.get("platform")) or None,
            "last_event_at": state.get("last_event_at"),
        },
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
    }


def _list_personal_channel_accounts(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    accounts: list[Dict[str, Any]] = []
    try:
        registrations = gateway_state_repository.list_workspace_gateway_registrations(
            workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            include_revoked=False,
        )
    except Exception:
        return accounts
    for gateway in registrations:
        gateway_id = _text(gateway.get("gateway_id"))
        if not gateway_id:
            continue
        try:
            whatsapp = personal_channels_repository.get_whatsapp_state(
                gateway_id,
                channel_key="whatsapp_personal",
            )
            telegram = personal_channels_repository.get_telegram_state(
                gateway_id,
                channel_key="telegram_personal",
            )
        except Exception:
            continue
        for item in (
            _personal_account_from_state(gateway=gateway, channel_key="whatsapp_personal", state=whatsapp),
            _personal_account_from_state(gateway=gateway, channel_key="telegram_personal", state=telegram),
        ):
            if item:
                accounts.append(item)
    return accounts


def list_channel_catalog(*, surface: Optional[str] = None) -> Dict[str, Any]:
    normalized_surface = _token(surface)
    items = connection_catalog_service.studio_channel_catalog_items(normalized_surface or None)
    groups: Dict[str, int] = {}
    for item in items:
        group = _text(item.get("navigation_group")) or "other"
        groups[group] = groups.get(group, 0) + 1
    return {
        "items": items,
        "reserved": list(_RESERVED_PRIVATE_RUNTIME_ITEMS),
        "count": len(items),
        "groups": groups,
        "concepts": {
            "messaging_channel": "A place where a person or customer talks to an agent.",
            "connected_app": "A work system the agent can read, write, or act inside.",
            "agent_computer_bridge": "A local or dedicated runtime bridge for personal/native messaging and device work.",
            "channel_account": "Reusable credential, session, or connector configuration.",
            "agent_channel_binding": "Per-agent route, identity, policy, status, and audit scope.",
            "channel_adapter": "Cloud connector or Agent Computer local runtime behind a binding.",
            "extension": "A manifest-driven adapter package; it may add a channel, app connector, tool, runtime capability, or safe custom section.",
        },
    }


def list_workspace_channel_accounts(
    *,
    tenant_id: str,
    workspace_id: str,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_tenant_id, resolved_workspace_id = _assert_scope(tenant_id, workspace_id)
    accounts: list[Dict[str, Any]] = []
    for row in runtime_common.list_vault_connectors(workspace_id=resolved_workspace_id):
        account = _vault_account_from_connector(_dict(row))
        if account:
            accounts.append(account)
    accounts.extend(
        _list_personal_channel_accounts(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=_text((current_user or {}).get("user_id")) or None,
        )
    )
    return {
        "workspace_id": resolved_workspace_id,
        "items": accounts,
        "count": len(accounts),
    }


def _account_provider_tokens() -> set[str]:
    out: set[str] = set()
    for item in _catalog_items():
        if _text(item.get("runtime_lane")) == _PERSONAL_RUNTIME_LANE:
            continue
        for key in ("account_provider", "connector_id"):
            token = _token(item.get(key))
            if token:
                out.add(token)
    return out


async def create_workspace_channel_account(
    *,
    tenant_id: str,
    workspace_id: str,
    provider: str,
    label: str,
    credentials: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    skip_validation: bool = False,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _, resolved_workspace_id = _assert_scope(tenant_id, workspace_id)
    normalized_provider = _token(provider)
    if normalized_provider not in _account_provider_tokens():
        raise ChannelPlatformError(400, "Channel account provider is not supported by the Studio channel catalog.")
    if not isinstance(credentials, dict) or not credentials:
        raise ChannelPlatformError(400, "Channel account credentials are required.")
    created = await connectors_actions.create_vault_credential(
        CredentialUpsertRequest(
            label=_text(label) or normalized_provider.replace("_", " ").title(),
            provider=normalized_provider,
            workspace_id=resolved_workspace_id,
            mode="byok",
            credentials=credentials,
            metadata={
                **secret_redaction_service.sanitize_mapping(metadata or {}),
                "channel_account": True,
                "created_from": "studio_channel_platform",
            },
            skip_validation=bool(skip_validation),
        )
    )
    account = _vault_account_from_connector(
        {
            "id": created.get("id"),
            "label": created.get("label"),
            "connector": created.get("provider") or normalized_provider,
            "workspace_id": created.get("workspace_id") or resolved_workspace_id,
            "metadata": created.get("metadata"),
            "created_at": created.get("created_at"),
            "updated_at": created.get("updated_at"),
        }
    )
    if not account:
        raise ChannelPlatformError(500, "Channel account was created but could not be projected.")
    security_audit_service.emit_security_audit_event(
        action="studio.channel_account.create",
        status="success",
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        channel=normalized_provider,
        metadata={"provider": normalized_provider, "account_ref": account.get("account_ref")},
    )
    return {
        "workspace_id": resolved_workspace_id,
        "account": account,
    }


def _account_by_ref(
    *,
    tenant_id: str,
    workspace_id: str,
    current_user: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    return {
        _text(item.get("account_ref")): item
        for item in list_workspace_channel_accounts(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            current_user=current_user,
        ).get("items", [])
        if _text(item.get("account_ref"))
    }


async def _load_deployed_agent(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
) -> Dict[str, Any]:
    agent_id = _text(deployed_agent_id)
    if not agent_id:
        raise ChannelPlatformError(400, "deployed_agent_id is required.")
    record = await control_plane_repository.get_deployed_agent_by_id(
        agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=workspace_id,
    )
    if not record:
        raise ChannelPlatformError(404, "Native Studio agent not found.")
    return dict(record)


def _agent_channels(record: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(_dict(record.get("channels")))


def _binding_payload_from_channel(
    *,
    channel_key: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    catalog = _catalog_by_key().get(_token(config.get("catalog_id"))) or _catalog_by_binding_key().get(_token(channel_key)) or {}
    status = _token(config.get("binding_status")) or ("enabled" if bool(config.get("enabled", True)) else "paused")
    return {
        "channel_key": channel_key,
        "catalog_id": _text(config.get("catalog_id")) or _text(catalog.get("channel_key")) or channel_key,
        "label": _text(config.get("display_label")) or _text(catalog.get("label")) or channel_key.replace("_", " ").title(),
        "provider": _text(config.get("provider")) or _text(catalog.get("provider")),
        "connector_id": _text(config.get("connector_id")) or None,
        "account_ref": _text(config.get("account_ref")) or None,
        "secret_ref": _text(config.get("secret_ref")) or None,
        "credential_id": _text(config.get("credential_id")) or None,
        "endpoint_key": _text(config.get("endpoint_key")) or None,
        "enabled": bool(config.get("enabled", True)),
        "status": status,
        "runtime_lane": _text(config.get("runtime_lane")) or _text(catalog.get("runtime_lane")) or _STUDIO_RUNTIME_LANE,
        "launch_allowed": bool(config.get("launch_allowed", catalog.get("launch_allowed", False))),
        "live_capable": bool(config.get("live_capable", catalog.get("live_capable", False))),
        "is_inbound_owner": bool(config.get("is_inbound_owner", True)),
        "policy": _dict(config.get("policy")),
        "audit": _dict(config.get("audit")),
        "updated_at": config.get("updated_at"),
        "paused_at": config.get("paused_at"),
        "revoked_at": config.get("revoked_at"),
    }


async def list_agent_channel_bindings(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_tenant_id, resolved_workspace_id = _assert_scope(tenant_id, workspace_id)
    agent = await _load_deployed_agent(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=deployed_agent_id,
    )
    accounts = _account_by_ref(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    items: list[Dict[str, Any]] = []
    for channel_key, config in sorted(_agent_channels(agent).items()):
        payload = _binding_payload_from_channel(channel_key=channel_key, config=_dict(config))
        account = accounts.get(_text(payload.get("account_ref")))
        if account:
            payload["account_label"] = account.get("label")
            payload["account_status"] = account.get("status")
        items.append(payload)
    return {
        "workspace_id": resolved_workspace_id,
        "deployed_agent_id": _text(deployed_agent_id),
        "items": items,
        "count": len(items),
    }


def _validate_account_for_catalog(
    *,
    item: Dict[str, Any],
    account: Optional[Dict[str, Any]],
    account_ref: Optional[str],
) -> None:
    lane = _text(item.get("runtime_lane"))
    if lane == _PERSONAL_RUNTIME_LANE:
        raise ChannelPlatformError(
            403,
            "Personal channels are Agent Computer/Sage runtime channels and cannot be bound to Studio cloud agents.",
        )
    if not bool(item.get("launch_allowed")) or not bool(item.get("live_capable")):
        label = _text(item.get("label")) or _text(item.get("channel_key")) or "Channel"
        raise ChannelPlatformError(
            409,
            f"{label} is not launch-ready for Studio channel binding.",
        )
    required_providers = _supported_account_providers(item)
    if required_providers and not account_ref:
        raise ChannelPlatformError(400, "A saved channel account is required for this channel.")
    if not account_ref:
        return
    if not account:
        raise ChannelPlatformError(404, "Channel account not found.")
    account_lane = _text(account.get("runtime_lane"))
    if account_lane == _PERSONAL_RUNTIME_LANE:
        raise ChannelPlatformError(
            403,
            "Studio channel bindings cannot use personal Agent Computer sessions.",
        )
    account_provider = _token(account.get("provider"))
    if required_providers and account_provider not in required_providers:
        raise ChannelPlatformError(400, "Selected account does not match this channel adapter.")


def _default_endpoint_key(deployed_agent_id: str, channel_key: str) -> str:
    return f"agent:{_text(deployed_agent_id)}:{_token(channel_key)}"


async def _persist_agent_channels(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    agent: Dict[str, Any],
    channels: Dict[str, Any],
    updated_by_user_id: Optional[str],
) -> Dict[str, Any]:
    updated = await control_plane_repository.update_deployed_agent(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=workspace_id,
        updates={"channels": channels},
    )
    if updated is None:
        raise ChannelPlatformError(503, "Channel binding could not be saved because the control plane is unavailable.")
    backing_install_id = _text(agent.get("backing_install_id"))
    if backing_install_id:
        manifest_update = await agent_specialist_repository.save_workspace_specialist_channel_bindings(
            backing_install_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel_bindings=channels,
            updated_by_user_id=updated_by_user_id,
        )
        if manifest_update is None:
            raise ChannelPlatformError(503, "Channel binding manifest could not be saved.")
    return dict(updated)


async def upsert_agent_channel_binding(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    catalog_id: str,
    account_ref: Optional[str] = None,
    endpoint_key: Optional[str] = None,
    enabled: bool = True,
    is_inbound_owner: bool = True,
    display_label: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_tenant_id, resolved_workspace_id = _assert_scope(tenant_id, workspace_id)
    item = _catalog_item(catalog_id)
    agent = await _load_deployed_agent(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=deployed_agent_id,
    )
    accounts = _account_by_ref(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
    )
    normalized_account_ref = _text(account_ref) or None
    _validate_account_for_catalog(
        item=item,
        account=accounts.get(normalized_account_ref or ""),
        account_ref=normalized_account_ref,
    )
    channel_key = _token(item.get("binding_channel_key") or item.get("channel_key"))
    if not channel_key:
        raise ChannelPlatformError(400, "Channel catalog item is missing a binding channel key.")
    account = accounts.get(normalized_account_ref or "")
    now_iso = _utc_now_iso()
    channels = _agent_channels(agent)
    secret_ref = _text((account or {}).get("secret_ref")) or None
    credential_id = _text((account or {}).get("credential_id")) or None
    channels[channel_key] = {
        **_dict(channels.get(channel_key)),
        "enabled": bool(enabled),
        "binding_status": "enabled" if enabled else "paused",
        "catalog_id": _text(item.get("channel_key")),
        "display_label": _text(display_label) or _text(item.get("label")),
        "provider": _text(item.get("provider")),
        "connector_id": _text(item.get("connector_id")) or None,
        "account_ref": normalized_account_ref,
        "secret_ref": secret_ref,
        "credential_id": credential_id,
        "endpoint_key": _text(endpoint_key) or _default_endpoint_key(deployed_agent_id, channel_key),
        "is_inbound_owner": bool(is_inbound_owner),
        "runtime_lane": _text(item.get("runtime_lane")),
        "launch_allowed": bool(item.get("launch_allowed")),
        "live_capable": bool(item.get("live_capable")),
        "source": "channel_platform",
        "policy": secret_redaction_service.sanitize_mapping(policy or {}),
        "audit": {
            "created_by_user_id": _text((current_user or {}).get("user_id")) or None,
            "updated_by_user_id": _text((current_user or {}).get("user_id")) or None,
            "updated_at": now_iso,
        },
        "updated_at": now_iso,
    }
    secret_redaction_service.assert_secrets_free(
        secret_redaction_service.sanitize_mapping(channels[channel_key]),
        context="channel_binding",
    )
    updated = await _persist_agent_channels(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=_text(deployed_agent_id),
        agent=agent,
        channels=channels,
        updated_by_user_id=_text((current_user or {}).get("user_id")) or None,
    )
    security_audit_service.emit_security_audit_event(
        action="studio.channel_binding.upsert",
        status="success",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        channel=channel_key,
        metadata={
            "deployed_agent_id": _text(deployed_agent_id),
            "catalog_id": _text(item.get("channel_key")),
            "account_ref": normalized_account_ref,
            "enabled": bool(enabled),
        },
    )
    binding = _binding_payload_from_channel(channel_key=channel_key, config=_dict(updated.get("channels", {}).get(channel_key)))
    return {"binding": binding, "deployed_agent_id": _text(deployed_agent_id), "workspace_id": resolved_workspace_id}


async def set_agent_channel_binding_state(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    action: str,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_tenant_id, resolved_workspace_id = _assert_scope(tenant_id, workspace_id)
    normalized_channel_key = _token(channel_key)
    normalized_action = _token(action)
    if normalized_action not in {"pause", "resume", "revoke"}:
        raise ChannelPlatformError(400, "Unsupported channel binding action.")
    agent = await _load_deployed_agent(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=deployed_agent_id,
    )
    channels = _agent_channels(agent)
    binding = _dict(channels.get(normalized_channel_key))
    if not binding:
        raise ChannelPlatformError(404, "Channel binding not found.")
    if _text(binding.get("runtime_lane")) == _PERSONAL_RUNTIME_LANE:
        raise ChannelPlatformError(403, "Studio cannot mutate personal Agent Computer channel bindings.")
    now_iso = _utc_now_iso()
    if normalized_action == "resume":
        binding["enabled"] = True
        binding["binding_status"] = "enabled"
        binding.pop("paused_at", None)
    elif normalized_action == "pause":
        binding["enabled"] = False
        binding["binding_status"] = "paused"
        binding["paused_at"] = now_iso
    else:
        binding["enabled"] = False
        binding["binding_status"] = "revoked"
        binding["revoked_at"] = now_iso
    binding["updated_at"] = now_iso
    binding["audit"] = {
        **_dict(binding.get("audit")),
        "updated_by_user_id": _text((current_user or {}).get("user_id")) or None,
        "updated_at": now_iso,
        "last_action": normalized_action,
    }
    channels[normalized_channel_key] = binding
    secret_redaction_service.assert_secrets_free(
        secret_redaction_service.sanitize_mapping(binding),
        context="channel_binding",
    )
    updated = await _persist_agent_channels(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=_text(deployed_agent_id),
        agent=agent,
        channels=channels,
        updated_by_user_id=_text((current_user or {}).get("user_id")) or None,
    )
    security_audit_service.emit_security_audit_event(
        action=f"studio.channel_binding.{normalized_action}",
        status="success",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        channel=normalized_channel_key,
        metadata={
            "deployed_agent_id": _text(deployed_agent_id),
            "channel_key": normalized_channel_key,
        },
    )
    return {
        "binding": _binding_payload_from_channel(
            channel_key=normalized_channel_key,
            config=_dict(updated.get("channels", {}).get(normalized_channel_key)),
        ),
        "deployed_agent_id": _text(deployed_agent_id),
        "workspace_id": resolved_workspace_id,
    }


async def test_agent_channel_binding(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    message: str,
    dry_run: bool = True,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_tenant_id, resolved_workspace_id = _assert_scope(tenant_id, workspace_id)
    normalized_channel_key = _token(channel_key)
    if not _text(message):
        raise ChannelPlatformError(400, "Test message is required.")
    if len(_text(message)) > 2000:
        raise ChannelPlatformError(400, "Test message is too long.")
    agent = await _load_deployed_agent(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        deployed_agent_id=deployed_agent_id,
    )
    binding = _dict(_agent_channels(agent).get(normalized_channel_key))
    if not binding:
        raise ChannelPlatformError(404, "Channel binding not found.")
    if _text(binding.get("runtime_lane")) == _PERSONAL_RUNTIME_LANE:
        raise ChannelPlatformError(403, "Personal channel sends must go through Agent Computer/Sage.")
    if not bool(binding.get("enabled", True)) or _token(binding.get("binding_status")) in {"paused", "revoked"}:
        raise ChannelPlatformError(409, "Channel binding is paused or revoked.")
    if not dry_run:
        raise ChannelPlatformError(409, "Live channel test sends are not enabled from Studio in this foundation route.")
    security_audit_service.emit_security_audit_event(
        action="studio.channel_binding.test",
        status="dry_run",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        channel=normalized_channel_key,
        metadata={
            "deployed_agent_id": _text(deployed_agent_id),
            "channel_key": normalized_channel_key,
            "live_send_performed": False,
        },
    )
    return {
        "status": "dry_run_ready",
        "live_send_performed": False,
        "deployed_agent_id": _text(deployed_agent_id),
        "channel_key": normalized_channel_key,
        "binding": _binding_payload_from_channel(channel_key=normalized_channel_key, config=binding),
        "message_preview": secret_redaction_service.redact_text(_text(message))[:240],
        "adapter_contract": {
            "capability_manifest": True,
            "health_snapshot": True,
            "inbound_event_normalization": True,
            "outbound_send_envelope": True,
            "dedupe_idempotency": True,
            "quota_rate_limit_hooks": True,
            "audit_redaction_hooks": True,
        },
    }

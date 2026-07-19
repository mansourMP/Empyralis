import json
from types import SimpleNamespace
from urllib import parse as urlparse

import pytest
from fastapi import HTTPException

from server_modules import connection_catalog_service, connection_oauth_service, connectors_actions, gateway_state_repository, routes_connections


def _install_auth(monkeypatch):
    monkeypatch.setattr(
        routes_connections.auth_module,
        "enforce_workspace_access",
        lambda current_user, workspace_id, minimum_role="viewer", **_kwargs: workspace_id,
    )
    monkeypatch.setattr(
        routes_connections.auth_module,
        "workspace_tenant_id",
        lambda current_user, workspace_id: "tenant-1",
    )
    monkeypatch.setattr(routes_connections.auth_module, "validate_csrf", lambda request: None)


@pytest.mark.anyio
async def test_get_sage_agent_computer_selection_returns_current_user_selection(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setattr(
        routes_connections.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "selected_gateway_id": "gateway-1",
            "selected_at": "2026-06-06T00:00:00Z",
            "selected_by": user_id,
            "metadata": {},
        },
    )
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
        },
    )
    monkeypatch.setattr(
        routes_connections.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )

    payload = await routes_connections.get_sage_agent_computer_selection(
        workspace_id="ws-1",
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["selected_gateway_id"] == "gateway-1"
    assert payload["gateway"]["gateway_id"] == "gateway-1"


@pytest.mark.anyio
async def test_put_sage_agent_computer_selection_keeps_default_guarded_without_mode_metadata(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
            "metadata": {"runtime_access_mode": "default_guarded"},
        },
    )
    update_calls = []

    def _update_gateway_registration_state(**kwargs):
        update_calls.append(kwargs)
        return {
            "gateway_id": kwargs["gateway_id"],
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "update_gateway_registration_state",
        _update_gateway_registration_state,
    )
    monkeypatch.setattr(
        routes_connections.sage_agent_computer_selection_service,
        "set_selection",
        lambda **kwargs: {
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "selected_gateway_id": kwargs["selected_gateway_id"],
            "metadata": kwargs["metadata"],
        },
    )
    monkeypatch.setattr(
        routes_connections.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: {
            **registration,
            "runtime_access_mode": registration.get("metadata", {}).get("runtime_access_mode"),
        },
    )

    payload = await routes_connections.set_sage_agent_computer_selection(
        body=routes_connections.SageAgentComputerSelectionRequest(
            workspace_id="ws-1",
            selected_gateway_id="gateway-1",
        ),
        request=SimpleNamespace(),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["selected_gateway_id"] == "gateway-1"
    assert update_calls[0]["metadata"]["runtime_access_mode"] == "default_guarded"
    assert update_calls[0]["metadata"]["autonomous_agent_setup_warning_acknowledged"] is False
    assert update_calls[0]["metadata"]["sage_agent_computer_selected"] is True
    assert "runtime_access_setup_warning" in update_calls[0]["metadata_keys_to_remove"]
    assert payload["gateway"]["runtime_access_mode"] == "default_guarded"


@pytest.mark.anyio
async def test_put_sage_agent_computer_selection_rejects_full_access_without_warning_ack(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
            "metadata": {"runtime_access_mode": "default_guarded"},
        },
    )

    with pytest.raises(HTTPException) as raised:
        await routes_connections.set_sage_agent_computer_selection(
            body=routes_connections.SageAgentComputerSelectionRequest(
                workspace_id="ws-1",
                selected_gateway_id="gateway-1",
                metadata={"runtime_access_mode": "full_access"},
            ),
            request=SimpleNamespace(),
            current_user={"user_id": "user-1", "role": "member"},
        )

    assert raised.value.status_code == 400
    assert "Full Access setup warning" in str(raised.value.detail)


@pytest.mark.anyio
async def test_put_sage_agent_computer_selection_applies_full_access_after_warning_ack(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
            "metadata": {"runtime_access_mode": "default_guarded"},
        },
    )
    update_calls = []

    def _update_gateway_registration_state(**kwargs):
        update_calls.append(kwargs)
        return {
            "gateway_id": kwargs["gateway_id"],
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "update_gateway_registration_state",
        _update_gateway_registration_state,
    )
    monkeypatch.setattr(
        routes_connections.sage_agent_computer_selection_service,
        "set_selection",
        lambda **kwargs: {
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "selected_gateway_id": kwargs["selected_gateway_id"],
            "metadata": kwargs["metadata"],
        },
    )
    monkeypatch.setattr(
        routes_connections.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: {
            **registration,
            "runtime_access_mode": registration.get("metadata", {}).get("runtime_access_mode"),
        },
    )

    payload = await routes_connections.set_sage_agent_computer_selection(
        body=routes_connections.SageAgentComputerSelectionRequest(
            workspace_id="ws-1",
            selected_gateway_id="gateway-1",
            metadata={
                "runtime_access_mode": "full_access",
                "autonomous_agent_setup_warning_acknowledged": True,
            },
        ),
        request=SimpleNamespace(),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["selected_gateway_id"] == "gateway-1"
    assert update_calls[0]["metadata"]["runtime_access_mode"] == "full_access"
    assert update_calls[0]["metadata"]["autonomous_agent_setup_warning_acknowledged"] is True
    assert update_calls[0]["metadata"]["sage_agent_computer_selected"] is True
    assert payload["gateway"]["runtime_access_mode"] == "full_access"


@pytest.mark.anyio
async def test_put_sage_agent_computer_selection_applies_custom_mode(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
            "metadata": {"runtime_access_mode": "default_guarded"},
        },
    )
    update_calls = []

    def _update_gateway_registration_state(**kwargs):
        update_calls.append(kwargs)
        return {
            "gateway_id": kwargs["gateway_id"],
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "update_gateway_registration_state",
        _update_gateway_registration_state,
    )
    monkeypatch.setattr(
        routes_connections.sage_agent_computer_selection_service,
        "set_selection",
        lambda **kwargs: {
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "selected_gateway_id": kwargs["selected_gateway_id"],
            "metadata": kwargs["metadata"],
        },
    )
    monkeypatch.setattr(
        routes_connections.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: {
            **registration,
            "runtime_access_mode": registration.get("metadata", {}).get("runtime_access_mode"),
        },
    )

    payload = await routes_connections.set_sage_agent_computer_selection(
        body=routes_connections.SageAgentComputerSelectionRequest(
            workspace_id="ws-1",
            selected_gateway_id="gateway-1",
            metadata={"runtime_access_mode": "custom"},
        ),
        request=SimpleNamespace(),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["selected_gateway_id"] == "gateway-1"
    assert update_calls[0]["metadata"]["runtime_access_mode"] == "custom"
    assert update_calls[0]["metadata"]["autonomous_agent_setup_warning_acknowledged"] is False
    assert update_calls[0]["metadata"]["sage_agent_computer_selected"] is True
    assert "runtime_access_setup_warning" in update_calls[0]["metadata_keys_to_remove"]
    assert payload["selection"]["metadata"]["runtime_access_mode"] == "custom"
    assert payload["gateway"]["runtime_access_mode"] == "custom"


def test_gateway_registration_state_removes_stale_full_access_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gateway_state_repository,
        "_enforce_gateway_state_decision",
        lambda *_args, **_kwargs: {"next_action": "update_gateway_registration_state"},
    )
    db_path = tmp_path / "gateway-state.sqlite3"
    now = "2026-06-06T00:00:00Z"
    full_access_metadata = {
        "runtime_access_mode": "full_access",
        "runtime_access_label": "Full Access",
        "runtime_access_setup_warning": "old warning",
        "autonomous_agent_setup_warning_acknowledged": True,
        "autonomous_agent_setup_warning_acknowledged_at": now,
    }
    conn = gateway_state_repository._connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO gateway_registrations (
                gateway_id, device_id, tenant_id, workspace_id, user_id, status,
                device_trust_state, display_name, platform, gateway_token_hash,
                metadata, capabilities, journal_cursor, checkpoint_cursor,
                created_at, updated_at, last_seen_at, last_heartbeat_at,
                token_rotated_at, revoked_at, revoked_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gateway-1",
                "device-1",
                "tenant-1",
                "ws-1",
                "user-1",
                "active",
                "verified",
                "Agent Computer",
                "darwin",
                "token-hash",
                json.dumps(full_access_metadata, sort_keys=True),
                "[]",
                0,
                0,
                now,
                now,
                now,
                now,
                now,
                None,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    updated = gateway_state_repository.update_gateway_registration_state(
        gateway_id="gateway-1",
        db_path=db_path,
        metadata={
            "runtime_access_mode": "custom",
            "runtime_access_label": "Custom",
            "autonomous_agent_setup_warning_acknowledged": False,
        },
        metadata_keys_to_remove=[
            "runtime_access_setup_warning",
            "autonomous_agent_setup_warning_acknowledged_at",
        ],
    )

    metadata = (updated or {}).get("metadata") or {}
    assert metadata["runtime_access_mode"] == "custom"
    assert metadata["runtime_access_label"] == "Custom"
    assert metadata["autonomous_agent_setup_warning_acknowledged"] is False
    assert "runtime_access_setup_warning" not in metadata
    assert "autonomous_agent_setup_warning_acknowledged_at" not in metadata


@pytest.mark.anyio
async def test_put_sage_agent_computer_selection_rejects_another_users_gateway(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-2",
            "device_trust_state": "verified",
            "connection_status": "online",
        },
    )

    with pytest.raises(HTTPException) as raised:
        await routes_connections.set_sage_agent_computer_selection(
            body=routes_connections.SageAgentComputerSelectionRequest(
                workspace_id="ws-1",
                selected_gateway_id="gateway-1",
            ),
            request=SimpleNamespace(),
            current_user={"user_id": "user-1", "role": "member"},
        )

    assert raised.value.status_code == 403


@pytest.mark.anyio
async def test_put_sage_agent_computer_selection_rejects_revoked_gateway(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "revoked",
            "connection_status": "offline",
        },
    )

    with pytest.raises(HTTPException) as raised:
        await routes_connections.set_sage_agent_computer_selection(
            body=routes_connections.SageAgentComputerSelectionRequest(
                workspace_id="ws-1",
                selected_gateway_id="gateway-1",
            ),
            request=SimpleNamespace(),
            current_user={"user_id": "user-1", "role": "member"},
        )

    assert raised.value.status_code == 409


def test_connection_status_does_not_auto_select_gateway(monkeypatch):
    registrations = [
        {"gateway_id": "gateway-1", "workspace_id": "ws-1", "connection_status": "online"},
        {"gateway_id": "gateway-2", "workspace_id": "ws-1", "connection_status": "online"},
    ]
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda workspace_id, tenant_id=None, user_id=None, include_revoked=False: registrations,
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: None,
    )
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )

    payload = connection_catalog_service.list_status_payload(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="agent_computer",
    )

    item = payload["items"][0]
    assert item["id"] == "agent_computer"
    assert item["gateway_count"] == 2
    assert item["selected_gateway_id"] is None
    assert item["connected"] is False
    assert item["display_state"] == "connectable"
    assert item["online_gateway_count"] == 2
    assert item["online_gateway_candidate"] is None


def test_connection_status_exposes_single_online_candidate_without_auto_selecting(monkeypatch):
    registrations = [
        {"gateway_id": "gateway-1", "workspace_id": "ws-1", "connection_status": "online", "display_name": "Mac"},
    ]
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda workspace_id, tenant_id=None, user_id=None, include_revoked=False: registrations,
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: None,
    )
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )

    payload = connection_catalog_service.list_status_payload(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="agent_computer",
    )

    item = payload["items"][0]
    assert item["id"] == "agent_computer"
    assert item["selected_gateway_id"] is None
    assert item["selected_gateway_missing"] is False
    assert item["connected"] is False
    assert item["online_gateway_count"] == 1
    assert item["online_gateway_candidate"]["gateway_id"] == "gateway-1"


def test_connection_status_uses_persisted_selected_gateway(monkeypatch):
    registrations = [
        {"gateway_id": "gateway-1", "workspace_id": "ws-1", "connection_status": "online"},
        {"gateway_id": "gateway-2", "workspace_id": "ws-1", "connection_status": "online"},
    ]
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda workspace_id, tenant_id=None, user_id=None, include_revoked=False: registrations,
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {"selected_gateway_id": "gateway-2"},
    )
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )

    payload = connection_catalog_service.list_status_payload(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="agent_computer",
    )

    item = payload["items"][0]
    assert item["id"] == "agent_computer"
    assert item["selected_gateway_id"] == "gateway-2"
    assert item["connected"] is True
    assert item["display_state"] == "connected"


def test_connection_status_treats_fresh_active_gateway_as_online(monkeypatch):
    registrations = [
        {
            "gateway_id": "gateway-live",
            "workspace_id": "ws-1",
            "status": "active",
            "connection_status": None,
            "heartbeat_fresh": True,
            "display_name": "Mac",
        },
    ]
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda workspace_id, tenant_id=None, user_id=None, include_revoked=False: registrations,
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {"selected_gateway_id": "gateway-live"},
    )
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )

    payload = connection_catalog_service.list_status_payload(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="agent_computer",
    )

    item = payload["items"][0]
    assert item["id"] == "agent_computer"
    assert item["selected_gateway_id"] == "gateway-live"
    assert item["connected"] is True
    assert item["health_status"] == "healthy"
    assert item["online_gateway_count"] == 1
    assert item["online_gateway_candidate"]["gateway_id"] == "gateway-live"


def test_connection_status_preserves_stale_selected_gateway_id(monkeypatch):
    registrations = [
        {"gateway_id": "gateway-live", "workspace_id": "ws-1", "connection_status": "online", "display_name": "Mac"},
    ]
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda workspace_id, tenant_id=None, user_id=None, include_revoked=False: registrations,
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {"selected_gateway_id": "gateway-stale"},
    )
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )

    payload = connection_catalog_service.list_status_payload(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="agent_computer",
    )

    item = payload["items"][0]
    assert item["id"] == "agent_computer"
    assert item["selected_gateway_id"] == "gateway-stale"
    assert item["selected_gateway_missing"] is True
    assert item["health_status"] == "gateway_missing"
    assert item["connected"] is False
    assert item["online_gateway_candidate"]["gateway_id"] == "gateway-live"


def test_personal_channel_status_does_not_advertise_dead_generic_test(monkeypatch):
    registrations = [
        {"gateway_id": "gateway-1", "workspace_id": "ws-1", "connection_status": "online"},
    ]
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda workspace_id, tenant_id=None, user_id=None, include_revoked=False: registrations,
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {"selected_gateway_id": "gateway-1"},
    )
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )
    monkeypatch.setattr(
        connection_catalog_service.personal_channels_repository,
        "get_telegram_state",
        lambda gateway_id, channel_key="telegram_personal", agent_id="": {"status": "disconnected"},
    )
    monkeypatch.setattr(
        connection_catalog_service.personal_channels_repository,
        "get_whatsapp_state",
        lambda gateway_id, channel_key="whatsapp_personal", agent_id="": {"status": "disconnected"},
    )

    payload = connection_catalog_service.list_status_payload(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="sage",
    )

    by_id = {item["id"]: item for item in payload["items"]}
    assert by_id["telegram_personal"]["configured"] is True
    assert by_id["telegram_personal"]["connected"] is False
    assert by_id["telegram_personal"]["next_action"] == "connect"
    assert by_id["telegram_personal"]["display_state"] == "connectable"
    assert by_id["whatsapp_personal"]["next_action"] == "connect"
    assert by_id["whatsapp_personal"]["display_state"] == "connectable"


def test_sage_channel_catalog_exposes_cloud_telegram_bot_without_agent_computer():
    payload = connection_catalog_service.list_catalog_payload(surface="sage")
    by_id = {item["id"]: item for item in payload["items"]}

    assert "telegram_bot" in by_id
    assert by_id["telegram_bot"]["requires_gateway"] is False
    assert by_id["telegram_bot"]["setup_available"] is True
    assert by_id["telegram_bot"]["runtime_usable"] is True
    assert by_id["telegram_bot"]["launch_status"] == connection_catalog_service.LAUNCH_LIVE_WHEN_CONFIGURED
    assert by_id["telegram_bot"]["auth_required_fields"] == ["bot_token", "chat_id"]
    assert "telegram_personal" in by_id
    assert by_id["telegram_personal"]["requires_gateway"] is True
    assert "sage_telegram_hosted" in by_id
    assert by_id["sage_telegram_hosted"]["requires_gateway"] is False
    assert by_id["sage_telegram_hosted"]["launch_status"] == connection_catalog_service.LAUNCH_LIVE_WHEN_CONFIGURED


def test_sage_channel_status_marks_gateway_dependent_channels_from_backend(monkeypatch):
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda workspace_id, tenant_id=None, user_id=None, include_revoked=False: [],
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: None,
    )
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )

    payload = connection_catalog_service.list_status_payload(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="sage",
    )

    by_id = {item["id"]: item for item in payload["items"]}
    assert by_id["telegram_personal"]["display_state"] == "requires_agent_computer"
    assert by_id["whatsapp_personal"]["display_state"] == "requires_agent_computer"
    assert by_id["signal_personal"]["display_state"] == "unavailable"


def test_local_bridge_health_promotes_status_to_connected(monkeypatch):
    gateway = {
        "gateway_id": "gateway-1",
        "workspace_id": "ws-1",
        "user_id": "user-1",
        "status": "active",
        "connection_status": "online",
        "heartbeat_fresh": True,
        "metadata": {
            "personal_channel_health": {
                "signal_personal": {
                    "channel_key": "signal_personal",
                    "status": "connected",
                    "connected": True,
                }
            }
        },
    }
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda workspace_id, tenant_id=None, user_id=None, include_revoked=False: [gateway],
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {"selected_gateway_id": "gateway-1"},
    )
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )
    monkeypatch.setattr(
        connection_catalog_service.personal_channels_repository,
        "get_telegram_state",
        lambda gateway_id, channel_key="telegram_personal", agent_id="": None,
    )
    monkeypatch.setattr(
        connection_catalog_service.personal_channels_repository,
        "get_whatsapp_state",
        lambda gateway_id, channel_key="whatsapp_personal", agent_id="": None,
    )

    payload = connection_catalog_service.list_status_payload(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="sage",
    )

    signal = {item["id"]: item for item in payload["items"]}["signal_personal"]
    assert signal["connected"] is True
    assert signal["health_status"] == "healthy"
    assert signal["display_state"] == "connected"
    assert signal["next_action"] == "manage"
    assert signal["readiness_status"] == "launch_certified"
    assert signal["certification_required"] is False


def test_connection_catalog_exposes_launch_contract_without_ui_side_lock_lists():
    by_id = {item["id"]: item for item in connection_catalog_service.catalog_items()}

    google = by_id["google_workspace"]
    assert google["launch_status"] == connection_catalog_service.LAUNCH_LIVE_WHEN_CONFIGURED
    assert "google_calendar" not in by_id
    assert google["connector_ids"] == ["google_workspace", "gmail", "google_calendar", "google_drive"]
    assert google["setup_available"] is True
    assert google["runtime_usable"] is True
    assert google["launchable"] is True
    assert google["auth_required_fields"] == ["access_token"]
    assert google["test_runner"] == "not_implemented"
    assert "generic_connection_test_not_implemented" in google["proof_blockers"]

    signal = by_id["signal_personal"]
    assert signal["launch_status"] == connection_catalog_service.LAUNCH_PLANNED
    assert signal["setup_available"] is False
    assert signal["runtime_usable"] is False
    assert signal["launchable"] is False
    assert "launch_status:planned" in signal["launch_blockers"]
    assert signal["readiness_status"] == "planned"
    assert signal["certification_required"] is True

    apple = by_id["apple_messages_business"]
    assert apple["launch_status"] == connection_catalog_service.LAUNCH_PLANNED
    assert apple["setup_available"] is False
    assert apple["runtime_usable"] is False
    assert apple["launchable"] is False
    assert apple["requires_gateway"] is False
    assert apple["connector_id"] == "apple_messages_business"
    assert apple["certification_required"] is True

    discord = by_id["discord_bot"]
    assert discord["launch_status"] == connection_catalog_service.LAUNCH_LIVE_WHEN_CONFIGURED
    assert discord["setup_available"] is True
    assert discord["runtime_usable"] is True
    assert discord["launchable"] is True
    assert discord["auth_required_fields"] == [
        "bot_token",
        "channel_id",
        "guild_id",
        "application_id",
        "public_key",
        "application_public_key",
    ]
    assert connectors_actions.connector_contract("discord_bot")["allowed_secret_fields"] == [
        "bot_token",
        "channel_id",
        "guild_id",
        "application_id",
        "public_key",
        "application_public_key",
    ]

    github = by_id["github"]
    assert github["launch_status"] == connection_catalog_service.LAUNCH_LIVE_WHEN_CONFIGURED
    assert github["setup_available"] is True
    assert github["runtime_usable"] is True
    assert github["launchable"] is True
    assert github["supports_inbound"] is True

    notion = by_id["notion"]
    assert notion["launch_status"] == connection_catalog_service.LAUNCH_LIVE_WHEN_CONFIGURED
    assert notion["setup_available"] is True
    assert notion["runtime_usable"] is True
    assert notion["launchable"] is True
    assert notion["supports_inbound"] is False
    assert notion["supports_outbound"] is True

    linear = by_id["linear"]
    assert linear["launch_status"] == connection_catalog_service.LAUNCH_LIVE_WHEN_CONFIGURED
    assert linear["setup_available"] is True
    assert linear["runtime_usable"] is True
    assert linear["launchable"] is True
    assert linear["supports_inbound"] is False
    assert linear["supports_outbound"] is True


def test_oauth_status_locks_setup_when_provider_credentials_are_missing(monkeypatch):
    for name in (
        "GOOGLE_WORKSPACE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda **_kwargs: None,
    )

    by_id = {
        item["id"]: item
        for item in connection_catalog_service.status_items(
            workspace_id="ws-1",
            user_id="user-1",
            surface="sage",
        )
    }

    google = by_id["google_workspace"]
    assert google["setup_available"] is False
    assert google["next_action"] == "locked"
    assert google["health_status"] == "setup_missing"
    assert google["display_state"] == "requires_setup"


def test_oauth_status_allows_setup_when_provider_credentials_exist(monkeypatch):
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "github-client-id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "github-client-secret")
    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [],
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda **_kwargs: None,
    )

    by_id = {
        item["id"]: item
        for item in connection_catalog_service.status_items(
            workspace_id="ws-1",
            user_id="user-1",
            surface="sage",
        )
    }

    github = by_id["github"]
    assert github["setup_available"] is True
    assert github["next_action"] == "connect"
    assert github["health_status"] == "not_configured"
    assert github["display_state"] == "connectable"


@pytest.mark.anyio
async def test_google_workspace_connection_setup_starts_oauth_not_fake_session(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setenv("GOOGLE_WORKSPACE_OAUTH_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET", "google-client-secret")

    async def fail_create_setup_session(*_args, **_kwargs):
        raise AssertionError("generic setup session should not be used for launchable app connectors")

    monkeypatch.setattr(routes_connections.setup_sessions, "handle_create_setup_session", fail_create_setup_session)

    payload = await routes_connections.start_connection_setup(
        "google_workspace",
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="sage",
        ),
        request=SimpleNamespace(
            headers={"x-forwarded-proto": "http", "x-forwarded-host": "localhost:3000"},
            base_url="http://127.0.0.1:8001/",
        ),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["next_action"] == "oauth_redirect"
    assert payload["provider"] == "google_workspace"
    assert payload["redirect_uri"] == "http://localhost:3000/api/connections/oauth/google_workspace/callback"
    assert payload["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    query = urlparse.parse_qs(urlparse.urlparse(payload["authorization_url"]).query)
    scopes = query["scope"][0].split()
    assert "https://www.googleapis.com/auth/gmail.modify" in scopes
    assert "https://www.googleapis.com/auth/calendar" in scopes
    assert "https://www.googleapis.com/auth/drive.file" not in scopes


@pytest.mark.anyio
async def test_google_workspace_connection_setup_can_opt_into_drive_scope(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setenv("GOOGLE_WORKSPACE_OAUTH_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET", "google-client-secret")
    monkeypatch.setenv("GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE", "true")

    payload = await routes_connections.start_connection_setup(
        "google_workspace",
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="sage",
        ),
        request=SimpleNamespace(
            headers={"x-forwarded-proto": "http", "x-forwarded-host": "localhost:3000"},
            base_url="http://127.0.0.1:8001/",
        ),
        current_user={"user_id": "user-1", "role": "member"},
    )

    query = urlparse.parse_qs(urlparse.urlparse(payload["authorization_url"]).query)
    scopes = query["scope"][0].split()
    assert "https://www.googleapis.com/auth/drive.file" in scopes


@pytest.mark.anyio
async def test_microsoft_365_connection_setup_starts_oauth_not_fake_session(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setenv("MICROSOFT_365_OAUTH_CLIENT_ID", "microsoft-client-id")
    monkeypatch.setenv("MICROSOFT_365_OAUTH_CLIENT_SECRET", "microsoft-client-secret")

    async def fail_create_setup_session(*_args, **_kwargs):
        raise AssertionError("generic setup session should not be used for Microsoft 365 app connector")

    monkeypatch.setattr(routes_connections.setup_sessions, "handle_create_setup_session", fail_create_setup_session)

    payload = await routes_connections.start_connection_setup(
        "microsoft_365",
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="sage",
        ),
        request=SimpleNamespace(
            headers={"x-forwarded-proto": "http", "x-forwarded-host": "localhost:3000"},
            base_url="http://127.0.0.1:8001/",
        ),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["next_action"] == "oauth_redirect"
    assert payload["provider"] == "microsoft_365"
    assert payload["redirect_uri"] == "http://localhost:3000/api/connections/oauth/microsoft_365/callback"
    assert payload["authorization_url"].startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("connection_id", "client_env", "secret_env", "expected_provider", "expected_prefix"),
    [
        ("notion", "NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET", "notion", "https://mcp.notion.com/authorize?"),
        ("linear", "LINEAR_OAUTH_CLIENT_ID", "LINEAR_OAUTH_CLIENT_SECRET", "linear", "https://mcp.linear.app/authorize?"),
        ("dropbox", "DROPBOX_OAUTH_CLIENT_ID", "DROPBOX_OAUTH_CLIENT_SECRET", "dropbox", "https://www.dropbox.com/oauth2/authorize?"),
    ],
)
async def test_launchable_oauth_app_setup_starts_provider_oauth(
    monkeypatch,
    connection_id,
    client_env,
    secret_env,
    expected_provider,
    expected_prefix,
):
    _install_auth(monkeypatch)
    monkeypatch.setenv(client_env, f"{expected_provider}-client-id")
    monkeypatch.setenv(secret_env, f"{expected_provider}-client-secret")

    async def fail_create_setup_session(*_args, **_kwargs):
        raise AssertionError("generic setup session should not be used for launchable app connectors")

    monkeypatch.setattr(routes_connections.setup_sessions, "handle_create_setup_session", fail_create_setup_session)

    payload = await routes_connections.start_connection_setup(
        connection_id,
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="sage",
        ),
        request=SimpleNamespace(
            headers={"x-forwarded-proto": "http", "x-forwarded-host": "localhost:3000"},
            base_url="http://127.0.0.1:8001/",
        ),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["next_action"] == "oauth_redirect"
    assert payload["provider"] == expected_provider
    assert payload["redirect_uri"] == f"http://localhost:3000/api/connections/oauth/{expected_provider}/callback"
    assert payload["authorization_url"].startswith(expected_prefix)


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "connection_id",
        "client_env",
        "secret_env",
        "expected_provider",
        "expected_prefix",
        "expected_token_url",
        "uses_pkce",
        "uses_basic_auth",
        "includes_client_id_in_body",
        "includes_client_secret_in_body",
        "includes_redirect_uri_in_token_body",
        "includes_grant_type_in_token_body",
        "includes_redirect_uri_in_authorization_url",
        "includes_response_type",
        "token_request_format",
    ),
    [
        ("figma", "FIGMA_CLIENT_ID", "FIGMA_CLIENT_SECRET", "figma", "https://www.figma.com/oauth?", "https://api.figma.com/v1/oauth/token", False, True, False, False, True, True, True, True, "form"),
        ("todoist", "TODOIST_CLIENT_ID", "TODOIST_CLIENT_SECRET", "todoist", "https://app.todoist.com/oauth/authorize?", "https://todoist.com/oauth/access_token", False, False, True, True, True, True, True, True, "form"),
        ("airtable", "AIRTABLE_CLIENT_ID", "AIRTABLE_CLIENT_SECRET", "airtable", "https://airtable.com/oauth2/v1/authorize?", "https://airtable.com/oauth2/v1/token", True, True, True, False, True, True, True, True, "form"),
        ("canva", "CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "canva", "https://mcp.canva.com/authorize?", "https://mcp.canva.com/token", True, True, False, False, True, True, True, True, "form"),
        ("asana", "ASANA_CLIENT_ID", "ASANA_CLIENT_SECRET", "asana", "https://mcp.asana.com/authorize?", "https://mcp.asana.com/token", True, False, True, True, True, True, True, True, "form"),
        ("hubspot", "HUBSPOT_CLIENT_ID", "HUBSPOT_CLIENT_SECRET", "hubspot", "https://app.hubspot.com/oauth/authorize?", "https://api.hubapi.com/oauth/v3/token", False, False, True, True, True, True, True, True, "form"),
        ("zoom", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET", "zoom", "https://zoom.us/oauth/authorize?", "https://zoom.us/oauth/token", False, True, False, False, True, True, True, True, "form"),
        ("calendly", "CALENDLY_CLIENT_ID", "CALENDLY_CLIENT_SECRET", "calendly", "https://auth.calendly.com/oauth/authorize?", "https://auth.calendly.com/oauth/token", True, False, True, True, True, True, True, True, "form"),
        ("clickup", "CLICKUP_CLIENT_ID", "CLICKUP_CLIENT_SECRET", "clickup", "https://mcp.clickup.com/oauth/authorize?", "https://mcp.clickup.com/oauth/token", True, False, True, False, True, True, True, True, "form"),
        ("jira", "ATLASSIAN_CLIENT_ID", "ATLASSIAN_CLIENT_SECRET", "jira", "https://auth.atlassian.com/authorize?", "https://auth.atlassian.com/oauth/token", False, False, True, True, True, True, True, True, "json"),
        ("stripe", "STRIPE_CLIENT_ID", "STRIPE_CLIENT_SECRET", "stripe", "https://access.stripe.com/mcp/oauth2/authorize?", "https://access.stripe.com/mcp/oauth2/token", True, False, True, False, True, True, True, True, "form"),
        ("salesforce", "SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET", "salesforce", "https://login.salesforce.com/services/oauth2/authorize?", "https://login.salesforce.com/services/oauth2/token", False, False, True, True, True, True, True, True, "form"),
        ("webflow", "WEBFLOW_CLIENT_ID", "WEBFLOW_CLIENT_SECRET", "webflow", "https://webflow.com/oauth/authorize?", "https://api.webflow.com/oauth/access_token", False, False, True, True, True, True, True, True, "form"),
        ("monday", "MONDAY_CLIENT_ID", "MONDAY_CLIENT_SECRET", "monday", "https://auth.monday.com/oauth2/authorize?", "https://auth.monday.com/oauth2/token", False, False, True, True, True, False, True, True, "form"),
        ("box", "BOX_CLIENT_ID", "BOX_CLIENT_SECRET", "box", "https://account.box.com/api/oauth2/authorize?", "https://api.box.com/oauth2/token", False, False, True, True, True, True, True, True, "form"),
        ("gitlab", "GITLAB_CLIENT_ID", "GITLAB_CLIENT_SECRET", "gitlab", "https://gitlab.com/oauth/authorize?", "https://gitlab.com/oauth/token", False, False, True, True, True, True, True, True, "form"),
        ("confluence", "ATLASSIAN_CLIENT_ID", "ATLASSIAN_CLIENT_SECRET", "confluence", "https://auth.atlassian.com/authorize?", "https://auth.atlassian.com/oauth/token", False, False, True, True, True, True, True, True, "json"),
        ("miro", "MIRO_CLIENT_ID", "MIRO_CLIENT_SECRET", "miro", "https://miro.com/oauth/authorize?", "https://api.miro.com/v1/oauth/token", False, False, True, True, True, True, True, True, "form"),
        ("intercom", "INTERCOM_CLIENT_ID", "INTERCOM_CLIENT_SECRET", "intercom", "https://app.intercom.com/oauth?", "https://api.intercom.io/auth/eagle/token", False, False, True, True, False, False, False, False, "form"),
        ("docusign", "DOCUSIGN_CLIENT_ID", "DOCUSIGN_CLIENT_SECRET", "docusign", "https://account.docusign.com/oauth/auth?", "https://account.docusign.com/oauth/token", False, True, False, False, True, True, True, True, "form"),
        ("square", "SQUARE_CLIENT_ID", "SQUARE_CLIENT_SECRET", "square", "https://connect.squareup.com/oauth2/authorize?", "https://connect.squareup.com/oauth2/token", False, False, True, True, True, True, True, True, "json"),
        ("typeform", "TYPEFORM_CLIENT_ID", "TYPEFORM_CLIENT_SECRET", "typeform", "https://api.typeform.com/oauth/authorize?", "https://api.typeform.com/oauth/token", False, False, True, True, True, True, True, True, "form"),
        ("vercel", "VERCEL_CLIENT_ID", "VERCEL_CLIENT_SECRET", "vercel", "https://vercel.com/oauth/authorize?", "https://api.vercel.com/login/oauth/token", False, False, True, True, True, True, True, True, "form"),
    ],
)
async def test_new_oauth_app_setup_callback_and_status(
    monkeypatch,
    connection_id,
    client_env,
    secret_env,
    expected_provider,
    expected_prefix,
    expected_token_url,
    uses_pkce,
    uses_basic_auth,
    includes_client_id_in_body,
    includes_client_secret_in_body,
    includes_redirect_uri_in_token_body,
    includes_grant_type_in_token_body,
    includes_redirect_uri_in_authorization_url,
    includes_response_type,
    token_request_format,
):
    _install_auth(monkeypatch)
    monkeypatch.setenv(client_env, f"{expected_provider}-client-id")
    monkeypatch.setenv(secret_env, f"{expected_provider}-client-secret")

    payload = await routes_connections.start_connection_setup(
        connection_id,
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="sage",
        ),
        request=SimpleNamespace(
            headers={"x-forwarded-proto": "http", "x-forwarded-host": "localhost:3000"},
            base_url="http://127.0.0.1:8001/",
        ),
        current_user={"user_id": "user-1", "role": "owner"},
    )

    assert payload["next_action"] == "oauth_redirect"
    assert payload["provider"] == expected_provider
    assert payload["redirect_uri"] == f"http://localhost:3000/api/connections/oauth/{expected_provider}/callback"
    assert payload["authorization_url"].startswith(expected_prefix)
    query = urlparse.parse_qs(urlparse.urlparse(payload["authorization_url"]).query)
    assert query["client_id"] == [f"{expected_provider}-client-id"]
    if includes_redirect_uri_in_authorization_url:
        assert query["redirect_uri"] == [payload["redirect_uri"]]
    else:
        assert "redirect_uri" not in query
    assert query["state"]
    if includes_response_type:
        assert query["response_type"] == ["code"]
    else:
        assert "response_type" not in query
    if uses_pkce:
        assert query["code_challenge_method"] == ["S256"]
        assert len(query["code_challenge"][0]) == 43
    else:
        assert "code_challenge" not in query

    token_requests = []

    def fake_post_form_json(url, form_payload, *, headers=None):
        token_requests.append({"url": url, "payload": dict(form_payload), "headers": dict(headers or {}), "format": "form"})
        return {
            "access_token": f"{expected_provider}-access-token",
            "refresh_token": f"{expected_provider}-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(connection_oauth_service.OAUTH_PROVIDER_CONFIGS[expected_provider].scopes),
            "user_id_string": f"{expected_provider}-user",
        }

    def fake_post_json(url, json_payload, *, headers=None):
        token_requests.append({"url": url, "payload": dict(json_payload), "headers": dict(headers or {}), "format": "json"})
        return {
            "access_token": f"{expected_provider}-access-token",
            "refresh_token": f"{expected_provider}-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(connection_oauth_service.OAUTH_PROVIDER_CONFIGS[expected_provider].scopes),
            "user_id_string": f"{expected_provider}-user",
        }

    created = {}

    async def fake_create_connector_vault(body):
        created["body"] = body
        return {
            "id": f"{expected_provider}-connector-id",
            "provider": body.connector,
            "workspace_id": body.workspace_id,
            "metadata": body.metadata,
        }

    monkeypatch.setattr(connection_oauth_service, "_post_form_json", fake_post_form_json)
    monkeypatch.setattr(connection_oauth_service, "_post_json", fake_post_json)
    monkeypatch.setattr(connectors_actions, "create_connector_vault", fake_create_connector_vault)

    callback_payload = await connection_oauth_service.complete_oauth_callback(
        provider=expected_provider,
        code="provider-code",
        state=query["state"][0],
        request=SimpleNamespace(
            headers={"x-forwarded-proto": "http", "x-forwarded-host": "localhost:3000"},
            base_url="http://127.0.0.1:8001/",
        ),
    )

    assert callback_payload["ok"] is True
    assert callback_payload["provider"] == expected_provider
    assert created["body"].connector == expected_provider
    assert created["body"].workspace_id == "ws-1"
    assert created["body"].credentials["access_token"] == f"{expected_provider}-access-token"
    assert created["body"].metadata["oauth_provider"] == expected_provider
    assert token_requests
    assert token_requests[0]["url"] == expected_token_url
    assert token_requests[0]["format"] == token_request_format
    assert token_requests[0]["payload"]["code"] == "provider-code"
    if includes_redirect_uri_in_token_body:
        assert token_requests[0]["payload"]["redirect_uri"] == payload["redirect_uri"]
    else:
        assert "redirect_uri" not in token_requests[0]["payload"]
    if includes_grant_type_in_token_body:
        assert token_requests[0]["payload"]["grant_type"] == "authorization_code"
    else:
        assert "grant_type" not in token_requests[0]["payload"]
    if uses_pkce:
        assert len(token_requests[0]["payload"]["code_verifier"]) == 43
    else:
        assert "code_verifier" not in token_requests[0]["payload"]
    if uses_basic_auth:
        assert token_requests[0]["headers"]["Authorization"].startswith("Basic ")
    else:
        assert "Authorization" not in token_requests[0]["headers"]
    if includes_client_id_in_body:
        assert token_requests[0]["payload"]["client_id"] == f"{expected_provider}-client-id"
    else:
        assert "client_id" not in token_requests[0]["payload"]
    if includes_client_secret_in_body:
        assert token_requests[0]["payload"]["client_secret"] == f"{expected_provider}-client-secret"
    else:
        assert "client_secret" not in token_requests[0]["payload"]

    monkeypatch.setattr(
        connection_catalog_service.runtime_common,
        "list_vault_connectors",
        lambda workspace_id: [{"connector": expected_provider, "provider": expected_provider}],
    )
    monkeypatch.setattr(
        connection_catalog_service.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        connection_catalog_service.sage_agent_computer_selection_service,
        "get_selection",
        lambda **_kwargs: None,
    )

    status_payload = await routes_connections.list_connection_status(
        workspace_id="ws-1",
        surface="sage",
        selected_gateway_id=None,
        current_user={"user_id": "user-1", "role": "owner"},
    )
    by_id = {item["id"]: item for item in status_payload["items"]}
    assert by_id[expected_provider]["connected"] is True
    assert by_id[expected_provider]["health_status"] == "healthy"


@pytest.mark.anyio
async def test_personal_channel_setup_accepts_fresh_active_gateway(monkeypatch):
    _install_auth(monkeypatch)

    async def fail_create_setup_session(*_args, **_kwargs):
        raise AssertionError("personal channel setup should return gateway setup endpoint")

    monkeypatch.setattr(routes_connections.setup_sessions, "handle_create_setup_session", fail_create_setup_session)
    monkeypatch.setattr(
        routes_connections.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {"selected_gateway_id": "gateway-1"},
    )
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "status": "active",
        },
    )
    monkeypatch.setattr(
        routes_connections.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: {
            **dict(registration),
            "connection_status": None,
            "heartbeat_fresh": True,
        },
    )

    payload = await routes_connections.start_connection_setup(
        "telegram_personal",
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="sage",
            selected_gateway_id="gateway-1",
        ),
        request=SimpleNamespace(),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["next_action"] == "personal_channel_setup"
    assert payload["gateway_id"] == "gateway-1"
    assert payload["setup_endpoint"] == "/api/personal-channels/telegram/gateways/gateway-1/setup"


@pytest.mark.anyio
async def test_local_bridge_connection_setup_returns_agent_computer_contract(monkeypatch):
    _install_auth(monkeypatch)

    async def fail_create_setup_session(*_args, **_kwargs):
        raise AssertionError("generic setup session should not be used for local bridge personal channels")

    monkeypatch.setattr(routes_connections.setup_sessions, "handle_create_setup_session", fail_create_setup_session)
    monkeypatch.setattr(
        routes_connections.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {"selected_gateway_id": "gateway-1"},
    )
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
        },
    )
    monkeypatch.setattr(
        routes_connections.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )

    payload = await routes_connections.start_connection_setup(
        "signal_personal",
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="sage",
            selected_gateway_id="gateway-1",
        ),
        request=SimpleNamespace(),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["next_action"] == "agent_computer_local_bridge_setup"
    assert payload["certification_required"] is True
    assert payload["bridge_contract"]["channel_key"] == "signal_personal"
    assert payload["bridge_contract"]["http_contract"]["health"] == "GET /health"


@pytest.mark.anyio
async def test_connection_verify_route_returns_provider_identity(monkeypatch):
    _install_auth(monkeypatch)
    calls = []

    def fake_verify_connection(**kwargs):
        calls.append(kwargs)
        return {
            "valid": True,
            "account_name": "@empyralis_bot",
            "account_id": "123",
        }

    monkeypatch.setattr(routes_connections.connection_verify_service, "verify_connection", fake_verify_connection)

    payload = await routes_connections.verify_connection(
        "telegram_bot",
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="studio",
        ),
        request=SimpleNamespace(),
        current_user={"user_id": "owner-1", "role": "owner"},
    )

    assert payload["ok"] is True
    assert payload["connection_id"] == "telegram_bot"
    assert payload["provider"] == "telegram_bot"
    assert payload["valid"] is True
    assert payload["account_name"] == "@empyralis_bot"
    assert calls == [
        {
            "connection_id": "telegram_bot",
            "provider": "telegram_bot",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
        }
    ]


@pytest.mark.anyio
async def test_wechat_connection_setup_returns_agent_computer_contract(monkeypatch):
    _install_auth(monkeypatch)

    async def fail_create_setup_session(*_args, **_kwargs):
        raise AssertionError("generic setup session should not be used for WeChat local bridge personal channels")

    monkeypatch.setattr(routes_connections.setup_sessions, "handle_create_setup_session", fail_create_setup_session)
    monkeypatch.setattr(
        routes_connections.sage_agent_computer_selection_service,
        "get_selection",
        lambda workspace_id, user_id: {"selected_gateway_id": "gateway-1"},
    )
    monkeypatch.setattr(
        routes_connections.gateway_state_repository,
        "get_gateway_registration",
        lambda gateway_id: {
            "gateway_id": gateway_id,
            "workspace_id": "ws-1",
            "user_id": "user-1",
            "device_trust_state": "verified",
            "connection_status": "online",
        },
    )
    monkeypatch.setattr(
        routes_connections.gateway_registry_service,
        "gateway_registration_public_payload",
        lambda registration: dict(registration),
    )

    payload = await routes_connections.start_connection_setup(
        "wechat_personal",
        body=routes_connections.ConnectionActionRequest(
            workspace_id="ws-1",
            surface="sage",
            selected_gateway_id="gateway-1",
        ),
        request=SimpleNamespace(),
        current_user={"user_id": "user-1", "role": "member"},
    )

    assert payload["next_action"] == "agent_computer_local_bridge_setup"
    assert payload["certification_required"] is True
    assert payload["bridge_contract"]["channel_key"] == "wechat_personal"
    assert payload["bridge_contract"]["gateway_runtime"] == "empyralis-gateway/src/channels/local-bridge-runtime.ts"

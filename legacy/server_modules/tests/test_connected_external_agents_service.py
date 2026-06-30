from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

from server_modules import connected_external_agent_service as service


def _fake_scoped_connection(connection):
    @asynccontextmanager
    async def _scoped_connection(**_kwargs):
        yield connection

    return _scoped_connection


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, content: bytes | None = None) -> None:
        self._payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    def __init__(self) -> None:
        self.gets: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.gets.append({"url": url, "headers": headers})
        return _FakeResponse({
            "id": "external-openclaw",
            "name": "OpenClaw gateway",
            "auth_type": "bearer",
            "capabilities": ["chat", "knowledge_read", "logs"],
            "endpoints": {
                "chat": "https://example.com/external/chat",
                "logs": "https://example.com/external/logs",
            },
        })

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str] | None = None) -> _FakeResponse:
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse({
            "reply": "external reply",
            "access_token": "must-not-leak",
        })


class _LargeManifestClient:
    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse({}, content=b"x" * (service.MAX_MANIFEST_RESPONSE_BYTES + 1))


class _MalformedManifestClient:
    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse({
            "id": "external-bad",
            "capabilities": ["chat"],
            "native_deploy": True,
        })


class _UnsupportedSchemaManifestClient:
    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse({
            "id": "external-new-schema",
            "schema_version": "studio.external_agent.v99",
            "capabilities": ["chat"],
            "endpoints": {"chat": "https://example.com/chat"},
        })


class _HealthHandshakeClient:
    def __init__(self, **_kwargs: Any) -> None:
        self.closed = False
        self.gets: list[dict[str, Any]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        if self.closed:
            raise RuntimeError("client already closed")
        self.gets.append({"url": url, "headers": headers})
        if url == "https://example.com/health":
            return _FakeResponse({"ok": True})
        return _FakeResponse({
            "id": "external-health",
            "name": "Health checked agent",
            "capabilities": ["chat"],
            "endpoints": {
                "chat": "https://example.com/external/chat",
                "health": "https://example.com/health",
            },
        })

    async def aclose(self) -> None:
        self.closed = True


class _SectionDataClient:
    def __init__(self) -> None:
        self.gets: list[dict[str, Any]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.gets.append({"url": url, "headers": headers})
        if url == "https://example.com/events":
            return _FakeResponse({
                "display_kind": "timeline",
                "object_type": "external_agent_event",
                "items": [
                    {
                        "id": "evt-1",
                        "title": "Workflow finished",
                        "status": "ok",
                        "access_token": "must-not-leak",
                    }
                ],
            })
        return _FakeResponse({
            "id": "external-sections",
            "schema_version": service.EXTERNAL_AGENT_MANIFEST_SCHEMA_VERSION,
            "capabilities": ["chat", "events"],
            "endpoints": {
                "chat": "https://example.com/chat",
                "events": "https://example.com/events",
            },
            "surface_sections": [
                {
                    "id": "run_history",
                    "title": "Run History",
                    "icon": "workflows",
                    "capability_required": "events",
                    "data_endpoint_ref": "events",
                    "display_kind": "timeline",
                }
            ],
            "objects": ["external_agent_event"],
        })


class _SubAgentSectionClient:
    def __init__(self) -> None:
        self.gets: list[dict[str, Any]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.gets.append({"url": url, "headers": headers})
        if url == "https://example.com/sub-agents":
            return _FakeResponse({
                "display_kind": "cards",
                "object_type": "external_agent_sub_agent",
                "items": [
                    {
                        "id": "researcher",
                        "name": "Research worker",
                        "provider": "hermes",
                        "runtime": "external",
                        "status": "ready",
                        "summary": "Handles web research inside the external runtime.",
                    }
                ],
            })
        return _FakeResponse({
            "id": "external-subagents",
            "schema_version": service.EXTERNAL_AGENT_MANIFEST_SCHEMA_VERSION,
            "capabilities": ["chat", "sub_agents"],
            "endpoints": {
                "chat": "https://example.com/chat",
                "sub_agents": "https://example.com/sub-agents",
            },
            "surface_sections": [
                {
                    "id": "sub_agents",
                    "title": "Sub-agents",
                    "description": "Workers managed by this external runtime.",
                    "empty_state": "No external sub-agents are reported yet.",
                    "category": "resources",
                    "priority": 10,
                    "icon": "sub_agents",
                    "capability_required": "sub_agents",
                    "data_endpoint_ref": "sub_agents",
                    "actions_endpoint_ref": "actions",
                    "display_kind": "cards",
                }
            ],
            "sub_agents": [
                {
                    "id": "researcher",
                    "name": "Research worker",
                    "status": "ready",
                    "summary": "Handles web research inside the external runtime.",
                    "capabilities": ["chat", "knowledge_read"],
                }
            ],
            "objects": ["external_agent_sub_agent"],
        })


async def _fake_runtime_attachments(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "attachments": [
            {
                "attachment_id": "computer-macbook",
                "attachment_kind": "local_companion",
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "runtime_id": "gateway-macbook",
                "machine_id": "device-macbook",
                "label": "Mansur MacBook",
                "online": True,
                "healthy": True,
                "control_state": "active",
                "status": "ready",
                "capabilities": ["external_agent_proxy", "browser"],
                "capability_readiness": {
                    "ready": ["external_agent_proxy", "browser"],
                },
                "gateway_identity": {
                    "gateway_id": "gateway-macbook",
                    "device_id": "device-macbook",
                    "device_trust_state": "verified",
                    "status": "active",
                },
            }
        ]
    }


async def _fake_runtime_attachments_with_heartbeat_ready_proxy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "attachments": [
            {
                "attachment_id": "computer-macbook",
                "attachment_kind": "local_companion",
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "runtime_id": "gateway-macbook",
                "machine_id": "device-macbook",
                "label": "Mansur MacBook",
                "online": True,
                "healthy": True,
                "control_state": "active",
                "status": "ready",
                "capabilities": ["browser"],
                "capability_readiness": {
                    "requested": ["browser", "external_agent_proxy"],
                    "ready": ["browser", "external_agent_proxy"],
                },
                "gateway_identity": {
                    "gateway_id": "gateway-macbook",
                    "device_id": "device-macbook",
                    "device_trust_state": "verified",
                    "status": "active",
                },
            }
        ]
    }


@pytest.fixture(autouse=True)
def _reset_local_store():
    service._reset_for_tests()
    yield
    service._reset_for_tests()


@pytest.mark.anyio
async def test_create_external_agent_starts_unverified_and_does_not_store_raw_secret() -> None:
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Hermes gateway",
            provider_kind="hermes",
            endpoints={
                "manifest_url": "https://example.com/.well-known/agent-manifest.json",
                "chat_url": "https://example.com/chat",
            },
            manifest={
                "auth_type": "bearer",
                "capabilities": ["chat"],
            },
            secret_ref="vault://workspace/ws-1/hermes",
            created_by_user_id="owner-1",
        )

    assert created["surface_kind"] == "connected_external_agent"
    assert created["provider_kind"] == "hermes"
    assert created["connection_state"] == "unverified"
    assert created["secret_ref"] == "vault://workspace/ws-1/hermes"
    assert created["capability_manifest"]["chat"] is True
    assert "access_token" not in str(created).lower()


@pytest.mark.anyio
async def test_external_agent_chat_requires_verified_chat_capability() -> None:
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Custom agent",
            provider_kind="custom",
            endpoints={"chat_url": "https://example.com/chat"},
            manifest={"capabilities": ["chat"]},
        )

        with pytest.raises(ValueError, match="verified"):
            await service.chat_with_connected_external_agent(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                external_agent_id=created["id"],
                message="hello",
            )


@pytest.mark.anyio
async def test_refresh_manifest_enables_private_chat_through_backend_proxy() -> None:
    http_client = _FakeHttpClient()
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="OpenClaw gateway",
            provider_kind="openclaw",
            endpoints={"manifest_url": "https://example.com/manifest.json"},
            manifest={},
        )

        refreshed = await service.refresh_connected_external_agent_manifest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            http_client=http_client,
        )
        response = await service.chat_with_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            message="hello",
            recent_messages=[{"role": "user", "content": "previous"}],
            http_client=http_client,
        )

    assert refreshed["connection_state"] == "verified"
    assert refreshed["capability_manifest"]["knowledge_read"] is True
    assert response["reply"] == "external reply"
    assert response["raw_response"]["access_token"] == "[redacted]"
    assert http_client.posts[0]["url"] == "https://example.com/external/chat"
    assert http_client.posts[0]["json"]["channel"] == "private_workspace"
    assert http_client.posts[0]["json"]["agent_id"] == created["id"]


@pytest.mark.anyio
async def test_manifest_surface_sections_fetch_external_owned_objects_through_proxy() -> None:
    http_client = _SectionDataClient()
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Custom section agent",
            provider_kind="custom",
            endpoints={"manifest_url": "https://example.com/manifest.json"},
            manifest={},
        )
        refreshed = await service.refresh_connected_external_agent_manifest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            http_client=http_client,
        )
        section = await service.get_connected_external_agent_section_data(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            section_id="run_history",
            http_client=http_client,
        )

    assert refreshed["surface_sections"][0]["id"] == "run_history"
    assert refreshed["surface_sections"][0]["interaction"] == "read_only"
    assert refreshed["surface_sections"][0]["category"] == "activity"
    assert refreshed["surface_sections"][0]["priority"] == 50
    assert refreshed["object_types"] == ["external_agent_event"]
    assert section["display_kind"] == "timeline"
    assert section["section"]["title"] == "Run History"
    assert section["items"][0]["ownership"] == "external"
    assert section["items"][0]["object_type"] == "external_agent_event"
    assert section["items"][0]["raw_redacted"]["access_token"] == "[redacted]"
    assert [item["url"] for item in http_client.gets] == [
        "https://example.com/manifest.json",
        "https://example.com/events",
    ]


@pytest.mark.anyio
async def test_manifest_sub_agent_sections_stay_external_owned_and_read_only() -> None:
    http_client = _SubAgentSectionClient()
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Sub-agent runtime",
            provider_kind="custom",
            endpoints={"manifest_url": "https://example.com/manifest.json"},
            manifest={},
        )
        refreshed = await service.refresh_connected_external_agent_manifest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            http_client=http_client,
        )
        section = await service.get_connected_external_agent_section_data(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            section_id="sub_agents",
            http_client=http_client,
        )

    surface = refreshed["surface_sections"][0]
    assert surface["description"] == "Workers managed by this external runtime."
    assert surface["empty_state"] == "No external sub-agents are reported yet."
    assert surface["category"] == "resources"
    assert surface["priority"] == 10
    assert surface["actions_endpoint_ref"] == "actions_url"
    assert surface["interaction"] == "read_only"
    assert refreshed["object_types"] == ["external_agent_sub_agent"]
    assert refreshed["external_sub_agents"][0]["id"] == "researcher"
    assert refreshed["external_sub_agents"][0]["ownership"] == "external"
    assert refreshed["external_sub_agents"][0]["capabilities"] == ["chat", "knowledge_read"]
    assert section["items"][0]["ownership"] == "external"
    assert section["items"][0]["object_type"] == "external_agent_sub_agent"
    assert section["items"][0]["raw_redacted"]["provider"] == "hermes"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_kind", "expected_provider", "protocol_kind", "sub_agent_id", "sub_agent_name"),
    [
        ("openclaw", "openclaw", "openclaw", "browser-node", "OpenClaw browser node"),
        ("hermes", "hermes", "hermes", "researcher", "Hermes research worker"),
        ("nemoclaw", "nemoclaw", "nemoclaw", "planner", "NemoClaw planner"),
        ("nemo_claw", "nemoclaw", "nemoclaw", "planner", "NemoClaw planner"),
    ],
)
async def test_external_provider_families_project_sub_agents_without_native_ownership(
    provider_kind: str,
    expected_provider: str,
    protocol_kind: str,
    sub_agent_id: str,
    sub_agent_name: str,
) -> None:
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name=f"{sub_agent_name.split()[0]} Agents",
            provider_kind=provider_kind,
            endpoints={"chat_url": "https://example.com/chat"},
            manifest={
                "schema_version": service.EXTERNAL_AGENT_MANIFEST_SCHEMA_VERSION,
                "protocols": [{"kind": protocol_kind, "version": "1"}],
                "capabilities": ["chat", "sub_agents"],
                "sub_agents": [{
                    "id": sub_agent_id,
                    "name": sub_agent_name,
                    "status": "ready",
                    "summary": f"{sub_agent_name} is owned by the external runtime.",
                    "capabilities": ["chat"],
                }],
                "objects": ["external_agent_sub_agent"],
            },
        )

    assert created["provider_kind"] == expected_provider
    assert created["protocols"] == [{"kind": protocol_kind, "version": "1"}]
    assert created["object_types"] == ["external_agent_sub_agent"]
    assert created["external_sub_agents"] == [{
        "id": sub_agent_id,
        "external_id": sub_agent_id,
        "name": sub_agent_name,
        "label": sub_agent_name,
        "summary": f"{sub_agent_name} is owned by the external runtime.",
        "status": "ready",
        "role": None,
        "capabilities": ["chat"],
        "ownership": "external",
        "object_type": "external_agent_sub_agent",
    }]
    assert created["surface_kind"] == "connected_external_agent"
    assert created["studio_object_type"] == "connected_external_agent"


@pytest.mark.anyio
async def test_refresh_manifest_runs_health_handshake_before_closing_owned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    http_client = _HealthHandshakeClient()
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: http_client)

    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Health checked agent",
            provider_kind="custom",
            endpoints={"manifest_url": "https://example.com/manifest.json"},
            manifest={},
        )
        refreshed = await service.refresh_connected_external_agent_manifest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
        )

    assert refreshed["connection_state"] == "verified"
    assert [item["url"] for item in http_client.gets] == [
        "https://example.com/manifest.json",
        "https://example.com/health",
    ]
    assert http_client.closed is True


@pytest.mark.anyio
async def test_secret_ref_resolves_for_outbound_auth_and_never_returns_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    http_client = _FakeHttpClient()
    resolved: list[dict[str, Any]] = []

    def fake_resolve_vault_credential(credential_id: str, workspace_id: str, **scope: Any) -> dict[str, Any]:
        resolved.append({"credential_id": credential_id, "workspace_id": workspace_id, "scope": scope})
        return {"access_token": "resolved-secret-token"}

    monkeypatch.setattr(service.provider_profiles_service, "resolve_vault_credential", fake_resolve_vault_credential)

    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Secured external agent",
            provider_kind="custom",
            endpoints={"manifest_url": "https://example.com/manifest.json"},
            manifest={},
            secret_ref="vault://credential/cred-external",
        )
        refreshed = await service.refresh_connected_external_agent_manifest(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            http_client=http_client,
        )
        response = await service.chat_with_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=refreshed["id"],
            message="hello",
            http_client=http_client,
        )

    assert resolved[0]["credential_id"] == "cred-external"
    assert resolved[0]["workspace_id"] == "ws-1"
    assert resolved[0]["scope"]["allowed_fields"] == service.SECRET_RESOLUTION_FIELDS
    assert http_client.gets[0]["headers"]["Authorization"] == "Bearer resolved-secret-token"
    assert http_client.posts[0]["headers"]["Authorization"] == "Bearer resolved-secret-token"
    assert "resolved-secret-token" not in str(refreshed)
    assert "resolved-secret-token" not in str(response)


@pytest.mark.anyio
async def test_missing_secret_ref_resolution_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resolve_vault_credential(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("missing")

    monkeypatch.setattr(service.provider_profiles_service, "resolve_vault_credential", fake_resolve_vault_credential)

    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Secured external agent",
            provider_kind="custom",
            endpoints={"manifest_url": "https://example.com/manifest.json"},
            manifest={},
            secret_ref="vault://credential/missing",
        )
        with pytest.raises(ValueError, match="credential could not be resolved"):
            await service.refresh_connected_external_agent_manifest(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                external_agent_id=created["id"],
                http_client=_FakeHttpClient(),
            )


@pytest.mark.anyio
async def test_refresh_manifest_rejects_oversized_and_malformed_manifests() -> None:
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="External agent",
            provider_kind="custom",
            endpoints={"manifest_url": "https://example.com/manifest.json"},
            manifest={},
        )

        with pytest.raises(ValueError, match="exceeded"):
            await service.refresh_connected_external_agent_manifest(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                external_agent_id=created["id"],
                http_client=_LargeManifestClient(),
            )

        with pytest.raises(ValueError, match="unsupported fields"):
            await service.refresh_connected_external_agent_manifest(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                external_agent_id=created["id"],
                http_client=_MalformedManifestClient(),
            )

        with pytest.raises(ValueError, match="schema_version is unsupported"):
            await service.refresh_connected_external_agent_manifest(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                external_agent_id=created["id"],
                http_client=_UnsupportedSchemaManifestClient(),
            )


@pytest.mark.anyio
async def test_surface_section_metadata_is_limited_and_category_is_validated() -> None:
    long_text = "x" * 220
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Metadata limited agent",
            provider_kind="custom",
            endpoints={"events": "https://example.com/events"},
            manifest={
                "capabilities": ["events"],
                "surface_sections": [{
                    "id": "events",
                    "title": "Events",
                    "description": long_text,
                    "empty_state": long_text,
                    "category": "outputs",
                    "priority": 500,
                    "data_endpoint_ref": "events",
                    "display_kind": "timeline",
                }],
            },
        )

    section = created["surface_sections"][0]
    assert section["description"] == "x" * 160
    assert section["empty_state"] == "x" * 160
    assert section["category"] == "outputs"
    assert section["priority"] == 100

    with pytest.raises(ValueError, match="category is unsupported"):
        await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Bad category agent",
            provider_kind="custom",
            endpoints={"events": "https://example.com/events"},
            manifest={
                "capabilities": ["events"],
                "surface_sections": [{
                    "id": "events",
                    "title": "Events",
                    "category": "native",
                    "data_endpoint_ref": "events",
                    "display_kind": "timeline",
                }],
            },
        )


@pytest.mark.anyio
async def test_external_agent_chat_enforces_recent_message_limits() -> None:
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Chat agent",
            provider_kind="custom",
            endpoints={"chat_url": "https://example.com/chat"},
            manifest={"capabilities": ["chat"]},
        )
        service.LOCAL_STORE[("tenant-1", "ws-1", created["id"])] = {
            **created,
            "connection_state": "verified",
            "trust_state": "verified",
        }

        with pytest.raises(ValueError, match="more than"):
            await service.chat_with_connected_external_agent(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                external_agent_id=created["id"],
                message="hello",
                recent_messages=[{"role": "user", "content": "x"} for _ in range(service.MAX_RECENT_MESSAGES + 1)],
                http_client=_FakeHttpClient(),
            )
        with pytest.raises(ValueError, match="role"):
            await service.chat_with_connected_external_agent(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                external_agent_id=created["id"],
                message="hello",
                recent_messages=[{"role": "admin", "content": "x"}],
                http_client=_FakeHttpClient(),
            )


@pytest.mark.anyio
async def test_external_agent_rejects_private_network_endpoint() -> None:
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        with pytest.raises(ValueError, match="private-network"):
            await service.create_connected_external_agent(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                name="Local raw URL",
                provider_kind="custom",
                endpoints={"chat_url": "https://127.0.0.1:9999/chat"},
                manifest={"capabilities": ["chat"]},
            )


@pytest.mark.anyio
async def test_local_connector_projection_requires_explicit_agent_computer() -> None:
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Laptop-local agent",
            provider_kind="custom",
            endpoints={},
            manifest={
                "local_connector": {
                    "required": True,
                    "reason": "The external runtime is reachable only from the owner device.",
                },
            },
        )

    assert created["local_connector"]["required"] is True
    assert created["local_connector"]["mode"] == "agent_computer_proxy"
    assert created["local_connector"]["binding_state"] == "missing_agent_computer"
    assert created["local_connector"]["bound"] is False
    assert created["local_connector"]["proxy_available"] is False


@pytest.mark.anyio
async def test_local_connector_binds_to_agent_computer_and_enables_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.runtime_attachment_service,
        "list_workspace_runtime_attachments",
        _fake_runtime_attachments,
    )
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Bound local agent",
            provider_kind="custom",
            endpoints={"chat_url": "http://127.0.0.1:18789/chat"},
            manifest={
                "capabilities": ["chat"],
                "local_connector": {
                    "required": True,
                    "agent_computer_id": "computer-macbook",
                    "agent_computer_capability": "external_agent_proxy",
                },
            },
        )

    connector = created["local_connector"]
    assert connector["binding_state"] == "bound"
    assert connector["bound"] is True
    assert connector["agent_computer_label"] == "Mansur MacBook"
    assert connector["attachment_kind"] == "local_companion"
    assert connector["runtime_id"] == "gateway-macbook"
    assert connector["proxy_available"] is True


@pytest.mark.anyio
async def test_local_connector_accepts_heartbeat_ready_proxy_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.runtime_attachment_service,
        "list_workspace_runtime_attachments",
        _fake_runtime_attachments_with_heartbeat_ready_proxy,
    )
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Heartbeat-ready local agent",
            provider_kind="openclaw",
            endpoints={"chat_url": "http://127.0.0.1:18789/chat"},
            manifest={
                "capabilities": ["chat"],
                "local_connector": {
                    "required": True,
                    "agent_computer_id": "computer-macbook",
                    "agent_computer_capability": "external_agent_proxy",
                },
            },
        )

    connector = created["local_connector"]
    assert connector["binding_state"] == "bound"
    assert connector["bound"] is True
    assert connector["proxy_available"] is True


@pytest.mark.anyio
async def test_local_connector_required_chat_uses_agent_computer_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.runtime_attachment_service,
        "list_workspace_runtime_attachments",
        _fake_runtime_attachments,
    )
    proxy_calls: list[dict[str, Any]] = []

    async def fake_execute_tool_via_gateway(**kwargs: Any) -> dict[str, Any]:
        proxy_calls.append(dict(kwargs))
        return {"status": 200, "body_json": {"reply": "proxied reply"}}

    monkeypatch.setattr(service.gateway_execution_service, "execute_tool_via_gateway", fake_execute_tool_via_gateway)
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Bound local chat agent",
            provider_kind="custom",
            endpoints={"chat_url": "http://127.0.0.1:18789/chat"},
            manifest={
                "capabilities": ["chat"],
                "local_connector": {
                    "required": True,
                    "agent_computer_id": "computer-macbook",
                    "agent_computer_capability": "external_agent_proxy",
                },
            },
        )
        service.LOCAL_STORE[("tenant-1", "ws-1", created["id"])] = {
            **created,
            "connection_state": "verified",
            "trust_state": "verified",
        }

        response = await service.chat_with_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            message="hello",
        )

    assert response["reply"] == "proxied reply"
    assert proxy_calls[0]["gateway_id"] == "gateway-macbook"
    assert proxy_calls[0]["capability_id"] == "external_agent_proxy"
    assert proxy_calls[0]["arguments"]["url"] == "http://127.0.0.1:18789/chat"


@pytest.mark.anyio
async def test_external_agent_rejects_private_dns_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_resolve_host_is_private", lambda _hostname: True)
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        with pytest.raises(ValueError, match="private-network"):
            await service.create_connected_external_agent(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                name="Private DNS",
                provider_kind="custom",
                endpoints={"chat_url": "https://internal.example.com/chat"},
                manifest={"capabilities": ["chat"]},
            )


@pytest.mark.anyio
async def test_production_control_plane_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMPYRALIS_DEPLOY_ENV", "production")
    monkeypatch.delenv("EMPYRALIS_EXTERNAL_AGENT_LOCAL_STORE", raising=False)
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        with pytest.raises(RuntimeError, match="control-plane storage"):
            await service.list_connected_external_agents(tenant_id="tenant-1", workspace_id="ws-1")


@pytest.mark.anyio
async def test_disconnect_revokes_external_agent_and_blocks_chat() -> None:
    with patch("server_modules.control_plane_repository._scoped_connection", new=_fake_scoped_connection(None)):
        created = await service.create_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            name="Revocable agent",
            provider_kind="custom",
            endpoints={"chat_url": "https://example.com/chat"},
            manifest={"capabilities": ["chat"]},
        )
        updated = await service.update_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
            manifest={"capabilities": ["chat"], "endpoints": {"chat": "https://example.com/chat"}},
        )
        service.LOCAL_STORE[("tenant-1", "ws-1", created["id"])] = {
            **updated,
            "connection_state": "verified",
            "trust_state": "verified",
        }

        disconnected = await service.disconnect_connected_external_agent(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            external_agent_id=created["id"],
        )

        assert disconnected["connection_state"] == "revoked"
        assert disconnected["enabled"] is False
        with pytest.raises(ValueError, match="verified|disconnected"):
            await service.chat_with_connected_external_agent(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                external_agent_id=created["id"],
                message="hello",
                http_client=_FakeHttpClient(),
            )

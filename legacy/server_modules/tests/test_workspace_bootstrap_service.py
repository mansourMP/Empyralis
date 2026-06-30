from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi import HTTPException

from server_modules import workspace_bootstrap_service


def _workspace_access_entry(*, workspace_id: str, tenant_id: str, role: str) -> Dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "tenant_role": role,
        "role": role,
        "capabilities": {"allow": [], "deny": []},
        "tenant_capabilities": {"allow": [], "deny": []},
        "workspace_capabilities": {"allow": [], "deny": []},
        "dangerous_action_classes": {"allow": [], "deny": []},
        "tenant_dangerous_action_classes": {"allow": [], "deny": []},
        "workspace_dangerous_action_classes": {"allow": [], "deny": []},
        "connectors": {"allow": [], "deny": []},
        "tenant_connectors": {"allow": [], "deny": []},
        "workspace_connectors": {"allow": [], "deny": []},
        "machine_enrollment_scope": "workspace",
        "trusted_owner_machine_ids": [],
        "owner_user_id": "user-1",
        "owner_email": "user@example.com",
    }


def _current_user(*, workspace_id: str = "ws-1", tenant_id: str = "tenant-1", role: str = "member") -> Dict[str, Any]:
    return {
        "auth_type": "bearer",
        "user_id": "user-1",
        "email": "user@example.com",
        "role": role,
        "is_admin": role == "owner",
        "identity_versions": {"membership_version": 7},
        "workspace_access": {
            workspace_id: _workspace_access_entry(workspace_id=workspace_id, tenant_id=tenant_id, role=role),
        },
    }


def test_runtime_target_payload_exposes_cloud_computer_as_optional_metered_runtime() -> None:
    payload = workspace_bootstrap_service._runtime_target_payload(
        {
            "target_id": "sage_cloud_computer",
            "label": "Cloud Computer",
            "description": "Optional metered cloud computer.",
            "available": False,
            "online": False,
            "healthy": False,
            "status": "unavailable",
            "connection_mode": "platform_metered_cloud_computer",
            "execution_target": "cloud_computer",
            "default_for_workspace": False,
            "supports_runtime_modes": ["hosted_secure"],
            "supports_full_access": True,
            "execution_modes": [
                {
                    "id": "default",
                    "label": "Default",
                    "available": True,
                    "runtime_access_mode": "default_guarded",
                },
                {
                    "id": "full_access",
                    "label": "Full Access",
                    "available": True,
                    "runtime_access_mode": "full_access",
                    "setup_warning": "Full Access lets Sage operate this dedicated runtime.",
                },
            ],
        }
    )

    assert payload["kind"] == "cloud_computer"
    assert payload["canonicalId"] == "empyralis_cloud_computer"
    assert payload["label"] == "Cloud Computer"
    assert payload["publicLabel"] == "Cloud Computer"
    assert payload["statusLabel"] == "Not enabled"
    assert payload["trustTier"] == "hosted_cloud_computer"
    assert payload["supportsFullAccess"] is True
    assert payload["executionModes"][1]["label"] == "Full Access"
    assert payload["executionModes"][1]["runtimeAccessMode"] == "full_access"
    assert "dedicated runtime" in payload["executionModes"][1]["setupWarning"]
    assert "will not allocate a hosted computer automatically" in payload["statusReason"]


@pytest.mark.anyio
async def test_build_workspace_bootstrap_composes_canonical_payload(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_user_bundle_by_id(user_id: str):
        assert user_id == "user-1"
        return {
            "user": {
                "id": "user-1",
                "email": "user@example.com",
                "name": "Mansur",
            },
            "memberships": [
                {
                    "workspace_id": "ws-1",
                    "role": "member",
                    "updated_at": 1712000000,
                    "workspace_name": "Personal Workspace",
                }
            ],
        }

    async def fake_get_workspace_by_id(workspace_id: str):
        assert workspace_id == "ws-1"
        return {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "name": "Personal Workspace",
            "workspace_type": "personal",
            "metadata": {
                "billing": {
                    "plan_id": "personal",
                }
            },
        }

    async def fake_list_runtime_targets(*, tenant_id: str, workspace_id: str, include_snapshot_version: bool = False):
        assert tenant_id == "tenant-1"
        assert workspace_id == "ws-1"
        payload = {
            "deployment_mode": "hybrid",
            "targets": [
                {
                    "target_id": "cloud_default",
                    "label": "Cloud",
                    "description": "Cloud-hosted execution for the workspace.",
                    "available": True,
                    "online": True,
                    "healthy": True,
                    "status": "ready",
                    "connection_mode": "platform_cloud",
                    "execution_target": "cloud",
                    "default_for_workspace": True,
                    "supports_runtime_modes": ["hosted_secure"],
                },
                {
                    "target_id": "local_companion",
                    "label": "This Device",
                    "description": "Paired user device execution.",
                    "canonical_target_id": "user_device_gateway",
                    "public_label": "This Device",
                    "available": True,
                    "online": False,
                    "healthy": False,
                    "status": "offline",
                    "connection_mode": "platform_relay",
                    "execution_target": "local_companion",
                    "default_for_workspace": False,
                    "supports_runtime_modes": ["local_secure", "privileged_device"],
                    "supports_full_access": True,
                    "execution_modes": [
                        {
                            "id": "default",
                            "label": "Default",
                            "available": True,
                            "runtime_access_mode": "default_guarded",
                        },
                        {
                            "id": "full_access",
                            "label": "Full Access",
                            "available": True,
                            "runtime_access_mode": "full_access",
                            "requires_owner_approval": True,
                            "setup_warning": "Full Access lets Sage operate this dedicated runtime.",
                        },
                    ],
                    "sample_attachment_label": "Mansur Mac mini",
                },
            ],
        }
        if include_snapshot_version:
            payload["_snapshot_version"] = "runtime-targets:v1"
        return payload

    monkeypatch.setattr(
        workspace_bootstrap_service.control_plane_repository,
        "get_user_bundle_by_id",
        fake_get_user_bundle_by_id,
    )
    monkeypatch.setattr(
        workspace_bootstrap_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        workspace_bootstrap_service.runtime_attachment_service,
        "list_workspace_runtime_targets",
        fake_list_runtime_targets,
    )

    payload = await workspace_bootstrap_service.build_workspace_bootstrap(
        current_user=_current_user(),
        workspace_id="ws-1",
    )

    assert payload["account"]["id"] == "user-1"
    assert payload["workspace"] == {
        "id": "ws-1",
        "tenantId": "tenant-1",
        "label": "Personal Workspace",
        "kind": "personal",
    }
    assert payload["membership"]["role"] == "member"
    assert payload["membership"]["version"] == "7:1712000000"
    assert "chat.write" in payload["membership"]["permissions"]
    assert payload["entitlements"]["plan"] == "pro"
    assert payload["capabilities"]["mobile_app_enabled"] is True
    assert payload["capabilities"]["channel_pairing_enabled"] is True
    assert payload["runtime"]["deploymentMode"] == "hybrid"
    assert payload["runtime"]["runtimeTargets"][0]["id"] == "cloud_default"
    assert payload["runtime"]["runtimeTargets"][0]["label"] == "Cloud"
    assert payload["runtime"]["runtimeTargets"][0]["statusLabel"] == "Ready"
    assert payload["runtime"]["runtimeTargets"][0]["approvalMode"] == "policy_bound"
    assert payload["runtime"]["runtimeTargets"][1]["statusLabel"] == "Offline"
    assert payload["runtime"]["runtimeTargets"][1]["canonicalId"] == "user_device_gateway"
    assert payload["runtime"]["runtimeTargets"][1]["label"] == "This Device"
    assert "will not start device work" in payload["runtime"]["runtimeTargets"][1]["statusReason"]
    assert payload["runtime"]["runtimeTargets"][1]["approvalMode"] == "explicit_owner_approval"
    assert payload["runtime"]["runtimeTargets"][1]["supportsFullAccess"] is True
    assert payload["runtime"]["runtimeTargets"][1]["executionModes"][1]["label"] == "Full Access"
    assert payload["runtime"]["runtimeTargets"][1]["executionModes"][1]["requiresOwnerApproval"] is True
    assert payload["runtime"]["runtimeTargets"][1]["sampleAttachmentLabel"] == "Mansur Mac mini"
    assert payload["shellHints"]["defaultRoute"] == "/w/ws-1/chat"
    assert payload["shellHints"]["preferredProfile"] == "personal_shell"
    assert "channels.pair" in payload["membership"]["permissions"]


@pytest.mark.anyio
async def test_build_workspace_bootstrap_denies_unauthorized_workspace():
    with pytest.raises(HTTPException) as excinfo:
        await workspace_bootstrap_service.build_workspace_bootstrap(
            current_user=_current_user(workspace_id="ws-1"),
            workspace_id="ws-2",
        )

    assert excinfo.value.status_code == 403
    assert "Workspace is not accessible for this user." in str(excinfo.value.detail)


@pytest.mark.anyio
async def test_build_workspace_bootstrap_reuses_cached_payload_for_identical_versions(monkeypatch: pytest.MonkeyPatch):
    workspace_bootstrap_service._WORKSPACE_BOOTSTRAP_CACHE.clear()

    async def fake_get_user_bundle_by_id(user_id: str):
        return {
            "user": {
                "id": user_id,
                "email": "user@example.com",
                "name": "Mansur",
            },
            "memberships": [
                {
                    "workspace_id": "ws-1",
                    "role": "member",
                    "updated_at": 1712000000,
                    "workspace_name": "Personal Workspace",
                }
            ],
        }

    async def fake_get_workspace_by_id(workspace_id: str):
        return {
            "workspace_id": workspace_id,
            "tenant_id": "tenant-1",
            "name": "Personal Workspace",
            "workspace_type": "personal",
            "updated_at": "2026-04-12T00:00:00Z",
            "metadata": {"billing": {"plan_id": "personal"}},
        }

    assemble_calls = {"count": 0}

    async def fake_list_runtime_targets(*, tenant_id: str, workspace_id: str, include_snapshot_version: bool = False):
        payload = {
            "deployment_mode": "hybrid",
            "targets": [
                {
                    "target_id": "cloud_default",
                    "label": "Cloud",
                    "online": True,
                    "default_for_workspace": True,
                }
            ],
        }
        if include_snapshot_version:
            payload["_snapshot_version"] = "runtime-targets:v1"
        return payload

    original_builder = workspace_bootstrap_service._build_workspace_bootstrap_payload

    def counting_builder(**kwargs):
        assemble_calls["count"] += 1
        return original_builder(**kwargs)

    monkeypatch.setattr(
        workspace_bootstrap_service.control_plane_repository,
        "get_user_bundle_by_id",
        fake_get_user_bundle_by_id,
    )
    monkeypatch.setattr(
        workspace_bootstrap_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        workspace_bootstrap_service.runtime_attachment_service,
        "list_workspace_runtime_targets",
        fake_list_runtime_targets,
    )
    monkeypatch.setattr(
        workspace_bootstrap_service,
        "_build_workspace_bootstrap_payload",
        counting_builder,
    )

    first = await workspace_bootstrap_service.build_workspace_bootstrap(
        current_user=_current_user(),
        workspace_id="ws-1",
    )
    second = await workspace_bootstrap_service.build_workspace_bootstrap(
        current_user=_current_user(),
        workspace_id="ws-1",
    )

    assert first == second
    assert assemble_calls["count"] == 1


@pytest.mark.anyio
async def test_build_workspace_bootstrap_prefers_membership_tenant_over_stale_auth_workspace_access(
    monkeypatch: pytest.MonkeyPatch,
):
    current_user = _current_user(tenant_id="tenant-stale")

    async def fake_get_user_bundle_by_id(user_id: str):
        assert user_id == "user-1"
        return {
            "user": {
                "id": "user-1",
                "email": "user@example.com",
                "name": "Mansur",
            },
            "memberships": [
                {
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "role": "member",
                    "updated_at": 1712000000,
                    "workspace_name": "Personal Workspace",
                }
            ],
        }

    async def fake_get_workspace_by_id(workspace_id: str):
        assert workspace_id == "ws-1"
        return {
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "name": "Personal Workspace",
            "workspace_type": "personal",
            "metadata": {
                "billing": {
                    "plan_id": "personal",
                }
            },
        }

    async def fake_list_runtime_targets(*, tenant_id: str, workspace_id: str, include_snapshot_version: bool = False):
        assert tenant_id == "tenant-1"
        assert workspace_id == "ws-1"
        payload = {
            "deployment_mode": "cloud_default",
            "targets": [
                {
                    "target_id": "cloud_default",
                    "label": "Cloud",
                    "available": True,
                    "online": True,
                    "healthy": True,
                    "status": "ready",
                    "default_for_workspace": True,
                }
            ],
        }
        if include_snapshot_version:
            payload["_snapshot_version"] = "runtime-targets:v1"
        return payload

    monkeypatch.setattr(
        workspace_bootstrap_service.control_plane_repository,
        "get_user_bundle_by_id",
        fake_get_user_bundle_by_id,
    )
    monkeypatch.setattr(
        workspace_bootstrap_service.control_plane_repository,
        "get_workspace_by_id",
        fake_get_workspace_by_id,
    )
    monkeypatch.setattr(
        workspace_bootstrap_service.runtime_attachment_service,
        "list_workspace_runtime_targets",
        fake_list_runtime_targets,
    )

    payload = await workspace_bootstrap_service.build_workspace_bootstrap(
        current_user=current_user,
        workspace_id="ws-1",
    )

    assert payload["workspace"]["tenantId"] == "tenant-1"

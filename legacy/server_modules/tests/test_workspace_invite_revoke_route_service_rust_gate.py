from fastapi import HTTPException

from server_modules import routes_workspaces, workspace_admin_service


def _allow_invite_revoke_existing_record() -> dict:
    return {
        "ok": True,
        "decision": "allow",
        "reason": "control_plane_service_operation_allowed",
        "operation": "invite_revoke",
        "next_action": "return_existing_control_plane_record",
        "mutation_plan": {
            "apply": False,
            "next_action": "return_existing_control_plane_record",
        },
    }


def test_workspace_admin_invite_revoke_accepts_existing_record_next_action(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_admin_service.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        lambda command, payload: _allow_invite_revoke_existing_record(),
    )

    decision = workspace_admin_service._enforce_control_plane_service_decision(
        operation="invite_revoke",
        tenant_id="tenant-1",
        workspace_id="ws-1",
        actor_id="owner-1",
        actor_role="owner",
        target_status="revoked",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )

    assert decision["mutation_plan"]["next_action"] == "return_existing_control_plane_record"


def test_workspace_route_invite_revoke_accepts_existing_record_next_action(monkeypatch) -> None:
    monkeypatch.setattr(
        routes_workspaces.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        lambda command, payload: _allow_invite_revoke_existing_record(),
    )

    decision = routes_workspaces._enforce_control_plane_route_decision(
        operation="invite_revoke",
        record_type="workspace_invite",
        tenant_id="tenant-1",
        workspace_id="ws-1",
        actor_id="owner-1",
        actor_role="owner",
        target_status="revoked",
        idempotency_key="invite_revoke:ws-1:invite-1",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )

    assert decision["mutation_plan"]["next_action"] == "return_existing_control_plane_record"


def test_workspace_route_invite_revoke_wrong_next_action_blocks(monkeypatch) -> None:
    monkeypatch.setattr(
        routes_workspaces.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        lambda command, payload: {
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "invite_revoke",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    )

    try:
        routes_workspaces._enforce_control_plane_route_decision(
            operation="invite_revoke",
            record_type="workspace_invite",
            tenant_id="tenant-1",
            workspace_id="ws-1",
            actor_id="owner-1",
            actor_role="owner",
            target_status="revoked",
            idempotency_key="invite_revoke:ws-1:invite-1",
            owner_access=True,
            admin_access=True,
            workspace_access=True,
            billing_entitled=True,
            quota_ok=True,
            approval_provided=True,
            owner_approval_provided=True,
        )
    except HTTPException as exc:
        assert exc.status_code == 423
        assert "unexpected next_action" in str(exc.detail["reason"])
    else:
        raise AssertionError("expected HTTPException")


def test_workspace_route_invite_revoke_missing_apply_without_idempotent_return_blocks(monkeypatch) -> None:
    monkeypatch.setattr(
        routes_workspaces.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        lambda command, payload: {
            "ok": True,
            "decision": "allow",
            "reason": "control_plane_service_operation_allowed",
            "operation": "invite_revoke",
            "next_action": "apply_control_plane_write",
            "mutation_plan": {
                "apply": False,
                "next_action": "apply_control_plane_write",
            },
        },
    )

    try:
        routes_workspaces._enforce_control_plane_route_decision(
            operation="invite_revoke",
            record_type="workspace_invite",
            tenant_id="tenant-1",
            workspace_id="ws-1",
            actor_id="owner-1",
            actor_role="owner",
            target_status="revoked",
            idempotency_key="invite_revoke:ws-1:invite-1",
            owner_access=True,
            admin_access=True,
            workspace_access=True,
            billing_entitled=True,
            quota_ok=True,
            approval_provided=True,
            owner_approval_provided=True,
        )
    except HTTPException as exc:
        assert exc.status_code == 423
        assert exc.detail["reason"] == "control_plane_service_operation_allowed"
    else:
        raise AssertionError("expected HTTPException")

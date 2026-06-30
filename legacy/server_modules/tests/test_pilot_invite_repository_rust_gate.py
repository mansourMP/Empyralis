from unittest.mock import patch

import pytest

from server_modules import control_plane_repository


@pytest.mark.asyncio
async def test_pilot_invite_create_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.create_pilot_invite(
                code="ALPHA",
                email="user@example.com",
                created_by_user_id="admin-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "pilot_invite_create"
    assert payload["record_type"] == "pilot_invite"
    assert payload["actor_id"] == "admin-1"
    assert payload["idempotency_key"] == "ALPHA"
    assert payload["target_status"] == "active"


@pytest.mark.asyncio
async def test_pilot_invite_claim_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.claim_pilot_invite("ALPHA")

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "pilot_invite_claim"
    assert payload["record_type"] == "pilot_invite"
    assert payload["actor_id"] == "pilot-invite-claimer"
    assert payload["idempotency_key"] == "ALPHA"


@pytest.mark.asyncio
async def test_pilot_invite_revoke_blocks_before_repository_write():
    with patch.object(
        control_plane_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            await control_plane_repository.revoke_pilot_invite("pilot-invite-1")

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "pilot_invite_revoke"
    assert payload["record_type"] == "pilot_invite"
    assert payload["actor_id"] == "pilot-invite-admin"
    assert payload["idempotency_key"] == "pilot-invite-1"
    assert payload["target_status"] == "revoked"

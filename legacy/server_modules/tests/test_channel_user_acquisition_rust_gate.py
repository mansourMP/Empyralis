from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from unittest import mock

import pytest

from server_modules.channel_user_acquisition_service import ChannelUserAcquisitionService


@contextmanager
def _blocked_service():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row

    def _control_plane_runner(coro):
        try:
            coro.close()
        except Exception:
            pass
        return None

    try:
        yield ChannelUserAcquisitionService(
            now_epoch=lambda: 1_710_000_000,
            jwt_secret_resolver=lambda: "test-secret",
            control_plane_runner=_control_plane_runner,
            frontend_signup_url_resolver=lambda: "https://app.empyralist.com/signup",
            fallback_connection_factory=lambda: connection,
        )
    finally:
        connection.close()


def test_public_start_touch_write_blocks_before_fallback_mutation():
    with _blocked_service() as service, mock.patch(
        "server_modules.channel_user_acquisition_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            service.prepare_public_start_response(
                profile={
                    "public_start": {
                        "enabled": True,
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "deployed_agent_id": "dagent-1",
                    }
                },
                workspace_id="workspace-1",
                external_user_id="external-user-1",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "channel_user_acquisition_touch_write"
    assert payload["record_type"] == "channel_user_acquisition_touch"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["agent_id"] == "dagent-1"
    assert payload["idempotency_key"] == "dagent-1:telegram:external-user-1:touch"


def test_attribution_conversion_blocks_before_fallback_mutation():
    with _blocked_service() as service, mock.patch(
        "server_modules.channel_user_acquisition_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
        return_value={"ok": True, "decision": "allow"},
    ):
        response = service.prepare_public_start_response(
            profile={
                "public_start": {
                    "enabled": True,
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "deployed_agent_id": "dagent-1",
                }
            },
            workspace_id="workspace-1",
            external_user_id="external-user-1",
        )
        token = response["attribution_token"]

    with _blocked_service() as service, mock.patch(
        "server_modules.channel_user_acquisition_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
        side_effect=RuntimeError("rust denied"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="rust denied"):
            service.bind_attribution_token_to_user(
                attribution_token=token,
                user_id="user-1",
                email="user@example.com",
                auth_flow="signup",
            )

    command, payload = kernel.call_args.args
    assert command == "control-plane-service-decision"
    assert payload["operation"] == "channel_user_acquisition_conversion_write"
    assert payload["record_type"] == "channel_user_acquisition_touch"
    assert payload["actor_id"] == "user-1"
    assert payload["idempotency_key"].endswith(":conversion:user-1")


def test_public_start_touch_unexpected_next_action_blocks_before_fallback_mutation():
    with _blocked_service() as service, mock.patch(
        "server_modules.channel_user_acquisition_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
        return_value={
            "ok": True,
            "decision": "allow",
            "operation": "channel_user_acquisition_touch_write",
            "next_action": "apply_control_plane_destructive_write",
            "mutation_plan": {
                "apply": True,
                "next_action": "apply_control_plane_destructive_write",
            },
        },
    ):
        with pytest.raises(RuntimeError, match="unexpected next_action"):
            service.prepare_public_start_response(
                profile={
                    "public_start": {
                        "enabled": True,
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "deployed_agent_id": "dagent-1",
                    }
                },
                workspace_id="workspace-1",
                external_user_id="external-user-1",
            )

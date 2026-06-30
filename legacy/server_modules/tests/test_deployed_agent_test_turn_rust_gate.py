from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server_modules.deployed_agent_test_turn_service import execute_test_turn
from server_modules.schemas import DeployedAgentTestTurnRequest


def _request() -> DeployedAgentTestTurnRequest:
    return DeployedAgentTestTurnRequest(
        workspace_id="ws-1",
        message="hello",
        channel="test",
        runtime_mode="text_agent",
        customer_profile=None,
        recent_messages=[],
    )


def _config() -> MagicMock:
    config = MagicMock()
    config.deployment_state = "draft"
    config.runtime_placement = "managed_cloud"
    config.studio_agent_mode = "text_agent"
    config.runtime_target = "cloud_default"
    config.runtime_supply = {}
    config.provider = None
    config.model = None
    config.channels = {}
    config.tool_policy = None
    config.safety_policy = None
    config.memory_policy = None
    config.persona = ""
    config.system_prompt = ""
    config.instructions = ""
    return config


def test_deployed_test_turn_accepts_canonical_rust_action() -> None:
    async def run() -> None:
        def _fake_kernel(command: str, payload: dict[str, object]):
            if command == "deployed-agent-service-decision":
                return {"ok": True, "decision": "allow", "next_action": "execute_deployed_agent_test_turn"}
            return {"ok": True, "decision": "allow", "next_action": "execute_studio_test_turn"}

        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "draft", "id": "a1"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=_config(),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            side_effect=_fake_kernel,
        ) as kernel, patch(
            "server_modules.deployed_agent_test_turn_service._generate_live_test_reply",
            new=AsyncMock(
                return_value=(
                    "Live Studio reply.",
                    {
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "usage": {"total_tokens": 12},
                        "ai_source": {"kind": "empyralis_credits"},
                        "prompt_context": {},
                    },
                )
            ),
        ) as generate, patch(
            "server_modules.deployed_agent_test_turn_service.security_audit_service.emit_security_audit_event"
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_transparency_service.emit_deployed_agent_test_turn_events",
            return_value=[],
        ):
            result = await execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=_request(),
                current_user={"user_id": "u-1"},
            )

        first_command, first_payload = kernel.call_args_list[0].args
        second_command, second_payload = kernel.call_args_list[1].args
        assert first_command == "deployed-agent-service-decision"
        assert first_payload["operation"] == "test_turn"
        assert second_command == "deployed-readiness-decision"
        assert second_payload["stage"] == "test_turn"
        assert result.reply == "Live Studio reply."
        generate.assert_awaited_once()

    asyncio.run(run())


def test_deployed_test_turn_wrong_service_rust_action_blocks_before_readiness_and_reply_generation() -> None:
    async def run() -> None:
        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "draft", "id": "a1"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=_config(),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={"ok": True, "decision": "allow", "next_action": "read_telegram_readiness"},
        ), patch(
            "server_modules.deployed_agent_test_turn_service._generate_live_test_reply",
            new=AsyncMock(),
        ) as generate:
            with pytest.raises(Exception) as raised:
                await execute_test_turn(
                    deployed_agent_id="a1",
                    workspace_id="ws-1",
                    tenant_id="t-1",
                    request=_request(),
                    current_user={"user_id": "u-1"},
                )

        assert "unexpected next_action" in str(raised.value.detail if hasattr(raised.value, "detail") else raised.value)
        generate.assert_not_awaited()

    asyncio.run(run())


def test_deployed_test_turn_wrong_readiness_rust_action_blocks_before_reply_generation() -> None:
    async def run() -> None:
        def _fake_kernel(command: str, payload: dict[str, object]):
            if command == "deployed-agent-service-decision":
                return {"ok": True, "decision": "allow", "next_action": "execute_deployed_agent_test_turn"}
            return {"ok": True, "decision": "allow", "next_action": "read_telegram_readiness"}

        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "draft", "id": "a1"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=_config(),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            side_effect=_fake_kernel,
        ), patch(
            "server_modules.deployed_agent_test_turn_service._generate_live_test_reply",
            new=AsyncMock(),
        ) as generate:
            with pytest.raises(ValueError, match="unexpected_next_action:read_telegram_readiness"):
                await execute_test_turn(
                    deployed_agent_id="a1",
                    workspace_id="ws-1",
                    tenant_id="t-1",
                    request=_request(),
                    current_user={"user_id": "u-1"},
                )

        generate.assert_not_awaited()

    asyncio.run(run())

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules.deployed_agent_test_turn_service import (
    ALLOWED_TEST_CHANNELS,
    ALLOWED_RUNTIME_MODES,
    TESTABLE_STATES,
    _build_live_test_messages,
    _build_policy_decisions,
    _evaluate_tool_policy,
    _load_memory_context_for_test,
    execute_test_turn,
    rust_runtime_kernel_client,
)
from server_modules.schemas import DeployedAgentTestTurnRequest


class DummyConfig:
    def __init__(self, **kwargs):
        self.deployment_state = kwargs.get("deployment_state", "draft")
        self.runtime_placement = kwargs.get("runtime_placement", "managed_cloud")
        self.studio_agent_mode = kwargs.get("studio_agent_mode", "text_agent")
        self.runtime_target = kwargs.get("runtime_target", "cloud_default")
        self.runtime_supply = kwargs.get("runtime_supply", {})
        self.provider = kwargs.get("provider")
        self.model = kwargs.get("model")
        self.name = kwargs.get("name", "Test Agent")
        self.persona = kwargs.get("persona", "")
        self.system_prompt = kwargs.get("system_prompt", "")
        self.channels = kwargs.get("channels", {})
        self.tool_policy = kwargs.get("tool_policy")
        self.safety_policy = kwargs.get("safety_policy")
        self.memory_policy = kwargs.get("memory_policy")


class DummyToolPolicy:
    def __init__(self, **kwargs):
        self.allowed_tool_ids = kwargs.get("allowed_tool_ids", [])
        self.blocked_tool_ids = kwargs.get("blocked_tool_ids", [])


class DummySafetyPolicy:
    def __init__(self, **kwargs):
        self.approval_mode = kwargs.get("approval_mode", "balanced")
        self.handoff_mode = kwargs.get("handoff_mode", "always")


class DummyMemoryPolicy:
    def __init__(self, **kwargs):
        self.memory_enabled = kwargs.get("memory_enabled", False)
        self.context_budget_preset = kwargs.get("context_budget_preset", "compact")
        self.retention_preset = kwargs.get("retention_preset", "standard")


def _make_request(**overrides) -> DeployedAgentTestTurnRequest:
    return DeployedAgentTestTurnRequest(
        workspace_id=overrides.get("workspace_id", "ws-1"),
        message=overrides.get("message", "hello"),
        channel=overrides.get("channel", "test"),
        runtime_mode=overrides.get("runtime_mode", "text_agent"),
        customer_profile=overrides.get("customer_profile"),
        recent_messages=overrides.get("recent_messages", []),
    )


def _live_reply_patch(reply: str = "Live Studio reply."):
    return patch(
        "server_modules.deployed_agent_test_turn_service._generate_live_test_reply",
        new=AsyncMock(return_value=(
            reply,
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "usage": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "total_tokens": 12,
                    "retail_credits_charged": 0.01,
                },
                "ai_source": {"kind": "empyralis_credits"},
                "prompt_context": {
                    "prompt_source": "test_guardrails_only",
                    "recent_messages_included": 0,
                    "context_truncated": False,
                },
            },
        )),
    )


class TestTurnValidationTests(unittest.TestCase):
    def test_allowed_channels(self):
        self.assertIn("test", ALLOWED_TEST_CHANNELS)
        self.assertIn("telegram", ALLOWED_TEST_CHANNELS)
        self.assertIn("whatsapp", ALLOWED_TEST_CHANNELS)
        self.assertIn("web_widget", ALLOWED_TEST_CHANNELS)

    def test_allowed_runtime_modes(self):
        for mode in ("text_agent", "cloud_computer_agent", "my_computer_agent", "self_hosted_agent"):
            self.assertIn(mode, ALLOWED_RUNTIME_MODES)

    def test_testable_states(self):
        self.assertIn("draft", TESTABLE_STATES)
        self.assertIn("private_test", TESTABLE_STATES)
        self.assertIn("ready_for_review", TESTABLE_STATES)
        self.assertNotIn("live", TESTABLE_STATES)
        self.assertNotIn("archived", TESTABLE_STATES)

    def test_rejects_live_state(self):
        req = _make_request()
        denied = rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "deployed_agent_test_turn_state_invalid",
            },
            command="deployed-readiness-decision",
        )

        def _fake_kernel(command, payload):
            if command == "deployed-agent-service-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "next_action": "execute_deployed_agent_test_turn",
                }
            raise denied

        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "live", "id": "a1"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=DummyConfig(deployment_state="live"),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            side_effect=_fake_kernel,
        ):
            with self.assertRaises(ValueError) as ctx:
                import asyncio
                asyncio.run(execute_test_turn(
                    deployed_agent_id="a1",
                    workspace_id="ws-1",
                    tenant_id="t-1",
                    request=req,
                    current_user={"user_id": "u-1"},
                ))
            self.assertIn("state", str(ctx.exception).lower())

    def test_rejects_empty_message(self):
        req = _make_request(message="")
        with self.assertRaises(ValueError) as ctx:
            import asyncio
            asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={},
            ))
        self.assertIn("message", str(ctx.exception).lower())

    def test_rejects_invalid_channel(self):
        req = _make_request(channel="telegram_personal")
        with self.assertRaises(ValueError) as ctx:
            import asyncio
            asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={},
            ))
        self.assertIn("channel", str(ctx.exception).lower())

    def test_rejects_invalid_runtime_mode(self):
        req = _make_request(runtime_mode="unknown_mode")
        with self.assertRaises(ValueError) as ctx:
            import asyncio
            asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={},
            ))
        self.assertIn("runtime_mode", str(ctx.exception).lower())

    def test_rejects_customer_profile_owner_resource_bypass(self):
        req = _make_request(
            customer_profile={
                "name": "customer",
                "preferences": {
                    "sage_memory": "SAGE_PRIVATE_MEMORY_DO_NOT_LEAK",
                    "connectedComputer": {"gateway_id": "gateway-local-1"},
                },
            }
        )
        with self.assertRaises(ValueError) as ctx:
            import asyncio
            asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={},
            ))

        self.assertIn("customer_profile", str(ctx.exception))
        self.assertIn("sage_memory", str(ctx.exception))


class TestTurnPolicyTests(unittest.TestCase):
    def test_build_policy_decisions_includes_runtime_mode(self):
        config = DummyConfig(
            channels={"test": MagicMock(enabled=True)},
        )
        decisions = _build_policy_decisions(
            config=config,
            requested_mode="text_agent",
            requested_channel="test",
        )
        runtime_decisions = [d for d in decisions if d["policy"] == "runtime_mode"]
        self.assertEqual(len(runtime_decisions), 1)
        self.assertEqual(runtime_decisions[0]["requested"], "text_agent")

    def test_build_policy_decisions_uses_configured_runtime_not_raw_payload(self):
        config = DummyConfig(
            studio_agent_mode="text_agent",
            runtime_placement="managed_cloud",
            runtime_target="cloud_default",
            channels={"test": MagicMock(enabled=True)},
        )
        decisions = _build_policy_decisions(
            config=config,
            requested_mode="cloud_computer_agent",
            requested_channel="test",
        )
        runtime_decision = next(item for item in decisions if item["policy"] == "runtime_mode")
        self.assertEqual(runtime_decision["requested"], "cloud_computer_agent")
        self.assertEqual(runtime_decision["configured"], "text_agent")
        self.assertEqual(runtime_decision["resolved"], "text_agent")
        self.assertFalse(runtime_decision["allowed"])
        self.assertEqual(runtime_decision["reason"], "runtime_mode_mismatch")

    def test_build_policy_decisions_includes_channel(self):
        config = DummyConfig(
            channels={"test": MagicMock(enabled=True)},
        )
        decisions = _build_policy_decisions(
            config=config,
            requested_mode="text_agent",
            requested_channel="test",
        )
        channel_decisions = [d for d in decisions if d["policy"] == "channel"]
        self.assertEqual(len(channel_decisions), 1)
        self.assertTrue(channel_decisions[0]["simulated"])

    def test_evaluate_tool_policy_blocks_write_skills(self):
        from server_modules.skill_registry import SkillDefinition

        config = DummyConfig(
            tool_policy=DummyToolPolicy(allowed_tool_ids=["email-access"]),
        )
        with patch(
            "server_modules.skill_registry.list_skill_definitions",
            return_value=[
                SkillDefinition(
                    id="email-access", label="Email", description="Send",
                    permission_label="email", execution_mode="manual",
                    action_class="write", connector_scopes=("email",),
                    trigger_terms=("send email",), requires_approval=True,
                ),
            ],
        ):
            considered, used, approval = _evaluate_tool_policy(
                config=config,
                message="send email to boss",
            )

        self.assertTrue(approval)
        self.assertEqual(len(used), 0)
        self.assertTrue(any(c["reason"] == "requires_approval" for c in considered))

    def test_evaluate_tool_policy_allows_read_skills(self):
        from server_modules.skill_registry import SkillDefinition

        config = DummyConfig(
            tool_policy=DummyToolPolicy(allowed_tool_ids=["web-search"]),
        )
        with patch(
            "server_modules.skill_registry.list_skill_definitions",
            return_value=[
                SkillDefinition(
                    id="web-search", label="Web Search", description="Search",
                    permission_label="web", execution_mode="live",
                    action_class="read", connector_scopes=(),
                    trigger_terms=("search",), requires_approval=False,
                ),
            ],
        ):
            considered, used, approval = _evaluate_tool_policy(
                config=config,
                message="search for latest news",
            )

        self.assertFalse(approval)
        self.assertIn("web-search", used)

    def test_evaluate_tool_policy_blocks_by_policy(self):
        from server_modules.skill_registry import SkillDefinition

        config = DummyConfig(
            tool_policy=DummyToolPolicy(blocked_tool_ids=["browser"]),
        )
        with patch(
            "server_modules.skill_registry.list_skill_definitions",
            return_value=[
                SkillDefinition(
                    id="browser", label="Browser", description="Browse",
                    permission_label="browser", execution_mode="live",
                    action_class="read", connector_scopes=(),
                    trigger_terms=("browse",), requires_approval=False,
                ),
            ],
        ):
            considered, used, approval = _evaluate_tool_policy(
                config=config,
                message="browse the web",
            )

        self.assertFalse(approval)
        self.assertTrue(any(c["reason"] == "blocked_by_policy" for c in considered))

    def test_memory_context_disabled_when_policy_off(self):
        config = DummyConfig(memory_policy=DummyMemoryPolicy(memory_enabled=False))
        ctx = _load_memory_context_for_test(
            workspace_id="ws-1", tenant_id="t-1", config=config,
        )
        self.assertFalse(ctx["enabled"])
        self.assertFalse(ctx["applied"])

    def test_memory_context_enabled_when_policy_on(self):
        config = DummyConfig(memory_policy=DummyMemoryPolicy(memory_enabled=True))
        ctx = _load_memory_context_for_test(
            workspace_id="ws-1", tenant_id="t-1", config=config,
        )
        self.assertTrue(ctx["enabled"])
        self.assertTrue(ctx["applied"])

    def test_test_turn_returns_full_contract(self):
        req = _make_request(message="hello test")
        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "draft", "id": "a1"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=DummyConfig(
                deployment_state="draft",
                channels={"test": MagicMock(enabled=True)},
            ),
        ), patch(
            "server_modules.skill_registry.list_skill_definitions",
            return_value=[],
        ), patch(
            "server_modules.deployed_agent_test_turn_service.security_audit_service.emit_security_audit_event",
        ), _live_reply_patch("Live reply from DeepSeek."):
            import asyncio
            result = asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={"user_id": "u-1", "email": "test@test.com"},
            ))

            self.assertTrue(len(result.reply) > 0)
            self.assertEqual(result.reply, "Live reply from DeepSeek.")
            self.assertEqual(result.model_provider, "deepseek")
            self.assertEqual(result.model, "deepseek-chat")
            self.assertIsInstance(result.policy_decisions, list)
            self.assertIsInstance(result.tools_considered, list)
            self.assertIsInstance(result.tools_used, list)
            self.assertIsInstance(result.memory_context, dict)
            self.assertIsInstance(result.prompt_context, dict)
            self.assertFalse(result.approval_required)
            self.assertIsInstance(result.audit_events, list)
            self.assertTrue(len(result.trace_id) > 0)

    def test_test_turn_raw_runtime_payload_cannot_override_agent_config(self):
        req = _make_request(message="open a browser", runtime_mode="cloud_computer_agent")
        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "draft", "id": "a1"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=DummyConfig(
                deployment_state="draft",
                studio_agent_mode="text_agent",
                runtime_placement="managed_cloud",
                runtime_target="cloud_default",
                channels={"test": MagicMock(enabled=True)},
            ),
        ), patch(
            "server_modules.skill_registry.list_skill_definitions",
            return_value=[],
        ), patch(
            "server_modules.deployed_agent_test_turn_service.security_audit_service.emit_security_audit_event",
        ) as mock_audit:
            import asyncio
            result = asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={"user_id": "u-1", "email": "test@test.com"},
            ))

            runtime_decision = next(item for item in result.policy_decisions if item["policy"] == "runtime_mode")
            self.assertFalse(runtime_decision["allowed"])
            self.assertEqual(runtime_decision["resolved"], "text_agent")
            self.assertIn("runtime_mode_mismatch", result.reply)
            audit_metadata = mock_audit.call_args.kwargs["metadata"]
            self.assertEqual(audit_metadata["runtime_mode"], "text_agent")

    def test_test_turn_approval_required_for_risky_tool(self):
        from server_modules.skill_registry import SkillDefinition

        req = _make_request(message="send email now")
        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "draft", "id": "a1"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=DummyConfig(
                deployment_state="draft",
                channels={"test": MagicMock(enabled=True)},
                tool_policy=DummyToolPolicy(),
            ),
        ), patch(
            "server_modules.skill_registry.list_skill_definitions",
            return_value=[
                SkillDefinition(
                    id="email-access", label="Email", description="Send",
                    permission_label="email", execution_mode="manual",
                    action_class="write", connector_scopes=("email",),
                    trigger_terms=("send email",), requires_approval=True,
                ),
            ],
        ), patch(
            "server_modules.deployed_agent_test_turn_service.security_audit_service.emit_security_audit_event",
        ):
            import asyncio
            result = asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={"user_id": "u-1"},
            ))

            self.assertTrue(result.approval_required)
            self.assertIn("TEST MODE", result.reply)

    def test_audit_event_emitted(self):
        req = _make_request()
        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "draft", "id": "a1"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=DummyConfig(
                deployment_state="draft",
                channels={"test": MagicMock(enabled=True)},
            ),
        ), patch(
            "server_modules.skill_registry.list_skill_definitions",
            return_value=[],
        ), patch(
            "server_modules.deployed_agent_test_turn_service.security_audit_service.emit_security_audit_event",
        ) as mock_audit, _live_reply_patch():
            import asyncio
            result = asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={"user_id": "u-1"},
            ))

            mock_audit.assert_called()
            self.assertEqual(result.audit_events[0]["action"], "deployed_agent.test_turn")

    def test_test_turn_uses_live_deepseek_when_safe(self):
        req = _make_request(
            message="what were we talking about?",
            recent_messages=[
                {"role": "user", "content": "what model are you?"},
                {"role": "assistant", "content": "I am using DeepSeek for this private test turn."},
            ],
        )
        with patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_service.get_deployed_agent_detail",
            new=AsyncMock(return_value={"deployed_agent": {"deployment_state": "draft", "id": "a1", "name": "Sales Agent"}}),
        ), patch(
            "server_modules.deployed_agent_test_turn_service.deployed_agent_config_schema.deployed_agent_config_from_record",
            return_value=DummyConfig(
                deployment_state="draft",
                channels={"test": MagicMock(enabled=True)},
                runtime_supply={"ai_source": {"kind": "empyralis_credits"}},
            ),
        ), patch(
            "server_modules.skill_registry.list_skill_definitions",
            return_value=[],
        ), patch(
            "server_modules.deployed_agent_test_turn_service.security_audit_service.emit_security_audit_event",
        ), patch(
            "server_modules.deployed_agent_test_turn_service.model_router.resolve_call_credentials",
            return_value={
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "credentials": {"api_key": "secret", "base_url": "https://api.deepseek.com/v1"},
            },
        ) as mock_resolve, patch(
            "server_modules.deployed_agent_test_turn_service.model_router.call_model",
            new=AsyncMock(return_value={
                "content": "Hello there.",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "usage": {
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "total_tokens": 12,
                    "usage_accounting": {
                        "effective_provider": "deepseek",
                        "effective_model": "deepseek-v4-pro",
                        "input_tokens": 8,
                        "output_tokens": 4,
                        "total_tokens": 12,
                        "provider_cost_usd": 0.00001,
                        "retail_credits_charged": 0.2,
                        "pricing_known": True,
                        "estimation_mode": "provider_reported",
                    },
                },
            }),
        ) as mock_call, patch(
            "server_modules.deployed_agent_test_turn_service._persist_studio_test_turn_usage",
            new=AsyncMock(return_value={"total_tokens": 12, "retail_credits_charged": 0.2}),
        ) as mock_persist:
            import asyncio
            result = asyncio.run(execute_test_turn(
                deployed_agent_id="a1",
                workspace_id="ws-1",
                tenant_id="t-1",
                request=req,
                current_user={"user_id": "u-1"},
            ))

            self.assertEqual(result.reply, "Hello there.")
            self.assertEqual(result.model_provider, "deepseek")
            self.assertEqual(result.model, "deepseek-v4-pro")
            mock_resolve.assert_called_once_with(
                provider="deepseek",
                model="deepseek-v4-pro",
                workspace_id="ws-1",
            )
            self.assertEqual(mock_call.await_args.kwargs["source_surface"], "studio")
            model_messages = mock_call.await_args.args[0]
            self.assertEqual([item["role"] for item in model_messages[-3:]], ["user", "assistant", "user"])
            self.assertIn("what model are you", model_messages[-3]["content"])
            self.assertIn("DeepSeek", model_messages[-2]["content"])
            self.assertIn("what were we talking about", model_messages[-1]["content"])
            mock_persist.assert_awaited_once()
            persist_kwargs = mock_persist.await_args.kwargs
            self.assertEqual(persist_kwargs["prompt_context"]["recent_messages_included"], 2)
            self.assertFalse(persist_kwargs["prompt_context"]["internal_record_name_applied"])

    def test_live_test_prompt_does_not_inject_internal_agent_name(self):
        messages, prompt_context = _build_live_test_messages(
            config=DummyConfig(
                name="Codex Studio Smoke Agent",
                persona="",
                system_prompt="",
            ),
            agent_record={"name": "Codex Studio Smoke Agent"},
            message="what model are you?",
            recent_messages=[],
        )

        system_message = messages[0]["content"]
        self.assertIn("configured Studio assistant", system_message)
        self.assertNotIn("Codex Studio Smoke Agent", system_message)
        self.assertNotIn("Sage boundary", system_message)
        self.assertNotIn("serve the signed-in user", system_message)
        self.assertEqual(prompt_context["prompt_source"], "test_guardrails_only")
        self.assertFalse(prompt_context["internal_record_name_applied"])
        self.assertFalse(prompt_context["persona_applied"])
        self.assertFalse(prompt_context["stored_instructions_applied"])

    def test_live_test_prompt_applies_stored_instructions_and_trims_history(self):
        recent_messages = [
            {"role": "user", "content": f"old-{index} " + ("x" * 3000)}
            for index in range(24)
        ]
        messages, prompt_context = _build_live_test_messages(
            config=DummyConfig(
                persona="Friendly but concise.",
                system_prompt="Only answer from approved business information.",
            ),
            agent_record={"name": "Internal Record Name"},
            message="summarize the current test",
            recent_messages=recent_messages,
        )

        system_message = messages[0]["content"]
        self.assertIn("Friendly but concise.", system_message)
        self.assertIn("Only answer from approved business information.", system_message)
        self.assertNotIn("Internal Record Name", system_message)
        self.assertEqual(prompt_context["prompt_source"], "stored_instructions")
        self.assertTrue(prompt_context["persona_applied"])
        self.assertTrue(prompt_context["stored_instructions_applied"])
        self.assertLessEqual(prompt_context["recent_messages_included"], 16)
        self.assertTrue(prompt_context["context_truncated"])


if __name__ == "__main__":
    unittest.main()

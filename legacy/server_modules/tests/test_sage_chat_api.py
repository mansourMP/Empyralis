from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from server_modules.schemas import SageChatRequest, SageVoiceTaskRequest


class SageChatApiContractTests(unittest.TestCase):
    def test_request_model_defaults(self):
        req = SageChatRequest(workspace_id="ws-1", message="hello")
        self.assertEqual(req.workspace_id, "ws-1")
        self.assertEqual(req.message, "hello")
        self.assertEqual(req.surface, "chat")
        self.assertEqual(req.mode, "owner_sage")

    def test_request_model_rejects_missing_workspace_id(self):
        with self.assertRaises(ValidationError):
            SageChatRequest(message="hello")

    def test_request_model_rejects_missing_message(self):
        with self.assertRaises(ValidationError):
            SageChatRequest(workspace_id="ws-1")

    def test_request_model_accepts_explicit_mode(self):
        req = SageChatRequest(workspace_id="ws-1", message="hello", mode="owner_sage")
        self.assertEqual(req.mode, "owner_sage")

    def test_request_model_rejects_invalid_mode(self):
        with self.assertRaises(ValidationError):
            SageChatRequest(workspace_id="ws-1", message="hello", mode="customer_live")

    def test_request_model_accepts_explicit_surface(self):
        req = SageChatRequest(workspace_id="ws-1", message="hello", surface="mobile")
        self.assertEqual(req.surface, "mobile")

    def test_voice_task_request_defaults(self):
        req = SageVoiceTaskRequest(workspace_id="ws-1", transcript="hello")
        self.assertEqual(req.workspace_id, "ws-1")
        self.assertEqual(req.transcript, "hello")
        self.assertEqual(req.source_channel, "mobile_voice")

    def test_mode_gate_rejects_non_owner_sage(self):
        from server_modules.sage_agent_runtime_service import ALLOWED_MODES

        self.assertNotIn("customer_live", ALLOWED_MODES)
        self.assertNotIn("studio_agent", ALLOWED_MODES)
        self.assertNotIn("deployed", ALLOWED_MODES)
        self.assertIn("owner_sage", ALLOWED_MODES)

    def test_response_contract_keys(self):
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider") as mock_provider,
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_profile.return_value = {"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}}
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_provider.return_value = ("openai", {"api_key": "test"})
            mock_generate.return_value = ("Reply", {"model": "gpt-4o"}, "openai", "")

            result = asyncio.run(
                __import__("server_modules.sage_agent_runtime_service", fromlist=["handle_sage_chat"]).handle_sage_chat(
                    workspace_id="ws-1", message="hello",
                )
            )

        required_keys = {
            "message",
            "error",
            "used_context",
            "tool_calls",
            "available_tools",
            "blocked_tools",
            "approvals_required",
            "memory_updates",
            "proof_log",
            "proof_log_id",
            "trace_id",
        }
        self.assertTrue(required_keys.issubset(set(result.keys())))

    def test_prompt_includes_identity_guardrails(self):
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile") as mock_profile,
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files") as mock_files,
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block") as mock_mem,
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider") as mock_provider,
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback") as mock_generate,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            mock_profile.return_value = {"profile": {"user_name": "", "identity_summary": "", "communication_style": "", "recurring_responsibility": "", "standing_rules": []}}
            mock_files.return_value = {}
            mock_mem.return_value = ""
            mock_provider.return_value = ("deepseek", {"api_key": "test-key"})
            mock_generate.return_value = ("Ok", {}, "deepseek", "")

            asyncio.run(
                __import__("server_modules.sage_agent_runtime_service", fromlist=["handle_sage_chat"]).handle_sage_chat(
                    workspace_id="ws-1", message="hello",
                )
            )

            system_prompt = mock_generate.call_args[0][3]
            routed_context = mock_generate.call_args[0][0]
            self.assertIn("ai assistant", system_prompt.lower())
            self.assertIn("user's personal ai assistant", system_prompt.lower())
            self.assertIn("don't wait to be micromanaged", system_prompt.lower())
            self.assertIn("explicit confirmation", system_prompt.lower())
            self.assertIn("never write xml", system_prompt.lower())
            self.assertTrue(routed_context["disable_provider_fallback"])
            self.assertNotIn("cannot send messages", system_prompt.lower())


if __name__ == "__main__":
    unittest.main()
